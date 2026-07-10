"""
src/analysis/newcomers.py — first-year NWSL players in 2026.

A "newcomer" is any player who appears in 2026 g+ but has NO g+ row in any
season 2019–2025. That set includes domestic college free-agent signings,
international transfers, and long-gap returnees — the function makes no further
distinction, so the UI must label it "Newcomers · First Year in NWSL" rather
than "Rookies" or "College Free Agents".

Pure functions except build_historical_player_ids, which reads (and, if a season
is uncached, pulls once) the raw g+ parquets to guarantee complete history — an
incomplete history would mislabel veterans as newcomers.
"""

from __future__ import annotations

import pandas as pd

from src.data.sources import fetch_player_goals_added
from src.analysis.ranking import rank_by_position
from src.analysis.drops import (
    FORMATION_SLOTS,
    SLOT_COORDS,
    UNDERVALUED_TOP_N,
    UNDERVALUED_TOP_PCT,
    _empty_slot,
)

_HISTORY_SEASONS = ["2019", "2020", "2021", "2022", "2023", "2024", "2025"]


def build_historical_player_ids() -> set[str]:
    """Union of player_ids across ALL 2019–2025 g+ pulls.

    Uses the per-season cache when present; pulls a season once (refresh=False)
    if its parquet is missing. Completeness matters: a season absent from this
    set would cause its veterans to be mislabelled as 2026 newcomers.
    """
    ids: set[str] = set()
    for year in _HISTORY_SEASONS:
        try:
            ga = fetch_player_goals_added(season_name=year)
        except Exception as exc:  # noqa: BLE001 — a missing season shouldn't crash the app
            print(f"[build_historical_player_ids] {year} unavailable: {exc}")
            continue
        if "player_id" in ga.columns:
            ids.update(ga["player_id"].dropna().astype(str).tolist())
    return ids


def identify_newcomers(
    season_2026_ga: pd.DataFrame,
    historical_player_ids: set[str],
) -> pd.DataFrame:
    """Rows of 2026 g+ whose player_id never appears in 2019–2025.

    Returns the filtered raw g+ frame (same schema as the input). Callers pass
    this through build_player_value_table to get value scores.
    """
    if "player_id" not in season_2026_ga.columns:
        return season_2026_ga.iloc[0:0].copy()
    ids = season_2026_ga["player_id"].astype(str)
    mask = ~ids.isin(historical_player_ids)
    return season_2026_ga[mask].copy()


def _college_value_map() -> dict[str, float]:
    """Normalized college player name → college-value percentile (0–100).

    Bridges a first-year NWSL player to how she rated in college by conference-
    adjusted attacking output. NWSL abolished the college draft in the 2024 CBA,
    so this is framed as college VALUE, not a draft ranking. (The source column
    from build_college_value_table is still named `draft_percentile` internally —
    that belongs to the separate Draft Board feature — but here it means the
    player's college output percentile.)

    Best-effort: any failure (missing NCAA cache, schema drift) yields an empty
    map and the bridge is silently skipped.
    """
    try:
        from src.analysis.college_ranking import build_college_value_table
        board = build_college_value_table(season="2026")["draft_board"]
        if board.empty or "name" not in board.columns or "draft_percentile" not in board.columns:
            return {}
        board = board.dropna(subset=["name", "draft_percentile"])
        return {
            str(n).lower().strip(): float(p)
            for n, p in zip(board["name"], board["draft_percentile"])
        }
    except Exception as exc:  # noqa: BLE001
        print(f"[newcomers] college bridge unavailable: {exc}")
        return {}


def select_newcomer_watch_xi(newcomer_vt: pd.DataFrame) -> list[dict]:
    """One newcomer per formation slot by (stabilized) value_score.

    Same top-N / top-PCT threshold + sentinel as the Undervalued XI: a slot is
    only filled if the best available newcomer ranks within the position window;
    otherwise a sentinel keeps a weak player off the card. Annotates each pick
    with `college_value_percentile` when the name matches the college value table.
    """
    college = _college_value_map()
    picked_names: set[str] = set()
    result: list[dict] = []

    for slot_def in FORMATION_SLOTS:
        slot, position, line = slot_def["slot"], slot_def["position"], slot_def["line"]
        x, y = SLOT_COORDS[slot]

        try:
            cohort = rank_by_position(newcomer_vt, position)
        except ValueError:
            result.append(_empty_slot(slot, position, line, x, y))
            continue

        threshold = max(UNDERVALUED_TOP_N, int(len(cohort) * UNDERVALUED_TOP_PCT))

        picked = None
        rank_in_pos = None
        for rank_0, (_, row) in enumerate(cohort.head(threshold).iterrows()):
            if row["player_name"] in picked_names:
                continue
            picked = row
            rank_in_pos = rank_0 + 1
            picked_names.add(row["player_name"])
            break

        if picked is None:
            result.append(_empty_slot(slot, position, line, x, y))
            continue

        entry = {
            "slot": slot, "position": position, "line": line, "x": x, "y": y,
            "player_name": picked["player_name"],
            "team_name": picked.get("team_name", ""),
            "team_abbreviation": picked.get("team_abbreviation", ""),
            "value_score": float(picked["value_score"]),
            "minutes_played": int(picked["minutes_played"]),
            "rank_in_position": rank_in_pos,
            "cohort_size": len(cohort),
        }
        pct = college.get(str(picked["player_name"]).lower().strip())
        if pct is not None:
            entry["college_value_percentile"] = pct
        result.append(entry)

    return result
