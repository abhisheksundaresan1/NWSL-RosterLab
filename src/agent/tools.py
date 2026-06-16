"""
Agent tools — wrap the data/analysis layer for the Scout agent.

RULE (see CLAUDE.md): tools compute, the model only narrates.
All metric math lives in src/analysis/; these functions only filter, sort, and slice.
"""

from __future__ import annotations

import pandas as pd

# Columns returned by query_players — lean set to keep tool outputs small.
# get_player_detail returns all of these plus cohort context fields.
LEAN_COLS = [
    "player_name",
    "team_abbreviation",
    "position",
    "age",
    "minutes_played",
    "value_score",
    "weighted_ga_p90",
    "goals_added_p90",
    "goals_added_total",
    "xgoals_p90",
    "xassists_p90",
    "xga_p90",
    # Per-90 action types (for position-aware sorting and agent reasoning)
    "ga_shooting_p90",
    "ga_dribbling_p90",
    "ga_passing_p90",
    "ga_receiving_p90",
    "ga_interrupting_p90",
    "ga_fouling_p90",
]

MAX_ROWS = 15  # hard cap on query_players output regardless of caller's limit arg


def describe_capabilities() -> dict:
    """
    Return the exact positions, seasons, sortable metrics, and unsupported filters.
    Call this first so the model never hallucinates valid position names or filter support.
    No API call needed — pure static metadata.
    """
    return {
        "positions": ["ST", "W", "AM", "CM", "DM", "FB", "CB"],
        "seasons": ["2025", "2024", "2023", "2022", "2021", "2020", "2019"],
        "sort_metrics": [
            "value_score",
            "weighted_ga_p90",
            "goals_added_p90",
            "goals_added_total",
            "xgoals_p90",
            "xassists_p90",
            "xga_p90",
            # Per-90 action type metrics (preferred over raw totals for fair comparison)
            "ga_shooting_p90",
            "ga_dribbling_p90",
            "ga_passing_p90",
            "ga_receiving_p90",
            "ga_interrupting_p90",
            "ga_fouling_p90",
            "minutes_played",
        ],
        "supported_age_filter": {
            "min_age": "integer (inclusive)",
            "max_age": "integer (inclusive)",
            "note": "~14% of players have unknown age and are silently excluded when an age filter is applied.",
        },
        "unsupported_filters": [
            "salary",
            "contract",
            "cost",
            "transfer_fee",
            "nationality",
            "height",
            "weight",
            "club_budget",
        ],
    }


def query_players(
    position: str | None = None,
    season: str = "2025",
    min_minutes: int = 500,
    sort_by: str = "value_score",
    ascending: bool = False,
    limit: int = 10,
    min_age: int | None = None,
    max_age: int | None = None,
) -> list[dict]:
    """
    Filter and rank NWSL players. Returns up to min(limit, MAX_ROWS) rows as plain dicts.

    All computation delegated to build_player_value_table + rank_by_position.
    Returns an empty list (with an error key) if the position is invalid.
    Age filters silently exclude players whose age is unknown (~14% of the dataset).
    """
    from src.data.sources import (
        fetch_player_goals_added,
        fetch_player_xgoals,
        fetch_players,
        fetch_teams,
        fetch_player_birthdates,
    )
    from src.analysis.ranking import build_player_value_table, rank_by_position

    try:
        ga = fetch_player_goals_added(season_name=season)
        xg = fetch_player_xgoals(season_name=season)
        pl = fetch_players()
        tm = fetch_teams()
        bd = fetch_player_birthdates()
        full = build_player_value_table(ga, xg, pl, tm, birthdates=bd, min_minutes=min_minutes, season=season)
    except Exception as e:
        return [{"error": f"data load failed: {e}"}]

    if position:
        try:
            df = rank_by_position(full, position).copy()
        except ValueError as e:
            return [{"error": str(e)}]
    else:
        df = full.copy()

    df["_rank"] = range(1, len(df) + 1)

    # Age filters: silently drop rows with unknown age when a filter is requested
    if (min_age is not None or max_age is not None) and "age" in df.columns:
        df = df[df["age"].notna()].copy()
        if min_age is not None:
            df = df[df["age"] >= min_age]
        if max_age is not None:
            df = df[df["age"] <= max_age]

    if sort_by not in df.columns:
        sort_by = "value_score"
    df = df.sort_values(sort_by, ascending=ascending)

    actual_limit = min(int(limit), MAX_ROWS)
    cols = ["_rank"] + LEAN_COLS
    available_cols = [c for c in cols if c in df.columns]
    return df[available_cols].head(actual_limit).round(3).to_dict(orient="records")


