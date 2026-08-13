"""This week — the landing page.

Opens with what changed this week: a plain-English headline, three headline
figures (biggest riser, biggest faller, top newcomer), then the live drops.
The Undervalued XI sits below as an evergreen section — it is a retrospective
built from a completed season and does not change week to week, so it should
not be the lede on a page called "This week".

Two things this page is careful about:

1. The Undervalued XI is not gated behind the season picker. The original tab
   branched — Undervalued XI *or* the in-season drops, never both — so with 2026
   as the default the page would have opened with "requires a completed season".
   It always renders from the most recent completed season, and says so.

2. Every headline figure is computed independently and has its own empty state.
   compute_movement() returns an empty frame when only one snapshot exists or
   when the minutes floor excludes everyone, and .head(1).iloc[0] on an empty
   frame raises IndexError. The newcomer list can also be legitimately empty
   now that a value floor applies. One missing figure must not take down the
   landing page.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis.drops import (
    select_undervalued_xi, best_xi_excluded_names, undervalued_min_minutes,
)
from src.analysis.movement import (
    list_snapshots, load_snapshot, select_risers_xi, select_fallers_xi,
)
from src.analysis.form import (
    FORM_WINDOW_DAYS, K_FORM, MIN_MINUTES_FORM, SMALL_COHORT, MIN_COHORT_FOR_RANK,
    form_as_card_rows, dominant_action,
)
from src.analysis.newcomers import select_newcomer_watch_xi
from src.analysis.season import IN_SEASON_YEAR
from src.share.card import render_leaderboard_card
from src.analytics import track
from src.ui import components as c
from src.ui import loaders, state, theme

from datetime import date as _date

MOVE_MIN_MINUTES = 270   # newcomer-table floor (~3 games)

# One sentence, reused wherever a form number appears, so the parameters travel
# with the metric instead of living only in the Method page or in code.
FORM_DISCLOSURE = (
    f"**Form** covers the last **{FORM_WINDOW_DAYS} days only**, for players with at "
    f"least **{MIN_MINUTES_FORM} minutes** in that window (about two full matches), "
    f"shrunk toward the position average with **K = {K_FORM} minutes**. It is a "
    f"separate metric from the season value score — the two are never combined."
)


def _human_date(iso: str | None) -> str:
    """'2026-08-10' -> 'AUGUST 10'. Avoids %-d/%#d, which differ across platforms."""
    if not iso:
        return ""
    d = _date.fromisoformat(iso)
    return f"{d:%B} {d.day}".upper()


def render() -> None:
    snaps = list_snapshots(IN_SEASON_YEAR)
    latest = snaps[-1] if snaps else None

    form_date = loaders.latest_form_date()
    form = loaders.load_form(form_date) if form_date else pd.DataFrame()

    # The dated eyebrow leads the page — a returning visitor's first question is
    # which week they are looking at.
    st.markdown(
        f'<p class="rl-eyebrow-hero">NWSL {IN_SEASON_YEAR} · WEEK OF '
        f'{_human_date(form_date or latest)}</p>' if (form_date or latest) else
        '<p class="rl-eyebrow-hero">NWSL ROSTERLAB</p>',
        unsafe_allow_html=True,
    )

    newcomers = _newcomer_table(latest)

    _headline(form, newcomers)
    _headline_figures(form, newcomers, snaps)

    if not form.empty:
        theme.rule()
        _form_cards(form, form_date)
    if snaps:
        theme.rule()
        _newcomers(snaps[-1])

    theme.rule()
    _undervalued_xi()


def _newcomer_table(latest: str | None) -> pd.DataFrame:
    if not latest:
        return pd.DataFrame()
    table = loaders.load_in_season_table(MOVE_MIN_MINUTES, latest)
    hist = loaders.cached_historical_ids()
    out = table[~table["player_id"].astype(str).isin(hist)]
    return out.sort_values("value_score", ascending=False)


# --- Headline ---------------------------------------------------------------

_ACTION_PHRASE = {
    "shooting": "her shooting", "dribbling": "her dribbling", "passing": "her passing",
    "receiving": "her movement to receive", "interrupting": "defensive actions",
    "fouling": "the fouls she draws",
}


