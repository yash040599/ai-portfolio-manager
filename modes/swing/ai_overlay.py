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

EVALUATION FRAMEWORK FOR DIP-BUY (different from technical swing):
A dip-buy candidate will typically be below SMA-200 with weak trends — that
is expected and DOES NOT automatically mean WATCH/SKIP. For dip-buys, evaluate:
- Is the business fundamentally sound? (market leader, earnings growth, moat)
- Is the dip due to sector rotation / market pullback (recoverable) or
  structural deterioration (permanent value destruction)?
- Has the stock historically recovered from similar dips?
- Is there a catalyst that could trigger the recovery?

VERDICT CALIBRATION for dip-buys:
- BUY: Fundamentally strong company, dip is cyclical/sentiment-driven, no
  structural damage. Most NIFTY 50 blue-chips that dip 20%+ on market
  rotation should get BUY if the business is intact.
- WATCH: Genuine uncertainty — earnings deterioration unclear, regulatory
  overhang with unknown timeline, or needs one more confirming signal.
- SKIP: Structural problems (fraud, market-share collapse, debt crisis),
  or the dip is from a corporate action (split/demerger/bonus) not a
  real decline.

CRITICAL CORPORATE-ACTION CHECK: Has this stock undergone a stock split,
bonus issue, demerger, or any corporate action in the last 2 years that
would have artificially reduced the share price? If YES, the apparent dip
is NOT a real dip. Flag this and recommend SKIPPING.
"""
    else:
        dip_section = ""

    return f"""You are a senior India equities buy-side analyst evaluating a swing-trade candidate (delivery / CNC, 2-day to 8-week holding period). The numeric / technical setup is already locked by deterministic NoAI math below — your job is the qualitative overlay only.

FIXED DATA (do NOT change these numbers):
- Symbol: {c.symbol} ({c.exchange})
- Setup: {c.setup_type}
- Score: {c.score}
- Entry: Rs.{c.entry_price:,.2f}
- Stop: Rs.{c.stop_price:,.2f}
- Target: Rs.{c.target_price:,.2f}
- R:R: {c.rr_ratio:.1f}
- RSI(14): {c.rsi_daily:.1f}
- Relative Strength vs NIFTY (60d): {c.relative_strength:+.1f}%
- Volume ratio (today vs 20d avg): {c.volume_ratio:.1f}x
- Above SMA-200: {'Yes' if c.close_price > c.sma_200 and c.sma_200 > 0 else 'No' if c.sma_200 > 0 else 'N/A'}
- Weekly trend: {'Up' if c.weekly_trend_up else 'Down'}
- 52w high: Rs.{c.high_52w:,.2f}    52w low: Rs.{c.low_52w:,.2f}
- Sector: {c.sector}
{dip_section}
Provide a structured response with EXACTLY these 8 sections, in this order. **Lead with the VERDICT (section 1) so the user sees the conclusion at the top — Claude has historically truncated responses at section 6 when the verdict was at the bottom.** Every section is mandatory; if you have nothing concrete say "None known" / "Unknown" rather than skipping the section.

1. **VERDICT FOR A SWING BUYER**: BUY / WATCH / SKIP — one word, then one sentence justification.
   - BUY: The setup is actionable. For technical setups (breakout/pullback/trend), strong trend + catalyst. For dip-buys, fundamentally sound company with recoverable dip.
   - WATCH: Genuine uncertainty that needs resolution before committing capital. Specify what signal you're waiting for.
   - SKIP: Clear disqualifier — structural damage, corporate-action distortion, or governance red flag.
   Default to BUY for NIFTY 50 blue-chips with intact businesses unless there is a SPECIFIC negative catalyst. Being below SMA-200 alone is NOT a reason to say WATCH for a dip-buy setup — that's expected.

2. **THESIS** (2–3 bullets) — why a swing buyer would take this setup right now. Tie to specific catalysts (orderbook, capex cycle, margin trajectory, regulatory tailwind) where you have concrete public-domain knowledge.

3. **RECENT NEWS / CATALYSTS** (last 60 days, bulleted) — earnings beat/miss, guidance changes, rating actions, M&A, regulatory hits, promoter pledge changes, large block deals. If you have NO concrete recent news for this name, say exactly "None known in last 60 days" — do not speculate.

4. **FUNDAMENTAL CONTEXT** (bulleted, only what you actually know):
   - Trailing P/E and how it compares to the sector median (rough numbers OK; never invent precise multiples).
   - ROE / ROCE band (high / mid / low for the sector).
   - Debt-to-equity sense (net cash, low, moderate, levered).
   - Promoter holding stability and pledge status if you know it.
   If a number is unknown, say "Unknown" rather than guessing.

5. **PEER COMPARISON** (1–2 closest listed sector peers, one sentence each) — better/worse on growth + valuation + technicals than this candidate.

6. **RISKS** (2–3 specific risks for THIS name, not generic) — sector cyclicality, key-customer concentration, regulatory exposure, currency, raw-material costs, governance flags, etc.

7. **CORPORATE-ACTION SANITY CHECK** — has this stock had a split / bonus / demerger / consolidation in the last 24 months that would distort the price-history-based signals above? If yes, name it and the date range. If unsure, say "Unsure — verify on Tickertape / NSE corp actions".

8. **WHY IT MIGHT FAIL** (1–2 sentences) — the cleanest invalidation path; what would make a senior PM cut the trade.

Hard rules:
- Do NOT suggest different entry/stop/target/qty — those are NoAI-owned.
- Be honest about what you don't know. "Unknown" / "None known" / "Unsure" are valid answers and far better than fabricated numbers.
- ALL 8 SECTIONS ARE MANDATORY. If a section is short, that's fine; never skip it. The verdict at section 1 ensures the user always sees the conclusion even if you self-limit on length.
- Aim for 400-600 words; you have ~2000 tokens budget so length is not the constraint, completeness is."""


