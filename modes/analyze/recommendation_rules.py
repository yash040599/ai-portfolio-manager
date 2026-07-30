# ================================================================
# modes/analyze/recommendation_rules.py
# ================================================================
# Deterministic, rule-based long-term recommendation engine for the
# Portfolio Analyser (--mode analyze).
#
# Inputs: a fully-enriched StockAnalysis record (everything except
# the AI overlay fields).
# Outputs: rule_action / rule_conviction / rule_horizon /
#          rule_target_price / rule_reasoning, all as Field[T]
#          stamped source=SRC_RULE_ENGINE.
#
# Design: HARD RULES ONLY. No magic numbers in inline floats; the
# thresholds live as named module constants so reviewers can tune
# them without hunting through the body.
#
# 2026-07-31 — two-layer split:
#   Layer 1 (modes/analyze/scoring.py) grades the *security* on six
#           factor pillars -> rating (STRONG BUY..SELL) + risk grade.
#   Layer 2 (this file) turns that rating plus your *position* context
#           into an instruction (HOLD / BUY MORE / PARTIAL EXIT / ...).
#   The two answer different questions and are deliberately separate:
#   a STRONG BUY you already hold at 22% of the book is still a trim.
#   The legacy threshold tree remains as the fallback whenever the
#   quant profile is too thin to score (new listing, no candles).
# ================================================================

from __future__ import annotations

from typing import Any

from config import now_ist
from modes.analyze.scoring import Scorecard, score as score_metrics
from modes.analyze.types import Field, StockAnalysis, SRC_RULE_ENGINE


# ── Tunable thresholds ──────────────────────────────────────────

DEEP_LOSS_PCT          = -25.0    # P&L% below this -> "AVERAGE DOWN" candidate
MILD_LOSS_PCT          = -10.0    # P&L% below this AND broken trend -> "PARTIAL EXIT"
EXTENDED_GAIN_PCT      = 50.0     # P&L% above this -> "PARTIAL EXIT" candidate
NEAR_52W_HIGH_PCT      = -5.0     # within 5% of 52w high
RSI_OVERBOUGHT         = 70.0
RSI_OVERSOLD           = 35.0
TREND_BROKEN_PCT       = -8.0     # price > this much below SMA200 = trend broken

# Position weight above which we refuse to say "BUY MORE" no matter how
# good the score is. Standard single-name risk limit for a retail book.
OVERWEIGHT_PCT         = 15.0
# Minimum share of the factor model that must have data before the
# scorecard is trusted over the legacy tree.
MIN_COVERAGE_PCT       = 45.0


# ── Public entry point ──────────────────────────────────────────

