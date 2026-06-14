"""
Analysis layer — turn raw ASA tables into a value ranking by position.

PHASE 1 GOAL (build this with Claude Code):
- join player metrics (g+, xG) with player reference (name, position)
- filter to a minimum-minutes threshold so small samples don't dominate
- normalize per-90 and within position
- output a tidy DataFrame: player, team, position, minutes, value_score, key metrics

Keep these as PURE functions (DataFrame in -> DataFrame out). No I/O, no UI.
This is the product's "opinion" and its spine.
"""

from __future__ import annotations
import pandas as pd


def build_player_value_table(
    goals_added: pd.DataFrame,
    xgoals: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    min_minutes: int = 500,
) -> pd.DataFrame:
    """TODO (Phase 1): implement with Claude Code.

    Steps to instruct Claude Code through:
      1. Inspect the real columns of each input (they come from ASA).
      2. Join metrics -> player names/positions -> team names.
      3. Apply the min_minutes filter.
      4. Build a transparent value_score (start simple, e.g. position-normalized g+).
      5. Return a sorted, tidy table.
    """
    raise NotImplementedError("Build this in Phase 1 with Claude Code.")
