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

from src.analysis.form import (
    FORM_WINDOW_DAYS, K_FORM, MIN_MINUTES_FORM, MIN_COHORT_FOR_RANK,
)
from src.analysis.ranking import rank_by_position
from src.analysis.season import IN_SEASON_YEAR
from src.ui import components as c
from src.ui import loaders, state, theme
from src.ui.pages.players import POSITION_LABELS, POSITION_ORDER, _player_detail

DEPTH_MIN_MINUTES = 1        # effectively "anyone with recorded minutes"
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

    qual_min = state.get_min_minutes(season)
    squad_table = _table(season, qual_min)

    # --- Selection, seeded from the URL so links are shareable --------------
    # A ?team= link always wins. Without one, open on the strongest squad by
    # median value rather than whichever club sorts first alphabetically — that
    # was Angel City, currently the lowest-value squad with two empty depth
    # cells, i.e. the worst possible first impression of the page.
    #
    # The default is applied by SEEDING session state, not by passing index=.
    # A keyed widget whose key already exists in session state ignores index
    # entirely, so once a visitor had landed on any club that choice stuck for
    # the rest of their session and no change of default could take effect.
    requested = st.query_params.get("team")
    stored = st.session_state.get("team_pick")

    if requested in abbrs:
        # A deep link beats whatever the session happens to remember.
        if stored != requested:
            st.session_state["team_pick"] = requested
    elif stored not in abbrs:
        # No link and nothing valid remembered — open on the strongest squad.
        st.session_state["team_pick"] = _strongest_squad(squad_table, abbrs)

    chosen = st.selectbox(
        "Club", abbrs, format_func=lambda a: names.get(a, a), key="team_pick",
    )
    if st.query_params.get("team") != chosen:
        st.query_params["team"] = chosen

    squad = squad_table[squad_table["team_abbreviation"] == chosen] if squad_table is not None \
        else pd.DataFrame()
    depth = depth_table[depth_table["team_abbreviation"] == chosen]

    _header(chosen, names.get(chosen, chosen), squad, qual_min)
    theme.rule()
    _depth(depth)
    theme.rule()
    if in_season:
        _form(chosen)
        theme.rule()
    else:
        st.caption(
            f"Form covers the last {FORM_WINDOW_DAYS} days of the live season — "
            f"switch to {IN_SEASON_YEAR} to see it."
        )
        theme.rule()
    _squad(squad, squad_table, season, qual_min)


def _strongest_squad(squad_table: pd.DataFrame | None, abbrs: list[str]) -> str:
    """Club with the highest median value score, as the landing default.

    Median rather than mean because the mean is pulled hard by outliers in small
    qualifying pools. This is only a default view — it is never presented to the
    user as a league ranking, which the data does not support.
    """
    if squad_table is None or squad_table.empty:
        return abbrs[0]
    med = squad_table.groupby("team_abbreviation")["value_score"].median()
    med = med[med.index.isin(abbrs)]
    return med.idxmax() if not med.empty else abbrs[0]


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
        # pd.notna guard: some ASA player_ids have no name in the players
        # reference, and a NaN here rendered as the literal string "nan" in the
        # depth cell.
        best = None
        if n:
            top_name = at_pos.nlargest(1, "value_score")["player_name"].iloc[0]
            best = top_name if pd.notna(top_name) else None
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

def _form(abbr: str) -> None:
    """This club's form over the last FORM_WINDOW_DAYS — a level, then a change.

    Same metric and same constants as This week; the only difference is the
    filter to one club. Deliberately NOT the season value score, and never
    differenced against it.
    """
    theme.section(
        f"Form · last {FORM_WINDOW_DAYS} days",
        subtitle=(f"Weighted goals added per 90 over the last {FORM_WINDOW_DAYS} days "
                  f"only, for players with {MIN_MINUTES_FORM}+ minutes in that window. "
                  f"A separate metric from the season value score above."),
    )

    form_date = loaders.latest_form_date()
    form = loaders.load_form(form_date) if form_date else pd.DataFrame()
    if form.empty:
        c.empty_state(f"No form data yet for {IN_SEASON_YEAR}.")
        return

    mine = form[form["team_abbreviation"] == abbr]
    if mine.empty:
        c.empty_state(
            f"Nobody at this club has {MIN_MINUTES_FORM}+ minutes in the last "
            f"{FORM_WINDOW_DAYS} days."
        )
        return

    best, movers = st.columns(2)
    with best:
        st.caption(f"**In form** · best {FORM_WINDOW_DAYS}-day rate")
        for _, r in mine.nlargest(3, "form_weighted_p90").iterrows():
            _form_row(r, f"{r['form_weighted_p90']:.2f} g+/90", theme.POSITIVE)
    with movers:
        st.caption(f"**Change** vs the previous {FORM_WINDOW_DAYS} days")
        moved = mine.dropna(subset=["form_delta"])
        if moved.empty:
            st.caption(
                f"No player here clears {MIN_MINUTES_FORM} minutes in both windows, "
                f"so there is no comparable change to show."
            )
        else:
            for _, r in moved.reindex(
                moved["form_delta"].abs().sort_values(ascending=False).index
            ).head(3).iterrows():
                colour = theme.POSITIVE if r["form_delta"] > 0 else theme.NEGATIVE
                _form_row(r, f"{r['form_delta']:+.2f} g+/90", colour)

    st.caption(
        f"Form is shrunk toward the position average with K = {K_FORM} minutes. "
        f"Ranks are within position across the league, and a position with fewer than "
        f"{MIN_COHORT_FOR_RANK} qualifying players this window is shown as a rate only."
    )


def _form_row(row: pd.Series, value_text: str, colour: str) -> None:
    """One player line: position, sample size, cohort, then the rate."""
    cohort = row.get("form_cohort_n")
    rank = row.get("form_rank")
    if pd.notna(rank) and pd.notna(cohort):
        place = f"  ·  #{int(rank)} of {int(cohort)} {row['position']}s"
    else:
        # Below MIN_COHORT_FOR_RANK: a z-score over a handful of players is not a
        # ranking, so no rank and no score are shown — only the rate and sample.
        place = f"  ·  too few {row['position']}s to rank"
    st.markdown(
        f'<div class="rl-metric" style="margin-bottom:6px">'
        f'<p class="rl-metric-k">{c._esc(row["position"])}  ·  '
        f'{c._esc(row["form_sample_label"])}{c._esc(place)}</p>'
        f'<p class="rl-metric-v">{c._esc(row["player_name"])} '
        f'<span style="color:{colour}">{c._esc(value_text)}</span></p>'
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
