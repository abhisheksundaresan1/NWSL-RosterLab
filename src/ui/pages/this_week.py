"""This week — the landing page, rebuilt around an actual week.

WHAT CHANGED AND WHY

The page used to be five near-identical pitch graphics over one 30-day window,
with the same players named in the headline, in three stat cards, and again in
the XIs below. Nothing on it was weekly, on a page called "This week".

Now:
  * the hero makes ONE claim about ONE player, drawn from the matchday just
    played, instead of three clauses of equal weight;
  * Team of the Week is the lead block — the thing that makes the page weekly;
  * one movement card follows (In form, the 30-day view) rather than two that
    share players and split the reader's attention;
  * the disclosure that used to sit between the hero and the first content is in
    an expander. Credibility should be available, not mandatory reading;
  * Newcomers, Undervalued XI and Risers & Fallers moved to Drops. They are
    seasonal and monthly artifacts, and they were burying the weekly one.

WHY "IN FORM" AND NOT "RISERS" IS THE CARD THAT STAYED
Risers needs a qualifying sample in two consecutive windows. Across the August
international break only 56 of 203 players had one at all, and at MIN_FORM_DELTA
roughly eight clear it league-wide — a landing-page block that can go nearly
empty. In form is always computable, and beside Team of the Week it reads as a
clean zoom-out: this week, then this month.
"""

from __future__ import annotations

from datetime import date as _date

import pandas as pd
import streamlit as st

from src.analysis.form import (
    FORM_WINDOW_DAYS, K_FORM, MIN_MINUTES_FORM, SMALL_COHORT, MIN_COHORT_FOR_RANK,
    form_as_card_rows, dominant_action,
)
from src.analysis.matches import (
    TOTW_MIN_MINUTES, coverage_line, latest_complete_matchday, match_coverage,
    select_team_of_the_week,
)
from src.analysis.movement import select_risers_xi
from src.analysis.season import IN_SEASON_YEAR
from src.analytics import track
from src.share.card import render_leaderboard_card
from src.ui import components as c
from src.ui import loaders, theme

# Rendered card width on the page. The downloaded PNG stays 1080x1350 — st.image
# scales the display only — so the page stops being a long scroll of full-width
# graphics without touching what a visitor actually shares.
CARD_PX = 430

_ACTION_PHRASE = {
    "shooting": "her shooting", "dribbling": "her dribbling", "passing": "her passing",
    "receiving": "her movement to receive", "interrupting": "defensive actions",
    "fouling": "the fouls she draws",
}


def _human_date(iso: str | None) -> str:
    """'2026-08-10' -> 'AUGUST 10'. Avoids %-d, which is not portable to Windows."""
    if not iso:
        return ""
    d = _date.fromisoformat(iso)
    return f"{d:%B} {d.day}".upper()


def render() -> None:
    fixtures = loaders.load_fixtures()
    matches = loaders.load_matches()
    keepers = loaders.load_matches(goalkeepers=True)

    matchday = latest_complete_matchday(fixtures) if not fixtures.empty else None
    cov = match_coverage(fixtures, matchday) if matchday else None

    form_date = loaders.latest_form_date()
    form = loaders.load_form(form_date) if form_date else pd.DataFrame()

    totw = (select_team_of_the_week(matches, matchday, gk_table=keepers)
            if matchday and not matches.empty else [])

    # --- Hero ---------------------------------------------------------------
    eyebrow = (f"NWSL {IN_SEASON_YEAR} · {coverage_line(cov)}" if cov
               else f"NWSL {IN_SEASON_YEAR}")
    st.markdown(f'<p class="rl-eyebrow-hero">{c._esc(eyebrow)}</p>',
                unsafe_allow_html=True)
    _hero(totw, form, cov)

    # --- Team of the Week ---------------------------------------------------
    if totw:
        theme.rule()
        _team_of_the_week(totw, cov, matchday)

    # --- In form ------------------------------------------------------------
    if not form.empty:
        theme.rule()
        _in_form(form, form_date)

    # --- Disclosure, collapsed ---------------------------------------------
    theme.rule()
    _how_calculated(form, cov)


# --- Hero -------------------------------------------------------------------

