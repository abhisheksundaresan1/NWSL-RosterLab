"""Teams — placeholder in Phase 1, built out in Phase 2.

Phase 2 will show, per club: squad ranked by value, recent risers and fallers,
and depth by position — all from data that already exists (the season table plus
compute_movement over the snapshot history). The page is registered now so the
navigation is final and its URL (/teams) is stable.
"""

from __future__ import annotations

import streamlit as st

from src.ui import theme, state


def render() -> None:
    theme.section(
        "Teams",
        subtitle="Squad value, recent movement and positional depth — coming next.",
        eyebrow_text=f"NWSL {state.get_season()}",
    )
    st.info(
        "**Coming in the next release.** Each club will get its own page: every "
        "player ranked by value, who has risen or fallen over recent weeks, and "
        "where the squad is deep or thin by position. Built from data already in "
        "the app — no new sources.",
        icon=":material/construction:",
    )
