"""
2026 in-season snapshot writer — two outputs, two different metrics.

  data/snapshots/value_2026_<date>.parquet
      CUMULATIVE season-to-date g+ through the cutoff, built into the value
      table. This is the season value score, unchanged; value_score is left
      un-stabilized here (stabilization is applied at display time).

  data/form/form_2026_<date>.parquet
      The rolling 30-day FORM window ending at the cutoff, plus the preceding
      30 days for the rate change. A separate metric with its own minutes floor
      and its own shrinkage constant — never mixed with the value score.

The app reads both parquets directly and never calls ASA at render time.

Runs daily (see .github/workflows/snapshot.yml). Snapshot frequency is
deliberately independent of every comparison window: windows are defined in days
and resolved by date, so pulling more often cannot change what they mean.

Usage:
    python scripts/snapshot.py                 # cutoff = yesterday, skip if exists
    python scripts/snapshot.py --date 2026-06-20
    python scripts/snapshot.py --force         # overwrite existing file
    python scripts/snapshot.py --backfill      # seed every Monday since 2026-03-13

Requires internet (pulls from ASA).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

# Put the repo root on sys.path so `import src.*` works when run as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data.sources import (  # noqa: E402
    fetch_player_goals_added,
    fetch_player_xgoals,
    fetch_players,
    fetch_teams,
    fetch_player_birthdates,
)
from src.data.sources import fetch_games, fetch_goalkeeper_goals_added  # noqa: E402
from src.analysis.ranking import build_player_value_table  # noqa: E402
from src.analysis.form import compute_form, window_bounds  # noqa: E402
from src.analysis.matches import build_match_table  # noqa: E402

SEASON = "2026"
SEASON_START = "2026-03-13"
SNAP_DIR = _REPO_ROOT / "data" / "snapshots"
FORM_DIR = _REPO_ROOT / "data" / "form"
MATCH_DIR = _REPO_ROOT / "data" / "matches"


def _snapshot_path(cutoff: str) -> Path:
    return SNAP_DIR / f"value_{SEASON}_{cutoff}.parquet"


def _form_path(cutoff: str) -> Path:
    return FORM_DIR / f"form_{SEASON}_{cutoff}.parquet"


def write_matches() -> Path:
    """Rebuild data/matches/matches_<season>.parquet from the whole season.

    ONE file, rewritten in full each run — not dated snapshots. Match history is
    immutable (matchday 16 will never change), so a dated daily copy would
    re-commit the entire season every day and inflate a repo that Streamlit Cloud
    clones on every deploy.

    Whole-season rebuild rather than an incremental append, for two reasons:

      * It is the only fetch that works. get_player_goals_added(game_ids=…)
        returns HTTP 500 in every form, so gaps cannot be repaired selectively.
        The season pull succeeds and covers all 148 fixtures exactly.
      * It makes gaps impossible instead of detectable. A fresh clone with no
        file and a run after the Action has been broken for a month take the
        identical path and produce the identical complete table — there is no
        first-run special case and no separate backfill mode to remember.

    Late ASA corrections land automatically, since every row is re-derived.
    """
    MATCH_DIR.mkdir(parents=True, exist_ok=True)
    path = MATCH_DIR / f"matches_{SEASON}.parquet"

    ga = fetch_player_goals_added(season_name=SEASON, split_by_games=True, refresh=True)
    games = fetch_games(season_name=SEASON, refresh=True)
    mt = build_match_table(ga, games, fetch_players(), fetch_teams())

    # Health assertion, not a repair path: every fixture must be represented. A
    # mismatch means something upstream changed, and since selective re-fetching
    # is impossible it should stop the run rather than be silently patched.
    fixtures = set(games["game_id"])
    covered = set(mt["game_id"])
    missing = fixtures - covered
    if missing:
        raise RuntimeError(
            f"match table is missing {len(missing)} of {len(fixtures)} fixtures "
            f"(e.g. {sorted(missing)[:3]}) — refusing to write an incomplete file."
        )

    mt.to_parquet(path, index=False)
    print(f"[write] {path.name}  rows={len(mt)}  fixtures={len(covered)}  "
          f"matchdays={mt['matchday'].min()}-{mt['matchday'].max()}  "
          f"players={mt['player_id'].nunique()}")

    # Goalkeepers, kept in a SEPARATE file because it is a separate metric:
    # Shotstopping/Claiming/Sweeping, not the outfield action types. Storing them
    # together would invite a join that ranks keepers against outfielders.
    gk_path = MATCH_DIR / f"matches_gk_{SEASON}.parquet"
    gk = fetch_goalkeeper_goals_added(season_name=SEASON, split_by_games=True, refresh=True)
    gkt = build_match_table(gk, games, fetch_players(), fetch_teams(),
                            default_position="GK")
    gkt.to_parquet(gk_path, index=False)
    print(f"[write] {gk_path.name}  rows={len(gkt)}  "
          f"keepers={gkt['player_id'].nunique()}")
    return path


def write_form(cutoff: str, force: bool = False) -> Path | None:
    """Write the rolling form window ending at `cutoff`.

    Kept separate from the value snapshot on purpose. The value snapshot is
    CUMULATIVE season-to-date and is what every existing surface reads; form is a
    fixed recent WINDOW and is a different metric with its own floors. Writing
    them to different files makes it structurally impossible for one to be
    mistaken for, or silently overwrite, the other.

    Precomputing here means the app never calls ASA at render time — it reads a
    parquet, exactly as it already does for value.
    """
    FORM_DIR.mkdir(parents=True, exist_ok=True)
    path = _form_path(cutoff)
    if path.exists() and not force:
        print(f"[skip] {path.name} already exists (use --force to overwrite).")
        return None

    cur_start, cur_end = window_bounds(cutoff)
    prev_start, prev_end = window_bounds(cur_start)

    # split_by_games=True: one row per player per match, so matches are counted
    # exactly rather than estimated from minutes (NWSL match days are usually
    # doubleheaders, so a date window alone cannot resolve them).
    ga_cur = fetch_player_goals_added(
        start_date=cur_start, end_date=cur_end, split_by_games=True, refresh=True)
    ga_prev = fetch_player_goals_added(
        start_date=prev_start, end_date=prev_end, split_by_games=True, refresh=True)

    if ga_cur.empty or "data" not in ga_cur.columns:
        print(f"[skip] form {cutoff}: no g+ in the {cur_start}..{cur_end} window.")
        return None

    ft = compute_form(ga_cur, fetch_players(), fetch_teams(), ga_prev=ga_prev)
    if ft.empty:
        print(f"[skip] form {cutoff}: nobody cleared the minutes floor.")
        return None

    ft.to_parquet(path, index=False)
    n_delta = int(ft["form_delta"].notna().sum())
    print(
        f"[write] {path.name}  window={cur_start}..{cur_end}  rows={len(ft)}  "
        f"fixtures={int(ft['form_fixtures'].iloc[0])}/{int(ft['prev_fixtures'].iloc[0])} (cur/prev)  "
        f"with_delta={n_delta}"
    )
    return path


def _mondays(start: date, end: date) -> list[date]:
    """Every Monday in [start, end] inclusive — the publish/comparison anchor.

    Monday, not Friday. NWSL 2026 plays primarily Fri/Sat/Sun (only three midweek
    fixtures per club all season), so a Friday cutoff lands mid-game-week and
    "this week" ends up meaning the weekend that has not been played yet. Anchoring
    on Monday makes it mean the weekend that just finished.

    Earlier Friday-anchored snapshots are left on disk untouched: they are still
    valid cumulative tables, and nothing addresses snapshots by weekday.
    """
    # Advance to the first Monday on or after `start` (weekday(): Mon=0 .. Sun=6).
    first = start + timedelta(days=(0 - start.weekday()) % 7)
    out: list[date] = []
    d = first
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def write_snapshot(cutoff: str, force: bool = False) -> Path | None:
    """Build and write one snapshot for g+ cumulative through `cutoff` (YYYY-MM-DD).

    Returns the path written, or None if it already existed and force=False.
    """
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    path = _snapshot_path(cutoff)
    if path.exists() and not force:
        print(f"[skip] {path.name} already exists (use --force to overwrite).")
        return None

    ga = fetch_player_goals_added(start_date=SEASON_START, end_date=cutoff, refresh=True)
    # Early cutoffs (before any matches) return an empty frame with no 'data'
    # column — nothing to snapshot yet.
    if ga.empty or "data" not in ga.columns:
        print(f"[skip] {cutoff}: no g+ data yet (0 matches through this date).")
        return None

    xg = fetch_player_xgoals(season_name=SEASON)
    pl = fetch_players()
    tm = fetch_teams()
    bd = fetch_player_birthdates()

    # min_minutes=0: record every player; the app + movement layer apply floors.
    vt = build_player_value_table(ga, xg, pl, tm, birthdates=bd, min_minutes=0, season=SEASON)
    vt.to_parquet(path, index=False)
    print(
        f"[write] {path.name}  rows={len(vt)}  "
        f"players={vt['player_id'].nunique()}  max_min={int(vt['minutes_played'].max())}"
    )
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Write a 2026 in-season value snapshot.")
    ap.add_argument("--date", help="Cutoff date YYYY-MM-DD (default: yesterday).")
    ap.add_argument("--force", action="store_true", help="Overwrite an existing snapshot.")
    ap.add_argument("--backfill", action="store_true",
                    help="Seed a snapshot for every Monday since the season start.")
    args = ap.parse_args()

    yesterday = date.today() - timedelta(days=1)

    if args.backfill:
        start = date.fromisoformat(SEASON_START)
        mondays = _mondays(start, yesterday)
        print(f"Backfill: {len(mondays)} weekly cutoffs from {SEASON_START} to {yesterday}.")
        written = 0
        for m in mondays:
            if write_snapshot(m.isoformat(), force=args.force):
                written += 1
            write_form(m.isoformat(), force=args.force)
        print(f"\nBackfill complete: {written} new snapshot(s), "
              f"{len(mondays) - written} skipped/existing.")
        return

    cutoff = args.date if args.date else yesterday.isoformat()
    write_snapshot(cutoff, force=args.force)
    write_form(cutoff, force=args.force)
    write_matches()


if __name__ == "__main__":
    main()