def _headline(form: pd.DataFrame, newcomers: pd.DataFrame) -> None:
    """One plain-English sentence about the football.

    HARD RULE: every clause is computed from columns we hold. This page has no
    access to fixtures, opponents, scorelines or goals, and no LLM is involved,
    so the sentence cannot reference them. The only texture it adds beyond names
    and numbers is WHICH ACTION TYPE drove a player's form — which is derived
    arithmetic on ga_*_p90, not narration.

    Units are stated ("0.31 g+/90") because form is a rate, not the z-scored
    value points used elsewhere. Degrades clause by clause.
    """
    parts: list[str] = []

    if not form.empty:
        top = form.nlargest(1, "form_weighted_p90").iloc[0]
        action = dominant_action(top)
        tail = f" — driven mostly by {_ACTION_PHRASE[action]}" if action else ""
        parts.append(
            f"<b>{top['player_name']}</b> has been the league's best performer over the "
            f"last {FORM_WINDOW_DAYS} days at {top['form_weighted_p90']:.2f} g+/90"
            f"{tail}, across {int(top['form_matches'])} matches."
        )

        movers = form.dropna(subset=["form_delta"])
        risers = movers[movers["form_delta"] > 0] if not movers.empty else movers
        if not risers.empty:
            r = risers.nlargest(1, "form_delta").iloc[0]
            if r["player_id"] == top["player_id"]:
                # Same player tops both. Say so once rather than opening a second
                # sentence with a name the reader just read.
                parts.append(
                    f"She has also improved most on the previous {FORM_WINDOW_DAYS} "
                    f"days, up {abs(r['form_delta']):.2f} g+/90."
                )
            else:
                parts.append(
                    f"<b>{r['player_name']}</b> has improved most on the previous "
                    f"{FORM_WINDOW_DAYS} days, up {abs(r['form_delta']):.2f} g+/90."
                )

    if not newcomers.empty:
        first = newcomers.iloc[0]
        parts.append(
            f"<b>{first['player_name']}</b> leads all first-year players on season "
            f"value at {first['value_score']:+.2f}."
        )

    if not parts:
        parts.append("The season is under way — check back once a few more games are in.")

    st.markdown(f'<p class="rl-lede">{" ".join(parts)}</p>', unsafe_allow_html=True)


def _headline_figures(form: pd.DataFrame, newcomers: pd.DataFrame,
                      snaps: list[str]) -> None:
    """Three figures, each computed and failing independently."""
    col_b, col_r, col_n = st.columns(3)

    with col_b:
        if form.empty:
            c.stat_card_empty("Best form", _why_no_form(snaps))
        else:
            b = form.nlargest(1, "form_weighted_p90").iloc[0]
            c.stat_card("Best form", str(b["player_name"]),
                        f"{b['form_weighted_p90']:.2f} g+/90  ·  {b['form_sample_label']}",
                        accent=theme.POSITIVE)

    with col_r:
        movers = form.dropna(subset=["form_delta"]) if not form.empty else form
        risers = movers[movers["form_delta"] > 0] if not movers.empty else movers
        if risers.empty:
            c.stat_card_empty(
                "Most improved",
                _why_no_form(snaps) if form.empty else
                f"Nobody has {MIN_MINUTES_FORM}+ minutes in both this window and the "
                f"previous one — the calendar between them was mostly a break."
            )
        else:
            r = risers.nlargest(1, "form_delta").iloc[0]
            c.stat_card("Most improved", str(r["player_name"]),
                        f"{r['form_delta']:+.2f} g+/90  ·  {r['team_name']}",
                        accent=theme.POSITIVE)

    with col_n:
        if newcomers.empty:
            c.stat_card_empty("Top newcomer", "No first-year player has enough minutes yet.")
        else:
            n = newcomers.iloc[0]
            c.stat_card("Top newcomer", str(n["player_name"]),
                        f"{n['value_score']:+.2f} season value  ·  {n['team_name']}")

    _form_caption(form)


def _form_caption(form: pd.DataFrame) -> None:
    """The parameters, the sample, and the cohort caveat — with the numbers."""
    st.caption(FORM_DISCLOSURE)
    if form.empty:
        return

    n_cur = int(form["form_fixtures"].iloc[0])
    n_prev = int(form["prev_fixtures"].iloc[0])
    st.caption(
        f"This window covers **{n_cur} fixtures**; the previous {FORM_WINDOW_DAYS} days "
        f"covered **{n_prev}**. Change is shown only for players clearing the minutes "
        f"floor in *both*, so an international break leaves most players without one."
    )

    thin = (
        form[["position", "form_cohort_n"]].drop_duplicates()
        .query(f"form_cohort_n <= {SMALL_COHORT}").sort_values("form_cohort_n")
    )
    if not thin.empty:
        bits = ", ".join(f"{r.position} ({int(r.form_cohort_n)})" for r in thin.itertuples())
        suppressed = thin[thin["form_cohort_n"] < MIN_COHORT_FOR_RANK]
        note = (
            f"Thin cohorts this window — {bits}. A form score is standardised against "
            f"the players at that position who clear the floor, so with a small group "
            f"treat the ranking as indicative."
        )
        if not suppressed.empty:
            note += (
                f" Positions with fewer than {MIN_COHORT_FOR_RANK} qualifiers show a rate "
                f"and sample size only — no rank or score is published for them."
            )
        st.caption(note)


