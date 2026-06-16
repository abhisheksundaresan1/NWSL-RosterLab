# -*- coding: utf-8 -*-
"""
Canned searches -- deterministic, zero LLM cost.

Each search is a named query_players call. Results render as a DataFrame table.
These handle casual browsing; the free-text Scout agent is for genuine scouting queries.
"""

from __future__ import annotations

import pandas as pd

from src.agent.tools import query_players

# Base display columns for all canned search results.
# Each search also injects its highlight_col (the sort metric) so users can
# see exactly what drove the ranking.
BASE_DISPLAY_COLS = [
    "player_name",
    "team_abbreviation",
    "minutes_played",
    "value_score",
]

DISPLAY_LABELS = {
    "player_name": "Player",
    "team_abbreviation": "Team",
    "minutes_played": "Minutes",
    "value_score": "Value Score",
    "weighted_ga_p90": "Weighted g+/90",
    "goals_added_p90": "g+/90 (raw)",
    "xga_p90": "xG+xA/90",
    "ga_passing_p90": "Passing g+/90",
    "ga_interrupting_p90": "Interrupting g+/90",
    "xgoals_p90": "xG/90",
}

CANNED_SEARCHES: list[dict] = [
    {
        "label": "Top undervalued strikers",
        "icon": "⚡",
        "description": "Strikers ranked highest by position-weighted value score.",
        "kwargs": {"position": "ST", "sort_by": "value_score", "limit": 10},
        # sort_by key to display in the results table for context
        "highlight_col": "weighted_ga_p90",
        "highlight_label": "Weighted g+/90",
    },
    {
        "label": "Best progressive midfielders",
        "icon": "\U0001f3af",
        "description": "Central mids ranked by passing g+ per 90 -- ball-progressors and creators. (Per-90, not season total -- fairly compares rotation players to starters.)",
        "kwargs": {"position": "CM", "sort_by": "ga_passing_p90", "limit": 10},
        "highlight_col": "ga_passing_p90",
        "highlight_label": "Passing g+/90",
    },
    {
        "label": "Elite defensive mids",
        "icon": "\U0001f6e1",
        "description": "Defensive mids ranked by interrupting g+ per 90 -- tacklers and interceptors. (Per-90, not season total.)",
        "kwargs": {"position": "DM", "sort_by": "ga_interrupting_p90", "limit": 10},
        "highlight_col": "ga_interrupting_p90",
        "highlight_label": "Interrupting g+/90",
    },
    {
        "label": "Top xG strikers",
        "icon": "\U0001f945",
        "description": "Strikers generating the highest shot quality per 90 -- clinical finishers.",
        "kwargs": {"position": "ST", "sort_by": "xgoals_p90", "limit": 10},
        "highlight_col": "xgoals_p90",
        "highlight_label": "xG/90",
    },
]


def run_canned(label: str, season: str, min_minutes: int) -> tuple[pd.DataFrame, str]:
    """
    Execute a canned search. Returns (DataFrame, description).
    No LLM call, no Anthropic client instantiation.
    """
    search = next((s for s in CANNED_SEARCHES if s["label"] == label), None)
    if search is None:
        raise ValueError(f"Unknown canned search: {label!r}")

    rows = query_players(season=season, min_minutes=min_minutes, **search["kwargs"])
    df = pd.DataFrame(rows)

    if df.empty or "error" in df.columns:
        return pd.DataFrame(), search["description"]

    # Build display columns: base set + the sort metric (highlight_col) so the
    # user sees exactly what drove the ranking
    highlight = search.get("highlight_col", "")
    display_cols = BASE_DISPLAY_COLS.copy()
    if highlight and highlight not in display_cols:
        display_cols.append(highlight)

    available = [c for c in display_cols if c in df.columns]
    df = df[available].rename(columns=DISPLAY_LABELS)
    return df, search["description"]
