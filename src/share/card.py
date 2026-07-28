"""
src/share/card.py — deterministic PNG card rendering, zero API cost.

render_player_card()   -> 1080x1350 portrait PNG bytes (social share)
render_og_image()      -> 1200x630  OG branded image bytes (manual use)

render_leaderboard_card() is stubbed — reuses helpers below when implemented.

Note: real Open Graph link-unfurl previews require a static HTML landing page
outside Streamlit (SPA injection into <body> is invisible to crawlers). Out of scope v1.
"""

from __future__ import annotations

import io
import re
import textwrap
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # non-interactive backend, required for Streamlit
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CARD_W, CARD_H = 1080, 1350
OG_W,   OG_H   = 1200,  630

APP_URL = "nwsl-rosterlab.streamlit.app"

DEFAULT_TEAM_COLOR = "#333333"

# Keys match ASA team_abbreviation values (verified against nwsl_teams.parquet).
# Corrections vs. user-provided dict: ACFC -> LA (Angel City), GFC -> NJY (Gotham).
TEAM_COLORS: dict[str, str] = {
    "LA":  "#E6447E",   # Angel City FC
    "BAY": "#0A4D8C",   # Bay FC
    "BOS": "#0D1B2A",   # Boston Legacy FC (same abbrev as historical Breakers)
    "CHI": "#0B132B",   # Chicago Stars FC
    "DEN": "#6A1B9A",   # Denver Summit FC (placeholder — update when branding finalised)
    "HOU": "#FF6B00",   # Houston Dash
    "KC":  "#63B1E5",   # Kansas City Current (same abbrev as historical FC Kansas City)
    "LOU": "#4B1E78",   # Racing Louisville FC
    "NC":  "#0046AD",   # North Carolina Courage
    "NJY": "#231F20",   # NJ/NY Gotham FC
    "ORL": "#633492",   # Orlando Pride
    "POR": "#9D2235",   # Portland Thorns FC
    "SD":  "#1B1F3B",   # San Diego Wave FC
    "SEA": "#1E3A8A",   # Seattle Reign FC
    "UTA": "#FFB81C",   # Utah Royals FC
    "WAS": "#C8102E",   # Washington Spirit
}

POSITION_LABELS: dict[str, str] = {
    "ST": "Striker",
    "W":  "Winger",
    "AM": "Attacking Mid",
    "CM": "Central Mid",
    "DM": "Defensive Mid",
    "FB": "Full Back",
    "CB": "Center Back",
}

ACTION_COLS = [
    "ga_shooting_p90",
    "ga_dribbling_p90",
    "ga_passing_p90",
    "ga_receiving_p90",
    "ga_interrupting_p90",
    "ga_fouling_p90",
]
ACTION_DISPLAY: dict[str, str] = {
    "ga_shooting_p90":    "Shooting",
    "ga_dribbling_p90":   "Dribbling",
    "ga_passing_p90":     "Passing",
    "ga_receiving_p90":   "Receiving",
    "ga_interrupting_p90": "Defending",
    "ga_fouling_p90":     "Fouling",
}

# Colour palette
BG_COLOR      = (13,  31,  45,  255)   # dark navy
TEXT_PRIMARY  = (255, 255, 255, 255)   # white
TEXT_SECONDARY = (180, 200, 220, 255)  # muted blue-white
ACCENT_POS    = "#4FC3F7"              # positive bar
ACCENT_NEG    = "#E57373"              # negative bar
ACCENT_AVG    = "#FFB74D"              # position-average reference line


# ---------------------------------------------------------------------------
# Font helpers  (DejaVuSans from matplotlib's bundled copy — identical on
# Windows + Linux; never arial.ttf or load_default())
# ---------------------------------------------------------------------------

def _get_font(size: int) -> ImageFont.FreeTypeFont:
    path = fm.findfont(fm.FontProperties(family="DejaVu Sans"))
    return ImageFont.truetype(path, size)


def _get_bold_font(size: int) -> ImageFont.FreeTypeFont:
    path = fm.findfont(fm.FontProperties(family="DejaVu Sans", weight="bold"))
    return ImageFont.truetype(path, size)


# ---------------------------------------------------------------------------
# "Broadcast Dossier" type system  (see assets/CARD_DESIGN_PHILOSOPHY.md)
#
# Three roles, three faces — bundled in assets/fonts so Linux (Streamlit Cloud)
# renders identically to Windows. All OFL-licensed; licences ship alongside.
#   display : Big Shoulders  — condensed, athletic. Names and hero figures.
#   text    : Work Sans      — grotesque. Body copy and labels.
#   mono    : Geist Mono     — tabular figures and micro-labels.
# Falls back to DejaVu if the directory is ever missing, so the card can never
# hard-fail on a font lookup.
# ---------------------------------------------------------------------------

_FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"


def _load(name: str, size: int, fallback_bold: bool = False) -> ImageFont.FreeTypeFont:
    path = _FONT_DIR / name
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return _get_bold_font(size) if fallback_bold else _get_font(size)


