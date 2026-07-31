"""
src/ui/state.py — shared UI state, resolved before any page body runs.

st.navigation reruns only the active page, so module-level globals no longer
survive between pages the way they did with the old single-script + sidebar
layout. Anything more than one page needs (season, the minutes floor) lives in
session_state and is read through these helpers.

This matters for correctness, not just tidiness: several cache keys embed these
values (e.g. cardpng__{name}__{season}__{pos}__{min}_v6), so they must resolve
to the same thing on every page or previously rendered cards silently miss.

No data or metric logic here — that stays in src/analysis and src/data.
"""

from __future__ import annotations

import streamlit as st

from src.data.sources import AVAILABLE_SEASONS
from src.analysis.season import IN_SEASON_YEAR

DEFAULT_SEASON = IN_SEASON_YEAR          # 2026 — the live season is the default view

_SEASON_KEY = "rl_season"
_MINUTES_KEY = "rl_min_minutes"


# --- Season -----------------------------------------------------------------

def seasons() -> list[str]:
    return list(AVAILABLE_SEASONS)


def most_recent_completed_season() -> str:
    """The newest season that has actually finished.

    Used by This week so the Undervalued XI is never gated behind the season
    picker: it is always drawn from a completed season and labelled as such,
    rather than showing "requires a completed season" on the landing page.
    """
    return next((s for s in AVAILABLE_SEASONS if s != IN_SEASON_YEAR), "2025")


def get_season() -> str:
    return st.session_state.setdefault(_SEASON_KEY, DEFAULT_SEASON)


def set_season(value: str) -> None:
    st.session_state[_SEASON_KEY] = value


def is_in_season(season: str | None = None) -> bool:
    return (season or get_season()) == IN_SEASON_YEAR


def season_label(season: str) -> str:
    """Human label distinguishing the live season from completed ones."""
    return f"{season} · in progress" if season == IN_SEASON_YEAR else f"{season} · completed"


# --- Minutes floor ----------------------------------------------------------

def default_min_minutes(season: str, games_est: int | None = None) -> int:
    """Qualifying floor. In-season it scales with games played (50% of games x 90,
    floor 180); completed seasons use the long-standing 500."""
    if season == IN_SEASON_YEAR and games_est:
        return max(180, games_est * 45)
    return 500


def get_min_minutes(season: str, games_est: int | None = None) -> int:
    key = f"{_MINUTES_KEY}_{season}"
    return st.session_state.setdefault(key, default_min_minutes(season, games_est))


def set_min_minutes(season: str, value: int) -> None:
    st.session_state[f"{_MINUTES_KEY}_{season}"] = value
