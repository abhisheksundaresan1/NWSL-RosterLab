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

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from src.analysis.ranking import build_player_value_table, rank_by_position, validate_value_table
from src.analysis.college_ranking import build_college_value_table
from src.explain.insight import one_line_insight
from src.data.sources import (
    fetch_player_goals_added,
    fetch_player_xgoals,
    fetch_players,
    fetch_teams,
    fetch_player_birthdates,
)
from src.agent.canned import CANNED_SEARCHES, run_canned
from src.agent.scout import check_rate_limit, get_cached, run_scout_query
from src.analysis.validation import run_validation, load_validation_cache, save_validation_cache

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

@st.cache_data(show_spinner="Loading college data...", ttl=86400)
def load_college_tables() -> dict:
    return build_college_value_table(season="2026")


@st.cache_data(show_spinner="Loading player data...", ttl=86400)
def load_value_table(min_minutes: int, season: str) -> pd.DataFrame:
    ga = fetch_player_goals_added(season_name=season)
    xg = fetch_player_xgoals(season_name=season)
    pl = fetch_players()
    tm = fetch_teams()
    bd = fetch_player_birthdates()
    return build_player_value_table(ga, xg, pl, tm, birthdates=bd, min_minutes=min_minutes, season=season)


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


_CARD_VERSION = "3"  # bump when render_player_card layout changes to bust the cache

@st.cache_data(show_spinner=False)
def _cached_player_card(player_name: str, season: str, min_minutes: int, position: str, card_version: str = _CARD_VERSION) -> bytes:
    """Cache rendered PNG bytes. card_version is hashed — bump _CARD_VERSION to invalidate old PNGs."""
    from src.share.card import render_player_card
    full   = load_value_table(min_minutes, season)
    cohort = rank_by_position(full, position).copy()
    cohort["_rank"] = range(1, len(cohort) + 1)
    match  = cohort[cohort["player_name"] == player_name]
    if match.empty:
        raise ValueError(f"{player_name} not found in cohort")
    row    = match.iloc[0].to_dict()
    insight = get_insight(player_name, season, min_minutes, position)
    return render_player_card(row, cohort, season, insight_text=insight)


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

    # Data freshness — read mtime from the goals-added parquet for this season
    _parquet_path = Path(__file__).parent / "data" / "raw" / f"nwsl_player_goals_added_{season}.parquet"
    if _parquet_path.exists():
        _mtime = datetime.fromtimestamp(_parquet_path.stat().st_mtime)
        st.caption(f"Data as of: {_mtime.strftime('%b %d, %Y %H:%M')}")
    else:
        st.caption("Data as of: not yet loaded")

    if st.button("Refresh data", help="Re-pulls latest data from ASA + Wikidata ages. Takes ~20 seconds."):
        with st.spinner("Pulling fresh data from ASA..."):
            fetch_player_goals_added(season_name=season, refresh=True)
            fetch_player_xgoals(season_name=season, refresh=True)
            fetch_players(refresh=True)
            fetch_teams(refresh=True)
        with st.spinner("Refreshing player ages from Wikidata..."):
            fetch_player_birthdates(refresh=True)
        st.cache_data.clear()
        st.rerun()

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

tab_rankings, tab_draft, tab_scout, tab_validation, tab_about = st.tabs(["Player Rankings", "Draft Board", "Scout Assistant", "Model Validation", "About"])

# ---------------------------------------------------------------------------
# Tab 1: Player Rankings (all existing content, unchanged)
# ---------------------------------------------------------------------------