def _hero(totw: list[dict], form: pd.DataFrame, cov: dict | None) -> None:
    """One dominant claim about one player.

    HARD RULE, unchanged: every clause is computed from columns we hold. The page
    has no access to commentary, so it cannot narrate a match — but it does now
    know the opponent and the scoreline, which is fact from the fixtures table
    rather than invention, and which action type drove the performance, which is
    arithmetic on the g+ breakdown.
    """
    filled = [r for r in totw if r["player_name"] != "—" and not r.get("scale_tag")]
    if filled:
        top = max(filled, key=lambda r: r["value_score"])
        st.markdown(
            f'<p class="rl-lede"><b>{c._esc(top["player_name"])}</b> was the standout of '
            f'the matchday, adding <b>{top["value_score"]:+.2f} goals</b> for '
            f'{c._esc(top["team_abbreviation"])} {c._esc(top["context"])} — the best '
            f'single performance of the round.</p>',
            unsafe_allow_html=True,
        )
        _named = {top["player_name"]}
    elif not form.empty:
        best = form.nlargest(1, "form_weighted_p90").iloc[0]
        action = dominant_action(best)
        tail = f" — driven mostly by {_ACTION_PHRASE[action]}" if action else ""
        st.markdown(
            f'<p class="rl-lede"><b>{c._esc(best["player_name"])}</b> has been the '
            f'league\'s best performer over the last {FORM_WINDOW_DAYS} days at '
            f'{best["form_weighted_p90"]:.2f} g+/90{tail}.</p>',
            unsafe_allow_html=True,
        )
        _named = {best["player_name"]}
    else:
        st.markdown('<p class="rl-lede">The season is under way — check back once a '
                    'few more games are in.</p>', unsafe_allow_html=True)
        _named = set()

    st.session_state["_tw_hero_named"] = _named


# --- Team of the Week -------------------------------------------------------

def _team_of_the_week(rows: list[dict], cov: dict | None, matchday: int) -> None:
    line = coverage_line(cov) if cov else f"MATCHDAY {matchday}"
    theme.section(
        "Team of the Week",
        subtitle=(f"The best performance at each position from matchday {matchday}, by "
                  f"raw goals added in those matches. Minimum {TOTW_MIN_MINUTES} minutes "
                  f"played. The keeper is ranked among keepers — goalkeeper g+ is built "
                  f"from shot-stopping, claiming and sweeping, so it is **not** comparable "
                  f"with an outfielder's number and is tagged GK on the card."),
    )

    key = f"totw_{matchday}_v1"
    if key not in st.session_state:
        with st.spinner("Building Team of the Week…"):
            st.session_state[key] = render_leaderboard_card(
                rows, title="Team of the Week", season=IN_SEASON_YEAR,
                subtitle="Best performance per position · raw goals added",
                coverage=line,
            )
    png = st.session_state[key]

    img_col, meta_col = st.columns([2, 3])
    with img_col:
        st.image(png, width=CARD_PX)
    with meta_col:
        if st.download_button("⬇ Download PNG", data=png,
                              file_name=f"team_of_the_week_{IN_SEASON_YEAR}_md{matchday}.png",
                              mime="image/png", key="dl_totw"):
            track.card_download("team_of_the_week", season=IN_SEASON_YEAR,
                                matchday=int(matchday))
        st.caption(f"**{line}**")
        _totw_table(rows)


def _totw_table(rows: list[dict]) -> None:
    filled = [r for r in rows if r["player_name"] != "—"]
    if not filled:
        return
    with st.expander("Full XI — match detail", expanded=False):
        df = pd.DataFrame([{
            "Pos": r["position"],
            "Player": r["player_name"] + (" (GK)" if r.get("scale_tag") else ""),
            "Team": r["team_abbreviation"],
            "Match": r.get("context", ""),
            "g+": f"{r['value_score']:+.2f}",
            "Minutes": r["minutes_played"],
        } for r in filled])
        st.dataframe(c.dash_blanks(df), hide_index=True, width="stretch")


# --- In form ----------------------------------------------------------------

