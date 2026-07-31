"""
src/ui/theme.py — the app's single source of visual styling.

Design tokens are imported from src/share/card.py rather than re-declared, so
the app and the downloadable cards cannot drift apart. Colour work that already
exists there — notably _display_accent(), which lifts the 11 of 16 NWSL club
colours that are illegible on a near-black ground — is reused, never duplicated.

Everything CSS lives in ONE block here. No inline styles scattered through the
pages: if a rule is needed, it goes in _CSS with a comment saying what it is for.
"""

from __future__ import annotations

import streamlit as st

from src.share.card import (
    INK, TEXT_1, TEXT_2, TEXT_3, RULE,
    TEAM_COLORS, DEFAULT_TEAM_COLOR, _display_accent, _hex_rgb,
)

# --- Tokens -----------------------------------------------------------------
# Card tokens are RGBA tuples; the app needs CSS hex.
def _hex(rgba) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgba[:3])


BG        = _hex(INK)        # #081018 — page base
SURFACE   = "#0F1B27"        # raised panels
SURFACE_2 = "#14232F"        # hover / nested
LINE      = _hex(RULE)       # #223242 — hairlines
FG        = _hex(TEXT_1)     # #FFFFFF
FG_MUTED  = _hex(TEXT_2)     # #96AABE
FG_FAINT  = _hex(TEXT_3)     # #5F768C
ACCENT    = "#FFC24B"        # the one accent for value/score emphasis
POSITIVE  = "#6FA88C"
NEGATIVE  = "#C4756A"

FONT_DISPLAY = "'Big Shoulders', 'Arial Narrow', sans-serif"
FONT_TEXT    = "'Work Sans', -apple-system, 'Segoe UI', sans-serif"
FONT_MONO    = "'Geist Mono', ui-monospace, 'SFMono-Regular', monospace"


def team_accent(team_abbr: str) -> str:
    """Contrast-corrected club colour as CSS hex — the same colour the card uses."""
    return _hex(_display_accent(TEAM_COLORS.get(str(team_abbr), DEFAULT_TEAM_COLOR)))


def value_color(v: float) -> str:
    """Accent for a positive value score, muted for a negative one."""
    return ACCENT if v >= 0 else FG_MUTED