def _f_display(size: int) -> ImageFont.FreeTypeFont:
    return _load("BigShoulders-Bold.ttf", size, fallback_bold=True)


def _f_text(size: int) -> ImageFont.FreeTypeFont:
    return _load("WorkSans-Regular.ttf", size)


def _f_text_bold(size: int) -> ImageFont.FreeTypeFont:
    return _load("WorkSans-Bold.ttf", size, fallback_bold=True)


def _f_mono(size: int) -> ImageFont.FreeTypeFont:
    return _load("GeistMono-Regular.ttf", size)


# --- Palette ---------------------------------------------------------------
# One accent only (the club colour). Everything else is a three-step neutral
# ladder. No second hue: a below-average value is signalled by direction, not
# by alarm — hence no red anywhere.
INK          = (8,  16,  24, 255)    # near-black navy base
TEXT_1       = (255, 255, 255, 255)  # primary
TEXT_2       = (150, 170, 190, 255)  # secondary
TEXT_3       = (95, 118, 140, 255)   # tertiary / micro-labels
RULE         = (34, 50, 66, 255)     # hairline
BAR_NEG      = (68, 88, 108, 255)    # below zero — muted, never red
AVG_TICK     = (128, 150, 172, 255)  # positional-average marker

MARGIN = 72


def _relative_luminance(rgb) -> float:
    def _lin(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (_lin(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _display_accent(hex_color: str, target: float = 4.5) -> tuple:
    """Return the club colour raised to a legible tint against the card base.

    Eleven of sixteen NWSL brand colours are darker than this card's near-black
    ground — six of them are effectively invisible on it. Rather than abandon
    club identity, we hold hue and saturation and lift ONLY lightness until the
    colour clears a readable contrast ratio. Portland still reads red, Orlando
    still reads purple; both become legible. This is standard practice for
    broadcast graphics over dark backgrounds.
    """
    import colorsys
    rgb = _hex_rgb(hex_color)
    if _contrast(rgb, INK[:3]) >= target:
        return rgb + (255,)
    h, l, s = colorsys.rgb_to_hls(*[c / 255 for c in rgb])
    # Achromatic brands (Gotham's black/white) carry no meaningful hue — the
    # residual tint in a near-black is noise, and amplifying it invents a colour
    # the club does not own. Resolve those to a bright neutral instead.
    if s < 0.18:
        return (232, 238, 245, 255)
    s = max(s, 0.45)                      # keep it chromatic, never washed grey
    for step in range(1, 101):
        cand_l = min(0.92, l + step * 0.01)
        r, g, b = colorsys.hls_to_rgb(h, cand_l, s)
        cand = (int(r * 255), int(g * 255), int(b * 255))
        if _contrast(cand, INK[:3]) >= target:
            return cand + (255,)
    return (235, 240, 245, 255)           # last resort: near-white


def _tracked(draw, xy, text, font, fill, tracking: int = 0):
    """Draw text with letter-spacing (PIL has no native tracking).

    Used for uppercase micro-labels, which need air to read as structural
    headings rather than shouting. Returns the x advance."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking
    return x - xy[0]


def _tracked_width(text: str, font, tracking: int = 0) -> float:
    return sum(font.getlength(c) for c in text) + tracking * max(len(text) - 1, 0)


def _rule(draw, y: int, x0: int = MARGIN, x1: int = CARD_W - MARGIN, fill=RULE):
    """A hairline. Separation is done with rules and silence, never boxes."""
    draw.line([(x0, y), (x1, y)], fill=fill, width=1)


# ---------------------------------------------------------------------------
# Hex -> RGB tuple helper
# ---------------------------------------------------------------------------

def _hex_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ---------------------------------------------------------------------------
# Headline hook  (deterministic, first-match wins)
# ---------------------------------------------------------------------------

def headline_hook(row: dict, cohort: pd.DataFrame, season: str) -> str:
    """Return the sharpest single claim for this player. No LLM."""
    rank    = int(row["_rank"])
    n       = len(cohort)
    pct     = (1 - (rank - 1) / max(n - 1, 1)) * 100   # higher = better
    pos     = POSITION_LABELS.get(row["position"], row["position"])
    age     = row.get("age")
    low_min = float(row["minutes_played"]) < float(cohort["minutes_played"].median())

    if rank == 1:
        return f"#1 {pos} in the NWSL by Value"
    if pct >= 85 and low_min:
        return f"Undervalued: Top {round(100 - pct)}% {pos} on limited minutes"
    if age is not None and age <= 22 and pct >= 80:
        return f"Rising: Top {round(100 - pct)}% {pos} at just {int(age)}"
    if pct >= 95:
        return f"Top 5% {pos}, {season}"
    if pct >= 90:
        return f"Top 10% {pos}, {season}"
    if rank <= 3:
        return f"Top {rank} {pos} by Value, {season}"
    if pct >= 75:
        return f"Top {round(100 - pct)}% {pos}, {season}"
    return f"{pos} · Value score {row['value_score']:+.2f} ({rank} of {n})"


# ---------------------------------------------------------------------------
# Private: text helpers
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """Replace em/en dashes with comma-space; fix space-before-comma; collapse double spaces."""
    result = str(text).replace("—", ", ").replace("–", ", ")
    result = re.sub(r'\s+,', ',', result)
    return re.sub(r'  +', ' ', result)


def _trim_sentences(text: str, max_sentences: int = 3) -> str:
    """Keep first max_sentences sentences. Normalizes whitespace before splitting."""
    normalized = re.sub(r'\s+', ' ', text.strip())
    sentences = re.split(r'(?<=[.!?])\s+', normalized)
    return " ".join(sentences[:max_sentences])


def _verdict(row: dict, cohort: pd.DataFrame, pos_label: str) -> str:
    """One bold quotable sentence, ≤8 words. Fully deterministic from rank + actions."""
    rank  = int(row["_rank"])
    n     = len(cohort)
    pct   = (1 - (rank - 1) / max(n - 1, 1)) * 100

    action_vals = {c: float(row.get(c, 0.0)) for c in ACTION_COLS}
    top_col   = max(action_vals, key=action_vals.get)
    top_label = ACTION_DISPLAY[top_col].lower()
    bot_col   = min(action_vals, key=action_vals.get)
    bot_label = ACTION_DISPLAY[bot_col].lower()

    if pct >= 95:
        return f"Elite {pos_label.lower()}, leads in {top_label}."
    if pct >= 80:
        return f"Top-tier {pos_label.lower()}, driven by {top_label}."
    if pct >= 60:
        return f"Above-average value, best at {top_label}."
    if pct >= 40:
        return f"Mid-tier; {top_label} is her best asset."
    return f"Below average; {top_label} strength, {bot_label} weakness."


# ---------------------------------------------------------------------------
# Private: action bar chart -> PIL Image
# ---------------------------------------------------------------------------

def _action_bar_chart(row: dict, cohort: pd.DataFrame) -> Image.Image:
    """Render 6-action horizontal bar chart. Returns PIL Image. Closes figure."""
    vals   = [float(row.get(col, 0.0)) for col in ACTION_COLS]
    avgs   = [float(cohort[col].mean()) for col in ACTION_COLS]
    labels = [ACTION_DISPLAY[c] for c in ACTION_COLS]
    colors = [ACCENT_NEG if v < 0 else ACCENT_POS for v in vals]

    bg = tuple(c / 255 for c in BG_COLOR[:3])
    fig, ax = plt.subplots(figsize=(9.5, 3.2), facecolor=bg)
    ax.set_facecolor(bg)

    y_pos = range(len(labels))
    ax.barh(y_pos, vals, color=colors, height=0.55, zorder=2)

    # Position-average markers
    for i, avg in enumerate(avgs):
        ax.plot([avg, avg], [i - 0.35, i + 0.35],
                color=ACCENT_AVG, linewidth=1.5, zorder=3)

    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels, color="white", fontsize=13,
                       fontfamily="DejaVu Sans")
    ax.tick_params(axis="x", colors="#aaaaaa", labelsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color("#444444")
    ax.axvline(0, color="#555555", linewidth=0.8, zorder=1)
    ax.set_xlabel("g+ per 90", color="#aaaaaa", fontsize=11, fontfamily="DejaVu Sans")
    ax.set_title("What drives her value", color="white", fontsize=13,
                 fontfamily="DejaVu Sans", pad=6)

    # Legend hint
    from matplotlib.lines import Line2D
    ax.legend(
        handles=[Line2D([0], [0], color=ACCENT_AVG, linewidth=1.5)],
        labels=["position avg"],
        loc="lower right",
        fontsize=10,
        framealpha=0,
        labelcolor="#aaaaaa",
    )

    fig.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight",
                facecolor=fig.get_facecolor(), dpi=110)
    plt.close(fig)   # prevent memory leaks on Streamlit reruns
    buf.seek(0)
    return Image.open(buf).copy()   # .copy() so BytesIO can close safely


# ---------------------------------------------------------------------------
# Private: shared header / footer drawing helpers (reusable for leaderboard)
# ---------------------------------------------------------------------------

def _draw_header(draw: ImageDraw.ImageDraw, img: Image.Image,
                 team_color: str, hook_text: str,
                 header_h: int = 220) -> None:
    """Fill header band with team color and draw the headline hook."""
    rgb = _hex_rgb(team_color)
    draw.rectangle([(0, 0), (CARD_W, header_h)], fill=rgb)

    font = _get_bold_font(52)
    wrapped = textwrap.fill(hook_text, width=28)
    # Center text vertically in header
    bbox = draw.textbbox((0, 0), wrapped, font=font)
    text_h = bbox[3] - bbox[1]
    y = (header_h - text_h) // 2
    draw.text((54, y), wrapped, font=font, fill=(255, 255, 255, 255))


def _draw_footer(draw: ImageDraw.ImageDraw,
                 footer_y: int, footer_h: int = 80) -> None:
    """Draw branding footer with app URL."""
    draw.rectangle([(0, footer_y), (CARD_W, footer_y + footer_h)],
                   fill=(8, 20, 30, 255))
    font = _get_font(28)
    text = f"NWSL RosterLab  ·  {APP_URL}"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    x = (CARD_W - text_w) // 2
    y = footer_y + (footer_h - (bbox[3] - bbox[1])) // 2
    draw.text((x, y), text, font=font, fill=(160, 190, 210, 255))


def _draw_action_bars(draw, row: dict, cohort: pd.DataFrame,
                      y0: int, accent: tuple, row_h: int = 46) -> int:
    """Draw the g+ breakdown as direct-labelled bars. Returns the bottom y.

    Data is drawn, not plotted: no axes, no ticks, no gridlines, no legend box.
    Each bar is labelled at its head (action) and tail (value) so it reads like
    a sentence, and the positional average is a single hairline tick for anyone
    who looks for it.
    """
    cols = ["ga_shooting_p90", "ga_dribbling_p90", "ga_passing_p90",
            "ga_receiving_p90", "ga_interrupting_p90", "ga_fouling_p90"]

    vals = [float(row.get(c, 0.0)) for c in cols]
    avgs = [float(cohort[c].mean()) if c in cohort.columns else 0.0 for c in cols]
    labels = [ACTION_DISPLAY.get(c, c.replace("ga_", "").replace("_p90", "").title()) for c in cols]

    # Sort strongest-first: the eye should meet her best trait immediately.
    order = sorted(range(len(cols)), key=lambda i: vals[i], reverse=True)

    LABEL_R = MARGIN + 216          # labels right-align here
    BAR_X0  = LABEL_R + 28
    BAR_X1  = CARD_W - MARGIN - 84  # leave a gutter for the value text

    lo = min(0.0, min(vals), min(avgs))
    hi = max(0.0, max(vals), max(avgs))
    span = (hi - lo) or 1.0
    zero_x = BAR_X0 + (0.0 - lo) / span * (BAR_X1 - BAR_X0)

    lab_font = _f_text(24)
    val_font = _f_mono(21)
    BAR_H = 20

    y = y0
    for i in order:
        v, a, lab = vals[i], avgs[i], labels[i]
        cy = y + row_h // 2

        # Action label, right-aligned into the bar column
        lw = lab_font.getlength(lab)
        draw.text((LABEL_R - lw, cy - 15), lab, font=lab_font, fill=TEXT_2)

        # The bar itself
        vx = BAR_X0 + (v - lo) / span * (BAR_X1 - BAR_X0)
        x_a, x_b = (zero_x, vx) if v >= 0 else (vx, zero_x)
        if abs(x_b - x_a) < 2:                      # keep near-zero visible
            x_b = x_a + 2
        draw.rectangle([(x_a, cy - BAR_H // 2), (x_b, cy + BAR_H // 2)],
                       fill=accent if v >= 0 else BAR_NEG)

        # Positional-average tick
        ax = BAR_X0 + (a - lo) / span * (BAR_X1 - BAR_X0)
        draw.rectangle([(ax - 1, cy - BAR_H // 2 - 6), (ax + 1, cy + BAR_H // 2 + 6)],
                       fill=AVG_TICK)

        # Value, tail of the bar. Values that round to nothing print as a plain
        # 0.00 — a signed "-0.00" is a rendering artefact, not a measurement.
        vtxt = "0.00" if abs(v) < 0.005 else f"{v:+.2f}"
        draw.text((BAR_X1 + 16, cy - 13), vtxt, font=val_font,
                  fill=TEXT_1 if v >= 0 else TEXT_3)
        y += row_h

    # Zero baseline, drawn last so it sits above the bars
    draw.line([(zero_x, y0 - 2), (zero_x, y - row_h + row_h - 2)],
              fill=(90, 112, 134, 255), width=1)
    return y


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_player_card(
    player_row: dict,
    cohort: pd.DataFrame,
    season: str,
    insight_text: Optional[str] = None,
) -> bytes:
    """
    Render a 1080x1350 PNG player card and return PNG bytes.

    player_row  — dict from a ranked DataFrame row; must include '_rank'.
    cohort      — within-position ranked DataFrame with '_rank' already added
                  (same as league_ranked in app.py). Used for percentile + chart averages.
    season      — season label string (e.g. "2025").
    insight_text — pre-resolved scout take (cached LLM or fallback). Never calls API.
    """
    assert "_rank" in cohort.columns, "cohort must have '_rank'; pass league_ranked from app.py"

    row = player_row

    # Resolved values
    team_abbr  = str(row.get("team_abbreviation", ""))
    team_color = TEAM_COLORS.get(team_abbr, DEFAULT_TEAM_COLOR)
    pos_label  = POSITION_LABELS.get(str(row.get("position", "")), str(row.get("position", "")))
    rank       = int(row["_rank"])
    n_cohort   = len(cohort)
    pct        = (1 - (rank - 1) / max(n_cohort - 1, 1)) * 100
    # NaN is truthy, so a bare `if row.get("age")` sends NaN into int() and
    # raises — ~10% of players have no Wikidata birthdate match.
    _age = row.get("age")
    age_str = f"  ·  Age {int(_age)}" if pd.notna(_age) else ""
    hook       = headline_hook(row, cohort, season)

    # Fallback insight (2-3 sentences from numbers; used when LLM unavailable)
    if not insight_text:
        action_vals = {c: float(row.get(c, 0.0)) for c in [
            "ga_shooting", "ga_dribbling", "ga_passing",
            "ga_receiving", "ga_interrupting", "ga_fouling"]}
        sorted_actions = sorted(action_vals.items(), key=lambda x: x[1], reverse=True)
        top_col, top_val = sorted_actions[0]
        bot_col, bot_val = sorted_actions[-1]
        top_label = ACTION_DISPLAY.get(top_col + "_p90", top_col.replace("ga_", "").replace("_", " ").title())
        bot_label = ACTION_DISPLAY.get(bot_col + "_p90", bot_col.replace("ga_", "").replace("_", " ").title())
        ga_p90    = float(row.get("goals_added_p90", 0))
        avg_ga    = float(cohort["goals_added_p90"].mean())
        xga_p90   = float(row.get("xga_p90", 0))
        avg_xga   = float(cohort["xga_p90"].mean())
        standing = (
            "the best in the league at her position" if rank == 1
            else f"in the top {100 - round(pct):.0f}% of all {pos_label}s"
        )
        insight_text = (
            f"Ranks #{rank} of {n_cohort} {pos_label}s on g+/90 "
            f"({ga_p90:.2f} vs. position avg {avg_ga:.2f}), placing her {standing}. "
            f"Her strongest action type is {top_label.lower()} ({top_val:+.2f} g+), "
            f"while {bot_label.lower()} is her weakest ({bot_val:+.2f} g+). "
            f"xG+xA/90 of {xga_p90:.2f} compares to a position average of {avg_xga:.2f}."
        )

    hook         = _clean_text(hook)
    insight_text = _clean_text(insight_text)

    # Auto-fit the scout take to whatever vertical space the layout leaves.
    # Floor of 22px: below that it dissolves at feed size, which the philosophy
    # forbids — so we trim sentences rather than shrink further.
    def _fit_take(text: str, max_w_px: int, max_h_px: int) -> tuple[ImageFont.FreeTypeFont, str]:
        """Return (font, wrapped_text) that fits within max_w_px x max_h_px."""
        _tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        for n_sent in (3, 2):
            candidate = _trim_sentences(text, max_sentences=n_sent)
            for size in range(30, 21, -1):
                font = _f_text(size)
                chars_per_line = max(20, int(max_w_px / font.getlength("n")))
                wrapped = textwrap.fill(candidate, width=chars_per_line)
                bb = _tmp.textbbox((0, 0), wrapped, font=font, spacing=10)
                if (bb[3] - bb[1]) <= max_h_px:
                    return font, wrapped
        font = _f_text(22)
        chars_per_line = max(20, int(max_w_px / font.getlength("n")))
        return font, textwrap.fill(_trim_sentences(text, max_sentences=2), width=chars_per_line)

    insight_trimmed = _trim_sentences(insight_text, max_sentences=3)

    # Stat strip text
    avg_wga  = float(cohort["weighted_ga_p90"].mean())
    avg_xga  = float(cohort["xga_p90"].mean())
    mins_int = int(row.get("minutes_played", 0))
    matches  = round(mins_int / 90)
    stat_text = (
        f"Minutes: {mins_int:,} (~{matches} full matches)"
        f"   Wtd g+/90: {float(row.get('weighted_ga_p90', 0)):.2f} (pos avg {avg_wga:.2f})"
        f"   xG+xA/90: {float(row.get('xga_p90', 0)):.2f} (pos avg {avg_xga:.2f})"
    )

    # =======================================================================
    # "Broadcast Dossier" layout — see assets/CARD_DESIGN_PHILOSOPHY.md
    # Zones are anchored to BOTH edges so slack is distributed, never pooled
    # into a dead gap at the bottom.
    # =======================================================================
    accent = _display_accent(team_color)
    img  = Image.new("RGBA", (CARD_W, CARD_H), INK)
    draw = ImageDraw.Draw(img)

    # Accent rail — the club colour states itself once, at the edge.
    draw.rectangle([(0, 0), (CARD_W, 7)], fill=accent)

    # --- Micro header: provenance left, standing right ---------------------
    micro = _f_mono(19)
    _tracked(draw, (MARGIN, 52), f"NWSL {season}".upper(), micro, TEXT_3, tracking=3)
    standing = f"#{rank} OF {n_cohort} {pos_label.upper()}S"
    sw = _tracked_width(standing, micro, 3)
    _tracked(draw, (CARD_W - MARGIN - sw, 52), standing, micro, TEXT_3, tracking=3)
    _rule(draw, 92)

    # --- Identity: the name is the headline ---------------------------------
    name = _clean_text(str(row.get("player_name", "")))
    n_size = 104
    while n_size > 54 and _f_display(n_size).getlength(name) > CARD_W - 2 * MARGIN:
        n_size -= 2
    name_font = _f_display(n_size)
    draw.text((MARGIN, 118), name, font=name_font, fill=TEXT_1)

    # Big Shoulders carries a tall ascent, so the subtitle needs real air
    # beneath the name rather than the optical minimum.
    sub = _clean_text(f"{row.get('team_name', '')}  ·  {pos_label}{age_str}")
    draw.text((MARGIN, 118 + n_size + 22), sub, font=_f_text(26), fill=TEXT_2)

    # The claim, in the club's colour.
    hook_y = 118 + n_size + 74
    h_size = 40
    while h_size > 26 and _f_display(h_size).getlength(hook.upper()) > CARD_W - 2 * MARGIN:
        h_size -= 1
    draw.text((MARGIN, hook_y), hook.upper(), font=_f_display(h_size), fill=accent)
    _rule(draw, hook_y + h_size + 26)

    # --- Hero figure --------------------------------------------------------
    vs = float(row.get("value_score", 0))
    hero_top = hook_y + h_size + 56
    _tracked(draw, (MARGIN, hero_top), "VALUE SCORE", micro, TEXT_3, tracking=3)

    hero_font = _f_display(132)
    hero_txt  = f"{vs:+.2f}"
    draw.text((MARGIN, hero_top + 24), hero_txt, font=hero_font, fill=TEXT_1)
    hero_w = hero_font.getlength(hero_txt)

    # Context sits on the hero's baseline, subordinate in scale.
    ctx_x = MARGIN + hero_w + 30
    ctx_y = hero_top + 24 + 132 - 62
    draw.text((ctx_x, ctx_y), "0.00 = positional average",
              font=_f_text(23), fill=TEXT_3)
    if rank > 1:
        draw.text((ctx_x, ctx_y + 30), f"Top {100 - round(pct)}% of {pos_label.lower()}s",
                  font=_f_text(23), fill=TEXT_2)
    else:
        draw.text((ctx_x, ctx_y + 30), f"Best {pos_label.lower()} in the league",
                  font=_f_text(23), fill=TEXT_2)

    chart_label_y = hero_top + 24 + 132 + 30
    _rule(draw, chart_label_y - 14)

    # --- Evidence -----------------------------------------------------------
    _tracked(draw, (MARGIN, chart_label_y + 14), "WHAT DRIVES HER VALUE",
             micro, TEXT_3, tracking=3)
    unit = "g+ PER 90"
    uw = _tracked_width(unit, micro, 3)
    _tracked(draw, (CARD_W - MARGIN - uw, chart_label_y + 14), unit, micro, TEXT_3, tracking=3)

    bars_top = chart_label_y + 52
    bars_bottom = _draw_action_bars(draw, row, cohort, bars_top, accent)

    # Average-marker key, stated once and quietly.
    key_y = bars_bottom + 10
    draw.rectangle([(MARGIN, key_y + 6), (MARGIN + 2, key_y + 22)], fill=AVG_TICK)
    draw.text((MARGIN + 14, key_y + 4), "positional average",
              font=_f_text(20), fill=TEXT_3)

    # --- Footer block, anchored to the bottom edge --------------------------
    BRAND_Y   = CARD_H - 62
    STATS_Y   = BRAND_Y - 46
    VERDICT_Y2 = STATS_Y - 56
    _rule(draw, VERDICT_Y2 - 30)

    verdict_text = _clean_text(_verdict(row, cohort, pos_label))
    v_size = 34
    while v_size > 22 and _f_display(v_size).getlength(verdict_text.upper()) > CARD_W - 2 * MARGIN:
        v_size -= 1
    draw.text((MARGIN, VERDICT_Y2), verdict_text.upper(),
              font=_f_display(v_size), fill=accent)

    stat_font = _f_mono(20)
    stat_line = (
        f"{mins_int:,} MIN  ~{matches} MATCHES     "
        f"WTD g+/90 {float(row.get('weighted_ga_p90', 0)):.2f} (AVG {avg_wga:.2f})     "
        f"xG+xA/90 {float(row.get('xga_p90', 0)):.2f} (AVG {avg_xga:.2f})"
    )
    while stat_font.getlength(stat_line) > CARD_W - 2 * MARGIN and stat_font.size > 14:
        stat_font = _f_mono(stat_font.size - 1)
    draw.text((MARGIN, STATS_Y), stat_line, font=stat_font, fill=TEXT_2)

    _rule(draw, BRAND_Y - 16)
    brand_font = _f_display(28)
    draw.text((MARGIN, BRAND_Y), "NWSL ROSTERLAB", font=brand_font, fill=TEXT_1)
    url_font = _f_mono(21)
    uw2 = url_font.getlength(APP_URL)
    draw.text((CARD_W - MARGIN - uw2, BRAND_Y + 4), APP_URL, font=url_font, fill=TEXT_3)

    # --- Interpretation: centred in the space the layout actually left ------
    take_top    = key_y + 34
    take_bottom = VERDICT_Y2 - 44
    zone_h = take_bottom - take_top
    take_font, take_wrapped = _fit_take(insight_trimmed, CARD_W - 2 * MARGIN, zone_h)
    tb = draw.textbbox((0, 0), take_wrapped, font=take_font, spacing=10)
    draw.text((MARGIN, take_top + max(0, (zone_h - (tb[3] - tb[1])) // 2)),
              take_wrapped, font=take_font, fill=(214, 226, 238, 255), spacing=10)

    # Serialize
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def render_og_image() -> bytes:
    """
    Render a static 1200x630 Open Graph branded image and return PNG bytes.
    Used for manual attachment to posts; real link-unfurl previews need a
    separate static landing page outside Streamlit (out of scope for v1).
    """
    img  = Image.new("RGBA", (OG_W, OG_H), (13, 31, 45, 255))
    draw = ImageDraw.Draw(img)

    # Accent stripe
    draw.rectangle([(0, 0), (OG_W, 8)], fill=_hex_rgb("#E6447E"))
    draw.rectangle([(0, OG_H - 8), (OG_W, OG_H)], fill=_hex_rgb("#E6447E"))

    title_font = _get_bold_font(72)
    sub_font   = _get_font(38)
    url_font   = _get_font(26)

    title = "NWSL RosterLab"
    sub   = "Who's actually valuable in the NWSL?"

    def _center_x(text, font):
        bb = draw.textbbox((0, 0), text, font=font)
        return (OG_W - (bb[2] - bb[0])) // 2

    draw.text((_center_x(title, title_font), 190),
              title, font=title_font, fill=(255, 255, 255, 255))
    draw.text((_center_x(sub, sub_font), 300),
              sub, font=sub_font, fill=(180, 200, 220, 255))
    draw.text((_center_x(APP_URL, url_font), 400),
              APP_URL, font=url_font, fill=(120, 160, 190, 255))

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def render_leaderboard_card(
    rows: list[dict],
    title: str,
    season: str,
    subtitle: str | None = None,
) -> bytes:
    """
    Render a 1080×1350 PNG leaderboard card and return PNG bytes.

    rows     — list[dict] from select_undervalued_xi(); each has slot, position,
               x, y, player_name, team_abbreviation, value_score, line.
    title    — large text in the header (e.g. "Undervalued XI").
    season   — season label (e.g. "2025").
    subtitle — smaller text below header (optional).

    Generic: pass any row set + title to reuse for future "Risers", "Rookie Watch" etc.
    $0 cost — no LLM calls.
    """
    # Neutral header color (multiple teams — no single team color)
    HEADER_COLOR = "#1A3A5C"
    HEADER_H     = 200
    SUBTITLE_H   = 60
    PITCH_Y      = HEADER_H + SUBTITLE_H      # 260
    PITCH_H      = 940
    FOOTER_Y     = CARD_H - 80               # 1270

    img  = Image.new("RGBA", (CARD_W, CARD_H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # --- Header ---
    hook_text = _clean_text(f"{title}  ·  {season}")
    _draw_header(draw, img, HEADER_COLOR, hook_text, header_h=HEADER_H)

    # --- Subtitle ---
    if subtitle:
        sub_font = _get_font(26)
        sub_text = _clean_text(subtitle)
        bb = draw.textbbox((0, 0), sub_text, font=sub_font)
        sub_x = (CARD_W - (bb[2] - bb[0])) // 2
        sub_y = HEADER_H + (SUBTITLE_H - (bb[3] - bb[1])) // 2
        draw.text((sub_x, sub_y), sub_text, font=sub_font,
                  fill=(160, 200, 230, 255))

    # --- Pitch (mplsoccer) ---
    try:
        pitch_img = _leaderboard_pitch(rows, pitch_h_px=PITCH_H)
        pitch_img = pitch_img.resize((CARD_W, PITCH_H), Image.LANCZOS)
        img.paste(pitch_img, (0, PITCH_Y))
    except Exception:
        # Fallback: grouped text list
        _draw_grouped_list(draw, rows, y_start=PITCH_Y, zone_h=PITCH_H)

    # --- Footer ---
    _draw_footer(draw, footer_y=FOOTER_Y)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _name_short(name: str, max_len: int = 14) -> str:
    """Return 'First L.' if name exceeds max_len chars."""
    if len(name) <= max_len:
        return name
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0]}."
    return name[:max_len]


def _leaderboard_pitch(rows: list[dict], pitch_h_px: int = 940) -> "Image.Image":
    """
    Pure-PIL pitch rendering — no matplotlib, no threading issues in Streamlit.

    Coordinate space: x 0–80 (width), y 0–120 (length, attack at top/low y).
    Maps to pixel space: px = x/80 * W, py = y/120 * H (y=0 → top of image).
    """
    import math

    W, H = CARD_W, pitch_h_px
    img  = Image.new("RGBA", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    LINE  = (58, 90, 122, 200)   # muted blue-grey lines
    PAD_X = int(W * 0.06)        # horizontal margin inside pitch markings
    PAD_Y = int(H * 0.04)        # vertical margin

    def _px(x_coord: float, y_coord: float) -> tuple[int, int]:
        """Convert formation coords (x 0–80, y 0–120, y=0 = defence end) to pixels.
        We flip y so attack (high y) appears at the TOP of the card."""
        px = PAD_X + int((x_coord / 80) * (W - 2 * PAD_X))
        # y=120 (attack) → PAD_Y (top); y=0 (defence) → H - PAD_Y (bottom)
        py = PAD_Y + int(((120 - y_coord) / 120) * (H - 2 * PAD_Y))
        return px, py

    # --- Pitch outline + centre line ---
    tl = _px(0, 120)
    br = _px(80, 0)
    draw.rectangle([tl, br], outline=LINE, width=2)

    mid_l = _px(0,  60)
    mid_r = _px(80, 60)
    draw.line([mid_l, mid_r], fill=LINE, width=2)

    # Centre circle (radius ≈ 9 in formation coords → scale to pixels)
    cx, cy = _px(40, 60)
    r_px = int(9 / 80 * (W - 2 * PAD_X))
    draw.ellipse([cx - r_px, cy - r_px, cx + r_px, cy + r_px], outline=LINE, width=2)

    # Penalty boxes (attack = top, defence = bottom)
    # Top box: y from 102 to 120, x from 18 to 62
    tbox_tl = _px(18, 120)
    tbox_br = _px(62, 102)
    draw.rectangle([tbox_tl, tbox_br], outline=LINE, width=2)

    # Bottom box: y from 0 to 18, x from 18 to 62
    bbox_tl = _px(18, 18)
    bbox_br = _px(62, 0)
    draw.rectangle([bbox_tl, bbox_br], outline=LINE, width=2)

    # --- Player markers ---
    marker_r = int(W * 0.028)   # radius ~30px
    name_font  = _get_font(24)
    score_font = _get_font(20)

    for row in rows:
        if row["player_name"] == "—":
            continue

        cx, cy = _px(row["x"], row["y"])
        hex_color = TEAM_COLORS.get(row.get("team_abbreviation", ""), DEFAULT_TEAM_COLOR)
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        fill   = (r, g, b, 255)
        border = (255, 255, 255, 220)

        # Filled circle with white border
        draw.ellipse(
            [cx - marker_r - 2, cy - marker_r - 2, cx + marker_r + 2, cy + marker_r + 2],
            fill=border,
        )
        draw.ellipse(
            [cx - marker_r, cy - marker_r, cx + marker_r, cy + marker_r],
            fill=fill,
        )

        name  = _name_short(_clean_text(row["player_name"]))
        score = f"{row['value_score']:+.2f}"

        # Name below marker
        nb = draw.textbbox((0, 0), name, font=name_font)
        nw = nb[2] - nb[0]
        draw.text((cx - nw // 2, cy + marker_r + 4), name,
                  font=name_font, fill=(255, 255, 255, 230))

        # Score above marker (amber)
        sb = draw.textbbox((0, 0), score, font=score_font)
        sw = sb[2] - sb[0]
        draw.text((cx - sw // 2, cy - marker_r - (sb[3] - sb[1]) - 4), score,
                  font=score_font, fill=(255, 183, 77, 255))

    return img


def _draw_grouped_list(draw: ImageDraw.ImageDraw, rows: list[dict],
                       y_start: int, zone_h: int) -> None:
    """Fallback: render DEF / MID / FWD text list if mplsoccer is unavailable."""
    lines_by_group: dict[str, list[str]] = {"DEF": [], "MID": [], "FWD": []}
    for r in rows:
        if r["player_name"] == "—":
            continue
        label = f"{r['position']}  {r['player_name']}  {r['value_score']:+.2f}"
        lines_by_group.get(r["line"], lines_by_group["FWD"]).append(label)

    name_font  = _get_font(28)
    group_font = _get_bold_font(30)
    y = y_start + 30
    for group, members in lines_by_group.items():
        draw.text((54, y), group, font=group_font, fill=(100, 160, 210, 255))
        y += 38
        for line in members:
            draw.text((80, y), _clean_text(line), font=name_font,
                      fill=(220, 230, 240, 255))
            y += 36
        y += 16
