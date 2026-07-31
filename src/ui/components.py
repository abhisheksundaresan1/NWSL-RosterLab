"""
src/ui/components.py — reusable presentation pieces.

Keeps raw HTML out of the page modules: pages call these, the markup and class
names live here and are styled by the single CSS block in theme.py.
"""

from __future__ import annotations

import html as _html

import pandas as pd
import streamlit as st

from src.ui import theme


def _esc(v) -> str:
    """Escape anything interpolated into HTML — player and team names are data."""
    return _html.escape(str(v), quote=True)


def player_row_header(rank: int, name: str, team_abbr: str, context: str,
                      value: float) -> None:
    """The visible part of a player row: rank · team bar · name · context · value.

    Replaces the old jargon-dense expander label
    ("#1  Name  ·  ORL  ·  Value: 3.14  ·  Wtd g+/90: 0.56  ·  ...").
    """
    accent = theme.team_accent(team_abbr)
    c_rank, c_bar, c_name, c_val = st.columns([0.6, 0.18, 6, 2], vertical_alignment="center")
    with c_rank:
        st.markdown(f'<p class="rl-rank">{rank}</p>', unsafe_allow_html=True)
    with c_bar:
        st.markdown(
            f'<div class="rl-teambar" style="background:{accent}"></div>',
            unsafe_allow_html=True,
        )
    with c_name:
        st.markdown(
            f'<p class="rl-name">{_esc(name)}</p>'
            f'<p class="rl-context">{_esc(context)}</p>',
            unsafe_allow_html=True,
        )
    with c_val:
        st.markdown(
            f'<p class="rl-value-label">Value</p>'
            f'<p class="rl-value" style="color:{theme.value_color(value)}">{value:+.2f}</p>',
            unsafe_allow_html=True,
        )


def metric_grid(metrics: dict[str, str], per_row: int = 4) -> None:
    """Compact metric tiles, N across — replaces the old vertical
    '**label:** value' markdown list."""
    items = list(metrics.items())
    for start in range(0, len(items), per_row):
        chunk = items[start:start + per_row]
        cols = st.columns(per_row)
        for col, (k, v) in zip(cols, chunk):
            with col:
                st.markdown(
                    f'<div class="rl-metric">'
                    f'<p class="rl-metric-k">{_esc(k)}</p>'
                    f'<p class="rl-metric-v">{_esc(v)}</p>'
                    f'</div>',
                    unsafe_allow_html=True,
                )


def stat_card(label: str, value: str, caption: str = "",
              accent: str | None = None) -> None:
    """A headline figure with a label above and context below.

    Used for the landing page's riser/faller/newcomer cards. `value` is passed
    already formatted — including "—" when the underlying data is empty, which
    is a legitimate state rather than an error (see stat_card_empty)."""
    colour = accent or theme.ACCENT
    st.markdown(
        f'<div class="rl-stat">'
        f'<p class="rl-stat-k">{_esc(label)}</p>'
        f'<p class="rl-stat-v" style="color:{colour}">{_esc(value)}</p>'
        f'<p class="rl-stat-c">{_esc(caption)}</p>'
        f'</div>',
        unsafe_allow_html=True,
    )


def stat_card_empty(label: str, reason: str) -> None:
    """The card still renders, with a dash and the reason it has no value."""
    st.markdown(
        f'<div class="rl-stat">'
        f'<p class="rl-stat-k">{_esc(label)}</p>'
        f'<p class="rl-stat-v" style="color:{theme.FG_FAINT}">—</p>'
        f'<p class="rl-stat-c">{_esc(reason)}</p>'
        f'</div>',
        unsafe_allow_html=True,
    )


def empty_state(message: str, hint: str | None = None) -> None:
    body = f'<p class="rl-sub">{_esc(message)}</p>'
    if hint:
        body += f'<p class="rl-eyebrow">{_esc(hint)}</p>'
    st.markdown(body, unsafe_allow_html=True)


def footer(data_as_of: str | None = None) -> None:
    right = f"Data as of {_esc(data_as_of)}" if data_as_of else ""
    st.markdown(
        f'<div class="rl-footer"><span>NWSL RosterLab</span><span>{right}</span></div>',
        unsafe_allow_html=True,
    )


def dash_blanks(df: pd.DataFrame) -> pd.DataFrame:
    """Render missing text values as an em dash instead of None/NaN/'None'.

    Note: pandas 3 gives text columns a dedicated StringDtype, so a
    `dtype == object` test silently matches nothing — use the API check.
    Numeric columns are left alone so Streamlit can still format them.
    """
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_numeric_dtype(out[c]) or pd.api.types.is_bool_dtype(out[c]):
            continue
        col = out[c].astype(object)
        col = col.where(pd.notna(col), "—")
        out[c] = col.replace({"None": "—", "nan": "—", "NaN": "—", "": "—"})
    return out


def titleize(col: str) -> str:
    """raw_column_name -> 'Raw Column Name', with known acronyms preserved."""
    special = {"pg": "/G", "sog": "SoG", "gp": "GP", "id": "ID"}
    parts = [special.get(p, p.capitalize()) for p in str(col).split("_")]
    return " ".join(parts).replace(" /G", "/G")
