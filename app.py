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

from src.analysis.ranking import build_player_value_table, rank_by_position, validate_value_table
from src.explain.insight import one_line_insight
from src.data.sources import (
    fetch_player_goals_added,
    fetch_player_xgoals,
    fetch_players,
    fetch_teams,
)
from src.agent.canned import CANNED_SEARCHES, run_canned
from src.agent.scout import check_rate_limit, get_cached, run_scout_query

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


def _render_scout_result(result: str, tools_used: list[str], cached: bool = False):
    """Parse and render the structured agent output (SHORTLIST + REASONING format)."""
    if "SHORTLIST:" in result and "REASONING:" in result:
        shortlist_part, reasoning_part = result.split("REASONING:", 1)
        shortlist_md = shortlist_part.replace("SHORTLIST:", "").strip()
        reasoning_md = reasoning_part.strip()

        st.markdown("**Shortlist**")
        st.markdown(shortlist_md)
        st.markdown("**Why these players**")
        st.markdown(reasoning_md)
    else:
        st.markdown(result)

    if cached:
        st.caption("_(cached result — this query did not use a scout query slot)_")
    elif tools_used:
        with st.expander("Tools used", expanded=False):
            for t in tools_used:
                st.caption(f"→ {t}")


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

    # QA validation — runs on the returned DataFrame, not inside the cached loader
    _qa_warnings = validate_value_table(full_table)

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
| **Value Score** | Position-weighted g+/90 z-scored within position. 0 = position average, +2 = elite. Not comparable across positions. |
| **Weighted g+ / 90** | Position-weighted sum of per-90 action-type g+ scores. Strikers get a higher weight on shooting; CBs get a higher weight on interrupting. This drives the value score ranking. |
| **Goals Added (g+)** | Total value added across all on-ball actions this season (unweighted season total). ASA's primary value metric. |
| **g+ / 90 (raw)** | Unweighted goals added per 90 — all action types counted equally. Shown for reference alongside the position-weighted score. |
| **xG / 90** | Expected goals per 90 — measures shot *quality*, not just volume. Based on shot location, angle, and assist type. |
| **xAssists / 90** | Expected assists per 90 — credit for passes that led to shots, regardless of whether the shot went in. |
| **xG+xA / 90** | Combined expected goal involvement per 90. The standard single-number summary of attacking output. |
| **g+ Shooting** | Season total g+ from shots taken. High = takes good shots or finishes well. |
| **g+ Dribbling** | Season total g+ from carrying the ball and beating players. |
| **g+ Passing** | Season total g+ from passing. Often negative for defensive players; positive for creative midfielders. |
| **g+ Receiving** | Season total g+ from how well she receives and controls possession. |
| **g+ Interrupting** | Season total g+ from defensive actions — interceptions, blocks, tackles. Key for valuing defenders. |
| **g+ Fouling** | Season total g+ from fouls committed. Almost always negative — fouls give opponents free kicks in dangerous areas. |
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
# Main area — tabbed layout
# ---------------------------------------------------------------------------

tab_rankings, tab_scout = st.tabs(["Player Rankings", "Scout Assistant"])

# ---------------------------------------------------------------------------
# Tab 1: Player Rankings (all existing content, unchanged)
# ---------------------------------------------------------------------------

