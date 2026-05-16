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
    """Build the Claude prompt for one swing candidate.

    Written as a buy-side analyst's pre-trade due diligence request.
    The prompt is structured so every answer directly informs a
    go/no-go decision.
    """

    # Setup context
    is_dip = c.setup_type in ("52W_DIP", "ATH_DIP")
    ref_label = "52-week high" if c.setup_type == "52W_DIP" else "all-time high"
    above_sma200 = "Yes" if (c.close_price > c.sma_200 and c.sma_200 > 0) else ("No" if c.sma_200 > 0 else "N/A")

    setup_context = ""
    if is_dip:
        setup_context = f"""
SETUP CONTEXT: This is a DIP-BUY candidate — it is {c.score:.1f}% below its {ref_label}.
The stock will typically show weak technicals (below SMA-200, downtrend). That is EXPECTED
for this strategy and is NOT a negative. The question is whether the BUSINESS is intact and
the dip is RECOVERABLE. Think of it as buying a quality asset on sale.

For NIFTY 50 blue-chips: the default answer should be BUY unless you find a specific
structural problem. These companies have survived multiple cycles — a 20% dip from
52-week high in a fundamentally sound company is historically a buying opportunity.
"""
    else:
        setup_context = f"""
SETUP CONTEXT: This is a TECHNICAL swing setup ({c.setup_type.replace('_', ' ').title()}).
The stock has passed quantitative filters for trend, momentum, and risk-reward.
Your job is to check if there's a qualitative reason to NOT take this trade.
"""

    return f"""I manage a personal portfolio and I'm about to commit real money to this trade. I need your honest, research-backed assessment to decide whether to enter TODAY or wait. Be direct — I'd rather hear a hard truth than a soft reassurance.

STOCK: {c.symbol} on {c.exchange} | Sector: {c.sector}
CURRENT PRICE: Rs.{c.close_price:,.2f}
PLAN: Buy at ~Rs.{c.entry_price:,.2f} | Stop-loss at Rs.{c.stop_price:,.2f} | Target Rs.{c.target_price:,.2f}
RISK/REWARD: {c.rr_ratio:.1f}x (risking Rs.{c.risk_rupees:,.0f} to make Rs.{c.reward_rupees:,.0f})
HOLDING PERIOD: 2 days to 8 weeks (delivery/CNC, not intraday)

TECHNICAL SNAPSHOT (already computed, don't change these):
- RSI(14): {c.rsi_daily:.1f}
- vs 20-day avg volume: {c.volume_ratio:.1f}x
- Above 200-day average: {above_sma200}
- Weekly trend: {'Up' if c.weekly_trend_up else 'Down'}
- Performance vs NIFTY (60 days): {c.relative_strength:+.1f}%
- 52-week range: Rs.{c.low_52w:,.2f} – Rs.{c.high_52w:,.2f}
{setup_context}
Answer these 10 questions. Be specific to THIS company — generic answers are useless to me. If you don't know something, say "Unknown" rather than guessing.

**1. VERDICT: Should I enter this trade today?**
Answer: BUY / WAIT / PASS — then ONE sentence explaining why.
- BUY = enter today, the setup + fundamentals align
- WAIT = interesting but needs a specific trigger first (name it)
- PASS = something is wrong that the numbers don't show

**2. What does this company actually do, and is the business getting better or worse?**
(Revenue trend, margin direction, market share — 2-3 bullets max)

**3. What happened in the last 60 days that I should know about?**
(Earnings, guidance, management changes, large deals, regulatory news. If nothing material, say "Nothing material in the last 60 days.")

**4. Is the stock expensive or cheap right now?**
(Approximate P/E vs sector peers. Where in its own historical valuation range? Is there a reason for premium/discount?)

**5. What's the bull case for the next 2-8 weeks?**
(Specific catalysts: results season, order wins, sector tailwind, policy change, technical breakout target)

**6. What's the bear case — what would make me lose money?**
(Specific risks: earnings miss, regulatory action, sector headwind, global macro, key customer loss, raw material cost spike)

**7. How does it compare to the best alternative in the same sector?**
(Name 1-2 peers. Would I be better off buying the peer instead? One line each.)

**8. Is there a corporate action I should worry about?**
(Stock split, bonus, demerger, rights issue, buyback in the last 24 months that could distort the price history? If unsure, say "Verify on NSE corporate actions page.")

**9. What's the promoter/management situation?**
(Promoter holding trend, any pledge concerns, management credibility. "Unknown" is fine if you're not sure.)

**10. What's the one thing that would make a senior fund manager say "don't touch this"?**
(The single biggest red flag or deal-breaker. If there isn't one, say "No obvious red flag — setup is clean.")

RULES:
- Do NOT suggest different entry/stop/target prices. Those are already locked.
- Be honest. "Unknown" beats a fabricated answer every time.
- Keep it under 600 words total. Concise > comprehensive.
- Start with the VERDICT — that's the most important line."""


