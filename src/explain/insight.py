"""
Explain layer (Phase 3) — thin Claude API wrapper that PHRASES numbers.

CRITICAL RULE (see CLAUDE.md): the LLM never calculates. You pass it the already-
computed numbers for one player; it returns a one-line plain-English "why".
Always display the raw numbers next to the generated sentence.
"""

from __future__ import annotations
import os


def one_line_insight(player_row: dict) -> str:
    """TODO (Phase 3): implement with Claude Code.

    - load ANTHROPIC_API_KEY from .env (python-dotenv)
    - call the anthropic SDK (claude haiku) with a tight prompt:
        "Given these precomputed NWSL metrics for {name}, write ONE sentence
         explaining why she ranks where she does. Do not invent numbers."
    - return the sentence (fallback to a templated string if the API fails)
    """
    raise NotImplementedError("Build this in Phase 3 with Claude Code.")
