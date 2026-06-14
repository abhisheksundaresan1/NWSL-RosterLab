"""
Analysis layer — turn raw ASA tables into a value ranking by position.

Pure functions only: DataFrame in → DataFrame out. No I/O, no UI.
This is the product's opinion and its spine (see CLAUDE.md).
"""

from __future__ import annotations

import ast
import warnings

import pandas as pd


# Action types present in the g+ data column.
_ACTION_TYPES = ["Dribbling", "Fouling", "Interrupting", "Passing", "Receiving", "Shooting"]


def _unpack_goals_added(goals_added: pd.DataFrame) -> pd.DataFrame:
    """Parse the stringified `data` column and expand into per-action-type columns.

    Adds:
      goals_added_total  — sum of goals_added_raw across all action types
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
    min_minutes: int = 500,
) -> pd.DataFrame:
    """Build a tidy, ranked player value table for all non-GK positions.

    Parameters
    ----------
    goals_added : raw g+ DataFrame from fetch_player_goals_added()
    xgoals      : raw xG DataFrame from fetch_player_xgoals()
    players     : player reference from fetch_players()
    teams       : team reference from fetch_teams()
    min_minutes : minimum minutes threshold to qualify (default 500)

    Returns
    -------
    DataFrame sorted by position then value_score descending, with columns:
      player_name, team_name, team_abbreviation, position, minutes_played,
      value_score, goals_added_total, goals_added_p90,
      xgoals_p90, xassists_p90, xga_p90,
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
        warnings.warn(f"{missing_names} rows have no player_name after join — check player_id coverage.")

    # Step 3: Minutes filter.
    n_before = len(df)
    df = df[df["minutes_played"] >= min_minutes].copy()
    n_after = len(df)
    print(f"Minutes filter (>={min_minutes}): {n_before} -> {n_after} rows ({n_before - n_after} dropped)")
    assert len(df) > 0, f"No players survived the {min_minutes}-minute filter — lower min_minutes?"

    # Step 4: Per-90 normalization.
    p90 = df["minutes_played"] / 90
    df["goals_added_p90"] = df["goals_added_total"] / p90
    df["xgoals_p90"]      = df["xgoals"]              / p90
    df["xassists_p90"]    = df["xassists"]             / p90
    df["xga_p90"]         = df["xgoals_plus_xassists"] / p90

    # Step 5: Value score — z-score of goals_added_p90 within position.
    df["value_score"] = df.groupby("position")["goals_added_p90"].transform(
        lambda g: (g - g.mean()) / g.std() if g.std() > 0 else 0.0
    )
    df["value_score"] = df["value_score"].fillna(0.0)
    assert df["value_score"].isna().sum() == 0, "NaN in value_score after z-score"

    # Step 6: Select and order output columns.
    out_cols = [
        "player_name", "team_name", "team_abbreviation", "position", "minutes_played",
        "value_score", "goals_added_total", "goals_added_p90",
        "xgoals_p90", "xassists_p90", "xga_p90",
        "ga_dribbling", "ga_passing", "ga_shooting", "ga_receiving", "ga_interrupting", "ga_fouling",
    ]
    df = df[out_cols].copy()

    round_cols = ["value_score", "goals_added_p90", "xgoals_p90", "xassists_p90", "xga_p90"]
    df[round_cols] = df[round_cols].round(3)

    df = df.sort_values(["position", "value_score"], ascending=[True, False]).reset_index(drop=True)

    print("\nPlayers per position in output table:")
    print(df.groupby("position").size().to_string())

    return df


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
        top3 = group.head(3)[["player_name", "team_abbreviation", "minutes_played", "value_score", "goals_added_p90"]]
        print(top3.to_string(index=False))