with tab_rankings:
    # QA warnings (only shown when data has unexpected nulls or out-of-range values)
    for _w in _qa_warnings:
        st.warning(f"Data QA: {_w}")

    pos_label = POSITION_LABELS[selected_pos]
    st.subheader(f"{len(ranked)} {pos_label}s ranked by value score")
    st.caption(
        f"Data: American Soccer Analysis — {season} NWSL season. "
        "Players with limited NWSL minutes or not tracked by ASA may be absent."
    )

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

                try:
                    card_bytes = _cached_player_card(
                        row["player_name"], season, min_minutes, selected_pos
                    )
                    st.download_button(
                        label="⬇ Download card (PNG)",
                        data=card_bytes,
                        file_name=f"{row['player_name'].replace(' ', '_')}_{season}_nwsl_rosterlab.png",
                        mime="image/png",
                        key=f"dl_{row['player_name']}_{season}",
                    )
                except Exception:
                    pass  # never crash the Rankings tab over a card render failure

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
# Tab 2: Draft Board
# ---------------------------------------------------------------------------

with tab_draft:
    st.subheader("College Draft Board — 2025-26 NCAA D-I Women's Soccer")
    st.caption(
        "Rankings based on conference-adjusted attacking output (goals, assists, shots on goal per game). "
        "Z-scored within conference tier so Power 5 and mid-major players are compared fairly."
    )

    try:
        college_tables = load_college_tables()
        draft_board = college_tables["draft_board"]
        draftable_summary = college_tables["draftable_summary"]
        trends = college_tables["trends"]
        _college_available = True
    except FileNotFoundError:
        _college_available = False
        st.info(
            "NCAA draft board data is not available on this deployment. "
            "The scraper requires a local Chrome browser — run `python -m src.data.ncaa` "
            "locally to populate the cache, then commit `data/raw/ncaa_players.parquet`."
        )

    if _college_available:
        # Filters
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            pos_options_ncaa = ["All"] + sorted(draft_board["position"].dropna().unique().tolist())
            ncaa_pos = st.selectbox("Position", pos_options_ncaa, key="ncaa_pos")
        with col_f2:
            yr_options = ["All"] + sorted(draft_board["class_year"].dropna().unique().tolist())
            ncaa_yr = st.selectbox("Class year", yr_options, key="ncaa_yr")
        with col_f3:
            conf_options = ["All"] + sorted(draft_board["conference"].dropna().unique().tolist())
            ncaa_conf = st.selectbox("Conference", conf_options, key="ncaa_conf")

        filtered = draft_board.copy()
        if ncaa_pos != "All":
            filtered = filtered[filtered["position"] == ncaa_pos]
        if ncaa_yr != "All":
            filtered = filtered[filtered["class_year"] == ncaa_yr]
        if ncaa_conf != "All":
            filtered = filtered[filtered["conference"] == ncaa_conf]

        # Player profile card — shown above the table when a row is selected
        # We use session state to persist selection across reruns
        profile_placeholder = st.container()

        # Compute prior-season deltas for table colouring
        all_seasons = college_tables["all_seasons"]
        prior = all_seasons[all_seasons["season"] == "2025"][["name", "school", "goals_pg", "assists_pg", "sog_pg"]].copy()
        prior = prior.rename(columns={"goals_pg": "_prev_goals_pg", "assists_pg": "_prev_assists_pg", "sog_pg": "_prev_sog_pg"})
        filtered = filtered.merge(prior, on=["name", "school"], how="left")

        _COLOUR_COLS  = {"goals_pg": "_prev_goals_pg", "assists_pg": "_prev_assists_pg", "sog_pg": "_prev_sog_pg"}
        _MARGIN_PG    = 0.03

        def _colour_row(row):
            styles = [""] * len(row)
            for col, prev_col in _COLOUR_COLS.items():
                if col not in row.index or prev_col not in row.index:
                    continue
                idx = row.index.get_loc(col)
                cur, pre = row[col], row[prev_col]
                if pd.isna(cur) or pd.isna(pre):
                    continue
                diff = float(cur) - float(pre)
                if diff > _MARGIN_PG:
                    styles[idx] = "color: #4CAF50; font-weight: bold"
                elif diff < -_MARGIN_PG:
                    styles[idx] = "color: #F44336; font-weight: bold"
            return styles

        st.markdown(f"**{len(filtered)} players** | sorted by draft score (conference-adjusted) — click a row to see player profile")

        display_cols = ["name", "school", "conference", "position", "class_year",
                        "goals", "assists", "goals_pg", "assists_pg", "sog_pg",
                        "draft_score", "draft_percentile"]
        display_cols = [c for c in display_cols if c in filtered.columns]

        display_df = filtered[display_cols + [c for c in ["_prev_goals_pg", "_prev_assists_pg", "_prev_sog_pg"] if c in filtered.columns]].reset_index(drop=True)
        styled_df = display_df.style.apply(_colour_row, axis=1).hide(
            axis="columns",
            subset=[c for c in ["_prev_goals_pg", "_prev_assists_pg", "_prev_sog_pg"] if c in display_df.columns]
        )

        board_selection = st.dataframe(
            styled_df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "name":             st.column_config.TextColumn("Player"),
                "school":           st.column_config.TextColumn("School"),
                "conference":       st.column_config.TextColumn("Conference"),
                "position":         st.column_config.TextColumn("Pos"),
                "class_year":       st.column_config.TextColumn("Year"),
                "goals":            st.column_config.NumberColumn("Goals", format="%.0f"),
                "assists":          st.column_config.NumberColumn("Assists", format="%.0f"),
                "goals_pg":         st.column_config.NumberColumn("Goals/G", format="%.2f"),
                "assists_pg":       st.column_config.NumberColumn("Ast/G", format="%.2f"),
                "sog_pg":           st.column_config.NumberColumn("SoG/G", format="%.2f"),
                "draft_score":      st.column_config.NumberColumn("Draft Score", format="%.2f"),
                "draft_percentile": st.column_config.ProgressColumn("Percentile", min_value=0, max_value=100, format="%.0f%%"),
            },
        )

        selected_rows = board_selection.selection.rows if board_selection.selection.rows else []
        if selected_rows:
            sel = display_df.iloc[selected_rows[0]]
            history = college_tables["all_seasons"]
            history = history[
                (history["name"] == sel["name"]) &
                (history["school"] == sel["school"])
            ].sort_values("season")

            with profile_placeholder:
                st.divider()
                st.markdown(f"### {sel['name']}")
                st.caption(f"{sel.get('school', '')} · {sel.get('conference', '')} · {sel.get('position', '')} · {sel.get('class_year', '')}")

                left_col, right_col = st.columns([1, 1])

                with left_col:
                    prev = history.iloc[-2] if len(history) >= 2 else None

                    def _delta(col, margin=0):
                        if prev is None:
                            return None
                        cur_v = sel.get(col)
                        pre_v = prev.get(col)
                        if pd.isna(cur_v) or pd.isna(pre_v):
                            return None
                        diff = round(float(cur_v) - float(pre_v), 2)
                        return None if abs(diff) <= margin else diff

                    m1, m2, m3 = st.columns(3)
                    m1.metric("Goals",     f"{int(sel['goals']) if pd.notna(sel.get('goals')) else '—'}",     delta=_delta("goals"))
                    m2.metric("Assists",   f"{int(sel['assists']) if pd.notna(sel.get('assists')) else '—'}",  delta=_delta("assists"))
                    m3.metric("Points",    f"{int(sel['points']) if pd.notna(sel.get('points')) else '—'}",    delta=_delta("points"))
                    m4, m5, m6 = st.columns(3)
                    m4.metric("Goals/G",   f"{sel['goals_pg']:.2f}"   if pd.notna(sel.get('goals_pg'))   else "—", delta=_delta("goals_pg"))
                    m5.metric("Assists/G", f"{sel['assists_pg']:.2f}" if pd.notna(sel.get('assists_pg')) else "—", delta=_delta("assists_pg"))
                    m6.metric("SoG/G",     f"{sel['sog_pg']:.2f}"     if pd.notna(sel.get('sog_pg'))     else "—", delta=_delta("sog_pg"))

                with right_col:
                    if len(history) > 1:
                        season_labels = {"2023": "22-23", "2024": "23-24", "2025": "24-25", "2026": "25-26"}
                        chart_df = history[["season", "goals_pg", "assists_pg", "points_pg"]].copy()
                        chart_df["season"] = chart_df["season"].map(season_labels).fillna(chart_df["season"])
                        chart_df = chart_df.set_index("season")
                        st.line_chart(chart_df, y=["goals_pg", "assists_pg", "points_pg"],
                                      y_label="Per Game", use_container_width=True, height=200)
                    else:
                        st.caption("Only one season of data — trend chart needs 2+ seasons.")
                st.divider()

        st.divider()

        # Draftable profile fingerprint by position + round
        if not draftable_summary.empty:
            st.markdown("**What did NWSL draft picks look like in college? (2021–2024)**")
            st.caption(
                "Median stats the season before being drafted, by position and round. "
                "Use this as a benchmark against current players in the board above."
            )
            fp_col_config = {
                "position_group": st.column_config.TextColumn("Position"),
                "round":          st.column_config.NumberColumn("Round", format="%d"),
                "n_players":      st.column_config.NumberColumn("# Matched", format="%d"),
                "goals_pg":       st.column_config.NumberColumn("Goals/G", format="%.2f"),
                "assists_pg":     st.column_config.NumberColumn("Ast/G", format="%.2f"),
                "points_pg":      st.column_config.NumberColumn("Pts/G", format="%.2f"),
                "sog_pg":         st.column_config.NumberColumn("SoG/G", format="%.2f"),
                "goals":          st.column_config.NumberColumn("Goals", format="%.1f"),
                "assists":        st.column_config.NumberColumn("Assists", format="%.1f"),
                "gp":             st.column_config.NumberColumn("Games", format="%.0f"),
            }
            fp_display = [c for c in draftable_summary.columns if c in fp_col_config]
            st.dataframe(draftable_summary[fp_display], use_container_width=True, hide_index=True,
                         column_config=fp_col_config)

        st.divider()

        # Biggest improvers
        st.markdown("**Biggest year-over-year improvers**")
        st.caption("Players whose goals/game increased most from the prior season.")
        if not trends.empty:
            trend_cols = ["name", "school", "season", "prev_goals_pg", "goals_pg", "goals_pg_delta",
                          "assists_pg_delta", "conference", "position", "class_year"]
            trend_cols = [c for c in trend_cols if c in trends.columns]
            st.dataframe(
                trends[trend_cols].head(20),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "goals_pg_delta":   st.column_config.NumberColumn("Goals/G Δ", format="%+.2f"),
                    "assists_pg_delta": st.column_config.NumberColumn("Ast/G Δ", format="%+.2f"),
                    "prev_goals_pg":    st.column_config.NumberColumn("Prev Goals/G", format="%.2f"),
                    "goals_pg":         st.column_config.NumberColumn("Curr Goals/G", format="%.2f"),
                },
            )


