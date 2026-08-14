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

# page_title is the question the site answers, not the brand — it is what shows
# in the browser tab and in a bookmark, where "NWSL RosterLab" tells a first-time
# visitor nothing.
#
# NOTE: this sets the tab title only. It does NOT set the link preview on
# LinkedIn/Reddit/Slack. Streamlit Community Cloud serves a shell containing
# `<title>Streamlit</title>` and no og:/twitter: meta tags, and the app is
# entirely client-rendered — a crawler never runs the JS that applies this. A
# real link preview needs a custom domain with a static landing page or an edge
# worker injecting the tags; there is no Streamlit-side fix.
st.set_page_config(
    page_title="Who's actually performing in the NWSL",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from src.analytics import track                      # noqa: E402
from src.ui import state, theme                      # noqa: E402
from src.ui.pages import (                           # noqa: E402
    this_week, players, teams, drops, prospects, ask, method,
)

theme.inject()


def _header(season_applies: bool = True) -> None:
    """Brand on the left, the single global season control on the right.

    Season lives here rather than on Players because it drives This week, Players
    and Teams alike — one control, one value, read everywhere via src/ui/state.

    `season_applies=False` disables the control on pages it does not drive
    (Prospects is NCAA college data). Showing a live "2026 · in progress" next to
    a college board invites the reader to assume they are looking at NWSL 2026.
    """
    left, right = st.columns([6, 2], vertical_alignment="center")
    with left:
        st.markdown(
            '<p class="rl-brand-eyebrow">NWSL</p>'
            '<p class="rl-brand">RosterLab</p>',
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
            disabled=not season_applies,
            help=(
                "NWSL season. Value scores are NOT comparable across seasons: the "
                "in-season year is Bayesian-shrunk (K=300) toward the position mean "
                "and then re-standardised, so its spread differs from a completed "
                "season even though the metric shares a name."
                if season_applies else
                "The Prospect Board is NCAA college data and is not affected by this control."
            ),
        )
        if season_applies and choice != current:
            state.set_season(choice)
            st.rerun()

    # Tagline spans the full width BELOW the columns, not inside the left one.
    # Nested under the wordmark it would be squeezed against the season control
    # and wrap at ordinary desktop widths.
    st.markdown(
        '<p class="rl-tagline">Beyond goals and assists.</p>'
        '<p class="rl-tagline-sub">Every NWSL player, ranked by what she actually '
        'adds to her team. Updated weekly.</p>',
        unsafe_allow_html=True,
    )


def _footer() -> None:
    """Persistent, always-visible attribution on every page.

    The sources list also appears on Method, but that is a click away — the
    credit for the underlying Goals Added model belongs where nobody has to
    look for it.
    """
    st.markdown(
        '<div class="rl-footer">'
        '<span>Player data: <a href="https://www.americansocceranalysis.com/" '
        'target="_blank" rel="noopener">American Soccer Analysis</a> '
        '(Goals Added) · ages: Wikidata · college: NCAA</span>'
        '<span>Independent project — not affiliated with the NWSL or ASA</span>'
        '</div>',
        unsafe_allow_html=True,
    )


PAGES = [
    st.Page(this_week.render, title="This week", url_path="this-week", default=True),
    st.Page(players.render,   title="Players",   url_path="players"),
    st.Page(teams.render,     title="Teams",     url_path="teams"),
    st.Page(drops.render,     title="Drops",     url_path="drops"),
    st.Page(prospects.render, title="Prospects", url_path="prospects"),
    st.Page(ask.render,       title="Ask",       url_path="ask"),
    st.Page(method.render,    title="Method",    url_path="method"),
]

# position="hidden" keeps the routing and URLs but suppresses Streamlit's own
# nav bar, which we render below instead.
nav = st.navigation(PAGES, position="hidden")

# The season control drives the NWSL pages only; Prospects is NCAA college data.
_season_applies = getattr(nav, "url_path", "") != "prospects"

# Analytics. Both calls are internally de-duplicated: session_start fires once
# per session, page_view only when the slug changes. Streamlit reruns the whole
# script on every interaction, so an unguarded call here would inflate counts by
# an order of magnitude. Both are no-ops when POSTHOG_API_KEY is unset.
# The identity component must render exactly once per run, before any event, so
# it lives here rather than inside track.event(). It returns None on a session's
# first run; events raised in the meantime are buffered and flushed when the
# browser reports its id on the following rerun.
track.ensure_identity()
track.start_session()
track.page_view(getattr(nav, "url_path", "") or "this-week")

# ?debug=identity — kept, not temporary. It is how the cookie failure was finally
# caught, and it only ever renders on an explicit query parameter. Cookie values
# are redacted apart from our own id; the same jar holds Streamlit's XSRF token.
if st.query_params.get("debug") == "identity":
    st.json(track.debug_identity())

_header(season_applies=_season_applies)

with st.container(key="rl_nav"):
    cols = st.columns(len(PAGES) + 2)
    for col, page in zip(cols, PAGES):
        with col:
            st.page_link(page, label=page.title, width="stretch")

nav.run()

_footer()
