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
    list_snapshots, load_snapshot, compute_movement, select_risers_xi, select_fallers_xi,
)
from src.analysis.newcomers import select_newcomer_watch_xi
from src.analysis.season import IN_SEASON_YEAR
from src.share.card import render_leaderboard_card
from src.ui import components as c
from src.ui import loaders, state, theme


MOVE_WINDOW = 5          # snapshots back for the "this month" comparison
MOVE_MIN_MINUTES = 270   # ~3 games; matches the Risers & Fallers floor


def render() -> None:
    snaps = list_snapshots(IN_SEASON_YEAR)
    latest = snaps[-1] if snaps else None

    theme.section(
        "This week",
        eyebrow_text=(f"NWSL {IN_SEASON_YEAR} · WEEK OF {latest}" if latest
                      else "NWSL ROSTERLAB"),
    )

    movement = _movement(snaps)
    newcomers = _newcomer_table(latest)

    _headline(movement, newcomers, latest)
    _headline_figures(movement, newcomers, snaps)

    if len(snaps) >= 2:
        theme.rule()
        _risers_and_fallers(snaps)
    if snaps:
        theme.rule()
        _newcomers(snaps[-1])

    theme.rule()
    _undervalued_xi()


# --- Shared data for the headline + figures ---------------------------------

@st.cache_data(show_spinner=False, ttl=3600)
def _movement_cached(snap_new: str, snap_old: str, min_minutes: int) -> pd.DataFrame:
    return compute_movement(load_snapshot(snap_new), load_snapshot(snap_old),
                            K=300, min_minutes_new=min_minutes)


def _movement(snaps: list[str]) -> pd.DataFrame:
    """Movement over roughly the last month, or an empty frame if impossible."""
    if len(snaps) < 2:
        return pd.DataFrame()
    return _movement_cached(snaps[-1], snaps[max(0, len(snaps) - MOVE_WINDOW)],
                            MOVE_MIN_MINUTES)


def _newcomer_table(latest: str | None) -> pd.DataFrame:
    if not latest:
        return pd.DataFrame()
    table = loaders.load_in_season_table(MOVE_MIN_MINUTES, latest)
    hist = loaders.cached_historical_ids()
    out = table[~table["player_id"].astype(str).isin(hist)]
    return out.sort_values("value_score", ascending=False)


# --- Headline ---------------------------------------------------------------

def _headline(movement: pd.DataFrame, newcomers: pd.DataFrame,
              latest: str | None) -> None:
    """One plain-English sentence about the football.

    Deliberately says nothing about snapshots or pipelines — this is the most
    read line on the site. Degrades clause by clause rather than all-or-nothing.
    """
    parts: list[str] = []
    if not movement.empty:
        top = movement.iloc[0]
        parts.append(
            f"<b>{top['player_name']}</b> is the league's biggest riser this month, "
            f"up {abs(top['delta_value_score']):.2f}."
        )
        bottom = movement.iloc[-1]
        if float(bottom["delta_value_score"]) < 0:
            parts.append(
                f"<b>{bottom['player_name']}</b> has fallen furthest, "
                f"down {abs(bottom['delta_value_score']):.2f}."
            )
    if not newcomers.empty:
        first = newcomers.iloc[0]
        parts.append(
            f"<b>{first['player_name']}</b> leads all first-year players "
            f"at {first['value_score']:+.2f}."
        )

    games = loaders.snapshot_games_est(latest) if latest else None
    if games:
        parts.append(f"About {games} games played so far.")

    if not parts:
        parts.append("The season is under way — check back once a few more games are in.")

    st.markdown(f'<p class="rl-lede">{" ".join(parts)}</p>', unsafe_allow_html=True)


def _headline_figures(movement: pd.DataFrame, newcomers: pd.DataFrame,
                      snaps: list[str]) -> None:
    """Three figures, each computed and failing independently."""
    col_r, col_f, col_n = st.columns(3)

    with col_r:
        if movement.empty:
            c.stat_card_empty("Biggest riser", _why_no_movement(snaps))
        else:
            r = movement.iloc[0]
            c.stat_card("Biggest riser", str(r["player_name"]),
                        f"{r['delta_value_score']:+.2f}  ·  {r['team_name']}",
                        accent=theme.POSITIVE)

    with col_f:
        fallers = movement[movement["delta_value_score"] < 0] if not movement.empty else movement
        if fallers.empty:
            c.stat_card_empty("Biggest faller", _why_no_movement(snaps)
                              if movement.empty else "No player has fallen this month.")
        else:
            f = fallers.iloc[-1]
            c.stat_card("Biggest faller", str(f["player_name"]),
                        f"{f['delta_value_score']:+.2f}  ·  {f['team_name']}",
                        accent=theme.NEGATIVE)

    with col_n:
        if newcomers.empty:
            c.stat_card_empty("Top newcomer", "No first-year player has enough minutes yet.")
        else:
            n = newcomers.iloc[0]
            c.stat_card("Top newcomer", str(n["player_name"]),
                        f"{n['value_score']:+.2f}  ·  {n['team_name']}")

    # The floor behind all three figures, stated where the figures are rather
    # than only in the Risers & Fallers section further down.
    st.caption(
        f"Riser and faller compare the latest weekly snapshot with roughly a month "
        f"earlier, counting only players with at least **{MOVE_MIN_MINUTES} minutes** "
        f"(about three matches) in the latest snapshot. Value scores are shrunk "
        f"toward the position mean (K = 300) and are not comparable across seasons."
    )


def _why_no_movement(snaps: list[str]) -> str:
    if not snaps:
        return "No in-season data yet."
    if len(snaps) < 2:
        return "Needs a second week of data to compare."
    return "No player clears the minutes threshold yet."


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
