"""
College player analysis — draft board, draftable profiles, trends, conference adjustment.

All functions are pure: they take DataFrames in, return DataFrames out. No I/O here.

Main entry point: build_college_value_table()
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
DATA_DIR = Path(__file__).resolve().parents[2] / "data"

DRAFT_HISTORY_PATH = DATA_DIR / "nwsl_draft_history.csv"
NCAA_CACHE_PATH = RAW_DIR / "ncaa_players.parquet"

# ---------------------------------------------------------------------------
# Conference tiers
# Power conferences produce more NWSL draftees; we z-score within tier
# so a .8 goals/game in the ACC and .8 in a mid-major aren't treated equally.
# ---------------------------------------------------------------------------

CONFERENCE_TIERS = {
    # Tier 1 — historically dominant in NWSL draft production
    "ACC": 1, "Big Ten": 1, "SEC": 1, "Big 12": 1, "Pac-12": 1, "Big East": 1,
    # Tier 2 — strong mid-majors
    "WCC": 2, "CAA": 2, "American Athletic": 2, "Atlantic 10": 2,
    "Mountain West": 2, "MAC": 2, "AAC": 2,
    # Tier 3 — everything else
}

def conference_tier(conf: str) -> int:
    return CONFERENCE_TIERS.get(str(conf).strip(), 3)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_ncaa() -> pd.DataFrame:
    return pd.read_parquet(NCAA_CACHE_PATH)


def load_draft_history() -> pd.DataFrame:
    df = pd.read_csv(DRAFT_HISTORY_PATH)
    df["player_name_norm"] = df["player_name"].str.lower().str.strip()
    return df


# ---------------------------------------------------------------------------
# 1. Conference-adjusted z-score ranking
# ---------------------------------------------------------------------------

def compute_draft_board(df: pd.DataFrame, season: str = "2026") -> pd.DataFrame:
    """
    Rank players in a given season by attacking output, adjusted for
    conference tier via z-scoring within tier.

    Returns one row per player with a draft_score column (higher = better).
    """
    s = df[df["season"] == season].copy()
    s["conf_tier"] = s["conference"].apply(conference_tier)

    # Composite attacking metric: weight goals more than assists, penalise low shot volume
    s["raw_score"] = (
        s["goals_pg"].fillna(0) * 3.0 +
        s["assists_pg"].fillna(0) * 1.5 +
        s["sog_pg"].fillna(0) * 0.5
    )

    # Z-score within conference tier so power-conference players aren't auto-penalised
    s["draft_score"] = s.groupby("conf_tier")["raw_score"].transform(
        lambda x: (x - x.mean()) / max(x.std(), 0.01)
    ).round(3)

    # Also keep a percentile rank (0-100) for display
    s["draft_percentile"] = (
        s["draft_score"].rank(pct=True) * 100
    ).round(1)

    return s.sort_values("draft_score", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 2. Draftable profile — what did draft picks look like in college?
# ---------------------------------------------------------------------------

def _norm_name(name: str) -> str:
    return str(name).lower().strip()


def compute_draftable_profiles(ncaa: pd.DataFrame, draft: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    For each draft pick (2021-2024), find their college stats the season
    before they were drafted. Then build a fingerprint of median stats
    grouped by position + draft round — so we can say "a Round 1 Forward
    typically posted X goals/game, Y assists/game in their final college season."

    Only includes attacking positions (F, M, FM) where goal stats are meaningful.

    Returns:
        draftees   — one row per matched pick with their college stats
        fingerprint — median stats by position_group + draft_round
    """
    ncaa = ncaa.copy()
    ncaa["name_norm"] = ncaa["name"].apply(_norm_name)

    records = []
    for _, pick in draft.iterrows():
        draft_year = str(pick["year"])
        name_norm = pick["player_name_norm"]

        for season in [draft_year, str(int(draft_year) - 1)]:
            match = ncaa[(ncaa["name_norm"] == name_norm) & (ncaa["season"] == season)]
            if not match.empty:
                row = match.iloc[0].to_dict()
                row["draft_year"] = pick["year"]
                row["draft_round"] = pick["round"]
                row["draft_pick"] = pick["pick"]
                row["drafted_by"] = pick["nwsl_team"]
                records.append(row)
                break

    if not records:
        return pd.DataFrame(), pd.DataFrame()

    draftees = pd.DataFrame(records)

    # Normalise position into broad groups
    def _pos_group(pos: str) -> str | None:
        pos = str(pos).upper().strip()
        if any(p in pos for p in ["F", "W"]):
            return "Forward / Winger"
        if "M" in pos:
            return "Midfielder"
        return None  # exclude defenders and GKs — goal stats aren't meaningful

    draftees["position_group"] = draftees["position"].apply(_pos_group)
    attacking = draftees[draftees["position_group"].notna()].copy()

    stat_cols = ["goals_pg", "assists_pg", "points_pg", "sog_pg", "goals", "assists", "gp"]
    available = [c for c in stat_cols if c in attacking.columns]

    grp = attacking.groupby(["position_group", "draft_round"])
    fingerprint = grp[available].median().round(3)
    fingerprint["n_players"] = grp["name"].count()
    fingerprint = fingerprint.reset_index().rename(columns={"draft_round": "round"})
    fingerprint = fingerprint[["position_group", "round", "n_players"] + available]

    return draftees, fingerprint.sort_values(["position_group", "round"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3. Player trend analysis — year-over-year improvement
# ---------------------------------------------------------------------------

def compute_trends(df: pd.DataFrame) -> pd.DataFrame:
    """
    For players appearing in consecutive seasons, compute year-over-year
    delta in goals_pg and assists_pg.

    Returns one row per player-season-pair with delta columns.
    Only includes players with at least 2 seasons of data.
    """
    df = df.copy()
    df["season_int"] = df["season"].astype(int)
    df = df.sort_values(["name", "school", "season_int"])

    key = ["name", "school"]
    shifted = df[key + ["season_int", "goals_pg", "assists_pg", "points_pg"]].copy()
    shifted["season_int"] = shifted["season_int"] + 1  # align with next season
    shifted = shifted.rename(columns={
        "goals_pg": "prev_goals_pg",
        "assists_pg": "prev_assists_pg",
        "points_pg": "prev_points_pg",
    })

    merged = df.merge(shifted, on=key + ["season_int"], how="inner")
    merged["goals_pg_delta"]   = (merged["goals_pg"]   - merged["prev_goals_pg"]).round(3)
    merged["assists_pg_delta"] = (merged["assists_pg"] - merged["prev_assists_pg"]).round(3)
    merged["points_pg_delta"]  = (merged["points_pg"]  - merged["prev_points_pg"]).round(3)

    return merged.sort_values("goals_pg_delta", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 4. Main entry point
# ---------------------------------------------------------------------------

def build_college_value_table(season: str = "2026") -> dict:
    """
    Load all college data and return a dict of analysis tables:

        "draft_board"         — ranked current-season players
        "draftee_stats"       — college stats of historical draft picks
        "draftable_summary"   — median stats per draft round
        "trends"              — year-over-year player improvements
        "all_seasons"         — raw combined NCAA data (all seasons)
    """
    ncaa = load_ncaa()
    draft = load_draft_history()

    draft_board = compute_draft_board(ncaa, season=season)

    draftee_result = compute_draftable_profiles(ncaa, draft)
    if isinstance(draftee_result, tuple):
        draftee_stats, draftable_summary = draftee_result
    else:
        draftee_stats = draftable_summary = pd.DataFrame()

    trends = compute_trends(ncaa)

    return {
        "draft_board": draft_board,
        "draftee_stats": draftee_stats,
        "draftable_summary": draftable_summary,
        "trends": trends,
        "all_seasons": ncaa,
    }


if __name__ == "__main__":
    tables = build_college_value_table(season="2026")

    print("=== DRAFT BOARD (top 20, 2025-26) ===")
    board = tables["draft_board"]
    cols = ["name", "school", "conference", "position", "class_year",
            "goals_pg", "assists_pg", "sog_pg", "draft_score", "draft_percentile"]
    print(board[[c for c in cols if c in board.columns]].head(20).to_string(index=False))

    print("\n=== DRAFTABLE PROFILE — median stats by round ===")
    print(tables["draftable_summary"].to_string(index=False))

    print("\n=== BIGGEST YEAR-OVER-YEAR IMPROVEMENTS ===")
    trend_cols = ["name", "school", "season", "goals_pg", "prev_goals_pg", "goals_pg_delta"]
    t = tables["trends"]
    print(t[[c for c in trend_cols if c in t.columns]].head(15).to_string(index=False))

    print(f"\nDraftee match rate: {len(tables['draftee_stats'])} / {len(load_draft_history())} picks matched to NCAA data")