def apply_rules(stock: StockAnalysis) -> StockAnalysis:
    """Mutates `stock` in place: sets `rule_action`, `rule_conviction`,
    `rule_horizon`, `rule_target_price`, `rule_reasoning`, plus the
    scorecard fields (`rule_rating`, `rule_score`, `rule_risk_grade`,
    `rule_risk_score`, `rule_scorecard`). Returns the same object for
    chaining convenience."""
    pnl_pct       = _val(stock.pnl_pct, 0.0)
    rsi           = _val(stock.rsi_daily, 50.0)
    above_sma200  = bool(_val(stock.above_sma_200, True))
    high_52w      = _val(stock.high_52w, 0.0)
    low_52w       = _val(stock.low_52w, 0.0)
    current       = _val(stock.current_price, 0.0)

    # Distance from 52w extremes (negative = below high; positive = above low).
    pct_from_high = _pct_diff(current, high_52w)
    pct_from_low  = _pct_diff(current, low_52w)

    ts = now_ist()

    # ── Layer 1: factor scorecard ──
    card = score_metrics(_scoring_inputs(stock))
    stock.rule_rating     = Field(value=card.rating, source=SRC_RULE_ENGINE, as_of=ts)
    stock.rule_score      = Field(value=round(card.composite, 1),
                                  source=SRC_RULE_ENGINE, as_of=ts)
    stock.rule_risk_grade = Field(value=card.risk_grade, source=SRC_RULE_ENGINE, as_of=ts)
    stock.rule_risk_score = Field(value=round(card.risk_score, 1),
                                  source=SRC_RULE_ENGINE, as_of=ts)
    stock.rule_scorecard  = Field(value=card.to_dict(), source=SRC_RULE_ENGINE, as_of=ts)

    # ── Layer 2: instruction ──
    if card.coverage_pct >= MIN_COVERAGE_PCT:
        action, conviction, horizon, target, why = _decide_from_scorecard(
            card=card,
            pnl_pct=pnl_pct,
            weight_pct=_val(stock.weight_in_portfolio_pct, 0.0),
            current=current,
            high_52w=high_52w,
        )
    else:
        action, conviction, horizon, target, why = _decide(
            pnl_pct=pnl_pct,
            rsi=rsi,
            above_sma200=above_sma200,
            pct_from_high=pct_from_high,
            pct_from_low=pct_from_low,
            current=current,
            high_52w=high_52w,
            low_52w=low_52w,
        )
        why = (f"{why} (Thin history — {card.coverage_pct:.0f}% factor "
               f"coverage, so the threshold rules decided this one.)")

    stock.rule_action       = Field(value=action,     source=SRC_RULE_ENGINE, as_of=ts)
    stock.rule_conviction   = Field(value=conviction, source=SRC_RULE_ENGINE, as_of=ts)
    stock.rule_horizon      = Field(value=horizon,    source=SRC_RULE_ENGINE, as_of=ts)
    stock.rule_target_price = Field(value=target,     source=SRC_RULE_ENGINE, as_of=ts)
    stock.rule_reasoning    = Field(value=why,        source=SRC_RULE_ENGINE, as_of=ts)
    return stock


def _scoring_inputs(stock: StockAnalysis) -> dict[str, Any]:
    """Flatten a StockAnalysis into the plain dict `scoring.score()` wants."""
    quant = stock.quant.value if (stock.quant and stock.quant.value) else {}
    data: dict[str, Any] = dict(quant) if isinstance(quant, dict) else {}
    data.update({
        "rsi_daily": _opt(stock.rsi_daily),
        "pe": _opt(stock.weighted_pe),
        "dividend_yield_pct": _opt(stock.dividend_yield_ttm),
        "sector": _opt_str(stock.sector),
        "market_cap_tier": _opt_str(stock.market_cap_tier),
        "weight_pct": _opt(stock.weight_in_portfolio_pct),
        "pnl_pct": _opt(stock.pnl_pct),
    })
    # The quant profile computes beta itself, but the enricher's
    # 250-day beta is the established number — prefer it when present.
    beta = _opt(stock.beta_vs_nifty)
    if beta is not None:
        data["beta"] = beta
    if data.get("above_sma_200") is None and stock.above_sma_200 is not None:
        data["above_sma_200"] = stock.above_sma_200.value
    return data


# ── Layer 2: rating + position context -> instruction ───────────

