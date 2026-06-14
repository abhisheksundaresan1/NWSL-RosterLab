"""
Data layer — ingest from American Soccer Analysis (ASA) and cache locally.

This module is intentionally decoupled from the UI (see CLAUDE.md). It only
fetches + caches data. No metric math, no Streamlit. Keeping it standalone is
what makes a future public data layer ("#3") cheap to add.

These functions are built on the documented itscalledsoccer API. Run them on a
machine with internet access.
"""

from __future__ import annotations

from pathlib import Path
import pandas as pd
from itscalledsoccer.client import AmericanSoccerAnalysis

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

_asa = AmericanSoccerAnalysis()


def _cache_path(name: str) -> Path:
    return RAW_DIR / f"{name}.parquet"


def _flatten_mixed_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Stringify any object column that contains lists or dicts so parquet can store it."""
    for col in df.select_dtypes(exclude=["number", "bool", "datetime"]).columns:
        if df[col].apply(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].apply(
                lambda v: ",".join(v) if isinstance(v, list) and all(isinstance(i, str) for i in v)
                else ("" if isinstance(v, dict) else str(v))
            )
    return df


def fetch_player_xgoals(refresh: bool = False) -> pd.DataFrame:
    """NWSL player expected-goals data (cached to data/raw)."""
    path = _cache_path("nwsl_player_xgoals")
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    df = _asa.get_player_xgoals(leagues="nwsl")
    df = _flatten_mixed_columns(df)
    df.to_parquet(path, index=False)
    return df


def fetch_player_goals_added(refresh: bool = False) -> pd.DataFrame:
    """NWSL player goals added (g+) data (cached to data/raw)."""
    path = _cache_path("nwsl_player_goals_added")
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    df = _asa.get_player_goals_added(leagues="nwsl")
    df = _flatten_mixed_columns(df)
    df.to_parquet(path, index=False)
    return df


def fetch_players(refresh: bool = False) -> pd.DataFrame:
    """NWSL player reference (names, positions) for joining onto metrics."""
    path = _cache_path("nwsl_players")
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    df = _asa.get_players(leagues="nwsl")
    df = _flatten_mixed_columns(df)
    df.to_parquet(path, index=False)
    return df


def fetch_teams(refresh: bool = False) -> pd.DataFrame:
    """NWSL team reference (names) for joining onto metrics."""
    path = _cache_path("nwsl_teams")
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    df = _asa.get_teams(leagues="nwsl")
    df = _flatten_mixed_columns(df)
    df.to_parquet(path, index=False)
    return df


if __name__ == "__main__":
    # Smoke test: fetch everything and report shapes.
    for name, fn in [
        ("players", fetch_players),
        ("teams", fetch_teams),
        ("xgoals", fetch_player_xgoals),
        ("goals_added", fetch_player_goals_added),
    ]:
        df = fn(refresh=True)
        print(f"{name:12s} -> {len(df):5d} rows, {len(df.columns)} cols")
