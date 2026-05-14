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

from config import Config
from core.claude_client import ClaudeClient
from core.logger import Logger
from modes.swing.types import SwingCandidate


# Hard upper bound when no Config knob is present (e.g. mid-upgrade).
# The real cap is `Config.SWING_AI_MAX_CANDIDATES` — see config.py for
# the rationale (cost-runaway prevention after a Ctrl+C'd long scan).
_FALLBACK_AI_MAX_CANDIDATES = 15


def overlay_ai_on_candidates(
    candidates: list[SwingCandidate],
    claude: ClaudeClient,
    log: Logger,
) -> list[SwingCandidate]:
    """Add AI qualitative overlay to accepted candidates.

    Mutates candidates in place. Only processes ACCEPTED candidates,
    capped at `Config.SWING_AI_MAX_CANDIDATES` to prevent the runaway
    cost mode (a NIFTY 100 scan that flagged ~50 ATH-dip + technical
    candidates would otherwise cost ~50 × CLAUDE_COST_PER_CALL per
    run — when the user once Ctrl+C'd a long scan partway, it
    produced no report and burned credits anyway).

    The cap selects candidates by `priority_rank` ascending so the
    Claude budget always lands on the strongest signals.

    Returns the same list for chaining.
    """
    accepted = [c for c in candidates if c.status == "ACCEPTED"]
    if not accepted:
        return candidates

    # Cap: take top-N by priority. priority_rank may be 0 for the very
    # first arriving candidate before manager rebanks; treat 0 as
    # "rank unset" and push to the end so explicit ranks win.
    cap = int(getattr(Config, "SWING_AI_MAX_CANDIDATES",
                      _FALLBACK_AI_MAX_CANDIDATES))
    cap = max(1, cap)
    accepted_sorted = sorted(
        accepted,
        key=lambda c: (c.priority_rank if c.priority_rank > 0 else 9_999,
                       -c.score),
    )
    overlaid = accepted_sorted[:cap]
    skipped = accepted_sorted[cap:]

    per_call = float(getattr(Config, "CLAUDE_COST_PER_CALL", 3.0))
    log.info(
        f"AI overlay: {len(overlaid)} of {len(accepted)} accepted "
        f"candidates (cap={cap}; ~Rs.{per_call * len(overlaid):.0f} "
        f"budget at Rs.{per_call:.0f}/call)"
    )
    if skipped:
        skipped_syms = ", ".join(c.symbol for c in skipped[:8])
        more = f" (+{len(skipped) - 8} more)" if len(skipped) > 8 else ""
        log.warning(
            f"AI overlay skipping {len(skipped)} lower-priority "
            f"candidates to stay under the cost cap: {skipped_syms}{more}. "
            f"Increase Config.SWING_AI_MAX_CANDIDATES to widen."
        )

    for c in overlaid:
        try:
            prompt = _build_prompt(c)
            response = claude.call(prompt)
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


# ── Single-candidate AI analyse (per-stock detail-page button) ──
#
# Used by the dashboard `/api/swing/ai_analyse/<symbol>` endpoint
# (Roadmap S37). Costs exactly one Claude call (~Rs.3 on Pro) so
# the user can selectively get qualitative thesis on a name they're
# considering, without having to re-run the full AI scan and pay
# for the entire top-K cap.

def analyse_single_candidate(
    candidate: SwingCandidate,
    claude: ClaudeClient,
    log: Logger,
) -> str:
    """Send `candidate` to Claude and return the JSON-encoded
    overlay payload. Mutates the candidate's `ai_overlay_json`
    in place too so a caller that just wants the side-effect
    can ignore the return value.

    On Claude failure the overlay payload carries an `"error"`
    key; the caller is responsible for surfacing it (e.g. via the
    error toast). Never raises.
    """
    try:
        prompt = _build_prompt(candidate)
        response = claude.call(prompt)
        if response:
            candidate.ai_overlay_json = json.dumps({
                "raw_response": response[:2000],
                "source": "claude_swing_overlay_single",
            })
        else:
            candidate.ai_overlay_json = json.dumps({
                "error": "Claude returned empty response",
            })
    except Exception as exc:
        log.warning(f"Single-candidate AI analyse failed for "
                    f"{candidate.symbol}: {exc}")
        candidate.ai_overlay_json = json.dumps({
            "error": str(exc)[:200],
        })
    return candidate.ai_overlay_json


def _build_prompt(c: SwingCandidate) -> str:
    """Build the Claude prompt for one swing candidate."""

    # Both legacy "ATH_DIP" rows (pre-2026-05-14) and current
    # "52W_DIP" rows trigger the dip-buy section so the corporate-
    # action sanity-check still runs against historical positions
    # if they're being re-reviewed.
    dip_section = ""
    if c.setup_type in ("52W_DIP", "ATH_DIP"):
        ref_label = "52-week high" if c.setup_type == "52W_DIP" else "all-time high"
        dip_section = f"""
IMPORTANT — DIP-BUY CANDIDATE ({ref_label} reference):
This stock was flagged because it is {c.score:.1f}% below its {ref_label}.
CRITICAL CHECK: Has this stock undergone a stock split, bonus issue, demerger,
or any corporate action in the last 2 years that would have artificially reduced
the share price? If YES, the apparent dip is NOT a real dip — the pre-action
reference price is not comparable to the current post-action price. Flag this
clearly in your RISKS section and recommend SKIPPING if the dip is entirely
due to a corporate action.

Examples of false dips:
- Stock split 1:5 → price drops 80% mechanically, not a dip
- Demerger → value carved out to new entity, price drops
- Bonus issue 1:1 → price halves, not a real decline
"""

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
- Above SMA-200: {'Yes' if c.close_price > c.sma_200 and c.sma_200 > 0 else 'No' if c.sma_200 > 0 else 'N/A'}
- Weekly trend: {'Up' if c.weekly_trend_up else 'Down'}
- Sector: {c.sector}
{dip_section}
Provide ONLY:
1. THESIS (2-3 bullets on why this setup works)
2. RISKS (2-3 specific risks for this name)
3. NEWS/CATALYST (any recent material events — say "None known" if unsure)
4. PEER COMPARISON (vs 1-2 sector peers)
5. WHY IT MIGHT FAIL (1-2 sentences)

Keep it concise. Do NOT suggest different entry/stop/target prices."""