with tab_rankings:
    # QA warnings (only shown when data has unexpected nulls or out-of-range values)
    for _w in _qa_warnings:
        st.warning(f"Data QA: {_w}")

    pos_label = POSITION_LABELS[selected_pos]
    st.subheader(f"{len(ranked)} {pos_label}s ranked by value score")
    st.caption(f"Data: American Soccer Analysis — {season} NWSL season.")

    with st.expander("What does the value score measure? (and its limits)", expanded=False):
        st.markdown(f"""
**Value score** is a position-weighted blend of on-ball goals added (g+), z-scored within each position group.

**How it works:** Each of the 6 g+ action types (shooting, dribbling, passing, receiving,
interrupting, fouling) is converted to per 90 minutes, then multiplied by a position-specific
weight. For example, interrupting g+/90 is weighted 1.6× for CBs but only 0.3× for strikers;
shooting is weighted 1.5× for strikers but 0.2× for CBs. The weighted sum is standardized
within position (0 = position average, +1 = one standard deviation above).

**Current {pos_label} weights (shooting / dribbling / passing / receiving / interrupting / fouling):**
see `POSITION_WEIGHTS` in `src/analysis/ranking.py` — edit freely to test alternative views.

**The raw g+/90 column** shows the unweighted total for reference — useful if you disagree
with the weights or want to compare across positions.

**Key limits:**
- **Off-ball defending is under-measured.** Goals added is an on-ball metric. A CB who
  marshals her backline without touching the ball won't look as good as her true value.
- **Volume and availability aren't captured.** A player at 0.20 weighted g+/90 over 1,800
  minutes may contribute more than one at 0.35 over 500 minutes.
- **Team context is missing.** A pass-heavy team inflates passing g+; a high-press system
  inflates interrupting g+. The score does not adjust for team style.
""")

    if ranked.empty:
        st.warning(
            "No players match the current filters. "
            "Try adjusting the team filter or lowering the minimum minutes."
        )
    else:
        # -------------------------------------------------------------------
        # Dashboard summary — three charts
        # -------------------------------------------------------------------
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

        # -------------------------------------------------------------------
        # Player cards
        # -------------------------------------------------------------------
        for i, row in ranked.iterrows():
            card_label = (
                f"#{int(row['_rank'])}  {row['player_name']}  ·  {row['team_abbreviation']}  "
                f"·  Value: {row['value_score']:.2f}  "
                f"·  Wtd g+/90: {row['weighted_ga_p90']:.3f}  "
                f"·  xG+xA/90: {row['xga_p90']:.3f}  "
                f"·  {int(row['minutes_played']):,} min"
            )

            with st.expander(card_label, expanded=False):
                insight_key = f"insight__{row['player_name']}__{season}__{selected_pos}__{min_minutes}"
                if insight_key not in st.session_state:
                    if st.button("Get analyst take", key=f"btn__{insight_key}"):
                        with st.spinner("Generating insight..."):
                            result = get_insight(row["player_name"], season, min_minutes, selected_pos)
                            st.session_state[insight_key] = result if result is not None else _fallback_insight(row, league_ranked)
                        st.rerun()
                if insight_key in st.session_state:
                    st.info(f"**Analyst take:** {st.session_state[insight_key]}")

                left, right = st.columns(2)

                with left:
                    st.markdown("**Core metrics**")
                    metrics = {
                        "Weighted g+ / 90":   f"{row['weighted_ga_p90']:.3f}",
                        "Raw g+ / 90":        f"{row['goals_added_p90']:.3f}",
                        "Goals Added Total":  f"{row['goals_added_total']:.2f}",
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

# ---------------------------------------------------------------------------
# Tab 2: Scout Assistant
# ---------------------------------------------------------------------------

with tab_scout:
    st.subheader("Scout Assistant")

    # --- Canned searches (zero LLM cost) ---
    st.markdown("**Quick searches** — instant, no AI cost")
    canned_cols = st.columns(len(CANNED_SEARCHES))
    for i, search in enumerate(CANNED_SEARCHES):
        with canned_cols[i]:
            if st.button(
                f"{search['icon']} {search['label']}",
                key=f"canned_{i}",
                use_container_width=True,
            ):
                df_result, description = run_canned(search["label"], season, min_minutes)
                st.session_state["canned_result"] = df_result
                st.session_state["canned_label"] = search["label"]
                st.session_state["canned_description"] = description

    if "canned_result" in st.session_state:
        df_c = st.session_state["canned_result"]
        st.markdown(f"**{st.session_state['canned_label']}**")
        st.caption(st.session_state.get("canned_description", ""))
        if df_c.empty:
            st.warning("No players found. Try a different season or lower the minimum minutes.")
        else:
            st.dataframe(df_c, use_container_width=True, hide_index=True)

    st.divider()

    # --- Free-text Scout (claude-sonnet-4-6, rate-limited) ---
    st.markdown("**Custom scouting request** — powered by Claude Sonnet")
    st.caption(
        "Ask in plain English. Age, salary, nationality, and cost data are not available. "
        "The agent will say so plainly if you ask for them."
    )

    allowed, remaining = check_rate_limit()
    scout_query = st.text_area(
        "Your scouting request",
        height=80,
        placeholder=(
            'e.g. "Find me an undervalued defensive mid with strong interrupting g+ in 2025" '
            'or "Which wingers are the best creators?"'
        ),
        disabled=not allowed,
        key="scout_query_input",
    )

    col_btn, col_status = st.columns([1, 3])
    with col_btn:
        scout_clicked = st.button(
            "Scout" if allowed else "Session limit reached",
            disabled=not allowed,
            type="primary",
            key="scout_btn",
        )
    with col_status:
        used = 8 - remaining
        if allowed:
            st.caption(f"{used} of 8 scout queries used this session.")
        else:
            st.caption("Session limit reached. Refresh the page to start a new session.")

    if scout_clicked:
        query_text = scout_query.strip()
        if not query_text:
            st.warning("Enter a scouting request first.")
        else:
            # Check cache before showing spinner
            cached_result = get_cached(query_text)
            if cached_result:
                _render_scout_result(cached_result, [], cached=True)
            else:
                with st.spinner(f"Scouting... ({remaining - 1} queries remaining after this)"):
                    scout_result, tools_used = run_scout_query(query_text, season, min_minutes)
                _render_scout_result(scout_result, tools_used, cached=False)