# ---------------------------------------------------------------------------
# Tab 3: Scout Assistant
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


# ---------------------------------------------------------------------------
# Tab 4: Model Validation — "Does this hold up?"
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Running validation across all seasons…", ttl=86400)
def _load_validation() -> dict:
    """Load validation result — from JSON cache if available, else compute."""
    cached = load_validation_cache()
    if cached is not None:
        return cached
    result = run_validation(min_minutes=500)
    save_validation_cache(result)
    return result


def _pct(val) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return "—"
    return f"{val:.0%}"


def _fmt(val, decimals=2) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return "—"
    return f"{val:.{decimals}f}"


with tab_validation:
    st.subheader("Does this hold up? — Value score vs. Best XI awards")
    st.caption(
        "We test whether our value_score identifies the same players that "
        "the NWSL's own Best XI voters chose. All deterministic — no AI cost."
    )

    if st.button("Re-run validation", help="Re-pulls Wikipedia data and recomputes all metrics. Takes ~30 seconds."):
        with st.spinner("Re-running validation…"):
            _load_validation.clear()
            v = run_validation(min_minutes=500)
            save_validation_cache(v)
        st.rerun()

    with st.spinner("Loading validation results…"):
        v = _load_validation()

    bxi_df = v.get("best_xi_ranked", pd.DataFrame())

    # -----------------------------------------------------------------------
    # Row 1: Headline metrics
    # -----------------------------------------------------------------------
    st.divider()
    st.markdown("### Headline — First XI outfielders (pooled across all seasons)")
    st.caption(
        "Slot-matched hit-rate = % of matched First XI players ranked within their bucket's Best XI quota "
        "(DEF top-4, MF/FW top-6). Median rank percentile = median rank ÷ bucket size. GKs excluded."
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    slot_pct = v.get("median_rank_pct")
    slot_str = f"{slot_pct:.1%}" if slot_pct is not None else "—"
    c1.metric("Slot-matched",     _pct(v.get("pooled_hit_rate_slot_matched")), help="% ranked within Best XI quota for their bucket (DEF≤4, MF/FW≤6)")
    c2.metric("Median rank %ile", slot_str,                                    help="Median within-bucket rank ÷ bucket size — lower is better")
    c3.metric("ROC-AUC",          _fmt(v.get("roc_auc"), 3),                   help="Pooled across all seasons/positions (0.5 = random, 1.0 = perfect)")
    c4.metric("Top-3 hit-rate",   _pct(v.get("pooled_hit_rate_top3")),         help="Secondary: % ranking top-3 in bucket")
    c5.metric("Matched",          str(v.get("n_first_matched", "—")),          help="# First XI outfield players matched to ASA dataset")

    # -----------------------------------------------------------------------
    # Row 2: Bucket breakdown
    # -----------------------------------------------------------------------
    st.divider()
    st.markdown("### By bucket (First XI)")
    col_def, col_mf = st.columns(2)

    with col_def:
        st.markdown("**Defenders (CB + FB → DEF)**")
        st.info(
            f"Slot-matched (top-4): **{_pct(v.get('defender_hit_rate_slot_matched'))}** &nbsp;|&nbsp; "
            f"Top-3: **{_pct(v.get('defender_hit_rate_top3'))}** &nbsp;|&nbsp; "
            f"Top-5: **{_pct(v.get('defender_hit_rate_top5'))}**"
        )
        st.caption("⚠️ Off-ball defending is under-measured — expect this to be the weakest bucket.")

    with col_mf:
        st.markdown("**Midfielders & Forwards (DM/CM/AM/W/ST → MF/FW)**")
        st.info(
            f"Slot-matched (top-6): **{_pct(v.get('mffw_hit_rate_slot_matched'))}** &nbsp;|&nbsp; "
            f"Top-3: **{_pct(v.get('mffw_hit_rate_top3'))}** &nbsp;|&nbsp; "
            f"Top-5: **{_pct(v.get('mffw_hit_rate_top5'))}**"
        )

    # -----------------------------------------------------------------------
    # Row 3: Second XI (softer tier)
    # -----------------------------------------------------------------------
    with st.expander("Second XI hit-rate (softer tier — for reference)", expanded=False):
        sc1, sc2 = st.columns(2)
        sc1.metric("Top-3 hit-rate (2nd XI)", _pct(v.get("pooled_hit_rate_top3_second")))
        sc2.metric("Top-5 hit-rate (2nd XI)", _pct(v.get("pooled_hit_rate_top5_second")))

    # -----------------------------------------------------------------------
    # Row 4: Team-level correlation
    # -----------------------------------------------------------------------
    st.divider()
    st.markdown("### Team-level: does a high-value roster win more points?")
    st.caption(
        f"Spearman correlation between team-average value_score and regular-season points. "
        f"N = {v.get('team_n_observations', '—')} team-seasons."
    )
    tc1, tc2 = st.columns(2)
    tc1.metric("Spearman ρ",  _fmt(v.get("team_spearman_rho"), 3))
    tc2.metric("p-value",     _fmt(v.get("team_spearman_p"), 3))

    # -----------------------------------------------------------------------
    # Row 5: Per-season breakdown
    # -----------------------------------------------------------------------
    st.divider()
    st.markdown("### Per-season breakdown (First XI)")
    per_season_rows = []
    for s, m in sorted(v.get("per_season", {}).items()):
        first = m.get("first", {})
        def_m = m.get("defender_first", {})
        mf_m  = m.get("mffw_first", {})
        per_season_rows.append({
            "Season": str(s),
            "Top-3": _pct(first.get("top3")),
            "Top-5": _pct(first.get("top5")),
            "Matched": first.get("n_matched", 0),
            "Unmatched": first.get("n_unmatched", 0),
            "Def top-3": _pct(def_m.get("top3")),
            "MF/FW top-3": _pct(mf_m.get("top3")),
            "Median rank": _fmt(first.get("median_rank"), 1),
        })
    if per_season_rows:
        st.dataframe(pd.DataFrame(per_season_rows), hide_index=True, use_container_width=True)

    # -----------------------------------------------------------------------
    # Row 6: Best XI player detail table
    # -----------------------------------------------------------------------
    st.divider()
    st.markdown("### Best XI players — our within-bucket rank")

    if not bxi_df.empty:
        display_bxi = bxi_df.copy()
        display_bxi["Season"] = display_bxi["season"].astype(str)
        display_bxi["XI"]     = display_bxi["team_selection"].str.capitalize()
        display_bxi["Bucket"] = display_bxi["position_group"]
        display_bxi["Player"] = display_bxi["best_xi_name"]
        display_bxi["ASA name"] = display_bxi["asa_name"].fillna("—")
        display_bxi["Our rank"] = display_bxi["bucket_rank"].apply(
            lambda x: int(x) if pd.notna(x) else "—"
        )
        display_bxi["Value score"] = display_bxi["value_score"].apply(
            lambda x: f"{x:.3f}" if pd.notna(x) else "—"
        )
        display_bxi["Status"] = display_bxi.apply(
            lambda r: "✓ matched" if r["matched"] else ("⚠ below minutes" if r["below_minutes"] else "✗ not found"),
            axis=1,
        )
        st.dataframe(
            display_bxi[["Season", "XI", "Bucket", "Player", "ASA name", "Our rank", "Value score", "Status"]],
            hide_index=True,
            use_container_width=True,
            column_config={
                "Our rank": st.column_config.NumberColumn("Our rank", format="%d"),
            },
        )

    # -----------------------------------------------------------------------
    # Row 7: Unmatched list
    # -----------------------------------------------------------------------
    unmatched_list = v.get("unmatched", [])
    if unmatched_list:
        diag_counts = {}
        for u in unmatched_list:
            d = u.get("diagnosis", "?")
            diag_counts[d] = diag_counts.get(d, 0) + 1
        diag_summary = ", ".join(f"{v} {k}" for k, v in diag_counts.items())
        with st.expander(
            f"Unmatched Best XI players ({len(unmatched_list)}: {diag_summary})",
            expanded=False,
        ):
            st.caption(
                "ABSENT = not found in ASA's player database at all (unexplained tracking gap). "
                "NAME-MISMATCH = present in ASA but below the minutes threshold that season. "
                "Add aliases to data/validation/name_aliases.csv to fix mismatches."
            )
            for u in unmatched_list:
                diag = u.get("diagnosis", "?")
                mins = u.get("actual_minutes")
                mins_str = f" — {int(mins)} min" if mins is not None else ""
                cands = ", ".join(u["candidates"]) if u["candidates"] else "no close matches"
                badge = "🔴" if diag == "ABSENT" else "🟡"
                st.markdown(
                    f"{badge} **{u['best_xi_name']}** ({u['season']} {u['team_selection']} "
                    f"{u['position_group']}) `{diag}`{mins_str} → _{cands}_"
                )

    # -----------------------------------------------------------------------
    # Caveats
    # -----------------------------------------------------------------------
    st.divider()
    with st.expander("Important caveats", expanded=False):
        st.warning("""
**Best XI is consensus, not ground truth.** Voters pick 11 players per season — this reflects
collective opinion, not objective performance. A player can be excellent and miss the XI; a
popular player may make it despite a down year.

**Small samples → pooled numbers are more reliable.** Each season has ~8 matched outfield Best XI
players. Per-season hit-rates should be read directionally, not as precise estimates.

**GKs are excluded.** Our model does not score goalkeepers, so they are not part of the hit-rate.

**Position buckets are approximate.** We collapse 7 model positions to 2 validation buckets
(DEF, MF/FW). A winger and a defensive mid compete in the same bucket.

**~{n_unmatched} Best XI players are unmatched** (below minutes threshold or name mismatch).
Hit-rate is computed only over the matched subset, which may be biased toward higher-minute players.

**Team correlation uses regular-season points.** Playoff performance, home/away splits, and
strength-of-schedule are not accounted for.
        """.format(n_unmatched=len(unmatched_list)))

# ---------------------------------------------------------------------------
# Tab 5: About
# ---------------------------------------------------------------------------
with tab_about:
    st.markdown("## About NWSL RosterLab")
    st.markdown(
        "_\"Goals added by the players. Words added by Claude. Value added by, hopefully, me.\"_"
    )
    st.markdown(
        "NWSL RosterLab turns free public data into ranked, explained, position-aware player value "
        "for the National Women's Soccer League. It also has an AI scout assistant you can ask things "
        'like "find me a ball-progressing center back under 23."'
    )

    st.markdown("### Why I built it")
    st.markdown(
        "I'm a product manager, and I'm a little obsessed with soccer. The NWSL is one of the "
        "fastest-rising leagues in American sports, and it just reshaped its roster rules: no more "
        "college draft, new free agency, a tight salary cap. But the public tools for understanding "
        "player value are still raw stat tables built for analysts, and the polished ones (Wyscout, "
        "StatsBomb, Opta) cost far more than fans or smaller clubs can spend. RosterLab is my attempt "
        "to close that gap with something opinionated, transparent, and free."
    )

    st.markdown("### How the value score works")
    st.markdown(
        "Every player's value starts from American Soccer Analysis's Goals Added (g+), a measure of "
        "total on-ball contribution across six action types: shooting, dribbling, passing, receiving, "
        "defending, and fouling. I convert those to per-90, weight them by position (a center back is "
        "judged mostly on defending and progression, a striker on finishing), and standardize within "
        "position into a single value score. The weights are an editorial scouting judgment, not a "
        "black box, so you are free to disagree with them. The plain-English note on each player is "
        "written by an AI layer that only phrases the numbers already computed. It never invents a stat."
    )

    st.markdown("### Does it hold up?")
    st.markdown(
        "I tested the value score against six seasons of NWSL Best XI selections, 2019 through 2025. "
        "It ranks a Best XI player above a non-selected player 79% of the time (ROC-AUC 0.79), puts "
        "Best XI players in the top 14% of their position, and rates winning rosters higher (Spearman "
        "correlation of 0.61). As a gut check, it independently rates 2022 MVP Sophia Smith (Wilson) "
        "as the league's number one striker in both 2022 and 2023. The Model Validation tab has the "
        "full breakdown. It is weakest on pure defenders, because on-ball data under-measures "
        "off-ball defending."
    )

    st.markdown("### Data and limitations")
    st.markdown(
        "Player metrics come from American Soccer Analysis (g+, xG, xA). Ages come from Wikidata. "
        "ASA uses players' current names, so some appear under a married name (for example, Sophia "
        "Wilson). Players with limited NWSL minutes, or who are not tracked by ASA, may be missing. "
        "This was built independently using public data. It is not affiliated with or endorsed by the "
        "NWSL, any club, or American Soccer Analysis."
    )

    st.divider()
    st.markdown(
        "Built by **Abhishek Sundaresan**. "
        "[LinkedIn](https://www.linkedin.com/in/abhishek-sundaresan/) · "
        "[GitHub](https://github.com/abhisheksundaresan1/NWSL-RosterLab)"
    )
    st.markdown("Feedback is welcome, especially from NWSL fans and people working in soccer analytics.")
