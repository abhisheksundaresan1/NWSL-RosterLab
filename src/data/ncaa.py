"""
NCAA D-I women's soccer data ingest — CDP browser scraper.

stats.ncaa.org is protected by Akamai Bot Manager which blocks headless browsers.
We bypass this by connecting Playwright to your real Chrome browser via CDP
(Chrome DevTools Protocol). A real browser is undetectable.

HOW TO RUN:
  1. Launch Chrome with remote debugging enabled (do this once per session):
       Windows: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222
       Mac:     open -a "Google Chrome" --args --remote-debugging-port=9222

  2. Run the scraper:
       python -m src.data.ncaa

What we scrape per player (D-I women's soccer, current season):
    name, school, class_year, gp, goals, assists, points,
    shots, sog, goals_pg, assists_pg
    + conference (joined from team list)

Stat pages scraped (from stats.ncaa.org individual rankings):
    Goals, Assists, Points, Shots, Shots on Goal

The DataTables on each page are fully rendered in the real browser.
We paginate through all rows (not just the default top 25/50).

Cache TTL: 7 days (NCAA_CACHE_DAYS env var).
"""

from __future__ import annotations

import io
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup  # still used in fetch_ncaa_conferences

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

NCAA_CACHE_PATH = RAW_DIR / "ncaa_players.parquet"       # combined all seasons
NCAA_CACHE_DAYS = int(os.environ.get("NCAA_CACHE_DAYS", "7"))

_BASE = "https://stats.ncaa.org"
_CDP_URL = os.environ.get("NCAA_CDP_URL", "http://localhost:9222")
_SPORT_CODE = "WSO"
_DIVISION = "1"
# Academic years to scrape. "2025" = 2024-25 season (current).
_SEASONS = ["2026", "2025", "2024", "2023"]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
}

# Map our output column names to the link text in the Individual dropdown
# on stats.ncaa.org (Women's Soccer, 2024-25, Division I)
_STAT_LINK_TEXTS = {
    "goals":   "Goals Per Game",      # gives goals total + gp + goals_pg
    "assists": "Assists Per Game",     # gives assists total + gp + assists_pg
    "points":  "Points Per Game",      # gives points total + gp + points_pg
    "shots":   "Shots Per Game",      # gives sh total + gp + shots_pg
    "sog":     "Shots on Goal Per Game",  # gives sog total + gp + sog_pg
}


def _is_cache_fresh(path: Path, days: int = NCAA_CACHE_DAYS) -> bool:
    if not path.exists():
        return False
    age = (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)).days
    return age < days


# ---------------------------------------------------------------------------
# Conference / team list (plain requests — this endpoint is not bot-protected)
# ---------------------------------------------------------------------------

def fetch_ncaa_conferences(refresh: bool = False) -> pd.DataFrame:
    """
    Fetch D-I women's soccer school → team_id mapping via the unprotected
    /team/inst_team_list endpoint.

    Returns DataFrame: school, team_id
    (Conference labels require additional requests per conf; skipped for MVP.)
    """
    if _is_cache_fresh(CONF_CACHE_PATH, days=30) and not refresh:
        return pd.read_parquet(CONF_CACHE_PATH)

    session = requests.Session()
    session.headers.update(_HEADERS)

    print("[fetch_ncaa_conferences] Fetching D-I team list...")
    r = session.get(
        f"{_BASE}/team/inst_team_list",
        params={"sport_code": _SPORT_CODE, "division": _DIVISION},
        timeout=20,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    main_table = max(soup.find_all("table"), key=lambda t: len(t.find_all("tr")))
    records = []
    for row in main_table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        a = cells[0].find("a")
        if a and "/teams/" in a.get("href", ""):
            team_id = a["href"].split("/teams/")[-1].split("?")[0]
            records.append({"school": a.text.strip(), "team_id": team_id})

    df = pd.DataFrame(records)
    print(f"[fetch_ncaa_conferences] Got {len(df)} D-I schools")
    df.to_parquet(CONF_CACHE_PATH, index=False)
    return df


# ---------------------------------------------------------------------------
# CDP browser scraper
# ---------------------------------------------------------------------------

def _check_chrome_running() -> None:
    """Raise a clear error if Chrome is not reachable on the CDP port."""
    try:
        r = requests.get(f"{_CDP_URL}/json/version", timeout=5)
        r.raise_for_status()
    except Exception:
        raise RuntimeError(
            f"Cannot connect to Chrome at {_CDP_URL}.\n"
            "Launch Chrome with remote debugging enabled first:\n\n"
            '  Windows: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"'
            " --remote-debugging-port=9222\n"
            "  Mac:     open -a \"Google Chrome\" --args --remote-debugging-port=9222\n\n"
            "Then re-run: python -m src.data.ncaa"
        )


def _wait_for_nav(page, action_fn, timeout: int = 20_000) -> None:
    """Run action_fn inside expect_navigation so we wait for the redirect."""
    with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout):
        action_fn()
    page.wait_for_timeout(800)


