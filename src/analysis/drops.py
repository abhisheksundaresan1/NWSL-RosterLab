"""
src/analysis/drops.py — Undervalued XI selection logic.

Pure function: DataFrame in → list[dict] out. No UI, no API calls.

select_undervalued_xi() picks the highest-value eligible outfield player
for each of 10 formation slots (4-3-3) whose season Best XI (both First
and Second XI) has NOT been selected. GK is deliberately excluded — our
model covers only outfield positions.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.data.ground_truth import fetch_best_xi, load_name_aliases, normalize_name
from src.analysis.ranking import rank_by_position

# ---------------------------------------------------------------------------
# Formation definition  (4-3-3, 10 outfield slots, no GK)
# ---------------------------------------------------------------------------

FORMATION_SLOTS: list[dict] = [
    {"slot": "FB_L", "position": "FB", "line": "DEF"},
    {"slot": "CB_L", "position": "CB", "line": "DEF"},
    {"slot": "CB_R", "position": "CB", "line": "DEF"},
    {"slot": "FB_R", "position": "FB", "line": "DEF"},
    {"slot": "DM",   "position": "DM", "line": "MID"},
    {"slot": "CM",   "position": "CM", "line": "MID"},
    {"slot": "AM",   "position": "AM", "line": "MID"},
    {"slot": "W_L",  "position": "W",  "line": "FWD"},
    {"slot": "ST",   "position": "ST", "line": "FWD"},
    {"slot": "W_R",  "position": "W",  "line": "FWD"},
]

# ---------------------------------------------------------------------------
# Availability threshold — filters injury-shortened players
# ---------------------------------------------------------------------------
# Regular-season game counts per NWSL season (used to compute 75% threshold).
# A player must have played ≥ 75% of possible minutes to appear in the
# Undervalued XI. This prevents injury-shortened campaigns from surfacing
# (e.g. a top player who missed half the season due to injury was not "snubbed").
_SEASON_GAMES: dict[int, int] = {
    2019: 24,
    2021: 24,
    2022: 22,
    2023: 22,
    2024: 26,
    2025: 26,
    2026: 26,   # projected full season; 75% floor = 1755 min (matches 2025)
}
_DEFAULT_GAMES = 26

# Only fill a slot if the best eligible player at that position ranks in the
# top UNDERVALUED_TOP_N players OR the top UNDERVALUED_TOP_PCT of her position
# cohort (whichever is more generous). Prevents league-average fillers.
UNDERVALUED_TOP_N   = 3
UNDERVALUED_TOP_PCT = 0.30


def undervalued_min_minutes(season: int | str) -> int:
    """Return the 75%-of-possible-minutes threshold for a given NWSL season."""
    games = _SEASON_GAMES.get(int(season), _DEFAULT_GAMES)
    return int(games * 90 * 0.75)


# VerticalPitch (mplsoccer statsbomb) coordinates: x=width (0-80), y=length (0-120)
# Attack at top (high y). These map each slot to its pitch position.
SLOT_COORDS: dict[str, tuple[float, float]] = {
    "FB_L": ( 7, 22),
    "CB_L": (27, 22),
    "CB_R": (53, 22),
    "FB_R": (73, 22),
    "DM":   (40, 45),
    "CM":   (26, 62),
    "AM":   (54, 62),
    "W_L":  (10, 88),
    "ST":   (40, 100),
    "W_R":  (70, 88),
}


def _seasons_with_both_tiers(best_xi_df: pd.DataFrame) -> list[int]:
    """Return seasons that have both 'first' and 'second' team_selection rows."""
    counts = best_xi_df.groupby("season")["team_selection"].nunique()
    return sorted(counts[counts == 2].index.tolist(), reverse=True)


def select_undervalued_xi(
    value_table: pd.DataFrame,
    season: str | int,
    min_minutes: int = 500,
) -> list[dict]:
    """
    Return 10 dicts (one per formation slot) for the highest-value outfield
    players in `value_table` who were NOT named in the season's Best XI
    (both First XI and Second XI are excluded — 22 players total per season).

    GK excluded — our model has no POSITION_WEIGHTS for goalkeepers.
    The card should be labelled "Outfield XI".

    Parameters
    ----------
    value_table : DataFrame from build_player_value_table(), already filtered
                  to min_minutes (or unfiltered — we apply the floor here).
    season      : season year as int or str (e.g. 2025 or "2025").
    min_minutes : minimum minutes played floor (default 500).

    Returns
    -------
    list[dict] with keys:
        slot, position, line, x, y,
        player_name, team_name, team_abbreviation,
        value_score, minutes_played, rank_in_position, cohort_size

    Raises
    ------
    ValueError if the season lacks both First + Second XI data.
    """
    season_int = int(season)

    # --- Load Best XI + assert both tiers present --------------------------
    best_xi_all = fetch_best_xi()
    season_bxi = best_xi_all[best_xi_all["season"] == season_int]

    tiers_present = set(season_bxi["team_selection"].unique())
    missing = {"first", "second"} - tiers_present
    if missing:
        available = _seasons_with_both_tiers(best_xi_all)
        raise ValueError(
            f"Season {season_int} is missing Best XI tier(s): {missing}. "
            f"Seasons with both tiers: {available}. "
            "Re-run fetch_best_xi(refresh=True) to pull latest Wikipedia data."
        )

    # --- Build exclusion set (both canonical and aliased names) -------------
    # Include BOTH the original wiki/seed spelling AND the ASA alias so that
    # a wrong or incomplete alias cannot cause a Best XI player to slip through.
    alias_map = load_name_aliases()   # {normalized_canonical: normalized_asa}

    exclusion_set: set[str] = set()
    for n in season_bxi["player_name"]:
        orig = normalize_name(n)
        exclusion_set.add(orig)                       # canonical (wiki/seed) form
        exclusion_set.add(alias_map.get(orig, orig))  # ASA alias form (may equal orig)

    # --- Apply minutes floor to value_table --------------------------------
    # Use the higher of the caller's floor and the full-season threshold so
    # injury-shortened players (who weren't "snubbed") are excluded.
    effective_min = max(min_minutes, undervalued_min_minutes(season_int))
    vt = value_table[value_table["minutes_played"] >= effective_min].copy()

    # Pre-normalize player names for fast lookup
    vt["_norm_name"] = vt["player_name"].apply(normalize_name)

    # --- Fill each slot ----------------------------------------------------
    picked_names: set[str] = set()       # normalized names already used
    result: list[dict] = []

    for slot_def in FORMATION_SLOTS:
        slot     = slot_def["slot"]
        position = slot_def["position"]
        line     = slot_def["line"]
        x, y     = SLOT_COORDS[slot]

        try:
            full_cohort = rank_by_position(vt, position)
        except ValueError:
            # No players at this position above the minutes floor
            result.append(_empty_slot(slot, position, line, x, y))
            continue

        cohort_size = len(full_cohort)
        threshold   = max(UNDERVALUED_TOP_N, int(cohort_size * UNDERVALUED_TOP_PCT))

        # Only consider players who rank within the threshold window. If no
        # non-excluded player falls within the window, leave the slot empty
        # rather than filling it with a league-average player.
        picked      = None
        rank_in_pos = None
        for rank_0, (_, row) in enumerate(full_cohort.head(threshold).iterrows()):
            norm = normalize_name(row["player_name"])
            if norm in exclusion_set or norm in picked_names:
                continue
            picked      = row
            rank_in_pos = rank_0 + 1
            picked_names.add(norm)
            break

        if picked is None:
            result.append(_empty_slot(slot, position, line, x, y))
            continue

        result.append({
            "slot":              slot,
            "position":          position,
            "line":              line,
            "x":                 x,
            "y":                 y,
            "player_name":       picked["player_name"],
            "team_name":         picked.get("team_name", ""),
            "team_abbreviation": picked.get("team_abbreviation", ""),
            "value_score":       float(picked["value_score"]),
            "minutes_played":    int(picked["minutes_played"]),
            "rank_in_position":  rank_in_pos,
            "cohort_size":       cohort_size,
        })

    return result


def _empty_slot(slot: str, position: str, line: str, x: float, y: float) -> dict:
    return {
        "slot": slot, "position": position, "line": line,
        "x": x, "y": y,
        "player_name": "—", "team_name": "", "team_abbreviation": "",
        "value_score": 0.0, "minutes_played": 0,
        "rank_in_position": None, "cohort_size": 0,
    }


def best_xi_excluded_names(season: str | int) -> tuple[list[str], list[str]]:
    """Return (first_xi_names, second_xi_names) for the season, for UI display."""
    season_int = int(season)
    bxi = fetch_best_xi()
    s = bxi[bxi["season"] == season_int]
    first  = sorted(s[s["team_selection"] == "first"]["player_name"].tolist())
    second = sorted(s[s["team_selection"] == "second"]["player_name"].tolist())
    return first, second
