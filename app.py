"""
Streamlit UI — PRESENT layer only.

RULE (see CLAUDE.md): no data-fetching and no metric math in this file.
It imports from src/ and renders. Phase 2 builds the real screen here.
"""

import streamlit as st

st.set_page_config(page_title="NWSL RosterLab", page_icon="⚽", layout="wide")

st.title("⚽ NWSL RosterLab")
st.caption("Ranked, plain-English player-value insights for the NWSL.")

st.info(
    "Phase 2 placeholder. Next: build the Player Value Explorer — "
    "filter by position, show a ranked list of player cards, click for detail."
)

# Phase 2 (build with Claude Code):
#   from src.data.sources import fetch_player_goals_added, fetch_player_xgoals, fetch_players, fetch_teams
#   from src.analysis.ranking import build_player_value_table
#   position = st.selectbox("Position", [...])
#   table = build_player_value_table(...)
#   render ranked cards for the chosen position
