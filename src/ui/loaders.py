"""
src/ui/loaders.py — Streamlit-cached wrappers around the analysis layer.

Ported unchanged from the old monolithic app.py so multiple pages can share
them. These only cache and adapt; all computation still happens in
src/analysis and src/data.

Cache-key note: the player-card and insight keys embed season / min_minutes /
position. Those formats are deliberately preserved from the pre-restructure app
so cards already rendered by users stay valid.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis.ranking import (
    build_player_value_table, rank_by_position, apply_stabilization,
)
from src.analysis.college_ranking import build_college_value_table
from src.analysis.movement import load_snapshot, list_snapshots
from src.analysis.newcomers import build_historical_player_ids
from src.analysis.season import load_season_value_table, IN_SEASON_YEAR
from src.data.sources import (
    fetch_player_goals_added, fetch_player_xgoals, fetch_players, fetch_teams,
    fetch_player_birthdates,
)
from src.explain.insight import one_line_insight
from src.share.card import render_player_card


# --- Tables -----------------------------------------------------------------

@st.cache_data(show_spinner="Loading college data...", ttl=86400)
def load_college_tables() -> dict:
    return build_college_value_table(season="2026")


@st.cache_data(show_spinner="Loading player data...", ttl=86400)
def load_value_table(min_minutes: int, season: str) -> pd.DataFrame:
    ga = fetch_player_goals_added(season_name=season)
    xg = fetch_player_xgoals(season_name=season)
    pl = fetch_players()
    tm = fetch_teams()
    bd = fetch_player_birthdates()
    return build_player_value_table(
        ga, xg, pl, tm, birthdates=bd, min_minutes=min_minutes, season=season
    )


@st.cache_data(show_spinner="Loading in-season snapshot...", ttl=86400)
def load_in_season_table(min_minutes: int, snapshot_date: str) -> pd.DataFrame:
    """Latest snapshot, filtered to the qualifying pool, then stabilized (K=300).
    Filtering BEFORE stabilization keeps the within-position z-score comparable
    to completed seasons. Keyed on snapshot date so a new snapshot busts it."""
    vt_raw = load_snapshot(snapshot_date)
    return apply_stabilization(vt_raw[vt_raw["minutes_played"] >= min_minutes].copy(), K=300)


def season_table(min_minutes: int, season: str) -> pd.DataFrame:
    """Season-aware table used by cards and insights.

    Delegates to src.analysis.season so the UI, the cards and the Scout agent
    all quote identical numbers; completed seasons go through the cached loader
    to avoid a redundant rebuild."""
    if season == IN_SEASON_YEAR:
        return load_season_value_table(season, min_minutes=min_minutes)
    return load_value_table(min_minutes, season)


@st.cache_data(show_spinner=False, ttl=86400)
def cached_historical_ids() -> set[str]:
    """2019-2025 g+ player_ids, for tagging in-season newcomers."""
    return build_historical_player_ids()


@st.cache_data(show_spinner=False, ttl=86400)
def snapshot_games_est(snapshot_date: str) -> int:
    """Games played, from the 90th-percentile minutes of a snapshot — reflects
    the top-load starters rather than the median, which understates it."""
    vt_raw = load_snapshot(snapshot_date)
    return int(vt_raw["minutes_played"].quantile(0.90) // 90)


def latest_snapshot() -> str | None:
    snaps = list_snapshots(IN_SEASON_YEAR)
    return snaps[-1] if snaps else None


# --- Insight + card ---------------------------------------------------------

@st.cache_data(show_spinner=False)
def _cached_insight(player_name: str, season: str, min_minutes: int, position: str) -> str:
    """Cache only successful LLM output. Raises on failure so nothing is stored."""
    full = season_table(min_minutes, season)
    cohort = rank_by_position(full, position).copy()
    cohort["_rank"] = range(1, len(cohort) + 1)
    match = cohort[cohort["player_name"] == player_name]
    if match.empty:
        raise RuntimeError("player not found in cohort")
    result = one_line_insight(match.iloc[0].to_dict(), cohort)
    if result is None:
        raise RuntimeError("insight generation failed — skip cache")
    return result


def get_insight(player_name: str, season: str, min_minutes: int, position: str) -> str | None:
    try:
        return _cached_insight(player_name, season, min_minutes, position)
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=3600)
def cached_player_card(player_name: str, season: str, min_minutes: int, position: str,
                       card_version: int = 6) -> bytes:
    """Cache rendered PNG bytes. card_version busts stale cards after a visual
    change to src/share/card.py — bump it whenever the card design changes.
    (v6 = "Broadcast Dossier" redesign.)"""
    full = season_table(min_minutes, season)
    cohort = rank_by_position(full, position).copy()
    cohort["_rank"] = range(1, len(cohort) + 1)
    match = cohort[cohort["player_name"] == player_name]
    if match.empty:
        raise ValueError(f"{player_name} not found in cohort")
    insight = get_insight(player_name, season, min_minutes, position)
    return render_player_card(match.iloc[0].to_dict(), cohort, season, insight_text=insight)


def fallback_insight(row: pd.Series, cohort: pd.DataFrame) -> str:
    """Deterministic stand-in when the LLM is unavailable. No API call."""
    action_labels = {
        "ga_shooting": "shooting", "ga_dribbling": "dribbling",
        "ga_passing": "passing", "ga_receiving": "receiving",
        "ga_interrupting": "defensive actions", "ga_fouling": "fouling",
    }
    action_vals = {col: float(row.get(col, 0.0)) for col in action_labels}
    top_col = max(action_vals, key=action_vals.get)
    return (
        f"Ranks #{int(row['_rank'])} of {len(cohort)} {row['position']}s on g+/90 "
        f"({row['goals_added_p90']:.2f} vs. position avg "
        f"{round(cohort['goals_added_p90'].mean(), 2):.2f}), "
        f"with her strongest contribution from {action_labels[top_col]} "
        f"({action_vals[top_col]:+.2f} g+)."
    )
