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
import textwrap
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
        return f"Rising: Top {round(100 - pct)}% {pos} at just {age}"
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
# Private: action bar chart -> PIL Image
# ---------------------------------------------------------------------------

def _action_bar_chart(row: dict, cohort: pd.DataFrame) -> Image.Image:
    """Render 6-action horizontal bar chart. Returns PIL Image. Closes figure."""
    vals   = [float(row.get(col, 0.0)) for col in ACTION_COLS]
    avgs   = [float(cohort[col].mean()) for col in ACTION_COLS]
    labels = [ACTION_DISPLAY[c] for c in ACTION_COLS]
    colors = [ACCENT_NEG if v < 0 else ACCENT_POS for v in vals]

    bg = tuple(c / 255 for c in BG_COLOR[:3])
    fig, ax = plt.subplots(figsize=(9.5, 2.6), facecolor=bg)
    ax.set_facecolor(bg)

    y_pos = range(len(labels))
    ax.barh(y_pos, vals, color=colors, height=0.55, zorder=2)

    # Position-average markers
    for i, avg in enumerate(avgs):
        ax.plot([avg, avg], [i - 0.35, i + 0.35],
                color=ACCENT_AVG, linewidth=1.5, zorder=3)

    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels, color="white", fontsize=10,
                       fontfamily="DejaVu Sans")
    ax.tick_params(axis="x", colors="#aaaaaa", labelsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color("#444444")
    ax.axvline(0, color="#555555", linewidth=0.8, zorder=1)
    ax.set_xlabel("g+ per 90", color="#aaaaaa", fontsize=8, fontfamily="DejaVu Sans")

    # Legend hint
    from matplotlib.lines import Line2D
    ax.legend(
        handles=[Line2D([0], [0], color=ACCENT_AVG, linewidth=1.5)],
        labels=["position avg"],
        loc="lower right",
        fontsize=7,
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
    age_str    = f"  ·  Age {int(row['age'])}" if row.get("age") else ""
    hook       = headline_hook(row, cohort, season)

    # Fallback insight if none provided
    if not insight_text:
        action_vals = {c: float(row.get(c, 0.0)) for c in [
            "ga_shooting", "ga_dribbling", "ga_passing",
            "ga_receiving", "ga_interrupting", "ga_fouling"]}
        top_col = max(action_vals, key=action_vals.get)
        top_label = ACTION_DISPLAY.get(top_col + "_p90",
                    top_col.replace("ga_", "").replace("_", " ").title())
        insight_text = (
            f"Ranks #{rank} of {n_cohort} {pos_label}s on g+/90 "
            f"({float(row.get('goals_added_p90', 0)):.3f} vs. position avg "
            f"{cohort['goals_added_p90'].mean():.3f}), "
            f"with her strongest contribution from {top_label.lower()} "
            f"({action_vals[top_col]:+.3f} g+)."
        )

    # Strip em/en dashes from all text drawn on the card (public-facing)
    def _clean(text: str) -> str:
        return text.replace("—", ",").replace("–", ",")

    hook         = _clean(hook)
    insight_text = _clean(insight_text)

    # Truncate insight to first 1-2 sentences (~180 chars max), cut on sentence boundary
    _sentences = insight_text.replace("! ", ". ").replace("? ", ". ").split(". ")
    _card_take = ""
    for _s in _sentences:
        candidate = (_card_take + (" " if _card_take else "") + _s.strip()).strip()
        if len(candidate) <= 180:
            _card_take = candidate
        else:
            break
    if not _card_take:
        _card_take = insight_text[:180]
    if _card_take and not _card_take.endswith("."):
        _card_take += "."

    # Layout constants
    FOOTER_Y  = 1270
    FOOTER_H  = 80      # 1270-1350
    TAKE_Y    = 760
    TAKE_H    = FOOTER_Y - TAKE_Y   # 510px for scout take

    # Canvas
    img  = Image.new("RGBA", (CARD_W, CARD_H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # --- Zone 1: Header (0-220) ---
    _draw_header(draw, img, team_color, hook, header_h=220)

    # --- Zone 2: Identity (220-340) ---
    name_font = _get_bold_font(48)
    draw.text((54, 232), _clean(str(row.get("player_name", ""))),
              font=name_font, fill=TEXT_PRIMARY)
    sub_font = _get_font(28)
    sub_text = _clean(f"{row.get('team_name', '')}  ·  {pos_label}{age_str}  ·  {season}")
    draw.text((54, 292), sub_text, font=sub_font, fill=TEXT_SECONDARY)

    # --- Zone 3: Value banner (340-440) ---
    draw.rectangle([(0, 340), (CARD_W, 440)], fill=(20, 45, 65, 255))
    val_font  = _get_bold_font(44)
    rank_font = _get_font(30)
    vs = float(row.get("value_score", 0))
    # Label + number
    label_font = _get_font(20)
    draw.text((54, 348), "Value score", font=label_font, fill=TEXT_SECONDARY)
    draw.text((54, 368), f"{vs:+.2f}", font=val_font, fill=(255, 255, 180, 255))
    # Rank + percentile pill — omit percentile when rank==1 (would show "Top 0%")
    if rank == 1:
        rank_text = f"#{rank} of {n_cohort} {pos_label}s"
    else:
        rank_text = f"#{rank} of {n_cohort} {pos_label}s  ·  Top {100 - round(pct)}%"
    draw.text((220, 378), rank_text, font=rank_font, fill=TEXT_SECONDARY)

    # --- Zone 4: Action chart (440-760) ---
    chart_img = _action_bar_chart(row, cohort)
    chart_img = chart_img.resize((CARD_W - 80, 300), Image.LANCZOS)
    img.paste(chart_img, (40, 448))

    # --- Zone 5: Scout take (760-1270) ---
    draw.rectangle([(0, TAKE_Y), (CARD_W, FOOTER_Y)], fill=(16, 36, 52, 255))
    take_font = _get_font(30)
    wrapped   = textwrap.fill(_card_take, width=48)
    # Clamp text so it never paints into the footer band
    draw.text((54, TAKE_Y + 24), wrapped, font=take_font,
              fill=TEXT_PRIMARY, spacing=10)

    # --- Zone 6: Footer flush at bottom (1270-1350) ---
    _draw_footer(draw, footer_y=FOOTER_Y, footer_h=FOOTER_H)

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


def render_leaderboard_card(rows: list[dict], title: str, season: str) -> bytes:
    """Placeholder — reuses _draw_header, _draw_footer, _action_bar_chart. Fast follow."""
    raise NotImplementedError("leaderboard card is a planned fast follow")