def _decide_from_scorecard(*, card: Scorecard, pnl_pct: float,
                           weight_pct: float, current: float,
                           high_52w: float
                           ) -> tuple[str, str, str, str, str]:
    """Map (rating, risk, position) onto the action vocabulary the rest
    of the app already speaks."""
    rating = card.rating
    overweight = weight_pct >= OVERWEIGHT_PCT
    high_risk = card.risk_grade in ("HIGH", "VERY HIGH")

    evidence = "; ".join(
        d for p in card.pillars for d in p.drivers
    )[:400] or "no standout factor drivers"
    risk_note = ("; ".join(card.risk_drivers)[:200]
                 or "no elevated risk flags")

    if rating in ("STRONG BUY", "BUY"):
        if overweight:
            action, conviction, horizon = "HOLD", card.conviction, "Long (2-3 years)"
            verdict = (
                f"{rating} on fundamentals and trend, but it is already "
                f"{weight_pct:.1f}% of the book (limit {OVERWEIGHT_PCT:.0f}%). "
                "Hold — do not add to a position this size."
            )
        elif pnl_pct <= DEEP_LOSS_PCT:
            action, conviction, horizon = "AVERAGE DOWN", card.conviction, "Long (2-3 years)"
            verdict = (
                f"{rating} despite being down {pnl_pct:+.1f}%. The factor model "
                "still ranks it well, so average in tranches rather than in one go."
            )
        else:
            action = "BUY MORE" if rating == "STRONG BUY" and not high_risk else "HOLD"
            conviction, horizon = card.conviction, "Long (2-3 years)"
            verdict = (
                f"{rating} — composite {card.composite:.0f}/100. "
                + ("Add on weakness; keep the position inside your single-name limit."
                   if action == "BUY MORE"
                   else "Keep holding; the thesis is working.")
            )
        target = _band(high_52w * 1.05, high_52w * 1.18) if high_52w > 0 else \
                 _band(current * 1.10, current * 1.25)

    elif rating == "HOLD":
        action, conviction, horizon = "HOLD", card.conviction, "Medium (6-18 months)"
        verdict = (
            f"Balanced scorecard ({card.composite:.0f}/100). Nothing here "
            "argues for adding or cutting — hold and re-review next cycle."
        )
        target = _band(current * 1.05, current * 1.15) if current > 0 else "monitor"

    elif rating == "REDUCE":
        action, conviction, horizon = "PARTIAL EXIT", card.conviction, "Short (<6 months)"
        verdict = (
            f"Scorecard has weakened to {card.composite:.0f}/100 with "
            f"{card.risk_grade.lower()} risk. Trim roughly a quarter to a third; "
            "keep a stub only if you still believe the long-term story."
        )
        target = _band(current * 0.92, current * 1.02)

    else:  # SELL
        action = "FULL EXIT" if (high_risk or pnl_pct <= DEEP_LOSS_PCT) else "PARTIAL EXIT"
        conviction, horizon = card.conviction, "Short (<6 months)"
        verdict = (
            f"Weakest band ({card.composite:.0f}/100) with {card.risk_grade.lower()} "
            "risk. Trend, momentum and quality all point down — "
            + ("exit and redeploy." if action == "FULL EXIT"
               else "cut at least half and reassess.")
        )
        target = _band(current * 0.85, current * 0.95)

    if overweight and action in ("BUY MORE", "AVERAGE DOWN"):
        action = "HOLD"

    why = (
        f"{verdict} Rating {rating} ({card.composite:.0f}/100), risk "
        f"{card.risk_grade} ({card.risk_score:.0f}/100). "
        f"Drivers: {evidence}. Risk flags: {risk_note}."
    )
    return action, conviction, horizon, target, why


# ── Rule body ──────────────────────────────────────────────────

