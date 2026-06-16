"""
Explain layer — turn pre-computed player metrics into a plain-English insight.

RULE (see CLAUDE.md): the LLM only phrases already-computed numbers.
It never calculates. All comparison context is derived here in Python
before the API call.
"""

from __future__ import annotations

import os

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

_SYSTEM = (
    "You are a sharp NWSL analyst writing concise, specific player insights for "
    "knowledgeable women's-soccer fans. Use ONLY the numbers provided — never invent "
    "stats, ages, injuries, transfers, or any real-world facts. "
    "Plain English, no jargon dumps, no generic praise."
)

_ACTION_LABELS: dict[str, str] = {
    "ga_shooting":     "shooting",
    "ga_dribbling":    "dribbling",
    "ga_passing":      "passing",
    "ga_receiving":    "receiving",
    "ga_interrupting": "defensive actions",
    "ga_fouling":      "fouling",
}

_FEW_SHOT = (
    '"Her value is almost entirely creation — 0.42 xA/90, nearly double the winger '
    "average of 0.22 — while her shooting g+ is slightly negative, so she's a provider, "
    "not a finisher. Ranking 3rd of 31 wingers on g+/90 despite the league's 7th-most "
    "minutes, she'd thrive next to a clinical striker but won't carry a frontline herself.\""
)


def one_line_insight(player_row: dict, cohort: pd.DataFrame) -> str | None:
    """Generate a 2-3 sentence scout insight for a player.

    Parameters
    ----------
    player_row : dict from a ranked DataFrame row; must include '_rank' and 'weighted_ga_p90'
    cohort     : full position cohort (league-wide, unfiltered) with '_rank' column

    Returns
    -------
    Insight string on success, None on any failure (missing key, API error, etc.).
    Caller is responsible for fallback and must NOT cache a None result.
    """
    name            = player_row["player_name"]
    team            = player_row["team_name"]
    pos             = player_row["position"]
    minutes         = int(player_row["minutes_played"])
    rank            = int(player_row["_rank"])
    n               = len(cohort)
    ga_p90          = player_row["goals_added_p90"]
    weighted_ga_p90 = player_row.get("weighted_ga_p90", ga_p90)
    xga_p90         = player_row["xga_p90"]
    xgoals_p90      = player_row["xgoals_p90"]
    xassists_p90    = player_row["xassists_p90"]

    avg_ga_p90          = round(cohort["goals_added_p90"].mean(), 3)
    avg_weighted_ga_p90 = (
        round(float(cohort["weighted_ga_p90"].mean()), 3)
        if "weighted_ga_p90" in cohort.columns else None
    )
    avg_xga_p90         = round(cohort["xga_p90"].mean(), 3)
    avg_xgoals_p90      = round(cohort["xgoals_p90"].mean(), 3)
    avg_xassists_p90    = round(cohort["xassists_p90"].mean(), 3)

    action_vals    = {col: float(player_row.get(col, 0.0)) for col in _ACTION_LABELS}
    sorted_actions = sorted(action_vals.items(), key=lambda x: x[1], reverse=True)
    top1_col, top1_val = sorted_actions[0]
    top2_col, top2_val = sorted_actions[1]
    bot1_col, bot1_val = sorted_actions[-1]

    weighted_line = ""
    if avg_weighted_ga_p90 is not None:
        weighted_line = (
            f"- Position-weighted g+/90: {weighted_ga_p90:.3f}  "
            f"(position avg: {avg_weighted_ga_p90:.3f})  "
            f"<-- drives her #{rank} ranking\n"
        )

    user_msg = (
        f"Player: {name} ({pos}, {team}) -- {minutes:,} minutes played\n\n"
        f"Her stats vs. position peers ({n} {pos}s qualified):\n"
        f"{weighted_line}"
        f"- Raw g+/90 (unweighted): {ga_p90:.3f}  (position avg: {avg_ga_p90:.3f})\n"
        f"- xG+xA/90: {xga_p90:.3f}  (position avg: {avg_xga_p90:.3f})\n"
        f"- xG/90: {xgoals_p90:.3f}  (position avg: {avg_xgoals_p90:.3f})\n"
        f"- xA/90: {xassists_p90:.3f}  (position avg: {avg_xassists_p90:.3f})\n"
        f"- Rank: #{rank} of {n} {pos}s (by position-weighted value score)\n"
        f"- Top actions (g+): {_ACTION_LABELS[top1_col]} ({top1_val:+.3f}), "
        f"{_ACTION_LABELS[top2_col]} ({top2_val:+.3f})\n"
        f"- Weakest action (g+): {_ACTION_LABELS[bot1_col]} ({bot1_val:+.3f})\n\n"
        f"Example output:\n{_FEW_SHOT}\n\n"
        f"In 2-3 sentences: (1) the main driver of her position-weighted value -- "
        f"which action types are amplified by her {pos} weights and carry her ranking; "
        f"(2) how she compares to other {pos}s, citing her #{rank} rank and her "
        f"position-weighted g+/90 vs. the position average; "
        f"(3) one sharp takeaway -- a strength-vs-weakness tradeoff, the type of team/role "
        f"she fits, or whether she is over- or under-used. "
        f"Reference specific numbers. Do not start with her name."
    )

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=220,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text.strip()
    except Exception:
        return None
