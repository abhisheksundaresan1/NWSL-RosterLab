"""Players — the ranked list, rebuilt.

Changes from the old "Player Rankings" tab:
  - filters are inline and scoped to this page (Position / Min minutes / Team).
    Season is global, in the app header — there is exactly one season control.
  - the top-10 bar chart and the top-of-page action chart are gone: both
    duplicated information already shown below.
  - the "Value vs. Chance Involvement" scatter moved into a collapsed
    "League view" expander beneath the list.
  - each row is rank / team-colour bar / name / context / value score, instead
    of the old jargon-dense expander label.
  - the vertical "Core metrics" list became a compact metric grid.
Insights, the shareable card and all cache keys behave exactly as before.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.analysis.ranking import rank_by_position, validate_value_table
from src.analysis.season import IN_SEASON_YEAR
from src.analytics import track
from src.ui import components as c
from src.ui import loaders, state, theme

POSITION_LABELS = {
    "ST": "Striker", "W": "Winger", "AM": "Attacking Mid", "CM": "Central Mid",
    "DM": "Defensive Mid", "FB": "Full Back", "CB": "Center Back",
}
POSITION_ORDER = ["ST", "W", "AM", "CM", "DM", "FB", "CB"]

ACTION_COLS = {
    "ga_shooting": "Shooting", "ga_dribbling": "Dribbling", "ga_passing": "Passing",
    "ga_receiving": "Receiving", "ga_interrupting": "Interrupting", "ga_fouling": "Fouling",
}


def render() -> None:
    season = state.get_season()
    in_season = season == IN_SEASON_YEAR

    # --- Resolve the in-season snapshot (guard lives here, not app-wide) -----
    snap = games_est = None
    if in_season:
        snap = loaders.latest_snapshot()
        if snap is None:
            theme.section("Players", eyebrow_text=f"NWSL {season}")
            st.warning(
                "No 2026 snapshots available yet. Run `python scripts/snapshot.py "
                "--backfill` to seed them, or switch to a completed season above."
            )
            return
        games_est = loaders.snapshot_games_est(snap)

    # --- Inline filters (no season here — it is global in the header) -------
    f1, f2, f3 = st.columns([2, 2, 3])
    with f1:
        pos_choice = st.selectbox(
            "Position",
            [f"{p} — {POSITION_LABELS[p]}" for p in POSITION_ORDER],
            index=0, key="pl_pos",
        )
        selected_pos = pos_choice[:2].strip()
    with f2:
        default_min = state.default_min_minutes(season, games_est)
        max_min = max(360, games_est * 90) if (in_season and games_est) else 2000
        min_minutes = st.slider(
            "Minimum minutes", min_value=90, max_value=max_min,
            value=min(default_min, max_min), step=45 if in_season else 90,
            key=f"pl_min_{season}",
            help=f"~{games_est} games played · default is 50% of that" if in_season else None,
        )
        state.set_min_minutes(season, min_minutes)

    # --- Load ---------------------------------------------------------------
    if in_season:
        full_table = loaders.load_in_season_table(min_minutes, snap)
    else:
        full_table = loaders.load_value_table(min_minutes, season)

    with f3:
        all_teams = sorted(full_table["team_name"].dropna().unique().tolist())
        selected_teams = st.multiselect(
            "Teams", options=all_teams, default=[], placeholder="All teams", key="pl_teams",
        )

    pos_label = POSITION_LABELS[selected_pos]
    league_ranked = rank_by_position(full_table, selected_pos).copy()
    league_ranked["_rank"] = range(1, len(league_ranked) + 1)
    ranked = (
        league_ranked[league_ranked["team_name"].isin(selected_teams)].reset_index(drop=True)
        if selected_teams else league_ranked
    )

    theme.rule()
    theme.section(
        f"{len(ranked)} {pos_label}s by value",
        subtitle=("Position-weighted goals added, scored against other "
                  f"{pos_label.lower()}s. 0.00 is positional average."),
        eyebrow_text=f"NWSL {season}" + (" · in progress" if in_season else " · completed season"),
    )

    if in_season:
        st.warning(
            f"**Early-season data** — about {games_est} games played. Value scores are "
            "Bayesian-shrunk toward the position average using **K = 300 minutes**, then "
            "re-standardised, so small samples can't dominate. That also means these "
            "scores are **not comparable with a completed season's** even though the "
            "metric shares a name. Treat them as directional.",
            icon=":material/info:",
        )
    for w in validate_value_table(full_table):
        st.caption(f"Data QA: {w}")

    if ranked.empty:
        c.empty_state("No players match these filters.",
                      "Try clearing the team filter or lowering the minutes threshold.")
        return

    # --- The list -----------------------------------------------------------
    for i, row in ranked.iterrows():
        with st.container(key=f"prow_{selected_pos}_{i}"):
            mins = int(row["minutes_played"])
            context = f"{row['team_name']}  ·  {mins:,} min"
            if in_season:
                context += f"  ·  ~{mins // 90} games"
            c.player_row_header(
                rank=int(row["_rank"]), name=row["player_name"],
                team_abbr=row.get("team_abbreviation", ""),
                context=context, value=float(row["value_score"]),
            )
            # Short label on purpose: a longer one wraps at narrow widths and
            # collides with neighbouring rows.
            with st.expander("Detail", expanded=False):
                _player_detail(row, league_ranked, season, min_minutes, selected_pos)

    # --- League view (moved off the top of the page) ------------------------
    theme.rule()
    with st.expander("League view — value vs. chance involvement", expanded=False):
        st.caption("Each dot is one player. Top-right is elite all-round.")
        st.scatter_chart(
            ranked[["player_name", "xga_p90", "goals_added_p90"]],
            x="xga_p90", y="goals_added_p90",
            x_label="xG+xA / 90", y_label="g+ / 90",
        )


def _player_detail(row: pd.Series, cohort: pd.DataFrame, season: str,
                   min_minutes: int, position: str) -> None:
    """Expanded row: analyst take, metric grid, action breakdown, card download."""
    name = row["player_name"]

    # Analyst take (LLM, cached; falls back to a deterministic line)
    insight_key = f"insight__{name}__{season}__{position}__{min_minutes}"
    if insight_key not in st.session_state:
        if st.button("Get analyst take", key=f"btn__{insight_key}"):
            with st.spinner("Generating insight..."):
                res = loaders.get_insight(name, season, min_minutes, position)
                st.session_state[insight_key] = res or loaders.fallback_insight(row, cohort)
            st.rerun()
    if insight_key in st.session_state:
        st.info(st.session_state[insight_key], icon=":material/neurology:")

    # Metric grid, replacing the old vertical bold label/value list
    c.metric_grid({
        "Weighted g+/90": f"{row['weighted_ga_p90']:.2f}",
        "Raw g+/90":      f"{row['goals_added_p90']:.2f}",
        "g+ total":       f"{row['goals_added_total']:.2f}",
        "xG+xA/90":       f"{row['xga_p90']:.2f}",
        "xG/90":          f"{row['xgoals_p90']:.2f}",
        "xA/90":          f"{row['xassists_p90']:.2f}",
        "Minutes":        f"{int(row['minutes_played']):,}",
        "Rank":           f"#{int(row['_rank'])} of {len(cohort)}",
    })

    st.markdown("")
    st.caption("Goals added by action type")
    st.bar_chart(
        pd.DataFrame({
            "Action": list(ACTION_COLS.values()),
            "Goals added": [row[k] for k in ACTION_COLS],
        }).set_index("Action"),
        horizontal=True,
    )

    # Shareable card — generated on demand (a render plus an LLM call), so it is
    # never produced for every player on page load.
    card_key = f"cardpng__{name}__{season}__{position}__{min_minutes}_v6"
    if card_key not in st.session_state:
        if st.button("Prepare shareable card", key=f"prep__{card_key}"):
            with st.spinner("Rendering card…"):
                try:
                    st.session_state[card_key] = loaders.cached_player_card(
                        name, season, min_minutes, position
                    )
                except Exception:
                    st.session_state[card_key] = b""
            st.rerun()
    card_bytes = st.session_state.get(card_key)
    if card_bytes:
        if st.download_button(
            "⬇ Download card (PNG)", data=card_bytes,
            file_name=f"{str(name).replace(' ', '_')}_{season}_nwsl_rosterlab.png",
            mime="image/png", key=f"dl_{name}_{season}",
        ):
            track.card_download("player", player=str(name), season=season,
                                position=position)
    elif card_key in st.session_state:
        st.caption("Card unavailable for this player.")
