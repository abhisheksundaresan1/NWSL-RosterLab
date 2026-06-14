# -*- coding: utf-8 -*-
"""
Canned searches -- deterministic, zero LLM cost.

Each search is a named query_players call. Results render as a DataFrame table.
These handle casual browsing; the free-text Scout agent is for genuine scouting queries.
"""

from __future__ import annotations

import pandas as pd

from src.agent.tools import query_players

# Display columns for canned search results
DISPLAY_COLS = [
    "player_name",
    "team_abbreviation",
    "value_score",
    "goals_added_p90",
    "xga_p90",
]

DISPLAY_LABELS = {
    "player_name": "Player",
    "team_abbreviation": "Team",
    "value_score": "Value Score",
    "goals_added_p90": "g+ / 90",
    "xga_p90": "xG+xA / 90",
}

CANNED_SEARCHES: list[dict] = [
    {
        "label": "Top undervalued strikers",
        "icon": "⚡",
        "description": "Strikers ranked highest by value score -- g+ z-score within the ST cohort.",
        "kwargs": {"position": "ST", "sort_by": "value_score", "limit": 10},
    },
    {
        "label": "Best progressive midfielders",
        "icon": "\U0001f3af",
        "description": "Central mids ranked by passing g+ -- ball-progressors and creators.",
        "kwargs": {"position": "CM", "sort_by": "ga_passing", "limit": 10},
    },
    {
        "label": "Elite defensive mids",
        "icon": "\U0001f6e1",
        "description": "Defensive mids ranked by interrupting g+ -- tacklers and interceptors.",
        "kwargs": {"position": "DM", "sort_by": "ga_interrupting", "limit": 10},
    },
    {
        "label": "Top xG strikers",
        "icon": "\U0001f945",
        "description": "Strikers generating the highest shot quality per 90 -- clinical finishers.",
        "kwargs": {"position": "ST", "sort_by": "xgoals_p90", "limit": 10},
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

    available = [c for c in DISPLAY_COLS if c in df.columns]
    df = df[available].rename(columns=DISPLAY_LABELS)
    return df, search["description"]