def _navigate_to_stat_page(page, stat_link_text: str, academic_year: str) -> None:
    """
    Navigate to the D-I individual leaders page for one stat and season.

    Clicks through the real UI: sport dropdown → year → division →
    Individual tab → specific stat link (matched by visible link text).
    """
    # Step 1: load rankings and select Women's Soccer
    page.goto(f"{_BASE}/rankings", wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_selector("select#sport", timeout=10_000)
    page.wait_for_timeout(800)

    _wait_for_nav(page, lambda: page.select_option("select#sport", _SPORT_CODE))

    # Step 2: year — select uses values like "2025.0"
    year_sel = page.locator("select#acadyr, select[name='acadyr']").first
    if year_sel.count():
        _wait_for_nav(page, lambda: year_sel.select_option(f"{academic_year}.0"))

    # Step 3: Division I
    div_sel = page.locator("select#u_div, select[name='u_div']").first
    if div_sel.count():
        _wait_for_nav(page, lambda: div_sel.select_option(f"{_DIVISION}.0"))

    # Step 4: open the Individual dropdown tab (click once to open it)
    indiv_tab = page.locator("a.nav-link:has-text('Individual'), button:has-text('Individual')").first
    if indiv_tab.count() == 0:
        indiv_tab = page.locator("text=Individual").first
    indiv_tab.click()
    page.wait_for_timeout(800)

    # Step 5: click the specific stat link by its visible text
    stat_link = page.locator(f"a:has-text('{stat_link_text}')").first
    if stat_link.count() == 0:
        raise RuntimeError(f"Could not find stat link '{stat_link_text}' in Individual dropdown")
    _wait_for_nav(page, lambda: stat_link.click(), timeout=25_000)

    # Step 6: wait for the DataTable to finish loading
    # Wait for at least one real data row (not the "No data" or "Processing" row)
    page.wait_for_function(
        "() => document.querySelectorAll('table tbody tr td').length > 5",
        timeout=20_000,
    )


def _copy_table_to_df(page) -> pd.DataFrame:
    """
    Click the DataTables 'Copy' button, which puts all rows (tab-separated)
    onto the clipboard, then read it back via the browser clipboard API.
    Returns a DataFrame.
    """
    copy_btn = page.locator(
        "a.dt-button:has-text('Copy'), button.dt-button:has-text('Copy'), "
        "a:has-text('Copy'), button:has-text('Copy')"
    ).first
    if copy_btn.count() == 0:
        raise RuntimeError("Copy button not found on page")

    # Grant clipboard-read permission so navigator.clipboard.readText() works
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])

    copy_btn.click()
    page.wait_for_timeout(800)  # let DataTables finish writing to clipboard

    tsv_text = page.evaluate("navigator.clipboard.readText()")
    if not tsv_text or not tsv_text.strip():
        raise RuntimeError("Clipboard was empty after clicking Copy")

    df = pd.read_csv(io.StringIO(tsv_text), sep="\t")
    return df


