"""
Scout agent — natural-language scouting requests → player shortlist with reasoning.

Model: claude-sonnet-4-6 (multi-step tool-use reasoning)
Tools compute; the model only narrates real returned numbers.

Cost controls:
- Per-session rate limit: _RATE_LIMIT queries (checked/incremented via st.session_state)
- Query result cache: keyed on normalized query string in st.session_state
  (only successful results are cached — error strings are never stored)
- Prompt caching: cache_control on system prompt + tool defs (GA, no beta header)
  Note: caching may not engage if the static prefix is under Anthropic's token minimum
  (~1,024 tokens). Applied as a minor optimization — do not depend on it.
- Tool output cap: MAX_ROWS=15 and LEAN_COLS enforced in tools.py
- Agent loop cap: _MAX_ITERATIONS = 4
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv

load_dotenv()

_MODEL = "claude-sonnet-4-6"
_MAX_ITERATIONS = 4
_RATE_LIMIT = 8  # scout queries per Streamlit session

_SYSTEM = """\
You are a sharp NWSL scouting assistant backed by real player data from American Soccer Analysis.

RULES — follow exactly:
1. Call describe_capabilities first whenever you are unsure which positions, metrics, or seasons \
are available. You must confirm the validity of any position name or filter before querying.
2. Age filters (min_age / max_age) ARE supported — pass them to query_players. \
About 14% of players have unknown age and are silently excluded when an age filter is applied; \
mention this caveat if it affects a small result set. \
If the user requests a filter listed in unsupported_filters (salary, contract, \
nationality, etc.), state plainly: "That filter is not available in this dataset." \
Do not estimate, proxy, or infer it from other data.
3. NEVER invent, estimate, or recall any statistic from training data. Every number you \
cite must appear in a tool result from this conversation.
4. Only mention players whose names appear in a tool result from this conversation.
5. After gathering data, return your answer in exactly this format:

SHORTLIST:
| Player | Team | Key Metric | Value |
|--------|------|------------|-------|
(3–5 rows, one player per row, cite the most relevant metric for each)

REASONING:
(One sentence per player, citing at least one specific number from tool results. \
Analyst voice — specific, grounded, no marketing copy.)

