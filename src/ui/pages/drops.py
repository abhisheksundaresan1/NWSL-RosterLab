"""Drops — the gallery of shareable cards.

These three artifacts used to live on This week, which made a page called "This
week" mostly not weekly: the Undervalued XI is a completed-season retrospective,
Newcomers is a season-to-date board, and Risers & Fallers compares two 30-day
windows. None of them changes week to week, and together they buried the one
block that does.

Grouping them here also puts the download buttons in one place, which is what a
creator actually wants when they come looking for something to post.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis.drops import (
    select_undervalued_xi, best_xi_excluded_names, undervalued_min_minutes,
)
from src.analysis.form import FORM_WINDOW_DAYS, MIN_FORM_DELTA, MIN_MINUTES_FORM
from src.analysis.newcomers import MIN_VALUE_SCORE, select_newcomer_watch_xi
from src.analysis.season import IN_SEASON_YEAR
from src.analytics import track
from src.share.card import render_leaderboard_card, render_ranked_list_card
from src.ui import components as c
from src.ui import loaders, state, theme


def render() -> None:
    theme.section(
        "Drops",
        subtitle="Shareable cards. Each one downloads as a 1080×1350 PNG.",
        eyebrow_text="NWSL ROSTERLAB",
    )

    _risers_and_fallers()
    theme.rule()
    _newcomers()
    theme.rule()
    _undervalued_xi()


# --- Risers & Fallers -------------------------------------------------------

def _risers_and_fallers() -> None:
    theme.section(
        f"Risers & Fallers · last {FORM_WINDOW_DAYS} days",
        subtitle=(f"Change in weighted goals added per 90 against the previous "
                  f"{FORM_WINDOW_DAYS} days, for players clearing {MIN_MINUTES_FORM} "
                  f"minutes in **both** windows. Only movements of **{MIN_FORM_DELTA:+.2f} "
                  f"g+/90 or more** are listed — below that the change is within the "
                  f"noise of the metric."),
    )

    form_date = loaders.latest_form_date()
    form = loaders.load_form(form_date) if form_date else pd.DataFrame()
    if form.empty:
        c.empty_state(f"No form data yet for {IN_SEASON_YEAR}.")
        return

    movers = form.dropna(subset=["form_delta"])
    if movers.empty:
        c.empty_state(
            f"No player has {MIN_MINUTES_FORM}+ minutes in both this window and the "
            f"previous {FORM_WINDOW_DAYS} days.",
            "This happens across an international break and resolves once two full "
            "windows of fixtures line up.",
        )
        return

    risers = movers[movers["form_delta"] >= MIN_FORM_DELTA].nlargest(10, "form_delta")
    fallers = movers[movers["form_delta"] <= -MIN_FORM_DELTA].nsmallest(10, "form_delta")

    key = f"drops_rf_list_{form_date}_v2"
    if key not in st.session_state:
        with st.spinner("Building movement lists…"):
            st.session_state[key] = (
                _list_card("Risers", risers, len(movers), form_date),
                _list_card("Fallers", fallers, len(movers), form_date),
            )
    rise_png, fall_png = st.session_state[key]

    left, right = st.columns(2)
    with left:
        st.caption(f"**Risers** — {len(risers)} qualified")
        st.image(rise_png, width=440)
        if st.download_button("⬇ Risers (PNG)", data=rise_png,
                              file_name=f"risers_{IN_SEASON_YEAR}_{form_date}.png",
                              mime="image/png", key="dl_risers"):
            track.card_download("risers", season=IN_SEASON_YEAR, window=form_date)
    with right:
        st.caption(f"**Fallers** — {len(fallers)} qualified")
        st.image(fall_png, width=440)
        if st.download_button("⬇ Fallers (PNG)", data=fall_png,
                              file_name=f"fallers_{IN_SEASON_YEAR}_{form_date}.png",
                              mime="image/png", key="dl_fallers"):
            track.card_download("fallers", season=IN_SEASON_YEAR, window=form_date)

    _movement_table(risers, fallers)


def _list_card(title: str, df: pd.DataFrame, n_movers: int, form_date: str) -> bytes:
    """A ranked list, not a formation card.

    At this threshold only a handful of players qualify league-wide, and a
    formation graphic showing three markers across eleven positions reads as
    broken rather than deliberate. A list has no fixed number of places.
    """
    rows = [{
        "player_name": r.player_name,
        "team_abbreviation": r.team_abbreviation,
        "position": r.position,
        "value_score": float(r.form_delta),
        "sample_label": r.form_sample_label,
    } for r in df.itertuples()]
    note = (f"{len(rows)} of {n_movers} eligible players moved by "
            f"{MIN_FORM_DELTA:.2f} g+/90 or more") if rows else None
    return render_ranked_list_card(
        rows, title=title, season=IN_SEASON_YEAR,
        subtitle=f"Change in weighted g+/90 · vs the previous {FORM_WINDOW_DAYS} days",
        value_label="Δ g+/90", note=note,
    )


def _movement_table(risers: pd.DataFrame, fallers: pd.DataFrame) -> None:
    both = pd.concat([risers, fallers])
    if both.empty:
        return
    with st.expander("Full movement list", expanded=False):
        out = both[["player_name", "position", "team_abbreviation",
                    "form_delta", "form_weighted_p90", "form_sample_label"]].copy()
        out.columns = ["Player", "Pos", "Team", "Δ g+/90", "g+/90 now", "Sample"]
        st.dataframe(c.dash_blanks(out), hide_index=True, width="stretch")


# --- Newcomers --------------------------------------------------------------

def _newcomers() -> None:
    snap = loaders.latest_snapshot()
    if snap is None:
        return
    min_minutes = state.get_min_minutes(IN_SEASON_YEAR)
    theme.section(
        "Newcomers · first year in NWSL",
        subtitle=("Highest-value outfield players in their first NWSL season — college "
                  "signings, international transfers and returnees alike. Outfield only. "
                  f"A slot is filled only if the player is at least **{MIN_VALUE_SCORE:.2f} "
                  "standard deviations above her position's average**; below that the "
                  "number is indistinguishable from average, so the slot is left blank."),
    )

    key = f"drops_newcomers_{snap}_{min_minutes}_v2"
    if key not in st.session_state:
        with st.spinner("Finding newcomers…"):
            table = loaders.load_in_season_table(min_minutes, snap)
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
    img_col, meta_col = st.columns([2, 3])
    with img_col:
        st.image(png, width=440)
    with meta_col:
        if st.download_button("⬇ Download PNG", data=png,
                              file_name=f"newcomers_{IN_SEASON_YEAR}_{snap}.png",
                              mime="image/png", key="dl_newcomers"):
            track.card_download("newcomers", season=IN_SEASON_YEAR)
        _stats_table(rows)


# --- Undervalued XI ---------------------------------------------------------

def _undervalued_xi() -> None:
    """Always the most recent COMPLETED season — it needs a final Best XI to
    exclude, so it cannot be computed mid-season."""
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
    img_col, meta_col = st.columns([2, 3])
    with img_col:
        st.image(png, width=440)
    with meta_col:
        if st.download_button("⬇ Download PNG", data=png,
                              file_name=f"undervalued_xi_{season}.png",
                              mime="image/png", key="dl_undervalued_xi"):
            track.card_download("undervalued_xi", season=season)
        _stats_table(rows)
        with st.expander(f"Who was excluded ({season} Best XI)", expanded=False):
            first, second = best_xi_excluded_names(season)
            st.caption("**First XI** — " + ", ".join(first))
            st.caption("**Second XI** — " + ", ".join(second))


# --- Shared ------------------------------------------------------------------

def _stats_table(rows: list[dict], value_label: str = "Value") -> None:
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
