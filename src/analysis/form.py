"""
src/analysis/form.py — Form: how a player has actually played *recently*.

WHAT THIS IS NOT
================
This is an ADDITION to the season value score, never a replacement for it. The
season-to-date `value_score` in ranking.py is untouched by this module: nothing
here writes to it, and `POSITION_WEIGHTS` is imported read-only. The two numbers
are shown side by side ("season +2.67 · last 30 days +1.20") and are NEVER
differenced, summed, or placed in the same column. They answer different
questions over different samples, and subtracting them would be meaningless.

WHY IT EXISTS
=============
The previous "movement" number diffed two cumulative season-to-date value scores.
Two problems, both structural:

  1. It is damped by construction. By August a player has ~1,500 minutes in the
     denominator, so even a superb month barely moves the season average — the
     metric understates exactly the recent change it claims to measure.
  2. It systematically favours low-minute players. A player with 300 season
     minutes has a small denominator, so the same absolute performance swing
     produces a much larger delta than it would for a regular starter.

Form fixes both by computing over a fixed recent WINDOW rather than differencing
two cumulative totals.

THE METHOD
==========
Identical in shape to the season model — per-90 → position weights → shrink →
z-score within position — so the editorial judgment in POSITION_WEIGHTS is shared
rather than forked. Only the time slice and two constants differ.

THE ONE SUBTLE DECISION: form_delta is a RAW RATE difference
============================================================
`form_score` is a z-score, standardised against whoever qualified in that window.
The current and previous windows qualify DIFFERENT players, so their z-scores sit
on different scales — differencing them would reintroduce precisely the
incomparability the app already warns about across seasons, and it would do so
invisibly, inside a number labelled as a change.

So `form_delta` subtracts `form_weighted_p90` — the weighted rate BEFORE
z-scoring. That is an actual quantity (goals added per 90) whose meaning does not
depend on who else happened to play. It is labelled in g+/90 everywhere it is
shown, never in "value points".

Typical magnitudes, so a wrong unit is obvious on sight: league `weighted_ga_p90`
runs mean ≈ 0.27, SD ≈ 0.09. A large 30-day swing is ~0.1. Anything near ±0.7 is
a bug, not a story.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.analysis.ranking import (
    POSITION_WEIGHTS,
    _ACTION_WEIGHT_COLS,
    _unpack_goals_added,
)

# --- The window ------------------------------------------------------------
# THE single definition of "recent" in this app. This_week and Teams both import
# it; there is deliberately no second window constant anywhere. It is expressed
# in DAYS, never in snapshots, so how often the cron pulls data cannot change
# what it means (see movement.snapshot_on_or_before).
FORM_WINDOW_DAYS = 30

# --- Thresholds ------------------------------------------------------------
# K_FORM = 120, not the season model's 300. A 30-day window is roughly four
# matches (~360 min). K=300 would shrink that sample ~45% toward the position
# mean, flattening the metric into noise and defeating the point of measuring
# recent form at all. K=120 shrinks it ~25%: still enough that one good cameo
# cannot top the table, not so much that real form is erased.
K_FORM = 120

# Two full matches. Below this nothing is published — not a shrunk score, nothing.
MIN_MINUTES_FORM = 180

# Cohort guards. A 30-day window at a 180-minute floor yields THINNER positional
# cohorts than the season table, where CM already has only 9 qualifiers. Both
# numbers were fixed before any cohort count was computed, so neither can be
# tuned to flatter the output after the fact.
SMALL_COHORT = 10          # at or below: publish the rank, but caveat it
MIN_COHORT_FOR_RANK = 5    # below: publish NO rank and NO z-score at all

# Minimum movement worth calling a rise or a fall, in weighted g+/90.
#
# The league's weighted_ga_p90 has SD ~= 0.09, so 0.10 is "this player's rate
# moved by about the gap between an average and a good player". Without it the
# cards fill with noise: measured on a live window the median |form_delta| is
# 0.075 and 63% of movers clear 0.05, which is how +0.047 and +0.046 came to
# occupy slots on a card headed "biggest risers".
#
# At this threshold only ~8 players qualify league-wide, which is why Risers and
# Fallers no longer use the formation card — see render_ranked_list_card.
MIN_FORM_DELTA = 0.10

# --- On the uneven calendar -------------------------------------------------
# The NWSL season is not uniform: 2026 has a 27-day international break
# (2026-05-31 → 06-27) and a 19-day one in April. The 30 days to 2026-08-10 hold
# 46 fixtures; the preceding 30 hold 14.
#
# The guard against this is the PER-PLAYER minutes floor, applied to BOTH
# windows: form_delta exists only for players with >= MIN_MINUTES_FORM in each.
# That is the honest control, because a per-90 rate is comparable on the strength
# of the player's own sample — 200 minutes of football is 200 minutes of football
# regardless of how many fixtures the rest of the league played.
#
# It also self-limits without being told to: across the August break only 28% of
# qualifying players have a delta at all, against 63% in a full month. Rather
# than suppress league-wide on a fixture-count ratio (which was tried and refused
# genuinely valid comparisons, e.g. 23 fixtures against 47), both windows'
# fixture counts are carried in the data and shown, so the reader can see the
# density difference and judge it.

_ACTION_KEYS = list(_ACTION_WEIGHT_COLS)


def window_bounds(anchor: date | str, days: int = FORM_WINDOW_DAYS) -> tuple[str, str]:
    """(start, end) ISO dates for the `days`-long window ending at `anchor`."""
    end = date.fromisoformat(str(anchor)) if not isinstance(anchor, date) else anchor
    return (end - timedelta(days=days)).isoformat(), end.isoformat()


def compute_form(
    ga_window: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    ga_prev: pd.DataFrame | None = None,
    min_minutes: int = MIN_MINUTES_FORM,
    K: int = K_FORM,
) -> pd.DataFrame:
    """Form over one window, optionally with the change from the previous window.

    Parameters
    ----------
    ga_window : per-match g+ for the current window — the output of
                fetch_player_goals_added(start_date=…, end_date=…,
                split_by_games=True). Per-match (not aggregated) so matches can
                be counted exactly rather than estimated from minutes.
    ga_prev   : same for the preceding window. If given, form_delta is computed.

    Returns one row per qualifying player:
      player_id, player_name, team_name, team_abbreviation, position,
      form_score, form_weighted_p90, form_ga_p90, form_delta,
      form_minutes, form_matches, form_cohort_n, form_rankable,
      form_rank, form_sample_label, ga_*_p90
    """
    cur = _window_rates(ga_window, players, teams, min_minutes)
    if cur.empty:
        return _empty_form()

    # Shrink toward the position mean, then z-score — same sequence as the season
    # model, over this window's qualifying pool only.
    pos_mean = cur.groupby("position")["form_weighted_p90"].transform("mean")
    minutes = cur["form_minutes"].astype(float)
    shrunk = (minutes * cur["form_weighted_p90"] + K * pos_mean) / (minutes + K)
    cur["form_score"] = (
        shrunk.groupby(cur["position"]).transform(
            lambda g: (g - g.mean()) / g.std() if g.std() > 0 else 0.0
        ).fillna(0.0)
    )

    cur["form_cohort_n"] = cur.groupby("position")["player_id"].transform("size")
    cur["form_rank"] = cur.groupby("position")["form_score"].rank(
        ascending=False, method="min"
    ).astype(int)

    # Below MIN_COHORT_FOR_RANK a z-score over a handful of players is not a
    # ranking, so the flag is carried in the data and the UI suppresses BOTH the
    # rank and the score. Deciding this here rather than in the UI means every
    # surface gets the same answer.
    cur["form_rankable"] = cur["form_cohort_n"] >= MIN_COHORT_FOR_RANK
    cur.loc[~cur["form_rankable"], ["form_score", "form_rank"]] = pd.NA

    # --- The change, on the RAW RATE (see module docstring) -----------------
    cur["form_fixtures"] = _fixture_count(ga_window)
    cur["prev_fixtures"] = _fixture_count(ga_prev)
    cur["form_delta"] = pd.NA

    if ga_prev is not None and not ga_prev.empty:
        prev = _window_rates(ga_prev, players, teams, min_minutes)
        if not prev.empty:
            # Inner-join semantics via a left merge: a player missing from `prev`
            # did not clear the minutes floor in that window, so she gets no
            # delta rather than a delta computed against a fragment.
            cur = cur.merge(
                prev[["player_id", "form_weighted_p90"]].rename(
                    columns={"form_weighted_p90": "_prev_p90"}
                ),
                on="player_id", how="left",
            )
            cur["form_delta"] = (cur["form_weighted_p90"] - cur["_prev_p90"]).round(3)
            cur = cur.drop(columns=["_prev_p90"])

    cur["form_sample_label"] = cur.apply(
        lambda r: f"{int(r['form_minutes']):,} min / {int(r['form_matches'])} matches", axis=1
    )

    for col in ("form_score", "form_weighted_p90", "form_ga_p90"):
        cur[col] = pd.to_numeric(cur[col], errors="coerce").round(3)

    return cur.sort_values(
        ["position", "form_weighted_p90"], ascending=[True, False]
    ).reset_index(drop=True)


def _fixture_count(ga: pd.DataFrame | None) -> int:
    """Distinct fixtures in a per-match g+ pull — free, it is already in the data."""
    if ga is None or ga.empty or "game_id" not in ga.columns:
        return 0
    return int(ga["game_id"].nunique())


def _window_rates(
    ga: pd.DataFrame, players: pd.DataFrame, teams: pd.DataFrame, min_minutes: int
) -> pd.DataFrame:
    """Per-match g+ rows → one qualifying row per player with weighted rates."""
    if ga is None or ga.empty or "data" not in ga.columns:
        return _empty_form()

    df = _unpack_goals_added(ga)

    # Exact match count from game_id when the per-match pull was used; otherwise
    # fall back to the minutes estimate and say so by name.
    has_games = "game_id" in df.columns
    agg = {c: "sum" for c in [*_ACTION_WEIGHT_COLS.values(), "goals_added_total"]}
    agg["minutes_played"] = "sum"
    grouped = df.groupby("player_id", as_index=False).agg(agg)

    if has_games:
        matches = df.groupby("player_id")["game_id"].nunique().rename("form_matches")
        grouped = grouped.merge(matches, on="player_id", how="left")
    else:
        grouped["form_matches"] = (grouped["minutes_played"] // 90).astype(int)

    # Position and team from the player's most recent row in the window.
    last = df.drop_duplicates("player_id", keep="last")[["player_id", "general_position", "team_id"]]
    grouped = grouped.merge(last, on="player_id", how="left")
    grouped["team_id"] = grouped["team_id"].apply(lambda v: str(v).split(",")[-1].strip())
    grouped = grouped.rename(columns={"general_position": "position"})

    grouped = grouped.merge(
        players[["player_id", "player_name"]].drop_duplicates("player_id"),
        on="player_id", how="left",
    ).merge(
        teams[["team_id", "team_name", "team_abbreviation"]], on="team_id", how="left"
    )

    grouped = grouped[grouped["minutes_played"] >= min_minutes].copy()
    # GK is excluded from the value model, so it is excluded here too.
    grouped = grouped[grouped["position"].isin(POSITION_WEIGHTS)].copy()
    # A handful of player_ids in the g+ feed have no row in the players reference.
    # They cannot be named on a card or in a sentence, and an unnamed player in a
    # cohort would still shift its z-score, so drop them from the pool entirely.
    grouped = grouped[grouped["player_name"].notna()].copy()
    if grouped.empty:
        return _empty_form()

    p90 = grouped["minutes_played"] / 90
    grouped["form_ga_p90"] = grouped["goals_added_total"] / p90
    for key, col in _ACTION_WEIGHT_COLS.items():
        grouped[f"{col}_p90"] = grouped[col] / p90

    grouped["form_weighted_p90"] = grouped.apply(
        lambda r: sum(
            r[f"{col}_p90"] * POSITION_WEIGHTS[r["position"]][key]
            for key, col in _ACTION_WEIGHT_COLS.items()
        ),
        axis=1,
    )

    return grouped.rename(columns={"minutes_played": "form_minutes"})


def form_as_card_rows(form: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """Rename a form table into the column names the XI card selectors expect.

    src/analysis/movement.py's _select_movers_xi (and so select_risers_xi /
    select_fallers_xi) fills one formation slot per position from a frame with
    `delta_value_score`, `minutes_new` and `rank_new`. An adapter here means the
    slot-filling, sentinel and threshold logic stay in exactly one place and keep
    working for both metrics — rather than a second, near-identical copy of that
    logic drifting out of sync inside the form code.

    `value_col` is the form column to rank and display: form_weighted_p90 for
    "who is playing best right now", form_delta for "who has changed most".
    Both are rates in g+/90, so the card's number always carries that unit.
    """
    if form.empty or value_col not in form.columns:
        return pd.DataFrame(columns=[
            "player_id", "player_name", "team_name", "team_abbreviation",
            "position", "delta_value_score", "minutes_new", "rank_new",
        ])
    out = form.dropna(subset=[value_col]).copy()
    out["delta_value_score"] = out[value_col].astype(float)
    out["minutes_new"] = out["form_minutes"]
    # rank within position on the column actually being displayed
    out["rank_new"] = out.groupby("position")["delta_value_score"].rank(
        ascending=False, method="min"
    ).astype(int)
    return out


def dominant_action(row: pd.Series, prev: pd.Series | None = None) -> str | None:
    """Which action type drove this player's form — for the headline sentence.

    Returns a plain-English action name ("interrupting", "passing") or None when
    nothing dominates. Strictly derived from columns we hold: it names an action
    type and nothing else. It cannot reference a match, opponent or scoreline,
    because it has no access to any.
    """
    contribs = {}
    for key, col in _ACTION_WEIGHT_COLS.items():
        w = POSITION_WEIGHTS.get(row.get("position"), {}).get(key, 1.0)
        cur = float(row.get(f"{col}_p90", 0.0) or 0.0) * w
        contribs[key] = cur - (float(prev.get(f"{col}_p90", 0.0) or 0.0) * w if prev is not None else 0.0)

    if not contribs:
        return None
    total = sum(abs(v) for v in contribs.values())
    if total <= 0:
        return None
    key = max(contribs, key=lambda k: abs(contribs[k]))
    # Only call it dominant if it really is: at least 40% of all movement.
    return key if abs(contribs[key]) / total >= 0.40 else None


def _empty_form() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "player_id", "player_name", "team_name", "team_abbreviation", "position",
        "form_score", "form_weighted_p90", "form_ga_p90", "form_delta",
        "form_minutes", "form_matches", "form_cohort_n", "form_rankable",
        "form_rank", "form_sample_label",
        "form_fixtures", "prev_fixtures",
    ])
