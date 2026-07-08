"""
src/analysis/movement.py — Risers & Fallers across 2026 snapshots.

Pure functions: snapshot DataFrames in → movement DataFrame / list[dict] out.
No UI, no API calls. Reuses the formation, slot coordinates, threshold constants
and sentinel from drops.py so the Risers/Fallers cards match the Undervalued XI.

Snapshots are written by scripts/snapshot.py as
data/snapshots/value_2026_YYYY-MM-DD.parquet — each a full build_player_value_table
output (un-stabilized). Movement stabilizes both snapshots before diffing so a
small-sample player can't top the risers list on noise.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.analysis.ranking import apply_stabilization, rank_by_position
from src.analysis.drops import (
    FORMATION_SLOTS,
    SLOT_COORDS,
    UNDERVALUED_TOP_N,
    UNDERVALUED_TOP_PCT,
    _empty_slot,
)

_SNAP_DIR = Path(__file__).resolve().parents[2] / "data" / "snapshots"


def list_snapshots(season: str = "2026") -> list[str]:
    """Sorted list of snapshot dates (YYYY-MM-DD) available for the season."""
    prefix = f"value_{season}_"
    dates = [
        p.stem[len(prefix):]
        for p in _SNAP_DIR.glob(f"{prefix}*.parquet")
    ]
    return sorted(dates)


def load_snapshot(date: str, season: str = "2026") -> pd.DataFrame:
    """Load data/snapshots/value_{season}_{date}.parquet."""
    path = _SNAP_DIR / f"value_{season}_{date}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No snapshot at {path}")
    return pd.read_parquet(path)


def compute_movement(
    snap_new: pd.DataFrame,
    snap_old: pd.DataFrame,
    K: int = 300,
    min_minutes_new: int = 270,
) -> pd.DataFrame:
    """Compute per-player value-score movement between two snapshots.

    Join is on player_id (stable across pulls) — NOT player_name+position, since
    ASA's general_position and name spellings can drift between snapshots. The
    latest snapshot's position/name/team are used for display.

    Both snapshots are stabilized (apply_stabilization) AFTER filtering the NEW
    snapshot to `min_minutes_new`, so tiny-sample players are excluded before the
    within-position z-score is computed and can't dominate movement.

    Returns one row per player present in BOTH snapshots (and above the minutes
    floor in the new one), sorted by delta_value_score descending. Columns:
      player_id, player_name, team_name, team_abbreviation, position,
      value_score_new, value_score_old, delta_value_score,
      rank_new, rank_old, delta_rank,
      minutes_new, games_est, sample_label
    """
    # Filter the new snapshot to the qualifying pool, then stabilize both.
    new_q = snap_new[snap_new["minutes_played"] >= min_minutes_new].copy()
    if new_q.empty:
        return _empty_movement()

    new_s = apply_stabilization(new_q, K=K)
    old_s = apply_stabilization(snap_old, K=K)

    # Within-position rank (1 = best) in each stabilized table.
    new_s["rank_new"] = new_s.groupby("position")["value_score"].rank(
        ascending=False, method="min"
    ).astype(int)
    old_s["rank_old"] = old_s.groupby("position")["value_score"].rank(
        ascending=False, method="min"
    ).astype(int)

    new_cols = new_s[[
        "player_id", "player_name", "team_name", "team_abbreviation",
        "position", "value_score", "minutes_played", "rank_new",
    ]].rename(columns={"value_score": "value_score_new", "minutes_played": "minutes_new"})

    old_cols = old_s[["player_id", "value_score", "rank_old"]].rename(
        columns={"value_score": "value_score_old"}
    )

    merged = new_cols.merge(old_cols, on="player_id", how="inner")
    if merged.empty:
        return _empty_movement()

    merged["delta_value_score"] = (merged["value_score_new"] - merged["value_score_old"]).round(3)
    merged["delta_rank"] = merged["rank_old"] - merged["rank_new"]  # positive = climbed
    merged["games_est"] = (merged["minutes_new"] // 90).astype(int)
    merged["sample_label"] = merged.apply(
        lambda r: f"{int(r['minutes_new'])} min / ~{int(r['games_est'])} games", axis=1
    )

    return merged.sort_values("delta_value_score", ascending=False).reset_index(drop=True)


def _empty_movement() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "player_id", "player_name", "team_name", "team_abbreviation", "position",
        "value_score_new", "value_score_old", "delta_value_score",
        "rank_new", "rank_old", "delta_rank",
        "minutes_new", "games_est", "sample_label",
    ])


def _select_movers_xi(movement: pd.DataFrame, rising: bool) -> list[dict]:
    """Shared slot-filler for risers (rising=True) and fallers (rising=False).

    One player per formation slot, chosen by delta_value_score in the right
    direction. A slot is only filled if the best available mover ranks within
    the position's top-N / top-PCT window (same threshold as the Undervalued XI)
    AND moves in the correct direction; otherwise the slot gets a sentinel so a
    flat/insignificant mover never pads the card.
    """
    picked_ids: set[str] = set()
    result: list[dict] = []

    for slot_def in FORMATION_SLOTS:
        slot, position, line = slot_def["slot"], slot_def["position"], slot_def["line"]
        x, y = SLOT_COORDS[slot]

        cohort = movement[movement["position"] == position].copy()
        cohort = cohort.sort_values("delta_value_score", ascending=not rising)

        if cohort.empty:
            result.append(_empty_slot(slot, position, line, x, y))
            continue

        threshold = max(UNDERVALUED_TOP_N, int(len(cohort) * UNDERVALUED_TOP_PCT))

        picked = None
        for rank_0, (_, row) in enumerate(cohort.head(threshold).iterrows()):
            if row["player_id"] in picked_ids:
                continue
            delta = row["delta_value_score"]
            if (rising and delta <= 0) or (not rising and delta >= 0):
                continue  # not actually moving in the target direction
            picked = row
            picked_ids.add(row["player_id"])
            break

        if picked is None:
            result.append(_empty_slot(slot, position, line, x, y))
            continue

        result.append({
            "slot": slot, "position": position, "line": line, "x": x, "y": y,
            "player_name": picked["player_name"],
            "team_name": picked.get("team_name", ""),
            "team_abbreviation": picked.get("team_abbreviation", ""),
            "value_score": float(picked["delta_value_score"]),  # card shows the delta
            "minutes_played": int(picked["minutes_new"]),
            "rank_in_position": int(picked["rank_new"]),
            "cohort_size": len(cohort),
        })

    return result


def select_risers_xi(movement: pd.DataFrame) -> list[dict]:
    """Biggest positive value-score movers, one per formation slot."""
    return _select_movers_xi(movement, rising=True)


def select_fallers_xi(movement: pd.DataFrame) -> list[dict]:
    """Biggest negative value-score movers, one per formation slot."""
    return _select_movers_xi(movement, rising=False)