6. Keep the shortlist to 3–5 players.\
"""


def _summarize_inputs(inputs: dict) -> str:
    """Format tool inputs into a compact readable string for the UI trace."""
    if not inputs:
        return "()"
    parts = [f"{k}={v!r}" for k, v in inputs.items()]
    return "(" + ", ".join(parts) + ")"


def _execute_tool(name: str, inputs: dict) -> str:
    """Dispatch a tool call and return the JSON result string."""
    from src.agent.tools import describe_capabilities, query_players, get_player_detail

    try:
        if name == "describe_capabilities":
            result = describe_capabilities()
        elif name == "query_players":
            # Enforce limit cap at the dispatch layer regardless of model request
            if "limit" in inputs:
                inputs["limit"] = min(int(inputs["limit"]), 15)
            result = query_players(**inputs)
        elif name == "get_player_detail":
            result = get_player_detail(**inputs)
        else:
            result = {"error": f"unknown tool: {name}"}
    except Exception as e:
        result = {"error": f"tool execution failed: {e}"}

    return json.dumps(result, default=str)


def _is_error_result(text: str) -> bool:
    """True if text is an error/limit message that should not be cached."""
    if not text or not text.strip():
        return True
    lowered = text.lower()
    return any(
        phrase in lowered
        for phrase in [
            "unavailable",
            "api key",
            "session limit",
            "session scout limit",
            "iteration limit",
            "scout agent",
            "error",
        ]
    )


# ---------------------------------------------------------------------------
# Session state helpers (import streamlit lazily to keep module importable
# in non-Streamlit contexts such as tests)
# ---------------------------------------------------------------------------

def _get_st():
    import streamlit as st
    return st


def check_rate_limit() -> tuple[bool, int]:
    """Returns (allowed, remaining). Reads from st.session_state."""
    st = _get_st()
    count = st.session_state.get("scout_query_count", 0)
    remaining = max(0, _RATE_LIMIT - count)
    return count < _RATE_LIMIT, remaining


def _increment_count():
    st = _get_st()
    st.session_state["scout_query_count"] = st.session_state.get("scout_query_count", 0) + 1


def _cache_key(query: str) -> str:
    return " ".join(query.lower().split())


def get_cached(query: str) -> str | None:
    st = _get_st()
    return st.session_state.get("scout_cache", {}).get(_cache_key(query))


def _set_cached(query: str, result: str):
    st = _get_st()
    if "scout_cache" not in st.session_state:
        st.session_state["scout_cache"] = {}
    st.session_state["scout_cache"][_cache_key(query)] = result


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

def run_scout_query(
    query: str,
    season: str = "2025",
    min_minutes: int = 500,
) -> tuple[str, list[str]]:
    """
    Run the scout agent on a natural-language scouting query.

    Returns
    -------
    (result_text, tools_used)
        result_text : always a non-empty string (answer or error message)
        tools_used  : list of "tool_name(inputs)" strings for UI trace;
                      empty list on cache hit or early failure
    """
    # 1. Check query cache first — cache hits bypass rate limit
    cached = get_cached(query)
    if cached:
        return cached, []

    # 2. Check rate limit
    allowed, remaining = check_rate_limit()
    if not allowed:
        return (
            "Session scout limit reached (8 queries). Refresh the page to start a new session.",
            [],
        )

    # 3. Check API key
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return "Scout agent unavailable — ANTHROPIC_API_KEY is not set.", []

    # 4. Build client (lazy import, consistent with insight.py pattern)
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
    except Exception as e:
        return f"Scout agent unavailable — could not initialize client: {e}", []

    # Inject season/min_minutes into query context so the model can pass them to tools
    augmented_query = (
        f"{query}\n\n"
        f"[Context: use season='{season}', min_minutes={min_minutes} as defaults "
        f"unless the user's request specifies otherwise.]"
    )

    messages: list[dict] = [{"role": "user", "content": augmented_query}]
    tools_used: list[str] = []

    # Import tool defs here to keep module-level imports light
    from src.agent.tools import TOOL_DEFS

    # 5. Agent loop
    for _ in range(_MAX_ITERATIONS):
        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=1500,
                # Prompt caching: cache_control on system + last tool def.
                # Caching is GA (no beta header). May not engage below ~1,024 token
                # threshold — applied as a minor optimization.
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=TOOL_DEFS,
                messages=messages,
            )
        except Exception as e:
            result = f"Scout agent error: {e}"
            _increment_count()
            return result, tools_used

        # Append assistant turn to message history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Extract final text
            text_blocks = [b.text for b in response.content if b.type == "text"]
            result = "\n".join(text_blocks).strip() if text_blocks else ""
            if not result:
                result = "The scout agent returned an empty response. Please try again."

            _increment_count()

            # Cache only genuine answers, never error strings
            if not _is_error_result(result):
                _set_cached(query, result)

            return result, tools_used

        if response.stop_reason != "tool_use":
            # Unexpected stop — return whatever text is available
            text_blocks = [b.text for b in response.content if b.type == "text"]
            result = "\n".join(text_blocks).strip() or "Scout agent stopped unexpectedly."
            _increment_count()
            return result, tools_used

        # Execute all tool calls in this turn
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tools_used.append(f"{block.name}{_summarize_inputs(block.input)}")
            result_str = _execute_tool(block.name, dict(block.input))
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                }
            )

        messages.append({"role": "user", "content": tool_results})

    # Iteration cap hit
    _increment_count()
    return (
        "Scout agent reached the iteration limit without completing. Try a more specific query.",
        tools_used,
    )
