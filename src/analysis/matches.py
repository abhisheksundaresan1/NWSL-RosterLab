"""
src/analysis/matches.py — the match-level layer.

Everything else in this app is either season-cumulative or a 30-day rolling
window, which can say who has been good lately but never what happened this
weekend. This module joins per-match player g+ to the fixtures table so every
row knows its date, matchday, opponent, home/away and scoreline — the texture a
weekly page needs.

Pure functions: DataFrames in, DataFrames out. No I/O, no UI.

TWO FACTS ABOUT THE SOURCE DATA THAT SHAPE THIS MODULE
======================================================
1. ASA returns PLAYED fixtures only. get_games(season_name="2026") comes back
   with all 148 rows marked status='FullTime' — the schedule is not included. So
   "is this matchday finished?" cannot be answered from `status`, and a matchday
   missing a postponed game looks exactly like a complete one. See
   latest_complete_matchday, which uses fixture dates, and match_coverage, which
   reports what a matchday actually contains rather than implying completeness.

2. Fetching individual fixtures is impossible. get_player_goals_added(game_ids=…)
   returns HTTP 500 in every form tested — a list, a single id, with and without
   split_by_games. The whole-season pull DOES work and covers every fixture
   exactly, which is why the caller rebuilds the full table rather than patching
   gaps incrementally.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.analysis.drops import FORMATION_SLOTS, SLOT_COORDS, _empty_slot
from src.analysis.ranking import _ACTION_WEIGHT_COLS, _unpack_goals_added

# A slot only fills if the player was on the pitch for at least this long in that
# match. Deliberately permissive, because ranking is on RAW g+ rather than per-90:
# g+ accumulates with time on the pitch, so a short appearance is already
# disadvantaged and the floor only needs to exclude a freak cameo. At 45, a
# 54-minute and a 69-minute performance can still place on merit.
TOTW_MIN_MINUTES = 45

# Team of the Week gets its own formation, built from the shared one plus a
# keeper. The shared FORMATION_SLOTS is deliberately NOT modified: it drives
# Undervalued XI, Risers, Fallers and Newcomers, all of which run on
# outfield-only tables, and adding a GK slot there would give every one of them a
# permanently empty keeper.
TOTW_SLOTS: list[dict] = [{"slot": "GK", "position": "GK", "line": "GK"}] + FORMATION_SLOTS
# TOTW uses its own coordinates, not the shared SLOT_COORDS values.
#
# Every marker on this card carries THREE lines — value above, name below,
# opponent/scoreline below that — roughly 60px more copy per player than the
# cards SLOT_COORDS was spaced for. Adding a keeper underneath the defence at
# the original spacing produced two visible collisions: the GK's value tag ran
# into a centre-back's name, and the GK's scoreline clipped off the bottom edge.
#
# So the back line is lifted from y=22 to y=27 and the keeper sits at y=8. This
# is deliberately a TOTW-only override: SLOT_COORDS still drives Undervalued XI,
# Newcomers and In form, whose two-line markers do not need the extra room.
TOTW_COORDS: dict[str, tuple[float, float]] = {
    **SLOT_COORDS,
    "FB_L": (7, 27), "CB_L": (27, 27), "CB_R": (53, 27), "FB_R": (73, 27),
    "GK": (40, 8),
}


def build_match_table(
    ga_by_game: pd.DataFrame,
    games: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    default_position: str | None = None,
) -> pd.DataFrame:
    """One row per player per match, carrying its fixture context.

    Returns: player_id, player_name, position, team_id, team_abbreviation,
    game_id, date, matchday, opponent, home, score, result, minutes_played,
    ga_total, and the per-action ga_* columns.
    """
    if ga_by_game is None or ga_by_game.empty or "data" not in ga_by_game.columns:
        return _empty_match_table()

    df = _unpack_goals_added(ga_by_game)

    # Multi-team players carry a comma-joined team_id; the last entry is current.
    df["team_id"] = df["team_id"].astype(str).str.split(",").str[-1].str.strip()
    # The goalkeeper feed has no general_position column at all, so callers pass
    # default_position="GK" rather than the column being assumed present.
    if "general_position" in df.columns:
        df = df.rename(columns={"general_position": "position"})
    elif default_position:
        df["position"] = default_position
    else:
        raise ValueError("no general_position column and no default_position given")

    fixtures = games[[
        "game_id", "date_time_utc", "matchday",
        "home_team_id", "away_team_id", "home_score", "away_score",
    ]].copy()

    df = (
        df.merge(players[["player_id", "player_name"]].drop_duplicates("player_id"),
                 on="player_id", how="left")
          .merge(teams[["team_id", "team_abbreviation"]], on="team_id", how="left")
          .merge(fixtures, on="game_id", how="inner")
    )

    # A handful of player_ids in the g+ feed have no row in the players
    # reference. They cannot be named on a card or in a sentence, and an unnamed
    # player would still occupy an XI slot, so drop them from the pool.
    df = df[df["player_name"].notna()].copy()
    if df.empty:
        return _empty_match_table()

    abbr = dict(zip(teams["team_id"], teams["team_abbreviation"]))
    df["home"] = df["team_id"] == df["home_team_id"]
    df["opponent"] = [
        abbr.get(a if h else b)
        for h, a, b in zip(df["home"], df["away_team_id"], df["home_team_id"])
    ]

    # Scoreline written from THIS player's team's point of view, so the same
    # fixture reads 4-3 for one side and 3-4 for the other.
    gf = df["home_score"].where(df["home"], df["away_score"]).astype("Int64")
    ga_ = df["away_score"].where(df["home"], df["home_score"]).astype("Int64")
    df["goals_for"], df["goals_against"] = gf, ga_
    df["score"] = gf.astype(str) + "-" + ga_.astype(str)
    df["result"] = ["W" if f > a else ("L" if f < a else "D") for f, a in zip(gf, ga_)]

    df["date"] = pd.to_datetime(df["date_time_utc"]).dt.date.astype(str)
    df["ga_total"] = df["goals_added_total"]

    cols = [
        "player_id", "player_name", "position", "team_id", "team_abbreviation",
        "game_id", "date", "matchday", "opponent", "home", "score", "result",
        "goals_for", "goals_against", "minutes_played", "ga_total",
        *_ACTION_WEIGHT_COLS.values(),
    ]
    out = df[[c for c in cols if c in df.columns]].copy()
    return out.sort_values(["matchday", "date", "ga_total"],
                           ascending=[True, True, False]).reset_index(drop=True)


def latest_complete_matchday(games: pd.DataFrame, as_of: date | None = None,
                             lag_days: int = 2) -> int | None:
    """Most recent matchday old enough to be treated as finished.

    `status` cannot answer this — ASA returns played fixtures only, so every row
    says FullTime and an in-progress matchday is indistinguishable from a
    finished one. Nor can fixture count: rounds legitimately run 8 to 11 games
    because makeups fold into the same matchday.

    So use time. A matchday counts as complete once its LAST fixture is at least
    `lag_days` old, which clears ASA's 1-3 day ingest lag and means a round still
    being played is never selected.
    """
    if games is None or games.empty:
        return None
    g = games.dropna(subset=["matchday"]).copy()
    if g.empty:
        return None
    g["_d"] = pd.to_datetime(g["date_time_utc"]).dt.date
    cutoff = (as_of or date.today()) - timedelta(days=lag_days)
    last = g.groupby("matchday")["_d"].max()
    eligible = last[last <= cutoff]
    return int(eligible.index.max()) if not eligible.empty else None


def full_round(games: pd.DataFrame) -> int | None:
    """Fixtures in a complete round, derived from the season's own fixtures.

    Counted as the distinct clubs actually appearing in this season's fixtures,
    halved — NOT len(teams)//2. The teams reference holds 19 rows including
    defunct clubs, while 16 played in 2026, so the reference would say a full
    round is 9. Seven of the season's sixteen matchdays have exactly 8 fixtures,
    and every one of them would have been falsely flagged as missing a game.
    """
    if games is None or games.empty:
        return None
    clubs = set(games["home_team_id"]) | set(games["away_team_id"])
    return (len(clubs) // 2) or None


def match_coverage(games: pd.DataFrame, matchday: int, teams: pd.DataFrame | None = None) -> dict:
    """What a matchday actually contains — never an implied completeness.

    Because ASA omits unplayed fixtures, a postponed game leaves no trace. The
    honest thing a card can do is state its coverage and flag when the count is
    short of a full round, rather than presenting a partial round as the week.

    Counts ABOVE a full round are normal — makeups fold into the same matchday,
    and 2026 has rounds of 9, 10, 11, 12 and 13 — so they are never flagged.

    `teams` is accepted and ignored; the round size comes from the fixtures.
    """
    g = games[games["matchday"] == matchday].copy()
    if g.empty:
        return {"matchday": matchday, "fixtures": 0, "expected": None,
                "short": False, "first_date": None, "last_date": None}
    g["_d"] = pd.to_datetime(g["date_time_utc"]).dt.date
    expected = full_round(games)
    n = int(len(g))
    return {
        "matchday": int(matchday),
        "fixtures": n,
        "expected": expected,
        "short": bool(expected and n < expected),
        "first_date": min(g["_d"]),
        "last_date": max(g["_d"]),
    }


def coverage_line(cov: dict) -> str:
    """One-line coverage summary for the card and the page."""
    if not cov or not cov["fixtures"]:
        return f"MATCHDAY {cov.get('matchday', '?')}  ·  NO FIXTURES"
    span = _date_span(cov["first_date"], cov["last_date"])
    count = (f"{cov['fixtures']} OF {cov['expected']} FIXTURES"
             if cov["short"] else f"{cov['fixtures']} FIXTURES")
    line = f"MATCHDAY {cov['matchday']}  ·  {count}  ·  {span}"
    if cov["short"]:
        line += "  ·  one or more fixtures not yet reported"
    return line


def _date_span(d1: date, d2: date) -> str:
    """'Aug 6-10' / 'Aug 30-Sep 1'. Avoids %-d, which is not portable to Windows."""
    if d1 == d2:
        return f"{d1:%b} {d1.day}"
    if d1.month == d2.month:
        return f"{d1:%b} {d1.day}-{d2.day}"
    return f"{d1:%b} {d1.day}-{d2:%b} {d2.day}"


def select_team_of_the_week(
    match_table: pd.DataFrame,
    matchday: int,
    gk_table: pd.DataFrame | None = None,
    min_minutes: int = TOTW_MIN_MINUTES,
) -> list[dict]:
    """Best XI from a single matchday, ranked on RAW g+ within position.

    DELIBERATELY SIMPLE. No shrinkage, no z-scores, no cohort standardisation —
    none of the machinery the season and form metrics need. Team of the Week is a
    single-match selection by construction, which every football fan already
    understands; the small sample is the format, not a flaw to correct for. The
    card shows raw g+ and minutes played, and nothing else pretends otherwise.

    Ranking on raw g+ rather than per-90 also means a short appearance is
    naturally disadvantaged, since g+ accumulates with time on the pitch. That is
    why the minutes floor can stay permissive.

    Slots that have no qualifying player return the shared sentinel rather than
    raising or being dropped — a thin matchday must render a blank slot, not an
    error, on the most public card the site has.
    """
    picked: set[str] = set()
    rows: list[dict] = []

    outfield = match_table[
        (match_table["matchday"] == matchday)
        & (match_table["minutes_played"] >= min_minutes)
    ] if match_table is not None and not match_table.empty else pd.DataFrame()

    keepers = gk_table[
        (gk_table["matchday"] == matchday)
        & (gk_table["minutes_played"] >= min_minutes)
    ] if gk_table is not None and not gk_table.empty else pd.DataFrame()

    for slot_def in TOTW_SLOTS:
        slot, position, line = slot_def["slot"], slot_def["position"], slot_def["line"]
        x, y = TOTW_COORDS[slot]

        pool = keepers if position == "GK" else (
            outfield[outfield["position"] == position] if not outfield.empty else pd.DataFrame()
        )
        if pool is None or pool.empty:
            rows.append(_empty_slot(slot, position, line, x, y))
            continue

        pool = pool.sort_values("ga_total", ascending=False)
        chosen = None
        for _, r in pool.iterrows():
            if r["player_id"] in picked:
                continue
            chosen = r
            picked.add(r["player_id"])
            break

        if chosen is None:
            rows.append(_empty_slot(slot, position, line, x, y))
            continue

        venue = "vs" if chosen["home"] else "at"
        row = {
            "slot": slot, "position": position, "line": line, "x": x, "y": y,
            "player_name": chosen["player_name"],
            "team_name": chosen.get("team_abbreviation", ""),
            "team_abbreviation": chosen.get("team_abbreviation", ""),
            "value_score": float(chosen["ga_total"]),
            "minutes_played": int(chosen["minutes_played"]),
            "rank_in_position": 1,
            "cohort_size": int(len(pool)),
            # Opponent and scoreline — the texture a weekly card needs.
            "context": f"{venue} {chosen['opponent']} {chosen['score']}",
        }
        if position == "GK":
            # GK g+ comes from Shotstopping/Claiming/Sweeping etc. — a different
            # quantity that happens to share a unit name. The tag makes the
            # different scale visible on the card, since eleven identically
            # formatted numbers otherwise read as one ranking.
            row["scale_tag"] = "GK"
        rows.append(row)

    return rows


def _empty_match_table() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "player_id", "player_name", "position", "team_id", "team_abbreviation",
        "game_id", "date", "matchday", "opponent", "home", "score", "result",
        "goals_for", "goals_against", "minutes_played", "ga_total",
        *_ACTION_WEIGHT_COLS.values(),
    ])