def get_player_detail(
    player_name: str,
    season: str = "2025",
    min_minutes: int = 0,
) -> dict:
    """
    Return full stats + cohort context for one player by exact name.
    Includes _rank, _cohort_size, and position averages for the model to cite.
    Returns {"error": "..."} if the player is not found.
    """
    from src.data.sources import (
        fetch_player_goals_added,
        fetch_player_xgoals,
        fetch_players,
        fetch_teams,
        fetch_player_birthdates,
    )
    from src.analysis.ranking import build_player_value_table, rank_by_position

    try:
        ga = fetch_player_goals_added(season_name=season)
        xg = fetch_player_xgoals(season_name=season)
        pl = fetch_players()
        tm = fetch_teams()
        bd = fetch_player_birthdates()
        full = build_player_value_table(ga, xg, pl, tm, birthdates=bd, min_minutes=min_minutes, season=season)
    except Exception as e:
        return {"error": f"data load failed: {e}"}

    match = full[full["player_name"].str.lower() == player_name.lower()]
    if match.empty:
        return {"error": f"player '{player_name}' not found"}

    row = match.iloc[0]
    position = row["position"]

    try:
        cohort = rank_by_position(full, position).copy()
    except ValueError:
        cohort = full[full["position"] == position].copy()

    cohort["_rank"] = range(1, len(cohort) + 1)
    rank_match = cohort[cohort["player_name"].str.lower() == player_name.lower()]
    rank = int(rank_match.iloc[0]["_rank"]) if not rank_match.empty else None

    result = {k: round(v, 3) if isinstance(v, float) else v for k, v in row.to_dict().items()}
    result["_rank"] = rank
    result["_cohort_size"] = len(cohort)
    result["_position_avg_ga_p90"] = round(float(cohort["goals_added_p90"].mean()), 3)
    result["_position_avg_xga_p90"] = round(float(cohort["xga_p90"].mean()), 3)
    result["_position_avg_xgoals_p90"] = round(float(cohort["xgoals_p90"].mean()), 3)
    result["_position_avg_xassists_p90"] = round(float(cohort["xassists_p90"].mean()), 3)

    return result


# ---------------------------------------------------------------------------
# Anthropic tool schemas
# cache_control on the last tool definition caches the full tool block prefix.
# This requires the combined system + tool tokens to exceed ~1,024 tokens to
# engage Anthropic's prompt cache — treat as a minor optimization, not guaranteed.
# ---------------------------------------------------------------------------

TOOL_DEFS = [
    {
        "name": "describe_capabilities",
        "description": (
            "Returns the exact positions, seasons, sortable metrics, and unsupported filters "
            "available in this dataset. Call this first whenever you are unsure whether a "
            "position name, metric, or filter is supported. The unsupported_filters list tells "
            "you which user requests (age, salary, nationality, etc.) you must refuse explicitly."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "query_players",
        "description": (
            "Filter and rank NWSL players by position, season, minimum minutes, and any metric. "
            "Returns a lean shortlist (max 15 rows). Always call describe_capabilities first to "
            "confirm valid position names and metric keys before calling this tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "position": {
                    "type": "string",
                    "description": "One of ST, W, AM, CM, DM, FB, CB — omit for all positions.",
                },
                "season": {
                    "type": "string",
                    "description": "Season year string e.g. '2025'. Default '2025'.",
                },
                "min_minutes": {
                    "type": "integer",
                    "description": "Minimum minutes played to qualify. Default 500.",
                },
                "sort_by": {
                    "type": "string",
                    "description": "Metric column to sort by. Default 'value_score'.",
                },
                "ascending": {
                    "type": "boolean",
                    "description": "Sort direction. False (default) = best first.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return. Default 10, capped at 15.",
                },
                "min_age": {
                    "type": "integer",
                    "description": "Minimum age (inclusive). Players with unknown age are excluded silently.",
                },
                "max_age": {
                    "type": "integer",
                    "description": "Maximum age (inclusive). Players with unknown age are excluded silently.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_player_detail",
        "description": (
            "Get full metric profile + cohort context for one specific player by exact name. "
            "Returns her rank within her position, cohort size, and position averages for "
            "comparison. Use this after query_players to get richer context before citing "
            "a player in your reasoning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "player_name": {
                    "type": "string",
                    "description": "Exact player_name string from a prior query_players result.",
                },
                "season": {
                    "type": "string",
                    "description": "Season year. Default '2025'.",
                },
                "min_minutes": {
                    "type": "integer",
                    "description": "Minimum minutes threshold for cohort. Default 0.",
                },
            },
            "required": ["player_name"],
        },
        # Marks the end of the static tool-definition block for prompt caching.
        # Everything up to and including this entry is eligible to be cached.
        "cache_control": {"type": "ephemeral"},
    },
]
