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

import re as _re

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

FONT_DISPLAY = "'Big Shoulders Display', 'Big Shoulders', 'Arial Narrow', sans-serif"
FONT_TEXT    = "'Work Sans', -apple-system, 'Segoe UI', sans-serif"
FONT_MONO    = "'Geist Mono', ui-monospace, 'SFMono-Regular', monospace"


def team_accent(team_abbr: str) -> str:
    """Contrast-corrected club colour as CSS hex — the same colour the card uses."""
    return _hex(_display_accent(TEAM_COLORS.get(str(team_abbr), DEFAULT_TEAM_COLOR)))


def value_color(v: float) -> str:
    """Accent for a positive value score, muted for a negative one."""
    return ACCENT if v >= 0 else FG_MUTED


# The display/text/mono faces are declared below in FONT_*, but declaring a
# family does not LOAD it. Until this link existed the stylesheet asked for
# 'Big Shoulders' and the browser, never having been given the file, silently
# fell back to Arial Narrow — so the wordmark had never once rendered in the
# brand face. The TTFs under static/fonts/ are for PIL (the PNG cards); a browser
# cannot use them from there, and Streamlit Cloud has no static asset route we
# can rely on.
#
# Google Fonts rather than base64-inlining the bundled TTFs: the three faces are
# 193 KB each, so inlining would add ~770 KB of base64 to the stylesheet on every
# render for zero visual gain. The families are identical to the bundled files,
# so the app and the downloadable cards stay visually in sync.
_FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Big+Shoulders+Display:wght@400;600;700&'
    'family=Work+Sans:wght@400;500;600&'
    'family=Geist+Mono:wght@400;500&display=swap">'
)

