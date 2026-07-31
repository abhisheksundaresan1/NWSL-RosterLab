"""
NWSL RosterLab — Streamlit entrypoint.

PRESENT layer only (see CLAUDE.md): no data fetching, no metric math here or in
any src/ui module. This file does four things and nothing else:
  1. page config
  2. inject the stylesheet
  3. render the app header, including the ONE global season control
  4. hand off to st.navigation

Pages live in src/ui/pages/ as plain callables. Routing is native st.navigation
— no third-party menu component, and every page gets a real URL, which is what
makes team pages linkable.

The nav BAR is rendered here with st.page_link rather than by st.navigation
itself. Streamlit's built-in top nav uses an rc-overflow container that decided
only one item fit and collapsed the rest behind a "5 more" popover, regardless
of viewport width or custom CSS. Driving the links directly keeps the routing
(the reason we chose st.navigation) while giving full control over appearance.
"""

import sys
from pathlib import Path

# Ensure the project root is importable regardless of how Streamlit is launched.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

st.set_page_config(
    page_title="NWSL RosterLab",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from src.ui import state, theme                      # noqa: E402
from src.ui.pages import (                           # noqa: E402
    this_week, players, teams, prospects, ask, method,
)

theme.inject()


def _header() -> None:
    """Brand on the left, the single global season control on the right.

    Season lives here rather than on Players because it drives This week, Players
    and Teams alike — one control, one value, read everywhere via src/ui/state.
    """
    left, right = st.columns([6, 2], vertical_alignment="center")
    with left:
        st.markdown(
            '<p class="rl-brand">NWSL RosterLab</p>'
            '<p class="rl-brand-sub">Ranked, plain-English player value</p>',
            unsafe_allow_html=True,
        )
    with right:
        options = state.seasons()
        current = state.get_season()
        choice = st.selectbox(
            "Season",
            options=options,
            index=options.index(current) if current in options else 0,
            format_func=state.season_label,
            key="rl_season_select",
            label_visibility="collapsed",
        )
        if choice != current:
            state.set_season(choice)
            st.rerun()


PAGES = [
    st.Page(this_week.render, title="This week", url_path="this-week"),
    st.Page(players.render,   title="Players",   url_path="players", default=True),
    st.Page(teams.render,     title="Teams",     url_path="teams"),
    st.Page(prospects.render, title="Prospects", url_path="prospects"),
    st.Page(ask.render,       title="Ask",       url_path="ask"),
    st.Page(method.render,    title="Method",    url_path="method"),
]

# position="hidden" keeps the routing and URLs but suppresses Streamlit's own
# nav bar, which we render below instead.
nav = st.navigation(PAGES, position="hidden")

_header()

with st.container(key="rl_nav"):
    cols = st.columns(len(PAGES) + 2)
    for col, page in zip(cols, PAGES):
        with col:
            st.page_link(page, label=page.title, width="stretch")

nav.run()
