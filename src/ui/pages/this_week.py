"""This week — the recurring drops.

Phase 1 hosts the former "Drops" tab, restyled but functionally unchanged.
Phase 2 adds a headline summary and riser/faller/newcomer metric cards above it
and takes over as the landing page.

One deliberate change from the old tab: the Undervalued XI is no longer gated
behind the season picker. The old tab branched — Undervalued XI *or* the
in-season drops, never both — so with 2026 as the default the page would have
opened with "requires a completed season". It now always renders from the most
recent completed season and says so, with the in-season drops alongside it.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis.drops import (
    select_undervalued_xi, best_xi_excluded_names, undervalued_min_minutes,
)
from src.analysis.movement import (
    list_snapshots, load_snapshot, compute_movement, select_risers_xi, select_fallers_xi,
)
from src.analysis.newcomers import select_newcomer_watch_xi
from src.analysis.season import IN_SEASON_YEAR
from src.share.card import render_leaderboard_card
from src.ui import components as c
from src.ui import loaders, state, theme


def render() -> None:
    theme.section(
        "This week",
        subtitle="Shareable drops, refreshed from the weekly snapshot.",
        eyebrow_text="NWSL ROSTERLAB",
    )

    _undervalued_xi()

    snaps = list_snapshots(IN_SEASON_YEAR)
    if len(snaps) >= 2:
        theme.rule()
        _risers_and_fallers(snaps)
    if snaps:
        theme.rule()
        _newcomers(snaps[-1])


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
                  "seasons don't read as snubs. Outfield only."),
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
        st.download_button(
            "⬇ Download PNG", data=png, file_name=f"undervalued_xi_{season}.png",
            mime="image/png", key="dl_undervalued_xi",
        )
        _stats_table(rows)
        with st.expander(f"Who was excluded ({season} Best XI)", expanded=False):
            first, second = best_xi_excluded_names(season)
            st.caption("**First XI** — " + ", ".join(first))
            st.caption("**Second XI** — " + ", ".join(second))


# --- Risers & Fallers -------------------------------------------------------

def _risers_and_fallers(snaps: list[str]) -> None:
    old_idx = max(0, len(snaps) - 5)
    snap_new, snap_old = snaps[-1], snaps[old_idx]
    weeks = (len(snaps) - 1) - old_idx
    min_minutes = max(270, state.get_min_minutes(IN_SEASON_YEAR))

    theme.section(
        "Risers & Fallers",
        subtitle=(f"Value-score movement from {snap_old} to {snap_new} (~{weeks} week"
                  f"{'s' if weeks != 1 else ''}). Minimum {min_minutes:,} minutes in the "
                  "latest snapshot; scores stabilized so a small sample can't fake a surge."),
        eyebrow_text=f"NWSL {IN_SEASON_YEAR} · IN PROGRESS",
    )

    key = f"drops_rf_{snap_new}_{snap_old}_{min_minutes}_v1"
    if key not in st.session_state:
        with st.spinner("Computing movement…"):
            mv = compute_movement(
                load_snapshot(snap_new), load_snapshot(snap_old),
                K=300, min_minutes_new=min_minutes,
            )
            rise, fall = select_risers_xi(mv), select_fallers_xi(mv)
            win = f"vs {snap_old}  ·  ~{weeks}w"
            st.session_state[key] = (
                render_leaderboard_card(rise, title="Risers", season=IN_SEASON_YEAR,
                                        subtitle=f"Biggest value-score gains  ·  {win}"),
                render_leaderboard_card(fall, title="Fallers", season=IN_SEASON_YEAR,
                                        subtitle=f"Biggest value-score drops  ·  {win}"),
                rise, fall,
            )

    rise_png, fall_png, rise_rows, fall_rows = st.session_state[key]
    left, right = st.columns(2)
    with left:
        st.caption("**Risers**")
        st.image(rise_png, width="stretch")
        st.download_button("⬇ Risers (PNG)", data=rise_png,
                           file_name=f"risers_{IN_SEASON_YEAR}_{snap_new}.png",
                           mime="image/png", key="dl_risers")
        _stats_table(rise_rows, value_label="Δ value")
    with right:
        st.caption("**Fallers**")
        st.image(fall_png, width="stretch")
        st.download_button("⬇ Fallers (PNG)", data=fall_png,
                           file_name=f"fallers_{IN_SEASON_YEAR}_{snap_new}.png",
                           mime="image/png", key="dl_fallers")
        _stats_table(fall_rows, value_label="Δ value")


# --- Newcomer Watch ---------------------------------------------------------

def _newcomers(latest_snap: str) -> None:
    min_minutes = state.get_min_minutes(IN_SEASON_YEAR)
    theme.section(
        "Newcomers · first year in NWSL",
        subtitle=("Highest-value outfield players in their first NWSL season — college "
                  "signings, international transfers and returnees alike. Outfield only."),
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
        st.download_button("⬇ Download PNG", data=png,
                           file_name=f"newcomers_{IN_SEASON_YEAR}_{latest_snap}.png",
                           mime="image/png", key="dl_newcomers")
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