def _why_no_form(snaps: list[str]) -> str:
    if not snaps:
        return "No in-season data yet."
    return (f"No player has {MIN_MINUTES_FORM}+ minutes in the last "
            f"{FORM_WINDOW_DAYS} days yet.")


# --- Undervalued XI ---------------------------------------------------------

def _undervalued_xi() -> None:
    """Always drawn from the most recent completed season, whatever season is
    selected — this is a retrospective artifact and can't be computed mid-season
    (it needs a final Best XI to exclude)."""
    season = state.most_recent_completed_season()
    min_minutes = 500
    uv_min = undervalued_min_minutes(season)

    theme.section(
        "Undervalued XI",
        subtitle=(f"Highest-value outfield players left out of the {season} NWSL Best XI "
                  f"(First or Second). Minimum {uv_min:,} minutes, so injury-shortened "
                  "seasons don't read as snubs. A slot is filled only if the best "
                  "available player ranks in the **top 3 — or top 30% — of her "
                  "position**; otherwise it is left blank. Outfield only."),
        eyebrow_text=f"FROM THE COMPLETED {season} SEASON",
    )

    png_key = f"drops_png_{season}_{min_minutes}_v5"
    rows_key = f"drops_rows_{season}_{min_minutes}_v5"
    if png_key not in st.session_state:
        try:
            with st.spinner("Generating Undervalued XI…"):
                table = loaders.load_value_table(min_minutes, season)
                rows = select_undervalued_xi(table, season, min_minutes)
                st.session_state[png_key] = render_leaderboard_card(
                    rows, title="Undervalued XI", season=season,
                    subtitle="Top-value outfield players outside the Best XI  ·  Outfield only",
                )
                st.session_state[rows_key] = rows
        except ValueError as e:
            st.warning(str(e))
            return
        except Exception as e:
            st.error(f"Could not render Undervalued XI: {e}")
            return

    png, rows = st.session_state[png_key], st.session_state.get(rows_key, [])
    img_col, meta_col = st.columns([3, 2])
    with img_col:
        st.image(png, width="stretch")
    with meta_col:
        if st.download_button(
            "⬇ Download PNG", data=png, file_name=f"undervalued_xi_{season}.png",
            mime="image/png", key="dl_undervalued_xi",
        ):
            track.card_download("undervalued_xi", season=season)
        _stats_table(rows)
        with st.expander(f"Who was excluded ({season} Best XI)", expanded=False):
            first, second = best_xi_excluded_names(season)
            st.caption("**First XI** — " + ", ".join(first))
            st.caption("**Second XI** — " + ", ".join(second))


# --- Risers & Fallers -------------------------------------------------------

def _form_cards(form: pd.DataFrame, form_date: str | None) -> None:
    """In-form XI, plus risers/fallers when a comparable previous window exists.

    The level ("who is playing best right now") leads, because it is always
    computable. The change is secondary and genuinely unavailable across an
    international break — rather than fabricate one, the section says so.
    """
    theme.section(
        f"In form · last {FORM_WINDOW_DAYS} days",
        subtitle=(f"Position-weighted goals added per 90 over the last {FORM_WINDOW_DAYS} "
                  f"days only — not the season score. Minimum {MIN_MINUTES_FORM} minutes "
                  f"in the window; shrunk toward the position average (K = {K_FORM})."),
        eyebrow_text=f"NWSL {IN_SEASON_YEAR} · IN PROGRESS",
    )

    key = f"form_cards_{form_date}_v1"
    if key not in st.session_state:
        with st.spinner("Building form XI…"):
            best_rows = select_risers_xi(form_as_card_rows(form, "form_weighted_p90"))
            st.session_state[key] = (
                render_leaderboard_card(
                    best_rows, title="In form", season=IN_SEASON_YEAR,
                    subtitle=f"Weighted g+/90 · last {FORM_WINDOW_DAYS} days · outfield only",
                ),
                best_rows,
            )

    png, rows = st.session_state[key]
    img_col, meta_col = st.columns([3, 2])
    with img_col:
        st.image(png, width="stretch")
    with meta_col:
        if st.download_button("⬇ Download PNG", data=png,
                              file_name=f"in_form_{IN_SEASON_YEAR}_{form_date}.png",
                              mime="image/png", key="dl_in_form"):
            track.card_download("in_form", season=IN_SEASON_YEAR, window=form_date)
        _stats_table(rows, value_label="g+/90")

    movers = form.dropna(subset=["form_delta"])
    if movers.empty:
        st.caption(
            f"**No risers and fallers this window.** Form change needs "
            f"{MIN_MINUTES_FORM}+ minutes in both this window and the previous "
            f"{FORM_WINDOW_DAYS} days, and no player clears that on both sides of the "
            f"break in the calendar. It returns once two full windows of fixtures line up."
        )
        return

    theme.rule()
    theme.section(
        "Risers & Fallers",
        subtitle=(f"Change in weighted g+/90 against the previous {FORM_WINDOW_DAYS} days, "
                  f"for the {len(movers)} players who clear {MIN_MINUTES_FORM} minutes in "
                  f"both windows. This is a change in rate — the season value score "
                  f"is a separate number and is not involved."),
    )
    rkey = f"form_rf_{form_date}_v1"
    if rkey not in st.session_state:
        with st.spinner("Computing form change…"):
            adapted = form_as_card_rows(movers, "form_delta")
            rise, fall = select_risers_xi(adapted), select_fallers_xi(adapted)
            win = f"vs previous {FORM_WINDOW_DAYS} days"
            st.session_state[rkey] = (
                render_leaderboard_card(rise, title="Risers", season=IN_SEASON_YEAR,
                                        subtitle=f"Biggest g+/90 gains  ·  {win}"),
                render_leaderboard_card(fall, title="Fallers", season=IN_SEASON_YEAR,
                                        subtitle=f"Biggest g+/90 drops  ·  {win}"),
                rise, fall,
            )

    rise_png, fall_png, rise_rows, fall_rows = st.session_state[rkey]
    left, right = st.columns(2)
    with left:
        st.caption("**Risers**")
        st.image(rise_png, width="stretch")
        if st.download_button("⬇ Risers (PNG)", data=rise_png,
                              file_name=f"risers_{IN_SEASON_YEAR}_{form_date}.png",
                              mime="image/png", key="dl_risers"):
            track.card_download("risers", season=IN_SEASON_YEAR, window=form_date)
        _stats_table(rise_rows, value_label="Δ g+/90")
    with right:
        st.caption("**Fallers**")
        st.image(fall_png, width="stretch")
        if st.download_button("⬇ Fallers (PNG)", data=fall_png,
                              file_name=f"fallers_{IN_SEASON_YEAR}_{form_date}.png",
                              mime="image/png", key="dl_fallers"):
            track.card_download("fallers", season=IN_SEASON_YEAR, window=form_date)
        _stats_table(fall_rows, value_label="Δ g+/90")


