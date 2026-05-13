# ================================================================
# modes/swing/ai_overlay.py
# ================================================================
# Optional AI overlay for swing candidates (SWING_ROADMAP S15).
#
# AI receives the complete NoAI data (indicators, score, risk) as
# fixed context and fills ONLY qualitative fields:
#   - thesis
#   - risks
#   - news/catalyst context
#   - peer comparison
#   - why this setup may fail
#
# AI must NOT change entry, stop, target, qty, or R:R.
# AI overlay runs only when explicitly requested by the user.
# ================================================================

from __future__ import annotations

import json

from core.claude_client import ClaudeClient
from core.logger import Logger
from modes.swing.types import SwingCandidate


def overlay_ai_on_candidates(
    candidates: list[SwingCandidate],
    claude: ClaudeClient,
    log: Logger,
) -> list[SwingCandidate]:
    """Add AI qualitative overlay to accepted candidates.

    Mutates candidates in place. Only processes ACCEPTED candidates.
    Returns the same list.
    """
    accepted = [c for c in candidates if c.status == "ACCEPTED"]
    if not accepted:
        return candidates

    log.info(f"AI overlay: processing {len(accepted)} accepted candidates")

    for c in accepted:
        try:
            prompt = _build_prompt(c)
            response = claude.ask(prompt)
            if response:
                c.ai_overlay_json = json.dumps({
                    "raw_response": response[:2000],
                    "source": "claude_swing_overlay",
                })
        except Exception as exc:
            log.warning(f"AI overlay failed for {c.symbol}: {exc}")
            c.ai_overlay_json = json.dumps({
                "error": str(exc)[:200],
            })

    return candidates


def _build_prompt(c: SwingCandidate) -> str:
    """Build the Claude prompt for one swing candidate."""
    return f"""You are a swing trading analyst. Review this candidate and provide a brief qualitative assessment.

FIXED DATA (do NOT change these numbers):
- Symbol: {c.symbol} ({c.exchange})
- Setup: {c.setup_type}
- Score: {c.score}
- Entry: Rs.{c.entry_price:,.2f}
- Stop: Rs.{c.stop_price:,.2f}
- Target: Rs.{c.target_price:,.2f}
- R:R: {c.rr_ratio:.1f}
- RSI: {c.rsi_daily:.1f}
- Relative Strength vs NIFTY: {c.relative_strength:+.1f}%
- Volume ratio: {c.volume_ratio:.1f}x
- Above SMA-200: {'Yes' if c.close_price > c.sma_200 else 'No'}
- Weekly trend: {'Up' if c.weekly_trend_up else 'Down'}
- Sector: {c.sector}

Provide ONLY:
1. THESIS (2-3 bullets on why this setup works)
2. RISKS (2-3 specific risks for this name)
3. NEWS/CATALYST (any recent material events — say "None known" if unsure)
4. PEER COMPARISON (vs 1-2 sector peers)
5. WHY IT MIGHT FAIL (1-2 sentences)

Keep it concise. Do NOT suggest different entry/stop/target prices."""
