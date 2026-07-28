"""
src/analysis/season.py — one season-aware value table for the whole app.

Single source of truth so the Rankings list, the player cards, the LLM insight
lines and the Scout agent all quote the SAME number for the same player.

Completed seasons (2019-2025) come from the cached ASA season pull.
The in-season year (2026) comes from the latest committed weekly snapshot and is
Bayesian-stabilized (see apply_stabilization) — filtering to the qualifying pool
BEFORE stabilizing so the within-position z-score stays comparable to completed
seasons. Without routing every consumer through here, the Scout agent would cite
un-stabilized 2026 numbers that disagree with what the UI displays.
"""

from __future__ import annotations

import pandas as pd

IN_SEASON_YEAR = "2026"
STABILIZATION_K = 300


def load_season_value_table(season: str, min_minutes: int = 500) -> pd.DataFrame:
    """Return the value table for `season`, filtered to `min_minutes`.

    For the in-season year this is the latest stabilized snapshot; for completed
    seasons it is the standard cached build. Falls back to the standard build if
    the in-season year has no snapshots yet.
    """
    season = str(season)

    if season == IN_SEASON_YEAR:
        from src.analysis.movement import list_snapshots, load_snapshot
        from src.analysis.ranking import apply_stabilization

        snaps = list_snapshots(IN_SEASON_YEAR)
        if snaps:
            vt = load_snapshot(snaps[-1])
            vt = vt[vt["minutes_played"] >= min_minutes].copy()
            return apply_stabilization(vt, K=STABILIZATION_K)
        # No snapshot yet — fall through to the live season pull.

    from src.data.sources import (
        fetch_player_goals_added,
        fetch_player_xgoals,
        fetch_players,
        fetch_teams,
        fetch_player_birthdates,
    )
    from src.analysis.ranking import build_player_value_table

    ga = fetch_player_goals_added(season_name=season)
    xg = fetch_player_xgoals(season_name=season)
    pl = fetch_players()
    tm = fetch_teams()
    bd = fetch_player_birthdates()
    return build_player_value_table(
        ga, xg, pl, tm, birthdates=bd, min_minutes=min_minutes, season=season
    )
