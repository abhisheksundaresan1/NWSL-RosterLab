"""
Analysis layer -- turn raw ASA tables into a value ranking by position.

Pure functions only: DataFrame in -> DataFrame out. No I/O, no UI.
This is the product's opinion and its spine (see CLAUDE.md).

Value score methodology:
  Each g+ action type is converted to per-90, multiplied by a position-specific
  weight from POSITION_WEIGHTS, summed into weighted_ga_p90, then z-scored within
  position. Raw (unweighted) goals_added_p90 is kept as a separate reference column.
  Weights encode an editorial scouting judgment -- edit POSITION_WEIGHTS freely to
  test alternative views.
"""

from __future__ import annotations

import ast
import unicodedata
import warnings as _warnings

import pandas as pd


# ---------------------------------------------------------------------------
# Position-specific action weights for the value score.
#
# Rows = position. Columns = g+ action type (all per-90).
# Higher weight = this action matters more for evaluating this position.
# These are editorial scouting judgments, not derived from outcome data.
# Edit freely -- the weights only affect value_score and weighted_ga_p90.
# ---------------------------------------------------------------------------
POSITION_WEIGHTS: dict[str, dict[str, float]] = {
    #         shooting  dribbling  passing  receiving  interrupting  fouling
    "ST": {"shooting": 1.5, "dribbling": 0.8, "passing": 0.6, "receiving": 1.2, "interrupting": 0.3, "fouling": 0.5},
    "W":  {"shooting": 1.1, "dribbling": 1.4, "passing": 1.0, "receiving": 1.0, "interrupting": 0.4, "fouling": 0.5},
    "AM": {"shooting": 0.9, "dribbling": 1.0, "passing": 1.4, "receiving": 1.2, "interrupting": 0.5, "fouling": 0.5},
    "CM": {"shooting": 0.6, "dribbling": 0.8, "passing": 1.4, "receiving": 1.2, "interrupting": 1.0, "fouling": 0.5},
    "DM": {"shooting": 0.4, "dribbling": 0.6, "passing": 1.3, "receiving": 1.0, "interrupting": 1.5, "fouling": 0.6},
    "FB": {"shooting": 0.4, "dribbling": 0.9, "passing": 1.2, "receiving": 1.0, "interrupting": 1.3, "fouling": 0.6},
    "CB": {"shooting": 0.2, "dribbling": 0.4, "passing": 1.2, "receiving": 0.9, "interrupting": 1.6, "fouling": 0.7},
}

# Maps weight-dict key -> raw ga_* column name in the DataFrame
_ACTION_WEIGHT_COLS: dict[str, str] = {
    "shooting":     "ga_shooting",
    "dribbling":    "ga_dribbling",
    "passing":      "ga_passing",
    "receiving":    "ga_receiving",
    "interrupting": "ga_interrupting",
    "fouling":      "ga_fouling",
}

# Action types present in the g+ data column.
_ACTION_TYPES = ["Dribbling", "Fouling", "Interrupting", "Passing", "Receiving", "Shooting"]

# Most recent season for season-aware age reference date.
_CURRENT_SEASON = "2025"


def _normalize_name(name: str) -> str:
    """NFKD → ASCII → lowercase → whitespace collapse for name matching."""
    s = unicodedata.normalize("NFKD", str(name))
    s = s.encode("ascii", "ignore").decode("ascii")
    return " ".join(s.lower().split())


def _unpack_goals_added(goals_added: pd.DataFrame) -> pd.DataFrame:
    """Parse the stringified `data` column and expand into per-action-type columns.

    Adds:
      goals_added_total  -- sum of goals_added_raw across all action types
      ga_dribbling, ga_fouling, ga_interrupting, ga_passing, ga_receiving, ga_shooting
    """
    def _parse_row(raw):
        try:
            return ast.literal_eval(raw)
        except Exception:
            return []

    parsed = goals_added["data"].apply(_parse_row)

    df = goals_added.copy()
    df["goals_added_total"] = parsed.apply(
        lambda actions: sum(a.get("goals_added_raw", 0.0) for a in actions)
    )

    for action in _ACTION_TYPES:
        col = f"ga_{action.lower()}"
        df[col] = parsed.apply(
            lambda actions, a=action: next(
                (x.get("goals_added_raw", 0.0) for x in actions if x.get("action_type") == a), 0.0
            )
        )

    assert df["goals_added_total"].isna().sum() == 0, "NaN in goals_added_total after unpack"
    return df