_CSS = f"""
<style>
/* ---- Chrome removal -----------------------------------------------------
   toolbarMode="minimal" hides the hamburger but NOT the Fork / GitHub badge
   that Streamlit Cloud renders into stToolbarActions — verified on the
   deployed app, where it stayed visible top-right. Hide it explicitly.     */
#MainMenu, footer, header [data-testid="stDecoration"] {{ display: none !important; }}
[data-testid="stStatusWidget"] {{ display: none !important; }}
[data-testid="stToolbarActions"] {{ display: none !important; }}

/* ---- Page rhythm ---------------------------------------------------------
   Streamlit renders a FIXED toolbar band (~3.75rem) above the app. At the old
   1.2rem top padding the band sat on top of the first row of content, clipping
   the wordmark and the season dropdown. The band is emptied by the rules above,
   so collapse it to zero height AND give the content real room — doing only one
   of the two leaves either an overlap or a dead gap.                          */
header[data-testid="stHeader"] {{ height: 0; min-height: 0; background: transparent; }}
.block-container {{ padding-top: 3.2rem; padding-bottom: 4rem; max-width: 1180px; }}

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

/* ---- Nav on narrow screens ----------------------------------------------
   st.columns stacks vertically below ~640px, which turned the nav into seven
   full-width rows stacked above the content — a wall to scroll past before
   reaching the page on a phone. Overriding the stack to a wrapping flex row
   gives two compact rows instead. Streamlit's own overflow behaviour is left
   alone; this only changes how the columns lay out.                          */
@media (max-width: 640px) {{
    [class*="st-key-rl_nav"] [data-testid="stHorizontalBlock"] {{
        flex-direction: row !important;
        flex-wrap: wrap !important;
        gap: 2px 14px !important;
    }}
    [class*="st-key-rl_nav"] [data-testid="stColumn"] {{
        width: auto !important;
        flex: 0 0 auto !important;
        min-width: 0 !important;
    }}
    [class*="st-key-rl_nav"] [data-testid="stPageLink"] a {{ padding: 4px 0; }}
}}

/* ---- App header (brand + global season) ----------------------------------
   Stacked lockup: the league as a small tracked eyebrow over the product name,
   so "RosterLab" carries the weight and reads as a logo rather than as a line
   of running text. The eyebrow takes the accent; the name stays white.        */
.rl-brand-eyebrow {{
    font-family: {FONT_DISPLAY}; font-weight: 700; font-size: 15px;
    letter-spacing: .22em; color: {ACCENT}; line-height: 1; margin: 0 0 1px 0;
}}
.rl-brand {{
    font-family: {FONT_DISPLAY}; font-weight: 700; font-size: 42px;
    letter-spacing: -.005em; color: {FG}; line-height: 1.02; margin: 0;
}}
/* Two-line tagline, below the lockup and the season control so it spans the
   full width. Line 1 is deliberately smaller than the 42px wordmark: at equal
   size the two compete and neither reads as the brand.                        */
.rl-tagline {{
    font-family: {FONT_DISPLAY}; font-weight: 600; font-size: 22px;
    color: {FG}; line-height: 1.15; margin: 10px 0 0 0;
}}
.rl-tagline-sub {{
    font-family: {FONT_TEXT}; font-size: 13px; color: {FG_MUTED};
    margin: 3px 0 0 0; line-height: 1.45;
}}
@media (max-width: 700px) {{
    .rl-brand {{ font-size: 32px; }}
    .rl-tagline {{ font-size: 18px; }}
}}

/* ---- Section headers ----------------------------------------------------- */
.rl-eyebrow {{
    font-family: {FONT_MONO}; font-size: 12px; letter-spacing: .16em;
    text-transform: uppercase; color: {FG_FAINT}; margin: 0 0 6px 0;
}}
/* The dated eyebrow on This week — the most prominent thing after the wordmark,
   because "which week am I looking at?" is the first question a returning
   visitor has. Larger, accented and more tracked than the section eyebrow.    */
.rl-eyebrow-hero {{
    font-family: {FONT_MONO}; font-size: 17px; font-weight: 500;
    letter-spacing: .14em; text-transform: uppercase; color: {ACCENT};
    margin: 0 0 10px 0;
}}
@media (max-width: 700px) {{ .rl-eyebrow-hero {{ font-size: 14px; letter-spacing: .1em; }} }}
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
    /* Truncate rather than run into the value column when the window is narrow. */
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.rl-context {{
    font-family: {FONT_TEXT}; font-size: 13px; color: {FG_MUTED}; margin: 1px 0 0 0;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
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

/* The per-player "Detail" expander sits inside the row container, in NORMAL
   FLOW beneath the name/value line. It is made unobtrusive by removing its
   panel chrome and shrinking it — never by pulling it up with a negative
   margin, which overlapped the team-colour bar and collided with the next row.
   Subtlety comes from weight, not from overlap.                              */
[class*="st-key-prow_"] [data-testid="stExpander"] {{
    margin-top: 0; margin-bottom: 0;
}}
[class*="st-key-prow_"] [data-testid="stExpander"] details {{
    border: none !important; background: transparent !important;
}}
/* Streamlit animates the expander by writing an inline height onto <details>.
   Restyling the summary makes that measurement stale, so an opened row stayed
   pinned at its collapsed height (18.8px) while its content rendered outside
   the box and over the next row. Force the open state to size to its content —
   an inline style loses to !important.                                       */
[class*="st-key-prow_"] [data-testid="stExpander"] details[open] {{
    height: auto !important;
    overflow: visible !important;
}}
[class*="st-key-prow_"] [data-testid="stExpander"] summary {{
    padding: 0 0 2px 0 !important; min-height: 0;
    font-size: 12px; line-height: 1.2; color: {FG_FAINT};
    width: max-content;            /* only as wide as the chevron + label */
    white-space: nowrap;           /* never wraps into the row above/below */
}}
[class*="st-key-prow_"] [data-testid="stExpander"] summary:hover {{ color: {FG}; }}
/* Expanded body gets its air back. */
[class*="st-key-prow_"] [data-testid="stExpanderDetails"] {{
    padding-top: 12px; padding-left: 0;
}}

/* The row's own text block needs a little breathing room above the expander so
   the two never touch, and the team bar spans only the name/value line. */
[class*="st-key-prow_"] .rl-name {{ margin-bottom: 0; }}
[class*="st-key-prow_"] .rl-context {{ margin-bottom: 4px; }}

/* ---- Stat cards (landing page headline figures) -------------------------- */
.rl-stat {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 8px;
    padding: 14px 16px; height: 100%;
}}
.rl-stat-k {{
    font-family: {FONT_MONO}; font-size: 10px; letter-spacing: .14em;
    text-transform: uppercase; color: {FG_FAINT}; margin: 0 0 6px 0;
}}
.rl-stat-v {{
    font-family: {FONT_DISPLAY}; font-weight: 700; font-size: 27px;
    line-height: 1.1; margin: 0 0 4px 0;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.rl-stat-c {{ font-family: {FONT_TEXT}; font-size: 12px; color: {FG_MUTED}; margin: 0; }}

/* ---- Headline sentence --------------------------------------------------- */
.rl-lede {{
    font-family: {FONT_TEXT}; font-size: 17px; line-height: 1.55;
    color: {FG}; margin: 0 0 18px 0; max-width: 62ch;
}}
.rl-lede b {{ color: {ACCENT}; font-weight: 600; }}

/* ---- Depth grid (Teams) -------------------------------------------------- */
.rl-depth {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 6px;
    padding: 10px 12px; height: 100%;
}}
.rl-depth-pos {{
    font-family: {FONT_MONO}; font-size: 10px; letter-spacing: .12em;
    text-transform: uppercase; color: {FG_FAINT}; margin: 0 0 4px 0;
}}
.rl-depth-n {{
    font-family: {FONT_DISPLAY}; font-weight: 700; font-size: 22px;
    color: {FG}; margin: 0; line-height: 1.1;
}}
.rl-depth-best {{
    font-family: {FONT_TEXT}; font-size: 12px; color: {FG_MUTED}; margin: 3px 0 0 0;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.rl-depth-thin .rl-depth-n {{ color: {NEGATIVE}; }}

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

/* ---- Narrow windows ------------------------------------------------------
   Streamlit columns stay side by side well below desktop widths, so the row's
   type has to step down or the name crowds the value score.                  */
@media (max-width: 700px) {{
    .rl-name  {{ font-size: 20px; }}
    .rl-value {{ font-size: 26px; }}
    .rl-context {{ font-size: 12px; }}
    .block-container {{ padding-left: 1rem; padding-right: 1rem; }}
}}

/* ---- Footer -------------------------------------------------------------- */
.rl-footer {{
    font-family: {FONT_MONO}; font-size: 11px; color: {FG_FAINT};
    border-top: 1px solid {LINE}; margin-top: 40px; padding-top: 14px;
    display: flex; justify-content: space-between; gap: 20px; flex-wrap: wrap;
}}
.rl-footer a {{ color: {FG_MUTED}; text-decoration: underline; }}
.rl-footer a:hover {{ color: {ACCENT}; }}
</style>
"""