def _scrape_one_stat(page, stat_name: str, stat_link_text: str, academic_year: str) -> pd.DataFrame:
    """Navigate to one stat page and copy its full table via the Copy button."""
    print(f"  Scraping {stat_name} ('{stat_link_text}')...")
    _navigate_to_stat_page(page, stat_link_text, academic_year)
    df = _copy_table_to_df(page)
    print(f"    → {len(df)} rows, cols: {df.columns.tolist()[:8]}")
    time.sleep(1.0)
    return df


def _scrape_one_season(page, academic_year: str) -> pd.DataFrame:
    """Scrape all stat pages for one season and return a merged DataFrame."""
    print(f"\n[NCAA] Scraping season {academic_year} ({int(academic_year)-1}-{academic_year[-2:]})...")
    frames: dict[str, pd.DataFrame] = {}
    for stat_name, stat_link_text in _STAT_LINK_TEXTS.items():
        try:
            df = _scrape_one_stat(page, stat_name, stat_link_text, academic_year)
            if not df.empty:
                df = _normalise_columns(df, stat_name)
                frames[stat_name] = df
        except Exception as exc:
            print(f"    [WARN] {stat_name} failed: {exc}")

    if not frames:
        print(f"  [WARN] No data for season {academic_year}, skipping.")
        return pd.DataFrame()

    merged = _merge_frames(frames)
    merged["season"] = academic_year

    pass  # all per-game rates come directly from source pages

    # Numeric cleanup
    for col in merged.columns:
        if col not in {"name", "school", "class_year", "conference", "position", "height", "season"}:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")

    return merged.reset_index(drop=True)


def _split_player_column(df: pd.DataFrame) -> pd.DataFrame:
    """
    The 'Player' column from stats.ncaa.org Copy export is:
        "First Last, School Name (Conference)"
    Split into separate name, school, conference columns.
    """
    if "Player" not in df.columns:
        return df

    def _parse(val: str):
        val = str(val).strip()
        # Split on first comma: "First Last, School (Conf)"
        if "," in val:
            name_part, rest = val.split(",", 1)
            rest = rest.strip()
            # Extract conference from trailing "(Conf)"
            if rest.endswith(")") and "(" in rest:
                school_part, conf_part = rest.rsplit("(", 1)
                return name_part.strip(), school_part.strip(), conf_part.rstrip(")")
            return name_part.strip(), rest, ""
        return val, "", ""

    parsed = df["Player"].apply(_parse)
    df = df.copy()
    df["name"]       = parsed.apply(lambda x: x[0])
    df["school"]     = parsed.apply(lambda x: x[1])
    df["conference"] = parsed.apply(lambda x: x[2])
    df.drop(columns=["Player"], inplace=True)
    return df


