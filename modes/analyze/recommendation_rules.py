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
# ================================================================

from __future__ import annotations

from config import now_ist
from modes.analyze.types import Field, StockAnalysis, SRC_RULE_ENGINE


# ── Tunable thresholds ──────────────────────────────────────────

DEEP_LOSS_PCT          = -25.0    # P&L% below this -> "AVERAGE DOWN" candidate
MILD_LOSS_PCT          = -10.0    # P&L% below this AND broken trend -> "PARTIAL EXIT"
EXTENDED_GAIN_PCT      = 50.0     # P&L% above this -> "PARTIAL EXIT" candidate
NEAR_52W_HIGH_PCT      = -5.0     # within 5% of 52w high
RSI_OVERBOUGHT         = 70.0
RSI_OVERSOLD           = 35.0
TREND_BROKEN_PCT       = -8.0     # price > this much below SMA200 = trend broken


# ── Public entry point ──────────────────────────────────────────

def apply_rules(stock: StockAnalysis) -> StockAnalysis:
    """Mutates `stock` in place: sets `rule_action`, `rule_conviction`,
    `rule_horizon`, `rule_target_price`, `rule_reasoning`. Returns
    the same object for chaining convenience."""
    pnl_pct       = _val(stock.pnl_pct, 0.0)
    rsi           = _val(stock.rsi_daily, 50.0)
    above_sma200  = bool(_val(stock.above_sma_200, True))
    high_52w      = _val(stock.high_52w, 0.0)
    low_52w       = _val(stock.low_52w, 0.0)
    current       = _val(stock.current_price, 0.0)

    # Distance from 52w extremes (negative = below high; positive = above low).
    pct_from_high = _pct_diff(current, high_52w)
    pct_from_low  = _pct_diff(current, low_52w)

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

    ts = now_ist()
    stock.rule_action       = Field(value=action,     source=SRC_RULE_ENGINE, as_of=ts)
    stock.rule_conviction   = Field(value=conviction, source=SRC_RULE_ENGINE, as_of=ts)
    stock.rule_horizon      = Field(value=horizon,    source=SRC_RULE_ENGINE, as_of=ts)
    stock.rule_target_price = Field(value=target,     source=SRC_RULE_ENGINE, as_of=ts)
    stock.rule_reasoning    = Field(value=why,        source=SRC_RULE_ENGINE, as_of=ts)
    return stock


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
