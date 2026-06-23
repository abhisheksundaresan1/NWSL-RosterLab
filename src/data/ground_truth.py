# -*- coding: utf-8 -*-
"""
Ground truth data for value model validation.
Sources: Wikipedia (Best XI, awards, standings) + manual seed CSVs.
All functions cache to parquet; pass refresh=True to re-pull.

Position bucket schema (for validation only — does not change the model):
  GK      -> excluded from hit-rate
  DEF     -> CB, FB in model terms
  MF/FW   -> DM, CM, AM, W, ST in model terms
"""

from __future__ import annotations

import difflib
import io
import unicodedata
from pathlib import Path

import pandas as pd
import requests
import urllib3

# Wikipedia uses HTTPS; on Windows the system cert store may have an expired cert.
# We suppress the warning and bypass verification for public read-only Wikipedia fetches.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_WIKI_HEADERS = {
    "User-Agent": "NWSLRosterLab/1.0 (abhishek.sundaresan1@gmail.com; educational/non-commercial)"
}


def _read_html_from_url(url: str) -> list:
    """Fetch URL via requests (SSL verify=False for Windows cert compat) and parse HTML tables."""
    resp = requests.get(url, headers=_WIKI_HEADERS, verify=False, timeout=30)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text), flavor="lxml")

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
VALIDATION_DIR = Path(__file__).resolve().parents[2] / "data" / "validation"
RAW_DIR.mkdir(parents=True, exist_ok=True)
VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

COMPLETED_SEASONS = [2019, 2021, 2022, 2023, 2024, 2025]  # 2020 excluded (cancelled)

_SEED_PATH        = VALIDATION_DIR / "best_xi_seed.csv"
_NAME_ALIASES     = VALIDATION_DIR / "name_aliases.csv"
_TEAM_ALIASES     = VALIDATION_DIR / "team_aliases.csv"

_WIKI_BEST_XI_URL = "https://en.wikipedia.org/wiki/NWSL_Best_XI"
_WIKI_AWARDS_URL  = "https://en.wikipedia.org/wiki/NWSL_Awards"

_WIKI_SEASON_URLS = {
    2019: "https://en.wikipedia.org/wiki/2019_NWSL_season",
    2021: "https://en.wikipedia.org/wiki/2021_NWSL_season",
    2022: "https://en.wikipedia.org/wiki/2022_NWSL_season",
    2023: "https://en.wikipedia.org/wiki/2023_NWSL_season",
    2024: "https://en.wikipedia.org/wiki/2024_NWSL_season",
    2025: "https://en.wikipedia.org/wiki/2025_NWSL_season",
}

# Position group labels as they appear on Wikipedia (mapped to our schema)
_POS_MAP: dict[str, str] = {
    "gk":          "GK",
    "goalkeeper":  "GK",
    "goalkeeper":  "GK",
    "def":         "DEF",
    "defender":    "DEF",
    "defenders":   "DEF",
    "mf":          "MF/FW",
    "mf/fw":       "MF/FW",
    "mid/fwd":     "MF/FW",
    "midfielder":  "MF/FW",
    "midfielders": "MF/FW",
    "forward":     "MF/FW",
    "forwards":    "MF/FW",
    "mid-fwd":     "MF/FW",
    "mid/forward": "MF/FW",
}


def normalize_name(name: str) -> str:
    """NFKD -> ASCII -> lowercase -> whitespace collapse."""
    s = unicodedata.normalize("NFKD", str(name))
    s = s.encode("ascii", "ignore").decode("ascii")
    return " ".join(s.lower().split())


def load_team_aliases() -> dict[str, str]:
    """Return {wiki_name_lower: canonical_name} from team_aliases.csv."""
    if not _TEAM_ALIASES.exists():
        return {}
    df = pd.read_csv(_TEAM_ALIASES)
    return {str(r["wiki_name"]).lower(): str(r["canonical_name"]) for _, r in df.iterrows()}


def load_name_aliases() -> dict[str, str]:
    """Return {normalize(canonical): normalize(asa_name)} from name_aliases.csv."""
    if not _NAME_ALIASES.exists():
        return {}
    df = pd.read_csv(_NAME_ALIASES)
    return {
        normalize_name(r["canonical_name"]): normalize_name(r["asa_name"])
        for _, r in df.iterrows()
    }


