"""
Streamlit UI — PRESENT layer only.

RULE (see CLAUDE.md): no data-fetching and no metric math in this file.
Calls src/data/sources.py and src/analysis/ranking.py; renders results.
"""

import pandas as pd
import streamlit as st

from src.analysis.ranking import build_player_value_table, rank_by_position
from src.data.sources import (
    AVAILABLE_SEASONS,
    DEFAULT_SEASON,
    fetch_player_goals_added,
    fetch_player_xgoals,
    fetch_players,
    fetch_teams,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POSITION_LABELS = {
    "ST": "Striker",
    "W":  "Winger",
    "AM": "Attacking Mid",
    "CM": "Central Mid",
    "DM": "Defensive Mid",
    "FB": "Full Back",
    "CB": "Center Back",
}

# Ordered for the selectbox (fan-friendliest first)
POSITION_ORDER = ["ST", "W", "AM", "CM", "DM", "FB", "CB"]

ACTION_COLS = {
    "ga_shooting":     "Shooting",
    "ga_dribbling":    "Dribbling",
    "ga_passing":      "Passing",
    "ga_receiving":    "Receiving",
    "ga_interrupting": "Interrupting",
    "ga_fouling":      "Fouling",
}

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="NWSL RosterLab", page_icon="⚽", layout="wide")

st.title("NWSL RosterLab")
st.caption("Ranked, plain-English player-value insights for the NWSL.")

# ---------------------------------------------------------------------------
# Cached data loader — recomputes only when min_minutes changes
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Loading player data...")
def load_value_table(min_minutes: int, season: str) -> pd.DataFrame:
    ga = fetch_player_goals_added(season_name=season)
    xg = fetch_player_xgoals(season_name=season)
    pl = fetch_players()
    tm = fetch_teams()
    return build_player_value_table(ga, xg, pl, tm, min_minutes=min_minutes)

# ---------------------------------------------------------------------------
# Sidebar — filters
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Filters")

    season = st.selectbox(
        "Season",
        options=AVAILABLE_SEASONS,
        index=AVAILABLE_SEASONS.index(DEFAULT_SEASON),
    )

    pos_options = [f"{p} — {POSITION_LABELS[p]}" for p in POSITION_ORDER]
    pos_choice = st.selectbox("Position", pos_options, index=0)
    selected_pos = pos_choice[:2].strip()

    min_minutes = st.slider(
        "Minimum minutes played",
        min_value=90,
        max_value=2000,
        value=500,
        step=90,
    )

    # Load full table (cached per season + min_minutes combination)
    full_table = load_value_table(min_minutes, season)

    all_teams = sorted(full_table["team_name"].dropna().unique().tolist())
    selected_teams = st.multiselect(
        "Filter by team (optional)",
        options=all_teams,
        default=[],
        placeholder="All teams",
    )

# ---------------------------------------------------------------------------
# Filter and rank
# ---------------------------------------------------------------------------

ranked = rank_by_position(full_table, selected_pos)

if selected_teams:
    ranked = ranked[ranked["team_name"].isin(selected_teams)].reset_index(drop=True)

# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

pos_label = POSITION_LABELS[selected_pos]
st.subheader(f"{len(ranked)} {pos_label}s ranked by value score")
st.caption(f"Data: American Soccer Analysis — {season} NWSL season.")

if ranked.empty:
    st.warning(
        "No players match the current filters. "
        "Try adjusting the team filter or lowering the minimum minutes."
    )
else:
    for i, row in ranked.iterrows():
        rank_num = i + 1
        card_label = (
            f"#{rank_num}  {row['player_name']}  ·  {row['team_abbreviation']}  "
            f"·  Value: {row['value_score']:.2f}  "
            f"·  g+/90: {row['goals_added_p90']:.3f}  "
            f"·  xG+xA/90: {row['xga_p90']:.3f}  "
            f"·  {int(row['minutes_played']):,} min"
        )

        with st.expander(card_label, expanded=False):
            left, right = st.columns(2)

            with left:
                st.markdown("**Core metrics**")
                metrics = {
                    "Goals Added Total":  f"{row['goals_added_total']:.2f}",
                    "Goals Added / 90":   f"{row['goals_added_p90']:.3f}",
                    "xG / 90":            f"{row['xgoals_p90']:.3f}",
                    "xAssists / 90":      f"{row['xassists_p90']:.3f}",
                    "xG + xA / 90":       f"{row['xga_p90']:.3f}",
                    "Minutes Played":     f"{int(row['minutes_played']):,}",
                    "Team":               row['team_name'],
                }
                for label, val in metrics.items():
                    st.markdown(f"**{label}:** {val}")

            with right:
                st.markdown("**Goals added by action type**")
                action_data = pd.DataFrame({
                    "Action": list(ACTION_COLS.values()),
                    "Goals Added": [row[col] for col in ACTION_COLS],
                }).set_index("Action")
                st.bar_chart(action_data, horizontal=True)
