"""Prospects — college players, formerly the "Draft Board" tab.

Naming matters here. The NWSL abolished the college draft in the 2024 CBA, so
the *live* board is a prospect board and its metric is a prospect score. The
2021-2024 benchmark section is kept and labelled explicitly as historical,
because it genuinely does describe real past drafts.

Other fixes vs. the old tab:
  - the internal _prev_* columns were IN the displayed frame and only suppressed
    by a pandas Styler; they are now dropped before display
  - None renders as an em dash
  - the improvers table had raw lowercase headers (name, class_year, ...)
  - the percentile ProgressColumn rendered solid at both 99% and 100%, so its
    colour carried no information; it is now a plain number column plus the bar
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.ui import components as c
from src.ui import loaders, theme

_PREV_COLS = ["_prev_goals_pg", "_prev_assists_pg", "_prev_sog_pg"]

# Columns that should read as whole numbers; everything else numeric gets 2dp.
_INT_COLS = {"goals", "assists", "points", "gp", "n_players", "round"}
# Deltas keep an explicit sign so a gain reads as a gain.
_DELTA_COLS = {"goals_pg_delta", "assists_pg_delta"}


def _styled(df: pd.DataFrame, extra_styles=None, keep_numeric: set[str] | None = None):
    """DataFrame -> Styler that renders missing values as an em dash.

    Number formatting has to live on the Styler rather than in column_config:
    a NaN left in a float column reaches the grid and renders as "None", and
    column_config has no null-representation option.

    `keep_numeric` names columns to leave unformatted — ProgressColumn needs a
    real number, so formatting its column to a string would break the bar.
    """
    keep = keep_numeric or set()
    fmt = {}
    for col in df.columns:
        if col in keep:
            continue
        if pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col]):
            if col in _DELTA_COLS:
                fmt[col] = "{:+.2f}"
            else:
                fmt[col] = "{:.0f}" if col in _INT_COLS else "{:.2f}"
    styler = c.dash_blanks(df).style.format(fmt, na_rep="—")
    if extra_styles is not None:
        styler = styler.apply(lambda _: extra_styles, axis=None)
    return styler
_COLOUR_PAIRS = {"goals_pg": "_prev_goals_pg", "assists_pg": "_prev_assists_pg",
                 "sog_pg": "_prev_sog_pg"}
_MARGIN_PG = 0.03
_SEASON_LABELS = {"2023": "22-23", "2024": "23-24", "2025": "24-25", "2026": "25-26"}


def render() -> None:
    theme.section(
        "Prospect Board",
        subtitle=("Conference-adjusted attacking output (goals, assists, shots on goal per "
                  "game), z-scored within conference tier so Power-conference and mid-major "
                  "players are compared fairly."),
        eyebrow_text="NCAA D-I WOMEN'S SOCCER · 2025-26",
    )

    try:
        if "college_tables" not in st.session_state:
            st.session_state["college_tables"] = loaders.load_college_tables()
        tables = st.session_state["college_tables"]
    except FileNotFoundError:
        st.info(
            "College data isn't available on this deployment. The scraper needs a local "
            "Chrome browser — run `python -m src.data.ncaa` locally, then commit "
            "`data/raw/ncaa_players.parquet`."
        )
        return

    board = tables["draft_board"]
    _live_board(board, tables)
    theme.rule()
    _historical_benchmark(tables.get("draftable_summary", pd.DataFrame()))
    theme.rule()
    _improvers(tables.get("trends", pd.DataFrame()))


# --- Live board -------------------------------------------------------------

def _live_board(board: pd.DataFrame, tables: dict) -> None:
    f1, f2, f3 = st.columns(3)
    with f1:
        pos = st.selectbox("Position", ["All"] + sorted(board["position"].dropna().unique()),
                           key="ncaa_pos")
    with f2:
        yr = st.selectbox("Class year", ["All"] + sorted(board["class_year"].dropna().unique()),
                          key="ncaa_yr")
    with f3:
        conf = st.selectbox("Conference", ["All"] + sorted(board["conference"].dropna().unique()),
                            key="ncaa_conf")

    filtered = board.copy()
    if pos != "All":
        filtered = filtered[filtered["position"] == pos]
    if yr != "All":
        filtered = filtered[filtered["class_year"] == yr]
    if conf != "All":
        filtered = filtered[filtered["conference"] == conf]

    profile_slot = st.container()

    # Prior-season values, used only to colour the current-season columns.
    prior = tables["all_seasons"]
    prior = prior[prior["season"] == "2025"][
        ["name", "school", "goals_pg", "assists_pg", "sog_pg"]
    ].rename(columns=dict(zip(["goals_pg", "assists_pg", "sog_pg"], _PREV_COLS)))
    filtered = filtered.merge(prior, on=["name", "school"], how="left")

    st.caption(f"**{len(filtered)} players**, sorted by prospect score — select a row for a profile.")
    st.caption(
        "Goals/G, Ast/G and SoG/G are coloured against **that player's own previous season** — "
        "green means she improved on herself, red means she declined. They are not compared "
        "between players, so a lower green number can sit beside a higher red one."
    )

    display_cols = [x for x in ["name", "school", "conference", "position", "class_year",
                                "goals", "assists", "goals_pg", "assists_pg", "sog_pg",
                                "draft_score", "draft_percentile"] if x in filtered.columns]

    # Keep _prev_* only long enough to compute the styling, then drop them so
    # they cannot leak into the rendered table.
    with_prev = filtered[display_cols + [x for x in _PREV_COLS if x in filtered.columns]].reset_index(drop=True)
    styles = with_prev.apply(_colour_row, axis=1, result_type="expand")
    styles = styles[display_cols]
    shown = with_prev[display_cols].copy()

    # Percentile stays numeric for the progress bar, but as a whole number so it
    # never renders as "100.000000" whichever text path the grid takes.
    if "draft_percentile" in shown.columns:
        shown["draft_percentile"] = shown["draft_percentile"].round(0).astype("Int64")

    # draft_percentile stays a real number for ProgressColumn (it has no nulls);
    # every other numeric goes through the Styler so blanks show as em dashes.
    styled = _styled(shown, extra_styles=styles, keep_numeric={"draft_percentile"})

    selection = st.dataframe(
        styled, width="stretch", hide_index=True,
        on_select="rerun", selection_mode="single-row",
        column_config={
            "name":       st.column_config.TextColumn("Player"),
            "school":     st.column_config.TextColumn("School"),
            "conference": st.column_config.TextColumn("Conference"),
            "position":   st.column_config.TextColumn("Pos", width="small"),
            "class_year": st.column_config.TextColumn("Year", width="small"),
            "goals":      st.column_config.NumberColumn("Goals"),
            "assists":    st.column_config.NumberColumn("Assists"),
            "goals_pg":   st.column_config.NumberColumn("Goals/G"),
            "assists_pg": st.column_config.NumberColumn("Ast/G"),
            "sog_pg":     st.column_config.NumberColumn("SoG/G"),
            # "draft_*" are the internal column names; the labels are what users read.
            "draft_score": st.column_config.NumberColumn("Prospect score"),
            # width="medium" so the bar plus its "100%" label are not clipped.
            "draft_percentile": st.column_config.ProgressColumn(
                "Percentile", min_value=0, max_value=100, format="%.0f%%",
                width="medium"),
        },
    )

    rows = selection.selection.rows if selection.selection.rows else []
    if rows:
        _profile(profile_slot, shown.iloc[rows[0]], tables["all_seasons"])


def _colour_row(row: pd.Series) -> list[str]:
    """Green/red on current-season columns that moved vs. the prior season."""
    styles = pd.Series("", index=row.index)
    for col, prev_col in _COLOUR_PAIRS.items():
        if col not in row.index or prev_col not in row.index:
            continue
        cur, pre = row[col], row[prev_col]
        if pd.isna(cur) or pd.isna(pre):
            continue
        diff = float(cur) - float(pre)
        if diff > _MARGIN_PG:
            styles[col] = "color: #6FA88C; font-weight: 600"
        elif diff < -_MARGIN_PG:
            styles[col] = "color: #C4756A; font-weight: 600"
    return styles


def _profile(slot, sel: pd.Series, all_seasons: pd.DataFrame) -> None:
    history = all_seasons[
        (all_seasons["name"] == sel["name"]) & (all_seasons["school"] == sel["school"])
    ].sort_values("season")

    with slot:
        theme.rule()
        theme.section(
            str(sel["name"]),
            eyebrow_text=" · ".join(
                str(sel.get(k, "")) for k in ["school", "conference", "position", "class_year"]
                if pd.notna(sel.get(k))
            ).upper(),
        )
        prev = history.iloc[-2] if len(history) >= 2 else None

        def _delta(col, margin=0.0):
            if prev is None or pd.isna(sel.get(col)) or pd.isna(prev.get(col)):
                return None
            diff = round(float(sel[col]) - float(prev[col]), 2)
            return None if abs(diff) <= margin else diff

        def _num(col, fmt):
            return format(sel[col], fmt) if pd.notna(sel.get(col)) else "—"

        left, right = st.columns(2)
        with left:
            a, b, d = st.columns(3)
            a.metric("Goals",   _num("goals", ".0f"),   delta=_delta("goals"))
            b.metric("Assists", _num("assists", ".0f"), delta=_delta("assists"))
            d.metric("Points",  _num("points", ".0f"),  delta=_delta("points"))
            e, f, g = st.columns(3)
            e.metric("Goals/G",   _num("goals_pg", ".2f"),   delta=_delta("goals_pg"))
            f.metric("Assists/G", _num("assists_pg", ".2f"), delta=_delta("assists_pg"))
            g.metric("SoG/G",     _num("sog_pg", ".2f"),     delta=_delta("sog_pg"))
        with right:
            if len(history) > 1:
                chart = history[["season", "goals_pg", "assists_pg", "points_pg"]].copy()
                chart["season"] = chart["season"].map(_SEASON_LABELS).fillna(chart["season"])
                st.line_chart(chart.set_index("season"),
                              y=["goals_pg", "assists_pg", "points_pg"],
                              y_label="Per game", width="stretch", height=200)
            else:
                st.caption("Only one season of data — a trend needs two or more.")
        theme.rule()


# --- Historical benchmark ---------------------------------------------------

def _historical_benchmark(summary: pd.DataFrame) -> None:
    """Genuinely historical: these were real drafts, before the 2024 CBA."""
    if summary is None or summary.empty:
        return
    theme.section(
        "How past draft picks (2021–2024) compared in college",
        subtitle=("Median college stats the season before each player was drafted, by position "
                  "and round. A historical benchmark for the board above — the NWSL no longer "
                  "holds a college draft."),
        eyebrow_text="HISTORICAL · PRE-2024 CBA",
    )
    cfg = {
        "position_group": st.column_config.TextColumn("Position"),
        "round":      st.column_config.NumberColumn("Round", format="%d"),
        "n_players":  st.column_config.NumberColumn("# Matched", format="%d"),
        "goals_pg":   st.column_config.NumberColumn("Goals/G"),
        "assists_pg": st.column_config.NumberColumn("Ast/G"),
        "points_pg":  st.column_config.NumberColumn("Pts/G"),
        "sog_pg":     st.column_config.NumberColumn("SoG/G"),
        "goals":      st.column_config.NumberColumn("Goals"),
        "assists":    st.column_config.NumberColumn("Assists"),
        "gp":         st.column_config.NumberColumn("Games"),
    }
    cols = [x for x in summary.columns if x in cfg]
    st.dataframe(_styled(summary[cols]), width="stretch", hide_index=True,
                 column_config=cfg)


# --- Improvers --------------------------------------------------------------

def _improvers(trends: pd.DataFrame) -> None:
    if trends is None or trends.empty:
        return
    theme.section(
        "Biggest year-over-year improvers",
        subtitle="Players whose goals per game rose most against their prior season.",
    )
    cols = [x for x in ["name", "school", "season", "prev_goals_pg", "goals_pg",
                        "goals_pg_delta", "assists_pg_delta", "conference", "position",
                        "class_year"] if x in trends.columns]
    data = trends[cols].head(20)

    # Every column gets a proper header — previously only the four numeric ones
    # were configured and the rest rendered as raw snake_case.
    # No format= here: the Styler owns number formatting so nulls can render as
    # em dashes. These entries supply labels only.
    cfg = {
        "goals_pg_delta":   st.column_config.NumberColumn("Goals/G Δ"),
        "assists_pg_delta": st.column_config.NumberColumn("Ast/G Δ"),
        "prev_goals_pg":    st.column_config.NumberColumn("Prev Goals/G"),
        "goals_pg":         st.column_config.NumberColumn("Curr Goals/G"),
        "name":             st.column_config.TextColumn("Player"),
        "school":           st.column_config.TextColumn("School"),
        "season":           st.column_config.TextColumn("Season"),
        "conference":       st.column_config.TextColumn("Conference"),
        "position":         st.column_config.TextColumn("Pos"),
        "class_year":       st.column_config.TextColumn("Year"),
    }
    st.dataframe(_styled(data), width="stretch", hide_index=True, column_config=cfg)
