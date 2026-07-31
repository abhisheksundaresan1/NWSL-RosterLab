"""Ask — natural-language scouting.

Restructured from the old "Scout Assistant" tab:
  - the free-text box leads. It is the differentiated feature and used to sit
    below a row of buttons, off the fold.
  - the canned searches became suggestion chips underneath, doubling as examples
    of what can be asked. They remain deterministic, instant, free, and still do
    not consume a scout query.
  - one results area serves both canned and agent answers.
  - results are persisted in session_state. Previously they were rendered inline
    and vanished on the next rerun (any widget interaction wiped the answer).
  - the rate counter is quiet and the "not available" caveat moved into help text.

The agent itself is untouched: check_rate_limit / get_cached / run_scout_query
are called exactly as before, so rate limiting and caching behave identically.
"""

from __future__ import annotations

import streamlit as st

from src.agent.canned import CANNED_SEARCHES, run_canned
from src.agent.scout import check_rate_limit, get_cached, run_scout_query
from src.ui import components as c
from src.ui import state, theme

_RESULT_KEY = "ask_result"        # dict: kind, title, caption, payload, tools, cached
_PENDING_KEY = "ask_pending"      # a chip click queues its query for the next run


def render() -> None:
    season = state.get_season()
    min_minutes = state.get_min_minutes(season)

    theme.section(
        "Ask",
        subtitle="Describe the player you're looking for in plain English.",
        eyebrow_text=f"NWSL {season}",
    )

    allowed, remaining = check_rate_limit()

    # --- The input leads ----------------------------------------------------
    query = st.text_area(
        "Your scouting request",
        placeholder="e.g. find me a ball-progressing center back",
        height=88,
        key="scout_query_input",
        disabled=not allowed,
        label_visibility="collapsed",
        help=("Answers are built only from numbers in this dataset. Age is supported; "
              "salary, contract, nationality and market value are not in the data, and "
              "the assistant will say so rather than guess."),
    )

    run_col, status_col = st.columns([1, 4], vertical_alignment="center")
    with run_col:
        submitted = st.button(
            "Ask" if allowed else "Session limit reached",
            type="primary", disabled=not allowed, key="scout_btn", width="stretch",
        )
    with status_col:
        used = 8 - remaining
        st.caption(f"{used}/8 AI queries used this session · suggestions below are free")

    # --- Suggestion chips ---------------------------------------------------
    st.caption("Or start from one of these — instant, no AI cost:")
    chip_cols = st.columns(len(CANNED_SEARCHES))
    for i, search in enumerate(CANNED_SEARCHES):
        with chip_cols[i]:
            with st.container(key=f"chip_{i}"):
                if st.button(f"{search['icon']} {search['label']}",
                             key=f"canned_{i}", width="stretch"):
                    df, description = run_canned(search["label"], season, min_minutes)
                    st.session_state[_RESULT_KEY] = {
                        "kind": "table", "title": search["label"],
                        "caption": description, "payload": df,
                    }
                    st.rerun()

    # --- Run the agent ------------------------------------------------------
    pending = st.session_state.pop(_PENDING_KEY, None)
    to_run = pending or (query.strip() if submitted else None)
    if to_run:
        cached = get_cached(to_run)
        if cached:
            st.session_state[_RESULT_KEY] = {
                "kind": "agent", "title": to_run, "payload": cached,
                "tools": [], "cached": True,
            }
        else:
            with st.spinner(f"Scouting… ({remaining - 1} queries remaining after this)"):
                result, tools = run_scout_query(to_run, season, min_minutes)
            st.session_state[_RESULT_KEY] = {
                "kind": "agent", "title": to_run, "payload": result,
                "tools": tools, "cached": False,
            }
        st.rerun()
    elif submitted and not query.strip():
        st.warning("Enter a scouting request first.")

    # --- One results surface ------------------------------------------------
    theme.rule()
    result = st.session_state.get(_RESULT_KEY)
    if not result:
        c.empty_state(
            "Results will appear here.",
            "Ask a question above, or pick a suggestion.",
        )
        return

    if result["kind"] == "table":
        theme.section(result["title"], subtitle=result.get("caption") or None)
        df = result["payload"]
        if df is None or df.empty:
            st.warning("No players found. Try a different season or lower the minutes floor.")
        else:
            st.dataframe(c.dash_blanks(df), hide_index=True, width="stretch")
    else:
        theme.eyebrow("YOUR QUESTION")
        st.markdown(f"**{result['title']}**")
        _render_agent(result["payload"], result.get("tools", []), result.get("cached", False))


def _render_agent(result: str, tools_used: list[str], cached: bool) -> None:
    """Render the agent's SHORTLIST + REASONING output."""
    if "SHORTLIST:" in result and "REASONING:" in result:
        shortlist, reasoning = result.split("REASONING:", 1)
        st.markdown("**Shortlist**")
        st.markdown(shortlist.replace("SHORTLIST:", "").strip())
        st.markdown("**Why these players**")
        st.markdown(reasoning.strip())
    else:
        st.markdown(result)

    if cached:
        st.caption("Cached result — this did not use a query slot.")
    elif tools_used:
        with st.expander("How this was answered", expanded=False):
            for t in tools_used:
                st.caption(f"→ {t}")