def _in_form(form: pd.DataFrame, form_date: str | None) -> None:
    theme.section(
        f"In form · last {FORM_WINDOW_DAYS} days",
        subtitle=(f"Zooming out from the single matchday: position-weighted goals added "
                  f"per 90 across the last {FORM_WINDOW_DAYS} days. A different metric "
                  f"from the season value score, and never combined with it."),
    )

    key = f"form_card_{form_date}_v2"
    if key not in st.session_state:
        with st.spinner("Building form XI…"):
            rows = select_risers_xi(form_as_card_rows(form, "form_weighted_p90"))
            st.session_state[key] = (
                render_leaderboard_card(
                    rows, title="In form", season=IN_SEASON_YEAR,
                    subtitle=f"Weighted g+/90 · last {FORM_WINDOW_DAYS} days · outfield only",
                ),
                rows,
            )
    png, rows = st.session_state[key]

    img_col, meta_col = st.columns([2, 3])
    with img_col:
        st.image(png, width=CARD_PX)
    with meta_col:
        if st.download_button("⬇ Download PNG", data=png,
                              file_name=f"in_form_{IN_SEASON_YEAR}_{form_date}.png",
                              mime="image/png", key="dl_in_form"):
            track.card_download("in_form", season=IN_SEASON_YEAR, window=form_date)
        st.caption(
            f"Minimum {MIN_MINUTES_FORM} minutes in the window, shrunk toward the "
            f"position average (K = {K_FORM})."
        )
        _form_table(rows)


def _form_table(rows: list[dict]) -> None:
    filled = [r for r in rows if r["player_name"] != "—"]
    if not filled:
        return
    with st.expander("Full XI — full stats", expanded=False):
        df = pd.DataFrame([{
            "Pos": r["position"],
            "Player": r["player_name"],
            "Team": r["team_name"],
            "g+/90": f"{r['value_score']:+.2f}",
            "Minutes": f"{r['minutes_played']:,}",
        } for r in filled])
        st.dataframe(c.dash_blanks(df), hide_index=True, width="stretch")


# --- Disclosure -------------------------------------------------------------

def _how_calculated(form: pd.DataFrame, cov: dict | None) -> None:
    """All the caveats, in one expander.

    They used to occupy three paragraphs between the hero and the first content,
    which meant every visitor read the limitations before seeing anything the
    limitations applied to. Each card keeps a one-line version beside it.
    """
    with st.expander("How this is calculated", expanded=False):
        st.markdown(
            f"""
**Team of the Week** — raw goals added in that matchday's matches only, ranked within
position, minimum {TOTW_MIN_MINUTES} minutes. Deliberately no shrinkage and no z-scores:
a single-match sample is what the format *is*, and every football fan already reads it
that way. The goalkeeper is ranked among keepers, from a different set of actions
(shot-stopping, claiming, sweeping), so her number is tagged **GK** and is not comparable
with the outfielders' on the same card.

**Coverage** — ASA publishes played fixtures only, so a postponed game leaves no trace in
the data. The card states how many fixtures its matchday contains, and says so explicitly
when that is short of a full round, rather than implying a complete week.

**In form** — the last {FORM_WINDOW_DAYS} days only, for players with at least
{MIN_MINUTES_FORM} minutes in that window, shrunk toward the position average with
**K = {K_FORM} minutes** and then z-scored within position. It is a separate metric from
the season value score and the two are never added, subtracted or shown in one column.
"""
        )
        if not form.empty:
            n_cur, n_prev = int(form["form_fixtures"].iloc[0]), int(form["prev_fixtures"].iloc[0])
            st.markdown(
                f"This form window covers **{n_cur} fixtures**; the previous "
                f"{FORM_WINDOW_DAYS} days covered **{n_prev}**."
            )
            thin = (form[["position", "form_cohort_n"]].drop_duplicates()
                    .query(f"form_cohort_n <= {SMALL_COHORT}").sort_values("form_cohort_n"))
            if not thin.empty:
                bits = ", ".join(f"{r.position} ({int(r.form_cohort_n)})"
                                 for r in thin.itertuples())
                st.markdown(
                    f"**Thin cohorts this window** — {bits}. A form score is standardised "
                    f"against the players at that position who clear the floor, so with a "
                    f"small group the ranking is indicative. Positions with fewer than "
                    f"{MIN_COHORT_FOR_RANK} qualifiers get no rank or score at all."
                )
        st.caption(
            "Value scores are z-scored within position and are not comparable across "
            "positions or across seasons."
        )
