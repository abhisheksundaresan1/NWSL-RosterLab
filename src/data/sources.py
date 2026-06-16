"""
Data layer — ingest from American Soccer Analysis (ASA) and cache locally.

This module is intentionally decoupled from the UI (see CLAUDE.md). It only
fetches + caches data. No metric math, no Streamlit. Keeping it standalone is
what makes a future public data layer ("#3") cheap to add.

These functions are built on the documented itscalledsoccer API. Run them on a
machine with internet access.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from itscalledsoccer.client import AmericanSoccerAnalysis

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# Wikidata SPARQL endpoint for player birthdates.
# Query: all female footballers (P106=Q937857, P21=Q6581072) with a birthdate.
# Achieves ~86% NWSL name-match coverage with zero false positives.
_WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
_WIKIDATA_USER_AGENT = "NWSLRosterLab/1.0 (abhishek.sundaresan1@gmail.com)"
_WIKIDATA_QUERY = (
    "SELECT DISTINCT ?playerLabel ?birthdate WHERE {"
    "  ?player wdt:P21 wd:Q6581072 ."
    "  ?player wdt:P106 wd:Q937857 ."
    "  ?player wdt:P569 ?birthdate ."
    "  SERVICE wikibase:label { bd:serviceParam wikibase:language 'en' . }"
    "} LIMIT 80000"
)
_BIRTHDATE_REFRESH_DAYS = 30
_BIRTHDATES_MANUAL = Path(__file__).resolve().parents[2] / "data" / "birthdates_manual.csv"

_asa = AmericanSoccerAnalysis()

# Seasons available in the ASA NWSL dataset (most recent first).
# The API has no "list seasons" endpoint so these are hardcoded.
AVAILABLE_SEASONS = ["2025", "2024", "2023", "2022", "2021", "2020", "2019"]
DEFAULT_SEASON = "2025"


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


def fetch_player_xgoals(refresh: bool = False, season_name: str | None = None) -> pd.DataFrame:
    """NWSL player expected-goals data (cached to data/raw).

    season_name: e.g. "2025". None fetches all seasons aggregated.
    """
    suffix = season_name if season_name else "all"
    path = _cache_path(f"nwsl_player_xgoals_{suffix}")
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    kwargs = {"season_name": season_name} if season_name else {}
    df = _asa.get_player_xgoals(leagues="nwsl", **kwargs)
    df = _flatten_mixed_columns(df)
    df.to_parquet(path, index=False)
    return df


def fetch_player_goals_added(refresh: bool = False, season_name: str | None = None) -> pd.DataFrame:
    """NWSL player goals added (g+) data (cached to data/raw).

    season_name: e.g. "2025". None fetches all seasons aggregated.
    """
    suffix = season_name if season_name else "all"
    path = _cache_path(f"nwsl_player_goals_added_{suffix}")
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    kwargs = {"season_name": season_name} if season_name else {}
    df = _asa.get_player_goals_added(leagues="nwsl", **kwargs)
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


def _normalize_name(name: str) -> str:
    """NFKD → ASCII → lowercase → whitespace collapse for fuzzy name matching."""
    s = unicodedata.normalize("NFKD", str(name))
    s = s.encode("ascii", "ignore").decode("ascii")
    return " ".join(s.lower().split())


def fetch_player_birthdates(refresh: bool = False) -> pd.DataFrame:
    """Fetch player birthdates from Wikidata (all female footballers query).

    Returns a DataFrame with columns:
        player_name_normalized  -- ASCII-lowercased name for joining
        birthdate               -- pd.Timestamp (NaT if missing)

    Cache TTL is 30 days (not daily) because birthdates rarely change.
    Falls back to an empty DataFrame with the same columns on any network error
    so the left-join in build_player_value_table never raises a KeyError.
    Manual overrides from data/birthdates_manual.csv are merged in last.
    """
    path = _cache_path("wikidata_birthdates")
    _empty = pd.DataFrame(columns=["player_name_normalized", "birthdate"])

    # Check cache freshness (30-day TTL)
    if path.exists() and not refresh:
        age_days = (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)).days
        if age_days < _BIRTHDATE_REFRESH_DAYS:
            return pd.read_parquet(path)

    try:
        resp = requests.get(
            _WIKIDATA_ENDPOINT,
            params={"query": _WIKIDATA_QUERY, "format": "json"},
            headers={"User-Agent": _WIKIDATA_USER_AGENT, "Accept": "application/sparql-results+json"},
            timeout=60,
        )
        resp.raise_for_status()
        bindings = resp.json()["results"]["bindings"]
    except Exception as exc:
        print(f"[fetch_player_birthdates] Wikidata fetch failed: {exc}. Returning empty DataFrame.")
        return _empty.copy()

    rows = []
    for b in bindings:
        label = b.get("playerLabel", {}).get("value", "")
        bd_raw = b.get("birthdate", {}).get("value", "")
        if not label or not bd_raw:
            continue
        try:
            bd = pd.Timestamp(bd_raw[:10])  # "2002-08-20T00:00:00Z" -> "2002-08-20"
        except Exception:
            continue
        rows.append({"player_name_normalized": _normalize_name(label), "birthdate": bd})

    if not rows:
        print("[fetch_player_birthdates] Wikidata returned 0 usable rows. Returning empty DataFrame.")
        return _empty.copy()

    df = pd.DataFrame(rows).drop_duplicates("player_name_normalized")

    # Merge manual overrides (data/birthdates_manual.csv takes priority)
    if _BIRTHDATES_MANUAL.exists():
        manual = pd.read_csv(_BIRTHDATES_MANUAL)
        if not manual.empty and "player_name" in manual.columns and "birthdate" in manual.columns:
            manual["player_name_normalized"] = manual["player_name"].apply(_normalize_name)
            manual["birthdate"] = pd.to_datetime(manual["birthdate"], errors="coerce")
            manual = manual[["player_name_normalized", "birthdate"]].dropna()
            df = df[~df["player_name_normalized"].isin(manual["player_name_normalized"])]
            df = pd.concat([df, manual], ignore_index=True)

    df.to_parquet(path, index=False)
    print(f"[fetch_player_birthdates] Cached {len(df)} player birthdates.")
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
