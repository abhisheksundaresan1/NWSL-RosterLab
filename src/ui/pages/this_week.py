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
    FORM_WINDOW_DAYS, K_FORM, MIN_FORM_DELTA, MIN_MINUTES_FORM,
    SMALL_COHORT, MIN_COHORT_FOR_RANK, form_as_card_rows, dominant_action,
)
from src.analysis.matches import (
    TOTW_MIN_MINUTES, coverage_line, latest_complete_matchday, match_coverage,
    select_team_of_the_week, totw_changes, totw_history,
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
CARD_PX = 700

# Layout: the card takes 3 of 5 columns, controls the other 2.
#
# Measured rather than guessed. The context line ("vs NC 3-4") is 17px on a
# 1080px-wide card, so in a 1180px container it renders at:
#     400px display (the old half-width)  ->  6.3px   illegible
#     578px (two cards side by side)      ->  9.1px   still too small
#     708px (this layout)                 -> 11.1px   legible
#     1180px (full width)                 -> 18.6px   but 1475px tall per card
# Those context lines are the whole point of the match-level card, so legibility
# decided it. The 3:2 split also fills the right column, which previously held a
# download button and nothing else.
CARD_COLS = [2, 1]

# Time-scale colours. Two identical pitch graphics forced the reader to parse
# header text to tell "this weekend" from "this month".
TOTW_HEADER   = "#5C3A1A"   # warm — a single matchday
INFORM_HEADER = "#1A3A5C"   # cool — the rolling month (unchanged from before)

SHARE_URL = "https://nwsl-rosterlab.streamlit.app"

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
    changes = (totw_changes(totw_history(matches, keepers, matchday), matchday)
               if matchday and not matches.empty else None)
    newcomers = _newcomer_table(loaders.latest_snapshot())

    # --- Hero ---------------------------------------------------------------
    eyebrow = (f"NWSL {IN_SEASON_YEAR} · {coverage_line(cov)}" if cov
               else f"NWSL {IN_SEASON_YEAR}")
    st.markdown(f'<p class="rl-eyebrow-hero">{c._esc(eyebrow)}</p>',
                unsafe_allow_html=True)
    _hero(totw, form, newcomers, changes)
    _what_changed(changes)

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

def _newcomer_table(snap: str | None) -> pd.DataFrame:
    """First-year players by season value — only used as the hero's last resort.

    The Newcomers CARD lives on Drops; this is just the ranked frame behind it,
    so hero line 3 has something true to fall back on when there is no first-time
    Team of the Week selection and nobody has moved enough to count as a riser.
    """
    if not snap:
        return pd.DataFrame()
    try:
        table = loaders.load_in_season_table(MOVE_MIN_MINUTES, snap)
        hist = loaders.cached_historical_ids()
        out = table[~table["player_id"].astype(str).isin(hist)]
        return out.sort_values("value_score", ascending=False)
    except Exception:
        return pd.DataFrame()


def _hero(totw: list[dict], form: pd.DataFrame, newcomers: pd.DataFrame,
          changes: dict | None) -> None:
    """Three stories, largest first, each on its own line.

    HARD RULE, unchanged: every clause is computed from columns we hold. No LLM
    is involved and the page has no commentary, so it cannot narrate a match. It
    does know the opponent and scoreline (fact, from the fixtures table) and
    which action type drove a performance (arithmetic on the g+ breakdown).

    A player is named at most once across the three lines. Each line falls
    through to the next candidate if its subject is already spoken for, and a
    line that has nothing left to say is simply omitted rather than padded.
    """
    named: set[str] = set()

    # --- Line 1: the matchday standout -------------------------------------
    outfield = [r for r in totw if r["player_name"] != "—" and not r.get("scale_tag")]
    if outfield:
        top = max(outfield, key=lambda r: r["value_score"])
        named.add(top["player_name"])
        st.markdown(
            f'<p class="rl-hero-1"><b>{c._esc(top["player_name"])}</b> was the standout '
            f'of the matchday — {top["value_score"]:+.2f} goals added for '
            f'{c._esc(top["team_abbreviation"])} {c._esc(top["context"])}.</p>',
            unsafe_allow_html=True,
        )

    # --- Line 2: the 30-day form leader ------------------------------------
    if not form.empty:
        pool = form.sort_values("form_weighted_p90", ascending=False)
        pick = next((r for _, r in pool.iterrows() if r["player_name"] not in named), None)
        if pick is not None:
            named.add(pick["player_name"])
            action = dominant_action(pick)
            tail = f", mostly {_ACTION_PHRASE[action]}" if action else ""
            st.markdown(
                f'<p class="rl-hero-2"><b>{c._esc(pick["player_name"])}</b> leads the '
                f'league over the last {FORM_WINDOW_DAYS} days at '
                f'{pick["form_weighted_p90"]:.2f} g+/90{tail}.</p>',
                unsafe_allow_html=True,
            )

    # --- Line 3: one more computed story -----------------------------------
    third = _third_story(totw, form, newcomers, changes, named)
    if third:
        st.markdown(f'<p class="rl-hero-3">{third}</p>', unsafe_allow_html=True)

    if not named:
        st.markdown('<p class="rl-hero-1">The season is under way — check back once a '
                    'few more games are in.</p>', unsafe_allow_html=True)


def _third_story(totw: list[dict], form: pd.DataFrame, newcomers: pd.DataFrame,
                 changes: dict | None, named: set[str]) -> str | None:
    """The best remaining story, in priority order, skipping anyone already named.

    Priority is by how weekly the story is: a first Team of the Week selection is
    a this-weekend event, a form surge is a this-month one, and a newcomer's
    season total is the least time-bound of the three.
    """
    # 1. A first-ever Team of the Week selection.
    if changes and changes.get("first_time"):
        fresh = [n for n in changes["first_time"] if n not in named]
        if fresh:
            row = next((r for r in totw if r["player_name"] == fresh[0]), None)
            named.add(fresh[0])
            extra = f" for {c._esc(row['team_abbreviation'])}" if row else ""
            if len(fresh) > 1:
                return (f"<b>{c._esc(fresh[0])}</b>{extra} makes her first Team of the "
                        f"Week of the season, one of {len(fresh)} debutants this round.")
            return (f"<b>{c._esc(fresh[0])}</b>{extra} makes her first Team of the "
                    f"Week of the season.")

    # 2. The biggest improvement on the previous 30 days.
    if not form.empty and "form_delta" in form.columns:
        movers = form.dropna(subset=["form_delta"])
        movers = movers[movers["form_delta"] >= MIN_FORM_DELTA].sort_values(
            "form_delta", ascending=False)
        pick = next((r for _, r in movers.iterrows() if r["player_name"] not in named), None)
        if pick is not None:
            named.add(pick["player_name"])
            return (f"<b>{c._esc(pick['player_name'])}</b> has improved most on the "
                    f"previous {FORM_WINDOW_DAYS} days, up "
                    f"{pick['form_delta']:+.2f} g+/90.")

    # 3. The leading first-year player.
    if not newcomers.empty:
        pick = next((r for _, r in newcomers.iterrows() if r["player_name"] not in named), None)
        if pick is not None:
            named.add(pick["player_name"])
            return (f"<b>{c._esc(pick['player_name'])}</b> leads all first-year players "
                    f"on season value at {pick['value_score']:+.2f}.")
    return None


def _what_changed(changes: dict | None) -> None:
    """One line for a returning visitor: what is different from last time.

    Turnover alone would be dull — measured across the season it never leaves
    9-11 of 11, because a single match is volatile by nature. First-time
    selections range from 1 to 10, so that is the number that carries the news,
    and holding a slot is rare enough to be worth naming when it happens.
    """
    if not changes or not changes.get("previous_matchday"):
        return
    held, first, compared = changes["held"], changes["first_time"], changes["compared"]

    if held:
        who = ", ".join(held[:2]) + (f" and {len(held) - 2} others" if len(held) > 2 else "")
        bit = f"<b>{c._esc(who)}</b> held {'their' if len(held) > 1 else 'her'} place"
    else:
        bit = "<b>nobody held their place</b>"

    line = (f"Since matchday {changes['previous_matchday']}: {bit} "
            f"({changes['changed']} of {compared} slots changed hands)")
    if first:
        line += (f", and {len(first)} player{'s' if len(first) != 1 else ''} "
                 f"made {'their' if len(first) != 1 else 'her'} first XI of the season")
    st.markdown(f'<p class="rl-changed">{line}.</p>', unsafe_allow_html=True)


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

    key = f"totw_{matchday}_v2"
    if key not in st.session_state:
        with st.spinner("Building Team of the Week…"):
            st.session_state[key] = render_leaderboard_card(
                rows, title="Team of the Week", season=IN_SEASON_YEAR,
                subtitle="Best performance per position · raw goals added",
                coverage=line, header_color=TOTW_HEADER,
            )
    png = st.session_state[key]

    img_col, meta_col = st.columns(CARD_COLS)
    with img_col:
        st.image(png, width=CARD_PX)
    with meta_col:
        c.scale_badge("THIS WEEKEND", f"Matchday {matchday}", TOTW_HEADER)
        if st.download_button("⬇ Download PNG", data=png,
                              file_name=f"team_of_the_week_{IN_SEASON_YEAR}_md{matchday}.png",
                              mime="image/png", key="dl_totw"):
            track.card_download("team_of_the_week", season=IN_SEASON_YEAR,
                                matchday=int(matchday))
        st.caption(f"**{line}**")
        _share("team_of_the_week",
               f"NWSL Team of the Week — matchday {matchday}", "share_totw")
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

    n_fix = int(form["form_fixtures"].iloc[0]) if "form_fixtures" in form.columns else 0
    cov_line = f"LAST {FORM_WINDOW_DAYS} DAYS  ·  {n_fix} FIXTURES" if n_fix else \
               f"LAST {FORM_WINDOW_DAYS} DAYS"

    key = f"form_card_{form_date}_v3"
    if key not in st.session_state:
        with st.spinner("Building form XI…"):
            rows = select_risers_xi(form_as_card_rows(form, "form_weighted_p90"))
            st.session_state[key] = (
                render_leaderboard_card(
                    rows, title="In form", season=IN_SEASON_YEAR,
                    subtitle="Position-weighted g+/90 · outfield only",
                    coverage=cov_line, header_color=INFORM_HEADER,
                ),
                rows,
            )
    png, rows = st.session_state[key]

    img_col, meta_col = st.columns(CARD_COLS)
    with img_col:
        st.image(png, width=CARD_PX)
    with meta_col:
        c.scale_badge("THIS MONTH", f"Rolling {FORM_WINDOW_DAYS} days", INFORM_HEADER)
        if st.download_button("⬇ Download PNG", data=png,
                              file_name=f"in_form_{IN_SEASON_YEAR}_{form_date}.png",
                              mime="image/png", key="dl_in_form"):
            track.card_download("in_form", season=IN_SEASON_YEAR, window=form_date)
        st.caption(
            f"Minimum {MIN_MINUTES_FORM} minutes in the window, shrunk toward the "
            f"position average (K = {K_FORM})."
        )
        _share("in_form", f"Who's in form in the NWSL — last {FORM_WINDOW_DAYS} days",
               "share_in_form")
        _form_table(rows)


def _share(card_type: str, text: str, key: str) -> None:
    """Share row beneath a card.

    NOTE ON LINK PREVIEWS: the rich-preview channels (Facebook, LinkedIn, X,
    Reddit) will render a bare link until the app has a custom domain with OG
    tags. Streamlit Community Cloud serves no meta tags and the app is entirely
    client-rendered, so a crawler sees nothing. The buttons are correct now; the
    previews improve when that work lands, with no change needed here.
    """
    track.share_row(card_type, SHARE_URL, text, key)


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