def build_player_value_table(
    goals_added: pd.DataFrame,
    xgoals: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    birthdates: pd.DataFrame | None = None,
    min_minutes: int = 500,
    season: str = _CURRENT_SEASON,
) -> pd.DataFrame:
    """Build a tidy, ranked player value table for all non-GK positions.

    Parameters
    ----------
    goals_added : raw g+ DataFrame from fetch_player_goals_added()
    xgoals      : raw xG DataFrame from fetch_player_xgoals()
    players     : player reference from fetch_players()
    teams       : team reference from fetch_teams()
    birthdates  : optional DataFrame from fetch_player_birthdates(); columns
                  [player_name_normalized, birthdate]. If None, age is omitted.
    min_minutes : minimum minutes threshold to qualify (default 500)
    season      : season string (e.g. "2025") — used to compute age-at-season

    Returns
    -------
    DataFrame sorted by position then value_score descending, with columns:
      player_name, team_name, team_abbreviation, position, age, minutes_played,
      value_score, weighted_ga_p90, goals_added_total, goals_added_p90,
      xgoals_p90, xassists_p90, xga_p90,
      ga_shooting_p90, ga_dribbling_p90, ga_passing_p90,
      ga_receiving_p90, ga_interrupting_p90, ga_fouling_p90,
      ga_dribbling, ga_passing, ga_shooting, ga_receiving, ga_interrupting, ga_fouling
    """
    # Step 1: Unpack nested g+ data column.
    df = _unpack_goals_added(goals_added)

    # Step 2: Join player names and team names.
    # team_id may be a comma-joined list (multi-team players); take the last entry as most recent.
    df["team_id"] = df["team_id"].apply(lambda v: str(v).split(",")[-1].strip())

    player_ref = players[["player_id", "player_name"]].drop_duplicates("player_id")
    xg_ref = xgoals[["player_id", "xgoals", "xassists", "xgoals_plus_xassists", "xpoints_added"]]
    team_ref = teams[["team_id", "team_name", "team_abbreviation"]]

    df = df.merge(player_ref, on="player_id", how="left")
    df = df.merge(xg_ref, on="player_id", how="left")
    df = df.merge(team_ref, on="team_id", how="left")

    df = df.rename(columns={"general_position": "position"})

    missing_names = df["player_name"].isna().sum()
    if missing_names > 0:
        _warnings.warn(f"{missing_names} rows have no player_name after join -- check player_id coverage.")

    # Step 3: Join player ages from Wikidata birthdates.
    # Age reference date: today for current/most-recent season; Dec 31 for past seasons.
    if birthdates is not None and not birthdates.empty:
        ref_date = (
            pd.Timestamp.today().normalize()
            if season == _CURRENT_SEASON
            else pd.Timestamp(f"{season}-12-31")
        )
        df["player_name_normalized"] = df["player_name"].apply(
            lambda n: _normalize_name(n) if pd.notna(n) else ""
        )
        bd = birthdates[["player_name_normalized", "birthdate"]].copy()
        bd["birthdate"] = pd.to_datetime(bd["birthdate"], errors="coerce")
        df = df.merge(bd, on="player_name_normalized", how="left")
        df["age"] = df["birthdate"].apply(
            lambda bd: int((ref_date - bd).days // 365.25) if pd.notna(bd) else None
        )
        df = df.drop(columns=["player_name_normalized", "birthdate"], errors="ignore")
    else:
        df["age"] = None

    # Step 5: Minutes filter.
    n_before = len(df)
    df = df[df["minutes_played"] >= min_minutes].copy()
    n_after = len(df)
    print(f"Minutes filter (>={min_minutes}): {n_before} -> {n_after} rows ({n_before - n_after} dropped)")
    assert len(df) > 0, f"No players survived the {min_minutes}-minute filter -- lower min_minutes?"

    # Step 6: Per-90 normalization -- total metrics and per-action-type.
    p90 = df["minutes_played"] / 90
    df["goals_added_p90"] = df["goals_added_total"] / p90
    df["xgoals_p90"]      = df["xgoals"]              / p90
    df["xassists_p90"]    = df["xassists"]             / p90
    df["xga_p90"]         = df["xgoals_plus_xassists"] / p90

    # Per-90 for each action type (used for position-weighted score and canned search sorting)
    for key, col in _ACTION_WEIGHT_COLS.items():
        df[f"{col}_p90"] = df[col] / p90

    # Step 7: Position-weighted g+ per 90.
    # For each player, multiply each action-type per-90 by the position-specific weight and sum.
    # Falls back to equal weights (1.0) for any position not in POSITION_WEIGHTS.
    def _weighted_ga(row: pd.Series) -> float:
        pos = row["position"]
        weights = POSITION_WEIGHTS.get(pos, {k: 1.0 for k in _ACTION_WEIGHT_COLS})
        return sum(
            row[f"{col}_p90"] * weights[key]
            for key, col in _ACTION_WEIGHT_COLS.items()
        )

    df["weighted_ga_p90"] = df.apply(_weighted_ga, axis=1)

    # Step 8: Value score -- z-score of weighted_ga_p90 within position.
    # (Previously z-score of unweighted goals_added_p90; raw g+/90 kept as reference column.)
    df["value_score"] = df.groupby("position")["weighted_ga_p90"].transform(
        lambda g: (g - g.mean()) / g.std() if g.std() > 0 else 0.0
    )
    df["value_score"] = df["value_score"].fillna(0.0)
    assert df["value_score"].isna().sum() == 0, "NaN in value_score after z-score"

    # Step 9: Select and order output columns.
    # player_id is carried through so snapshots can be joined on a stable key
    # (ASA name spellings and general_position can drift between pulls).
    out_cols = [
        "player_id",
        "player_name", "team_name", "team_abbreviation", "position", "age", "minutes_played",
        "value_score", "weighted_ga_p90", "goals_added_total", "goals_added_p90",
        "xgoals_p90", "xassists_p90", "xga_p90",
        # Per-90 action types (for canned searches and agent sorting)
        "ga_shooting_p90", "ga_dribbling_p90", "ga_passing_p90",
        "ga_receiving_p90", "ga_interrupting_p90", "ga_fouling_p90",
        # Raw season totals (for action-type bar charts in the UI)
        "ga_dribbling", "ga_passing", "ga_shooting", "ga_receiving", "ga_interrupting", "ga_fouling",
    ]
    df = df[out_cols].copy()

    round_cols = [
        "value_score", "weighted_ga_p90", "goals_added_p90",
        "xgoals_p90", "xassists_p90", "xga_p90",
        "ga_shooting_p90", "ga_dribbling_p90", "ga_passing_p90",
        "ga_receiving_p90", "ga_interrupting_p90", "ga_fouling_p90",
    ]
    df[round_cols] = df[round_cols].round(3)

    df = df.sort_values(["position", "value_score"], ascending=[True, False]).reset_index(drop=True)

    print("\nPlayers per position in output table:")
    print(df.groupby("position").size().to_string())

    return df


def apply_stabilization(vt: pd.DataFrame, K: int = 300) -> pd.DataFrame:
    """Shrink weighted_ga_p90 toward the position mean, then re-z-score.

    Bayesian shrinkage for small in-season samples:

        stabilized = (minutes * player_p90 + K * pos_mean_p90) / (minutes + K)

    A player with K minutes is pulled 50% toward her position's mean; with 3*K
    minutes she keeps 75% of her own number. This stops a player with one strong
    90-minute cameo from topping the table on noise.

    IMPORTANT: the z-score is computed over the rows already in `vt`. Callers
    must pre-filter `vt` to the qualifying (min_minutes) pool BEFORE calling this,
    so the distribution isn't dominated by near-zero-minute players and the
    resulting value_score stays comparable to completed-season tables (which
    z-score over their qualified pool too).

    K is the only tunable. Returns a copy; `weighted_ga_p90` and `value_score`
    are replaced with stabilized versions. All other columns are untouched.
    """
    df = vt.copy()
    if df.empty:
        return df

    pos_mean = df.groupby("position")["weighted_ga_p90"].transform("mean")
    minutes = df["minutes_played"].astype(float)
    df["weighted_ga_p90"] = (
        (minutes * df["weighted_ga_p90"] + K * pos_mean) / (minutes + K)
    )

    df["value_score"] = df.groupby("position")["weighted_ga_p90"].transform(
        lambda g: (g - g.mean()) / g.std() if g.std() > 0 else 0.0
    )
    df["value_score"] = df["value_score"].fillna(0.0)

    df["weighted_ga_p90"] = df["weighted_ga_p90"].round(3)
    df["value_score"] = df["value_score"].round(3)

    return df.sort_values(["position", "value_score"], ascending=[True, False]).reset_index(drop=True)


def rank_by_position(value_table: pd.DataFrame, position: str) -> pd.DataFrame:
    """Return rows for one position, sorted by value_score descending."""
    pos = position.upper()
    result = value_table[value_table["position"] == pos].copy()
    if len(result) == 0:
        raise ValueError(
            f"No players found for position '{pos}'. "
            f"Valid positions: {sorted(value_table['position'].unique())}"
        )
    return result.sort_values("value_score", ascending=False).reset_index(drop=True)


def validate_value_table(df: pd.DataFrame) -> list[str]:
    """
    Run null and plausible-range checks on the value table.
    Returns a list of human-readable warning strings. Empty list = all clear.
    Call this in the UI layer on the returned DataFrame (not inside the cached loader).
    """
    issues: list[str] = []

    # Null checks on core columns
    core_cols = [
        "player_name", "team_name", "position", "minutes_played",
        "value_score", "weighted_ga_p90", "goals_added_p90", "xgoals_p90",
    ]
    for col in core_cols:
        if col not in df.columns:
            continue
        n_null = df[col].isna().sum()
        if n_null > 0:
            issues.append(f"{n_null} rows have null '{col}'")

    # Age coverage check (warn if Wikidata matched fewer than 80% of players)
    if "age" in df.columns:
        n_null_age = df["age"].isna().sum()
        pct_null = n_null_age / len(df) if len(df) > 0 else 0
        if pct_null > 0.20:
            issues.append(
                f"Age missing for {n_null_age}/{len(df)} players ({pct_null:.0%}) — "
                "Wikidata coverage may be low; check fetch_player_birthdates."
            )

    # Plausible-range checks (wide bounds -- flagging extreme outliers only)
    range_checks = [
        ("goals_added_p90",     -1.5,  1.5),
        ("weighted_ga_p90",     -3.0,  4.0),
        ("xgoals_p90",           0.0,  1.5),
        ("xassists_p90",         0.0,  1.0),
        ("xga_p90",              0.0,  2.0),
        ("minutes_played",       0.0, 3300.0),
    ]
    for col, lo, hi in range_checks:
        if col not in df.columns:
            continue
        out_of_range = df[(df[col] < lo) | (df[col] > hi)]
        if not out_of_range.empty:
            sample = ", ".join(out_of_range["player_name"].head(3).tolist())
            issues.append(
                f"{len(out_of_range)} players have {col} outside [{lo}, {hi}]: {sample}"
            )

    return issues


if __name__ == "__main__":
    from src.data.sources import (
        fetch_player_goals_added,
        fetch_player_xgoals,
        fetch_players,
        fetch_teams,
    )

    print("Loading cached data...")
    ga = fetch_player_goals_added()
    xg = fetch_player_xgoals()
    pl = fetch_players()
    tm = fetch_teams()

    print("Building value table...\n")
    table = build_player_value_table(ga, xg, pl, tm, min_minutes=500)

    print("\n--- Top 3 per position by value_score ---")
    for pos, group in table.groupby("position"):
        print(f"\n{pos}:")
        top3 = group.head(3)[[
            "player_name", "team_abbreviation", "minutes_played",
            "value_score", "weighted_ga_p90", "goals_added_p90"
        ]]
        print(top3.to_string(index=False))

    print("\n--- QA validation ---")
    issues = validate_value_table(table)
    if issues:
        for w in issues:
            print(f"WARNING: {w}")
    else:
        print("All checks passed.")
