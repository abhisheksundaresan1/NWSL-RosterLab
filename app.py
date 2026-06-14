"""
Streamlit UI — PRESENT layer only.

RULE (see CLAUDE.md): no data-fetching and no metric math in this file.
Calls src/data/sources.py and src/analysis/ranking.py; renders results.
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so `src` is importable regardless
# of how Streamlit is launched (with or without PYTHONPATH set).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import streamlit as st

from src.analysis.ranking import build_player_value_table, rank_by_position
from src.explain.insight import one_line_insight
from src.data.sources import (
    fetch_player_goals_added,
    fetch_player_xgoals,
    fetch_players,
    fetch_teams,
)

AVAILABLE_SEASONS = ["2025", "2024", "2023", "2022", "2021", "2020", "2019"]
DEFAULT_SEASON = "2025"

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
# LLM insight helpers
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _cached_insight(player_name: str, season: str, min_minutes: int, position: str) -> str:
    """Cache only successful LLM outputs. Raises on failure so st.cache_data skips storage."""
    full   = load_value_table(min_minutes, season)
    cohort = rank_by_position(full, position).copy()
    cohort["_rank"] = range(1, len(cohort) + 1)
    match  = cohort[cohort["player_name"] == player_name]
    if match.empty:
        raise RuntimeError("player not found in cohort")
    row    = match.iloc[0].to_dict()
    result = one_line_insight(row, cohort)
    if result is None:
        raise RuntimeError("insight generation failed — skip cache")
    return result


def get_insight(player_name: str, season: str, min_minutes: int, position: str) -> str | None:
    try:
        return _cached_insight(player_name, season, min_minutes, position)
    except Exception:
        return None


def _fallback_insight(row: pd.Series, cohort: pd.DataFrame) -> str:
    action_labels = {
        "ga_shooting": "shooting", "ga_dribbling": "dribbling",
        "ga_passing": "passing", "ga_receiving": "receiving",
        "ga_interrupting": "defensive actions", "ga_fouling": "fouling",
    }
    action_vals = {col: float(row.get(col, 0.0)) for col in action_labels}
    top_col = max(action_vals, key=action_vals.get)
    return (
        f"Ranks #{int(row['_rank'])} of {len(cohort)} {row['position']}s on g+/90 "
        f"({row['goals_added_p90']:.3f} vs. position avg "
        f"{round(cohort['goals_added_p90'].mean(), 3):.3f}), "
        f"with her strongest contribution from {action_labels[top_col]} "
        f"({action_vals[top_col]:+.3f} g+)."
    )

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

    st.divider()
    with st.expander("Metric glossary", expanded=False):
        st.markdown("""
| Metric | What it means |
|---|---|
| **Value Score** | Z-score within position — how many standard deviations above/below average for her position. 0 = average, +2 = elite. Not comparable across positions. |
| **Goals Added (g+)** | Total value added across all on-ball actions this season. ASA's primary value metric — positive means she made her team more likely to score or less likely to concede. |
| **g+ / 90** | Goals added per 90 minutes. Adjusts for playing time so a starter and a rotation player can be compared fairly. |
| **xG / 90** | Expected goals per 90 — measures shot *quality*, not just volume. Based on shot location, angle, and assist type. |
| **xAssists / 90** | Expected assists per 90 — credit for passes that led to shots, regardless of whether the shot went in. |
| **xG+xA / 90** | Combined expected goal involvement per 90. The standard single-number summary of attacking output. |
| **g+ Shooting** | Slice of g+ from shots taken. High = takes good shots or finishes well. Negative = shoots from poor positions. |
| **g+ Dribbling** | Slice of g+ from carrying the ball and beating players. |
| **g+ Passing** | Slice of g+ from passing. Often negative for defensive players; positive for creative midfielders. |
| **g+ Receiving** | Slice of g+ from how well she receives and controls possession. |
| **g+ Interrupting** | Slice of g+ from defensive actions — interceptions, blocks, tackles. Key for valuing defenders. |
| **g+ Fouling** | Slice of g+ from fouls committed. Almost always negative — fouls give opponents free kicks in dangerous areas. |
""")

# ---------------------------------------------------------------------------
# Filter and rank
# ---------------------------------------------------------------------------

# League-wide rank computed before any team filter so it stays consistent
# across the card header and insight text.
league_ranked = rank_by_position(full_table, selected_pos).copy()
league_ranked["_rank"] = range(1, len(league_ranked) + 1)

if selected_teams:
    ranked = league_ranked[league_ranked["team_name"].isin(selected_teams)].reset_index(drop=True)
else:
    ranked = league_ranked

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
    # -----------------------------------------------------------------------
    # Dashboard summary — three charts
    # -----------------------------------------------------------------------
    col_a, col_b, col_c = st.columns([1.2, 1, 1])

    with col_a:
        st.markdown(f"**Top 10 {pos_label}s by Value Score**")
        top10 = ranked.head(10)[["player_name", "value_score"]].set_index("player_name")
        st.bar_chart(top10, horizontal=True, y_label="Value Score")

    with col_b:
        st.markdown(f"**Value vs. Chance Involvement**")
        st.caption("Each dot = one player. Top-right = elite all-round.")
        scatter_data = ranked[["player_name", "xga_p90", "goals_added_p90"]].copy()
        st.scatter_chart(
            scatter_data,
            x="xga_p90",
            y="goals_added_p90",
            x_label="xG+xA / 90",
            y_label="g+ / 90",
        )

    with col_c:
        top_player = ranked.iloc[0]
        st.markdown(f"**How {top_player['player_name']} creates value**")
        st.caption(f"#{1} ranked {pos_label} — action type breakdown")
        action_data = pd.DataFrame({
            "Action": list(ACTION_COLS.values()),
            "Goals Added": [top_player[col] for col in ACTION_COLS],
        }).set_index("Action")
        st.bar_chart(action_data, horizontal=True)

    st.divider()

    # -----------------------------------------------------------------------
    # Player cards
    # -----------------------------------------------------------------------
    for i, row in ranked.iterrows():
        card_label = (
            f"#{int(row['_rank'])}  {row['player_name']}  ·  {row['team_abbreviation']}  "
            f"·  Value: {row['value_score']:.2f}  "
            f"·  g+/90: {row['goals_added_p90']:.3f}  "
            f"·  xG+xA/90: {row['xga_p90']:.3f}  "
            f"·  {int(row['minutes_played']):,} min"
        )

        with st.expander(card_label, expanded=False):
            insight = get_insight(row["player_name"], season, min_minutes, selected_pos)
            if insight is None:
                insight = _fallback_insight(row, league_ranked)
            st.info(f"**Analyst take:** {insight}")

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