def inject() -> None:
    """Apply the fonts and the stylesheet. Call once, from the entrypoint.

    st.html, NOT st.markdown(unsafe_allow_html=True). st.markdown runs the string
    through a Markdown parser before sanitizing it, and the parser mangles CSS:
    measured in the browser, a 12,775-character stylesheet arrived as 1,149
    characters, silently truncated at the first multi-line `/* ... */` comment.
    Everything declared after that point — the nav bar, player rows, the brand
    lockup, every .rl- class — simply had no styling, with no error raised
    anywhere and no visible sign except that the page looked wrong.

    st.html inserts the string as raw HTML with no Markdown pass, so the whole
    stylesheet survives. Two calls (link, then style) rather than one
    concatenated string: combining them was separately observed to make the
    sanitizer drop the <style> element entirely.
    """
    st.html(_FONT_LINK)
    st.html(_minify(_CSS))


def _minify(css: str) -> str:
    """Strip comments and force our font sizes to win.

    Comments: dropped because they are for whoever reads this file, not for the
    browser.

    font-size !important: this is the fix for an app-wide typography bug, not a
    style preference. Streamlit ships

        .st-emotion-cache-<hash> p, ... {font-size: inherit}

    at specificity (0,1,1). Every text class here is a single class selector at
    (0,1,0), so Streamlit won every time and each one inherited the container's
    16px: .rl-h2 section headings rendered at 16px instead of 30, .rl-eyebrow at
    16 instead of 12, .rl-sub at 16 instead of 14. The designed type hierarchy had
    never actually reached the page on any <p>-based element; only <div>-based
    ones such as .rl-footer were unaffected.

    Raising specificity by hand would mean touching every selector and would break
    again on the next emotion-hash change. Only font-size is contested, so only
    font-size is forced.
    """
    css = _re.sub(r"/\*.*?\*/", "", css, flags=_re.S)
    return _re.sub(r"font-size:\s*([^;!}]+?)\s*;", r"font-size: \1 !important;", css)


# --- Small render helpers (keep HTML out of the page modules) ---------------

def eyebrow(text: str) -> None:
    st.markdown(f'<p class="rl-eyebrow">{text}</p>', unsafe_allow_html=True)


def _bold(text: str) -> str:
    """Render **emphasis** as <b>, since these strings go out as raw HTML.

    Callers write subtitles in the same Markdown style used everywhere else in
    the app, but this helper emits HTML directly, so the asterisks were reaching
    the page literally — "**Positions are left blank**" appeared verbatim on the
    landing page and on Teams.
    """
    return _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def section(title: str, subtitle: str | None = None, eyebrow_text: str | None = None) -> None:
    """Standard page/section heading: optional eyebrow, title, optional subtitle."""
    html = ""
    if eyebrow_text:
        html += f'<p class="rl-eyebrow">{eyebrow_text}</p>'
    html += f'<p class="rl-h2">{_bold(title)}</p>'
    if subtitle:
        html += f'<p class="rl-sub">{_bold(subtitle)}</p>'
    st.markdown(html, unsafe_allow_html=True)


def rule() -> None:
    st.markdown('<hr class="rl-rule">', unsafe_allow_html=True)
