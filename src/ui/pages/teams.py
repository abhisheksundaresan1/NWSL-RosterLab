"""Teams — one club at a time: squad, recent movement, positional depth.

Deep-linkable via ?team=ABBR, so /teams?team=POR opens Portland directly. That
is done with st.query_params on this page rather than a second hidden route:
the URL is already shareable, and an extra page would only produce a different
path, not a more linkable one.

Two deliberate decisions, both about not overstating what the data supports:

  * No squad-value league ranking. value_score is a within-position z-score, so
    averaging it across positions over only the players above a minutes floor is
    not a measure of squad quality. At the usual floor, qualifying counts range
    9-13 across clubs, and swapping mean for median moves some teams ten places.
    The header reports descriptive figures instead — count, median, mean.

  * Two different minutes thresholds. The squad list uses the season's
    qualifying floor so it lines up with the rest of the app; DEPTH uses a
    near-zero floor, because the rotation players who reveal that a position is
    thin are exactly the ones a minutes filter removes. Both are stated in the
    UI, and the depth wording is "players with recorded minutes" — the dataset
    only contains players ASA has logged minutes for, so a gap means nobody has
    featured there, not that nobody is rostered there.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis.movement import list_snapshots, load_snapshot, compute_movement
from src.analysis.ranking import rank_by_position
from src.analysis.season import IN_SEASON_YEAR
from src.ui import components as c
from src.ui import loaders, state, theme
from src.ui.pages.players import POSITION_LABELS, POSITION_ORDER, _player_detail

DEPTH_MIN_MINUTES = 1        # effectively "anyone with recorded minutes"
MOVE_WINDOW = 5
MOVE_MIN_MINUTES = 270
THIN_DEPTH = 2               # positions at or below this are flagged


def render() -> None:
    season = state.get_season()
    in_season = season == IN_SEASON_YEAR

    # Depth deliberately loads at a near-zero floor; the squad list uses the
    # season's qualifying floor further down.
    depth_table = _table(season, DEPTH_MIN_MINUTES)
    if depth_table is None or depth_table.empty:
        theme.section("Teams", eyebrow_text=f"NWSL {season}")
        st.warning("No data available for this season yet.")
        return

    teams = (
        depth_table[["team_abbreviation", "team_name"]]
        .dropna().drop_duplicates().sort_values("team_name")
    )
    abbrs = teams["team_abbreviation"].tolist()
    names = dict(zip(teams["team_abbreviation"], teams["team_name"]))

    # --- Selection, seeded from the URL so links are shareable --------------
    requested = st.query_params.get("team")
    default_idx = abbrs.index(requested) if requested in abbrs else 0
    chosen = st.selectbox(
        "Club", abbrs, index=default_idx,
        format_func=lambda a: names.get(a, a), key="team_pick",
    )
    if st.query_params.get("team") != chosen:
        st.query_params["team"] = chosen

    qual_min = state.get_min_minutes(season)
    squad_table = _table(season, qual_min)
    squad = squad_table[squad_table["team_abbreviation"] == chosen] if squad_table is not None \
        else pd.DataFrame()
    depth = depth_table[depth_table["team_abbreviation"] == chosen]

    _header(chosen, names.get(chosen, chosen), squad, qual_min)
    theme.rule()
    _depth(depth)
    theme.rule()
    if in_season:
        _movement(chosen)
        theme.rule()
    else:
        st.caption(
            f"Week-to-week movement needs the live season's weekly snapshots — "
            f"switch to {IN_SEASON_YEAR} to see risers and fallers."
        )
        theme.rule()
    _squad(squad, squad_table, season, qual_min)


def _table(season: str, min_minutes: int) -> pd.DataFrame | None:
    """Season table at a given floor, or None when the in-season snapshot is missing."""
    if season == IN_SEASON_YEAR:
        snap = loaders.latest_snapshot()
        if snap is None:
            return None
        return loaders.load_in_season_table(min_minutes, snap)
    return loaders.load_value_table(min_minutes, season)


# --- Header -----------------------------------------------------------------

def _header(abbr: str, name: str, squad: pd.DataFrame, qual_min: int) -> None:
    accent = theme.team_accent(abbr)
    st.markdown(
        f'<p class="rl-eyebrow">CLUB PROFILE</p>'
        f'<p class="rl-h2" style="color:{accent}">{name}</p>',
        unsafe_allow_html=True,
    )
    if squad.empty:
        c.empty_state("No players from this club clear the minutes threshold.",
                      "Lower the minimum minutes on the Players page.")
        return

    a, b, d = st.columns(3)
    with a:
        c.stat_card("Qualifying players", str(len(squad)), f"≥ {qual_min:,} minutes")
    with b:
        c.stat_card("Median value", f"{squad['value_score'].median():+.2f}",
                    "Typical player at this club")
    with d:
        c.stat_card("Mean value", f"{squad['value_score'].mean():+.2f}",
                    "Pulled by outliers — median is sturdier")
    st.caption(
        "Value scores are z-scored **within position**, so these summarise this club's "
        "qualifying players only. They are not a league table and are not comparable "
        "across positions."
    )


# --- Depth ------------------------------------------------------------------

def _depth(depth: pd.DataFrame) -> None:
    theme.section(
        "Depth by position",
        subtitle=("Every player with recorded minutes, whatever their workload — a minutes "
                  "filter would hide exactly the squad players who show where a club is thin."),
    )
    if depth.empty:
        c.empty_state("No players with recorded minutes for this club.")
        return

    cols = st.columns(len(POSITION_ORDER))
    for col, pos in zip(cols, POSITION_ORDER):
        at_pos = depth[depth["position"] == pos]
        n = len(at_pos)
        best = at_pos.nlargest(1, "value_score")["player_name"].iloc[0] if n else None
        thin = " rl-depth-thin" if n <= THIN_DEPTH else ""
        with col:
            st.markdown(
                f'<div class="rl-depth{thin}">'
                f'<p class="rl-depth-pos">{pos}</p>'
                f'<p class="rl-depth-n">{n}</p>'
                f'<p class="rl-depth-best">{c._esc(best) if best else "—"}</p>'
                f'</div>',
                unsafe_allow_html=True,
            )
    st.caption(
        f"Counts are players with recorded minutes, so a low number means nobody has "
        f"featured there — not that the club has nobody rostered. "
        f"{THIN_DEPTH} or fewer is highlighted."
    )


# --- Movement ---------------------------------------------------------------

def _movement(abbr: str) -> None:
    theme.section("Recent movement",
                  subtitle="Value-score change over roughly the last month.")
    snaps = list_snapshots(IN_SEASON_YEAR)
    if len(snaps) < 2:
        c.empty_state("Needs a second week of data to compare.")
        return

    mv = compute_movement(
        load_snapshot(snaps[-1]), load_snapshot(snaps[max(0, len(snaps) - MOVE_WINDOW)]),
        K=300, min_minutes_new=MOVE_MIN_MINUTES,
    )
    mv = mv[mv["team_abbreviation"] == abbr] if not mv.empty else mv
    if mv.empty:
        c.empty_state("No player at this club clears the minutes threshold yet.")
        return

    up, down = st.columns(2)
    with up:
        st.caption("**Rising**")
        risers = mv[mv["delta_value_score"] > 0].head(3)
        if risers.empty:
            st.caption("Nobody at this club has risen this month.")
        for _, r in risers.iterrows():
            _mover(r, theme.POSITIVE)
    with down:
        st.caption("**Falling**")
        fallers = mv[mv["delta_value_score"] < 0].tail(3).iloc[::-1]
        if fallers.empty:
            st.caption("Nobody at this club has fallen this month.")
        for _, r in fallers.iterrows():
            _mover(r, theme.NEGATIVE)


def _mover(row: pd.Series, colour: str) -> None:
    st.markdown(
        f'<div class="rl-metric" style="margin-bottom:6px">'
        f'<p class="rl-metric-k">{c._esc(row["position"])}  ·  {c._esc(row["sample_label"])}</p>'
        f'<p class="rl-metric-v">{c._esc(row["player_name"])} '
        f'<span style="color:{colour}">{row["delta_value_score"]:+.2f}</span></p>'
        f'</div>',
        unsafe_allow_html=True,
    )


# --- Squad ------------------------------------------------------------------

def _squad(squad: pd.DataFrame, full_table: pd.DataFrame, season: str,
           qual_min: int) -> None:
    theme.section("Squad by value",
                  subtitle=f"Players with at least {qual_min:,} minutes, best first.")
    if squad.empty:
        return

    ordered = squad.sort_values("value_score", ascending=False)
    for i, (_, row) in enumerate(ordered.iterrows()):
        pos = row["position"]
        # League rank within position, so the number means the same thing it
        # does on the Players page.
        cohort = rank_by_position(full_table, pos).copy()
        cohort["_rank"] = range(1, len(cohort) + 1)
        match = cohort[cohort["player_name"] == row["player_name"]]
        league_rank = int(match.iloc[0]["_rank"]) if not match.empty else 0

        with st.container(key=f"prow_team_{i}"):
            mins = int(row["minutes_played"])
            c.player_row_header(
                rank=league_rank, name=row["player_name"],
                team_abbr=row.get("team_abbreviation", ""),
                context=f"{POSITION_LABELS.get(pos, pos)}  ·  {mins:,} min",
                value=float(row["value_score"]),
            )
            with st.expander("Detail", expanded=False):
                if match.empty:
                    st.caption("Detail unavailable for this player.")
                else:
                    _player_detail(match.iloc[0], cohort, season, qual_min, pos)