# --- Newcomer Watch ---------------------------------------------------------

def _newcomers(latest_snap: str) -> None:
    min_minutes = state.get_min_minutes(IN_SEASON_YEAR)
    theme.section(
        "Newcomers · first year in NWSL",
        subtitle=("Highest-value outfield players in their first NWSL season — college "
                  "signings, international transfers and returnees alike. Outfield only. "
                  "**Positions are left blank where no first-year player is above the "
                  "positional average** — an empty slot is a deliberate omission, not a "
                  "missing graphic."),
        eyebrow_text=f"NWSL {IN_SEASON_YEAR} · IN PROGRESS",
    )

    key = f"drops_newcomers_{latest_snap}_{min_minutes}_v1"
    if key not in st.session_state:
        with st.spinner("Finding newcomers…"):
            table = loaders.load_in_season_table(min_minutes, latest_snap)
            hist = loaders.cached_historical_ids()
            newcomers = table[~table["player_id"].astype(str).isin(hist)].copy()
            rows = select_newcomer_watch_xi(newcomers)
            st.session_state[key] = (
                render_leaderboard_card(
                    rows, title="Newcomers", season=IN_SEASON_YEAR,
                    subtitle="First-year NWSL players by value score  ·  Outfield only",
                ),
                rows,
            )

    png, rows = st.session_state[key]
    img_col, meta_col = st.columns([3, 2])
    with img_col:
        st.image(png, width="stretch")
    with meta_col:
        if st.download_button("⬇ Download PNG", data=png,
                              file_name=f"newcomers_{IN_SEASON_YEAR}_{latest_snap}.png",
                              mime="image/png", key="dl_newcomers"):
            track.card_download("newcomers", season=IN_SEASON_YEAR)
        _stats_table(rows)


# --- Shared ------------------------------------------------------------------

def _stats_table(rows: list[dict], value_label: str = "Value") -> None:
    """Full stats for the filled slots of a leaderboard card."""
    filled = [r for r in rows if r["player_name"] != "—"]
    if not filled:
        return
    show_college = any("college_value_percentile" in r for r in filled)

    def _row(r: dict) -> dict:
        d = {
            "Pos": r["position"],
            "Player": r["player_name"],
            "Team": r["team_name"],
            value_label: f"{r['value_score']:+.2f}",
            "Minutes": f"{r['minutes_played']:,}",
        }
        if show_college:
            pct = r.get("college_value_percentile")
            d["College value (%ile)"] = f"{pct:.0f}" if pct is not None else "—"
        return d

    with st.expander("Selected XI — full stats", expanded=False):
        st.dataframe(c.dash_blanks(pd.DataFrame([_row(r) for r in filled])),
                     hide_index=True, width="stretch")