_CSS = f"""
<style>
/* ---- Chrome removal -----------------------------------------------------
   toolbarMode="minimal" in config.toml removes the Fork/GitHub badge; these
   rules clear the residual footer and decorative header bar.               */
#MainMenu, footer, header [data-testid="stDecoration"] {{ display: none !important; }}
[data-testid="stStatusWidget"] {{ display: none !important; }}

/* ---- Page rhythm --------------------------------------------------------- */
.block-container {{ padding-top: 1.2rem; padding-bottom: 4rem; max-width: 1180px; }}

/* ---- Top navigation ------------------------------------------------------
   Rendered by app.py as st.page_link items inside a container keyed "rl_nav",
   because Streamlit's own top nav collapsed every item behind a "5 more"
   popover. Styling the links directly gives a real, always-visible bar.      */
[class*="st-key-rl_nav"] {{
    border-bottom: 1px solid {LINE};
    margin-bottom: 18px;
}}
[class*="st-key-rl_nav"] [data-testid="stPageLink"] a {{
    font-family: {FONT_TEXT}; font-size: 15px; font-weight: 500;
    color: {FG_MUTED}; padding: 6px 2px; border-bottom: 2px solid transparent;
    border-radius: 0; justify-content: center;
}}
[class*="st-key-rl_nav"] [data-testid="stPageLink"] a:hover {{
    color: {FG}; background: transparent; border-bottom-color: {LINE};
}}
/* Streamlit marks the active page link with aria-current. */
[class*="st-key-rl_nav"] [data-testid="stPageLink"] a[aria-current] {{
    color: {FG}; border-bottom-color: {ACCENT};
}}
[class*="st-key-rl_nav"] [data-testid="stPageLink"] span {{ font-weight: 500; }}

/* ---- App header (brand + global season) ---------------------------------- */
.rl-brand {{
    font-family: {FONT_DISPLAY}; font-weight: 700; font-size: 30px;
    letter-spacing: .01em; color: {FG}; line-height: 1.1; margin: 0;
}}
.rl-brand-sub {{
    font-family: {FONT_TEXT}; font-size: 13px; color: {FG_FAINT}; margin: 2px 0 0 0;
}}

/* ---- Section headers ----------------------------------------------------- */
.rl-eyebrow {{
    font-family: {FONT_MONO}; font-size: 12px; letter-spacing: .16em;
    text-transform: uppercase; color: {FG_FAINT}; margin: 0 0 6px 0;
}}
.rl-h2 {{
    font-family: {FONT_DISPLAY}; font-weight: 700; font-size: 30px;
    color: {FG}; margin: 0 0 2px 0; line-height: 1.15;
}}
.rl-sub {{ font-family: {FONT_TEXT}; font-size: 14px; color: {FG_MUTED}; margin: 0 0 14px 0; }}
.rl-rule {{ border: 0; border-top: 1px solid {LINE}; margin: 18px 0; }}

/* ---- Player rows ---------------------------------------------------------
   Rows are st.container(key="prow_*"); Streamlit emits .st-key-<key>, which
   is the supported hook for targeting a specific container.                  */
[class*="st-key-prow_"] {{
    border-bottom: 1px solid {LINE};
    padding: 2px 0 2px 0;
}}
[class*="st-key-prow_"]:hover {{ background: {SURFACE}; }}
.rl-rank {{
    font-family: {FONT_MONO}; font-size: 13px; color: {FG_FAINT};
    text-align: right; padding-top: 12px;
}}
.rl-name {{
    font-family: {FONT_DISPLAY}; font-weight: 700; font-size: 25px;
    color: {FG}; line-height: 1.15; margin: 0;
}}
.rl-context {{ font-family: {FONT_TEXT}; font-size: 13px; color: {FG_MUTED}; margin: 1px 0 0 0; }}
.rl-value {{
    font-family: {FONT_DISPLAY}; font-weight: 700; font-size: 34px;
    text-align: right; line-height: 1.1; margin: 0;
}}
.rl-value-label {{
    font-family: {FONT_MONO}; font-size: 10px; letter-spacing: .12em;
    text-transform: uppercase; color: {FG_FAINT}; text-align: right; margin: 0;
}}
/* the team-colour bar that opens each row */
.rl-teambar {{ width: 4px; border-radius: 2px; height: 46px; margin-top: 6px; }}

/* ---- Metric grid (inside player detail) ---------------------------------- */
.rl-metric {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 6px;
    padding: 10px 12px;
}}
.rl-metric-k {{
    font-family: {FONT_MONO}; font-size: 10px; letter-spacing: .1em;
    text-transform: uppercase; color: {FG_FAINT}; margin: 0 0 3px 0;
}}
.rl-metric-v {{ font-family: {FONT_MONO}; font-size: 19px; color: {FG}; margin: 0; }}

/* ---- Suggestion chips (Ask page) ----------------------------------------- */
[class*="st-key-chip_"] button {{
    background: {SURFACE}; border: 1px solid {LINE}; color: {FG_MUTED};
    font-family: {FONT_TEXT}; font-size: 13px; font-weight: 400;
    border-radius: 999px; padding: 4px 14px;
}}
[class*="st-key-chip_"] button:hover {{
    border-color: {ACCENT}; color: {FG}; background: {SURFACE_2};
}}

/* ---- Expanders ----------------------------------------------------------- */
[data-testid="stExpander"] details {{
    border: 1px solid {LINE}; border-radius: 6px; background: {SURFACE};
}}
[data-testid="stExpander"] summary {{ font-family: {FONT_TEXT}; font-size: 14px; }}

/* ---- Tables -------------------------------------------------------------- */
[data-testid="stDataFrame"] {{ border: 1px solid {LINE}; border-radius: 6px; }}

/* ---- Footer -------------------------------------------------------------- */
.rl-footer {{
    font-family: {FONT_MONO}; font-size: 11px; color: {FG_FAINT};
    border-top: 1px solid {LINE}; margin-top: 40px; padding-top: 14px;
    display: flex; justify-content: space-between;
}}
</style>
"""


def inject() -> None:
    """Apply the stylesheet. Call once, from the entrypoint, before pages run."""
    st.markdown(_CSS, unsafe_allow_html=True)


# --- Small render helpers (keep HTML out of the page modules) ---------------

def eyebrow(text: str) -> None:
    st.markdown(f'<p class="rl-eyebrow">{text}</p>', unsafe_allow_html=True)


def section(title: str, subtitle: str | None = None, eyebrow_text: str | None = None) -> None:
    """Standard page/section heading: optional eyebrow, title, optional subtitle."""
    html = ""
    if eyebrow_text:
        html += f'<p class="rl-eyebrow">{eyebrow_text}</p>'
    html += f'<p class="rl-h2">{title}</p>'
    if subtitle:
        html += f'<p class="rl-sub">{subtitle}</p>'
    st.markdown(html, unsafe_allow_html=True)


def rule() -> None:
    st.markdown('<hr class="rl-rule">', unsafe_allow_html=True)