def _decide(*, pnl_pct: float, rsi: float, above_sma200: bool,
            pct_from_high: float, pct_from_low: float,
            current: float, high_52w: float, low_52w: float
            ) -> tuple[str, str, str, str, str]:
    """Returns (action, conviction, horizon, target, reasoning)."""

    # 1. Extended gain near 52w high with overbought RSI -> trim.
    if pnl_pct >= EXTENDED_GAIN_PCT \
            and pct_from_high >= NEAR_52W_HIGH_PCT \
            and rsi >= RSI_OVERBOUGHT:
        target = _band(current * 0.92, current * 0.96)
        return (
            "PARTIAL EXIT", "Medium", "Short (<6 months)", target,
            f"Up {pnl_pct:+.1f}% with RSI {rsi:.0f} and price within "
            f"{abs(pct_from_high):.1f}% of 52w high. Book partial profits; "
            "let the rest ride with a trailing stop. Long-term thesis "
            "intact \u2014 not a full exit."
        )

    # 2. Up handsomely but not yet extended -> hold and let it run.
    if pnl_pct >= 25.0 and above_sma200 and rsi < RSI_OVERBOUGHT:
        target = _band(high_52w * 1.05, high_52w * 1.15) if high_52w > 0 else "monitor"
        return (
            "HOLD", "Medium", "Long (2-3 years)", target,
            f"Up {pnl_pct:+.1f}% above SMA-200 with RSI {rsi:.0f} (not "
            "overbought yet). Trend intact \u2014 hold; review on RSI > 70 "
            "or break of SMA-200."
        )

    # 3. Deep loss but trend repairing (above SMA-200, RSI not crashed) -> average.
    if pnl_pct <= DEEP_LOSS_PCT and above_sma200 and rsi >= 40.0:
        target = _band(current * 1.15, current * 1.25)
        return (
            "AVERAGE DOWN", "Medium", "Long (2-3 years)", target,
            f"Down {pnl_pct:+.1f}% but reclaimed SMA-200 (trend repairing) "
            f"and RSI {rsi:.0f} above panic. Average in tranches if "
            "thesis intact; do NOT add on a single red day."
        )

    # 4. Deep loss AND broken trend -> partial exit, cut concentration.
    if pnl_pct <= DEEP_LOSS_PCT and (not above_sma200) and rsi <= RSI_OVERSOLD:
        target = _band(current * 0.85, current * 0.95)
        return (
            "PARTIAL EXIT", "High", "Short (<6 months)", target,
            f"Down {pnl_pct:+.1f}% AND below SMA-200 with RSI {rsi:.0f}. "
            "Trend broken; cut at least a third to limit further bleed. "
            "Re-evaluate thesis before adding back."
        )

    # 5. Mild loss with broken trend -> partial exit (smaller).
    if pnl_pct <= MILD_LOSS_PCT and (not above_sma200):
        target = _band(current * 0.90, current * 1.00)
        return (
            "PARTIAL EXIT", "Low", "Short (<6 months)", target,
            f"Down {pnl_pct:+.1f}% and below SMA-200. Trim ~25% to "
            "reduce drag; full exit only if thesis is broken."
        )

    # 6. Near 52w low with oversold RSI but trend repairing -> add cautiously.
    if pct_from_low is not None \
            and pct_from_low <= 10.0 \
            and rsi <= RSI_OVERSOLD \
            and above_sma200:
        target = _band(current * 1.10, current * 1.20)
        return (
            "AVERAGE DOWN", "Low", "Long (2-3 years)", target,
            f"Within {pct_from_low:.1f}% of 52w low with RSI {rsi:.0f} "
            "(oversold) but back above SMA-200. Small add only \u2014 wait "
            "for confirmation candle before sizing up."
        )

    # 7. Default: HOLD (everything else).
    target = _band(current * 1.05, current * 1.15) if current > 0 else "monitor"
    note = (
        f"P&L {pnl_pct:+.1f}%, RSI {rsi:.0f}, "
        f"{'above' if above_sma200 else 'below'} SMA-200. "
        "No high-conviction trigger \u2014 hold and re-review next cycle."
    )
    return ("HOLD", "Medium", "Medium (6-18 months)", target, note)


# ── Helpers ────────────────────────────────────────────────────

def _val(field: Field | None, default: float) -> float:
    if field is None or field.value is None:
        return default
    try:
        return float(field.value)
    except (TypeError, ValueError):
        return default


def _opt(field: Field | None) -> float | None:
    """Field value as a float, or None when absent — the scorecard
    treats None as 'no data' and renormalises around it."""
    if field is None or field.value is None:
        return None
    try:
        return float(field.value)
    except (TypeError, ValueError):
        return None


def _opt_str(field: Field | None) -> str | None:
    if field is None or field.value is None:
        return None
    return str(field.value)


def _pct_diff(a: float, b: float) -> float:
    """((a - b) / b) * 100 — returns 0 when b == 0."""
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def _band(lo: float, hi: float) -> str:
    """Format a price band like 'Rs.450-500'."""
    if hi <= 0:
        return "monitor"
    return f"Rs.{int(round(lo))}-{int(round(hi))}"
