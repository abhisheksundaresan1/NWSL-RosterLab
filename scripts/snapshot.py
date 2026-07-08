"""
Phase B — 2026 in-season snapshot writer.

Pulls cumulative 2026 goals-added (g+) through a cutoff date, computes the
value table, and writes it to data/snapshots/value_2026_YYYY-MM-DD.parquet.
The app reads these parquet files directly — it never calls ASA for 2026 at
render time. Risers & Fallers diffs two snapshots; the value_score column is
left un-stabilized here (stabilization is applied at display time).

Usage:
    python scripts/snapshot.py                 # cutoff = yesterday, skip if exists
    python scripts/snapshot.py --date 2026-06-20
    python scripts/snapshot.py --force         # overwrite existing file
    python scripts/snapshot.py --backfill      # seed every Friday since 2026-03-13

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
from src.analysis.ranking import build_player_value_table  # noqa: E402

SEASON = "2026"
SEASON_START = "2026-03-13"
SNAP_DIR = _REPO_ROOT / "data" / "snapshots"


def _snapshot_path(cutoff: str) -> Path:
    return SNAP_DIR / f"value_{SEASON}_{cutoff}.parquet"


def _fridays(start: date, end: date) -> list[date]:
    """Every Friday in [start, end] inclusive. Fridays capture a full game week."""
    # Advance to the first Friday on or after `start` (weekday(): Mon=0 .. Fri=4).
    first = start + timedelta(days=(4 - start.weekday()) % 7)
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
                    help="Seed a snapshot for every Friday since the season start.")
    args = ap.parse_args()

    yesterday = date.today() - timedelta(days=1)

    if args.backfill:
        start = date.fromisoformat(SEASON_START)
        fridays = _fridays(start, yesterday)
        print(f"Backfill: {len(fridays)} weekly cutoffs from {SEASON_START} to {yesterday}.")
        written = 0
        for f in fridays:
            if write_snapshot(f.isoformat(), force=args.force):
                written += 1
        print(f"\nBackfill complete: {written} new snapshot(s), "
              f"{len(fridays) - written} skipped/existing.")
        return

    cutoff = args.date if args.date else yesterday.isoformat()
    write_snapshot(cutoff, force=args.force)


if __name__ == "__main__":
    main()