def _strip_club(player_str: str) -> tuple[str, str]:
    """
    Wikipedia Best XI cells often encode 'Player Name (Club)' or just 'Player Name'.
    Returns (player_name, club).
    """
    s = str(player_str).strip()
    if "(" in s and s.endswith(")"):
        name_part = s[:s.rfind("(")].strip()
        club_part = s[s.rfind("(") + 1:-1].strip()
        return name_part, club_part
    return s, ""


def _parse_best_xi_from_wiki() -> pd.DataFrame:
    """
    Parse NWSL Best XI from Wikipedia using BeautifulSoup section headings.

    Page structure: each year has an <h2> section containing two wikitables —
    the first is First XI, the second is Second XI. Each table has columns:
    POSITION | PLAYER | CLUB | NOTE (optional), with 11 rows.

    Output columns: season, team_selection, position_group, player_name, club
    """
    import re
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(
            _WIKI_BEST_XI_URL,
            headers=_WIKI_HEADERS,
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        print(f"[fetch_best_xi] Wikipedia fetch failed: {exc}")
        return pd.DataFrame()

    content = soup.find("div", {"id": "mw-content-text"})
    if not content:
        return pd.DataFrame()

    rows = []
    current_year: int | None = None
    year_table_count = 0  # within current year: 0 = First XI, 1 = Second XI

    for el in content.find_all(["h2", "h3", "table"]):
        if el.name in ("h2", "h3"):
            heading_text = el.get_text(strip=True).replace("[edit]", "").strip()
            m = re.search(r"(201[0-9]|202[0-9])", heading_text)
            if m:
                current_year = int(m.group(1))
                year_table_count = 0
            else:
                current_year = None
            continue

        if el.name != "table":
            continue
        if "wikitable" not in " ".join(el.get("class", [])):
            continue
        if current_year not in COMPLETED_SEASONS:
            year_table_count += 1
            continue

        team_selection = "first" if year_table_count == 0 else "second"
        year_table_count += 1

        current_pos_group: str | None = None
        for row in el.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue

            # Try to extract position from the first cell
            first_cell_text = cells[0].get_text(strip=True).lower()
            detected_pos = next(
                (v for k, v in _POS_MAP.items() if k in first_cell_text), None
            )

            if detected_pos is not None:
                # First cell IS a position group cell; player is in cell[1]
                current_pos_group = detected_pos
                if len(cells) < 2:
                    continue
                player_raw = cells[1].get_text(strip=True)
                club_raw   = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            elif current_pos_group is not None and len(cells) >= 2:
                # rowspan continuation: first cell is player name
                player_raw = cells[0].get_text(strip=True)
                club_raw   = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            else:
                # Header row or unrecognized structure — skip
                continue

            # Strip footnote markers [a], [1], asterisks
            player_name = re.sub(r"\[.*?\]|\*", "", player_raw).strip()
            club        = re.sub(r"\[.*?\]|\*", "", club_raw).strip()

            if not player_name or player_name.lower() in ("player", "nan"):
                continue

            rows.append({
                "season":         current_year,
                "team_selection": team_selection,
                "position_group": current_pos_group,
                "player_name":    player_name,
                "club":           club,
            })

    return (
        pd.DataFrame(rows, columns=["season", "team_selection", "position_group", "player_name", "club"])
        if rows
        else pd.DataFrame()
    )


def _load_seed() -> pd.DataFrame:
    """Load the manual seed CSV. Returns empty DataFrame if not found."""
    if not _SEED_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(_SEED_PATH)
    # Normalize column names to match our schema
    rename = {}
    if "team_selection" not in df.columns and "team" in df.columns:
        rename["team"] = "team_selection"
    if "position_group" not in df.columns and "bucket" in df.columns:
        rename["bucket"] = "position_group"
    if rename:
        df = df.rename(columns=rename)
    # Ensure position_group uses canonical labels
    if "position_group" in df.columns:
        df["position_group"] = df["position_group"].str.upper().str.strip()
        df["position_group"] = df["position_group"].replace({
            "MF/FW": "MF/FW", "MFFW": "MF/FW", "MF": "MF/FW",
            "FW": "MF/FW", "FWD": "MF/FW",
            "DEF": "DEF", "DEFENDER": "DEF",
            "GK": "GK",
        })
    if "season" in df.columns:
        df["season"] = df["season"].astype(int)
    return df


def fetch_best_xi(refresh: bool = False) -> pd.DataFrame:
    """
    Fetch NWSL Best XI for all completed seasons.

    Priority:
      1. Seed CSV wins for any (season, team_selection, player) it covers.
      2. Wikipedia fills remaining seasons / second teams.
      3. If Wikipedia parse fails, seed-only data is returned.

    Returns DataFrame with columns:
      season, team_selection (first/second), position_group (GK/DEF/MF/FW),
      player_name, club
    """
    cache_path = RAW_DIR / "nwsl_best_xi.parquet"
    if cache_path.exists() and not refresh:
        return pd.read_parquet(cache_path)

    seed = _load_seed()
    wiki = _parse_best_xi_from_wiki()

    if wiki.empty:
        print("[fetch_best_xi] Wikipedia parse returned 0 rows — using seed only.")
        result = seed.copy() if not seed.empty else pd.DataFrame(
            columns=["season", "team_selection", "position_group", "player_name", "club"]
        )
    else:
        # Cross-check: log cases where Wikipedia has a different name for a seed season/position
        if not seed.empty and not wiki.empty:
            seed_keys = set(
                zip(seed["season"].astype(str), seed["team_selection"], seed["position_group"])
            )
            for _, wr in wiki.iterrows():
                key = (str(wr["season"]), wr["team_selection"], wr["position_group"])
                if key in seed_keys:
                    # Check if this player appears in seed
                    match = seed[
                        (seed["season"] == wr["season"]) &
                        (seed["team_selection"] == wr["team_selection"]) &
                        (seed["position_group"] == wr["position_group"]) &
                        (seed["player_name"].apply(normalize_name) == normalize_name(wr["player_name"]))
                    ]
                    if match.empty:
                        print(
                            f"[fetch_best_xi] Wikipedia has '{wr['player_name']}' "
                            f"({wr['season']} {wr['team_selection']} {wr['position_group']}) "
                            f"— not in seed. Seed takes priority for this group."
                        )

        # Seed wins for any (season, team_selection) group it covers
        if not seed.empty:
            seed_covered = set(zip(seed["season"].astype(str), seed["team_selection"]))
            wiki_extra = wiki[
                ~wiki.apply(
                    lambda r: (str(r["season"]), r["team_selection"]) in seed_covered, axis=1
                )
            ]
            result = pd.concat([seed[["season", "team_selection", "position_group", "player_name", "club"]], wiki_extra], ignore_index=True)
        else:
            result = wiki.copy()

    result = result.dropna(subset=["player_name"])
    result = result[result["player_name"].str.strip() != ""]
    result = result.drop_duplicates(subset=["season", "team_selection", "position_group", "player_name"])
    result["season"] = result["season"].astype(int)

    result.to_parquet(cache_path, index=False)
    print(f"[fetch_best_xi] Cached {len(result)} Best XI rows across {result['season'].nunique()} seasons.")
    return result


def fetch_awards(refresh: bool = False) -> pd.DataFrame:
    """
    Fetch NWSL season awards from Wikipedia.
    Returns DataFrame: season, award, player
    Awards: MVP, Defender of Year, Midfielder of Year, Goalkeeper of Year, Rookie of Year
    """
    cache_path = RAW_DIR / "nwsl_awards.parquet"
    if cache_path.exists() and not refresh:
        return pd.read_parquet(cache_path)

    empty = pd.DataFrame(columns=["season", "award", "player"])
    try:
        tables = _read_html_from_url(_WIKI_AWARDS_URL)
    except Exception as exc:
        print(f"[fetch_awards] Wikipedia fetch failed: {exc}")
        empty.to_parquet(cache_path, index=False)
        return empty

    import re
    _AWARD_KEYWORDS = {
        "mvp":                  "MVP",
        "most valuable":        "MVP",
        "defender":             "Defender of the Year",
        "defensive":            "Defender of the Year",
        "midfielder":           "Midfielder of the Year",
        "goalkeeper":           "Goalkeeper of the Year",
        "rookie":               "Rookie of the Year",
        "newcomer":             "Rookie of the Year",
    }

    rows = []
    for tbl in tables:
        cols = [str(c).lower() for c in tbl.columns]
        year_col = next((c for c in tbl.columns if str(c).lower() in ("year", "season")), None)
        if year_col is None:
            continue

        for col in tbl.columns:
            col_lower = str(col).lower()
            award_label = next((v for k, v in _AWARD_KEYWORDS.items() if k in col_lower), None)
            if award_label is None or col == year_col:
                continue

            for _, row in tbl.iterrows():
                year_raw = str(row[year_col])
                m = re.search(r"(201[0-9]|202[0-9])", year_raw)
                if not m:
                    continue
                season = int(m.group(1))
                if season not in COMPLETED_SEASONS:
                    continue
                player = str(row[col]).strip()
                if player and player.lower() not in ("nan", "—", "-", ""):
                    name, _ = _strip_club(player)
                    rows.append({"season": season, "award": award_label, "player": name})

    df = pd.DataFrame(rows).drop_duplicates() if rows else empty.copy()
    df.to_parquet(cache_path, index=False)
    print(f"[fetch_awards] Cached {len(df)} award rows.")
    return df


def _parse_standings_from_season_page(season: int) -> pd.DataFrame:
    """
    Scrape regular-season standings from one Wikipedia season page.
    Returns DataFrame: team_name, points (integers), season
    """
    url = _WIKI_SEASON_URLS.get(season)
    if not url:
        return pd.DataFrame()

    import re
    try:
        tables = _read_html_from_url(url)
    except Exception as exc:
        print(f"[fetch_standings] Wikipedia fetch failed for {season}: {exc}")
        return pd.DataFrame()

    import re as _re
    team_aliases = load_team_aliases()
    rows = []

    for tbl in tables:
        str_cols = {str(c): str(c).lower() for c in tbl.columns}

        # Team column: "Team", "Teamvte", "Club", "Squad" (case-insensitive, may have "vte" suffix)
        team_col = next(
            (c for c, cl in str_cols.items() if cl.startswith("team") or cl in ("club", "squad")),
            None,
        )
        # Points column: "Pts", "Points", "P"
        pts_col = next(
            (c for c, cl in str_cols.items() if cl in ("pts", "points", "pt", "p")),
            None,
        )

        if team_col is None or pts_col is None:
            continue

        # Quick sanity: at least 6 rows with numeric points
        pts_sample = pd.to_numeric(tbl[pts_col], errors="coerce")
        if pts_sample.notna().sum() < 5:
            continue

        for _, row in tbl.iterrows():
            team_raw = str(row[team_col]).strip()
            # Strip qualification notes like "(C, S)", footnote markers, asterisks
            team_raw = _re.sub(r"\(.*?\)", "", team_raw)   # remove parenthetical notes
            team_raw = _re.sub(r"[\*†‡§\[\]0-9]", "", team_raw).strip()
            if not team_raw or team_raw.lower() in ("nan", "team"):
                continue
            pts_val = pd.to_numeric(row[pts_col], errors="coerce")
            if pd.isna(pts_val):
                continue
            canonical = team_aliases.get(team_raw.lower(), team_raw)
            rows.append({"team_name": canonical, "points": int(pts_val), "season": season})

        if rows:
            break  # use first valid standings table found

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def fetch_standings(refresh: bool = False) -> pd.DataFrame:
    """
    Fetch NWSL regular-season standings from Wikipedia season pages.
    Returns DataFrame: season, team_name, points
    Excludes 2020 (cancelled season).
    """
    cache_path = RAW_DIR / "nwsl_standings.parquet"
    if cache_path.exists() and not refresh:
        return pd.read_parquet(cache_path)

    all_rows = []
    for season in COMPLETED_SEASONS:
        season_df = _parse_standings_from_season_page(season)
        if season_df.empty:
            print(f"[fetch_standings] No standings found for {season}.")
        else:
            all_rows.append(season_df)
            print(f"[fetch_standings] {season}: {len(season_df)} teams.")

    df = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame(
        columns=["season", "team_name", "points"]
    )
    df.to_parquet(cache_path, index=False)
    print(f"[fetch_standings] Cached {len(df)} team-season standings rows.")
    return df