def _normalise_columns(df: pd.DataFrame, stat_col: str) -> pd.DataFrame:
    """Standardise column names across different stat pages."""
    df = _split_player_column(df)
    rename = {
        "#": "rank",
        "Rank": "rank",
        "Cl": "class_year",
        "Yr": "class_year",
        "Class": "class_year",
        "Ht": "height",
        "Pos": "position",
        "GP": "gp",
        "Games": "gp",
        "Min": "min",
        "Minutes": "min",
        "G": "g",
        "Goals": "goals",
        "A": "a",
        "Assists": "assists",
        "Pts": "pts",
        "Points": "points",
        "Sh": "sh",
        "ShAtt": "sh",
        "SoG": "sog",
        "Per Game": f"{stat_col}_pg",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    df.drop(columns=["rank"], errors="ignore", inplace=True)
    return df


def fetch_ncaa_players(refresh: bool = False, seasons: list[str] | None = None) -> pd.DataFrame:
    """
    Scrape D-I women's soccer individual stat leaders from stats.ncaa.org
    for multiple seasons using a real browser connected via CDP.

    Returns one row per player-season with a 'season' column (e.g. "2025" = 2024-25).
    Results cached to data/raw/ncaa_players.parquet.

    Requires Brave/Chrome running with --remote-debugging-port=9222.
    """
    if _is_cache_fresh(NCAA_CACHE_PATH) and not refresh:
        print(f"[fetch_ncaa_players] Loading from cache ({NCAA_CACHE_PATH})")
        return pd.read_parquet(NCAA_CACHE_PATH)

    _check_chrome_running()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "Playwright is required. Install it with:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium"
        )

    seasons = seasons or _SEASONS
    print(f"[fetch_ncaa_players] Connecting to Brave at {_CDP_URL}...")
    print(f"[fetch_ncaa_players] Seasons to scrape: {seasons}")

    season_frames: list[pd.DataFrame] = []

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(_CDP_URL)
        page = None
        for ctx in browser.contexts:
            if ctx.pages:
                page = ctx.pages[0]
                break
        if page is None:
            try:
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
            except Exception:
                raise RuntimeError(
                    "Could not open a tab via CDP.\n"
                    "Make sure Brave is open with at least one tab visible, then retry."
                )

        for year in seasons:
            df = _scrape_one_season(page, year)
            if not df.empty:
                season_frames.append(df)

    if not season_frames:
        raise RuntimeError("No data scraped across any season.")

    combined = pd.concat(season_frames, ignore_index=True)
    combined.to_parquet(NCAA_CACHE_PATH, index=False)
    print(f"\n[fetch_ncaa_players] Cached {len(combined)} player-seasons → {NCAA_CACHE_PATH}")
    return combined


def _merge_frames(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Outer-join all stat frames on name + school."""
    bio_cols = {"name", "school", "class_year", "gp", "min"}
    base: pd.DataFrame | None = None

    for stat_name, df in frames.items():
        if df.empty:
            continue
        df = df.copy()

        if base is None:
            base = df
        else:
            # Bring only new stat columns to avoid duplicating bio cols
            new_cols = ["name", "school"] + [
                c for c in df.columns if c not in base.columns
            ]
            # Also update gp/min/class_year from whichever frame has them non-null
            for shared in ["gp", "min", "class_year", "height", "position", "conference"]:
                if shared in df.columns and shared not in new_cols:
                    new_cols.append(shared)
            right = df[[c for c in new_cols if c in df.columns]].copy()
            base = base.merge(right, on=["name", "school"], how="outer",
                              suffixes=("", f"_{stat_name}"))
            # Coalesce duplicate bio columns
            for col in ["gp", "min", "class_year", "height", "position", "conference"]:
                dup = f"{col}_{stat_name}"
                if dup in base.columns:
                    base[col] = base[col].combine_first(base[dup])
                    base.drop(columns=[dup], inplace=True)

    return base if base is not None else pd.DataFrame()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", help="Scrape a single season and append (e.g. 2026)")
    parser.add_argument("--refresh", action="store_true", help="Re-scrape all seasons")
    args = parser.parse_args()

    if args.season:
        # Scrape one season and append to existing cache
        _check_chrome_running()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(_CDP_URL)
            page = next((p for ctx in browser.contexts for p in ctx.pages if p), None)
            if page is None:
                raise RuntimeError("No open tab found in Brave.")
            new_df = _scrape_one_season(page, args.season)

        if NCAA_CACHE_PATH.exists():
            existing = pd.read_parquet(NCAA_CACHE_PATH)
            existing = existing[existing["season"] != args.season]  # replace if already present
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df

        combined.to_parquet(NCAA_CACHE_PATH, index=False)
        print(f"\nUpdated cache: {len(combined)} player-seasons")
        print(combined["season"].value_counts().sort_index())
    else:
        df = fetch_ncaa_players(refresh=args.refresh)
        print(f"\nResult: {len(df)} player-seasons, columns: {df.columns.tolist()}")
        print(f"Season counts:\n{df['season'].value_counts().sort_index()}")
        print("\nTop goal scorers (2025-26):")
        current = df[df["season"] == "2026"].sort_values("goals", ascending=False)
        print(current[["name", "school", "conference", "position", "goals", "assists", "goals_pg", "assists_pg"]].head(15).to_string(index=False))
