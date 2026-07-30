"""
modes/swing/conviction.py
=========================

Conviction and risk grading for a swing candidate (2026-07-31).

The scanner already produces a raw setup score (roughly 0-9) from
`signals.classify_setup()`. That score answers "does a setup exist?" but
it is not comparable across setup families, it says nothing about how
much the trade can hurt, and a raw 5.5 means nothing to a reader.

This module adds the two numbers a trader actually acts on:

* **Conviction 0-100 + grade A/B/C/D** — how strong is the case, blending
  the setup score with trend quality, relative strength, participation
  (volume), trend strength (ADX), and the volatility regime.
* **Risk 0-100 + grade LOW..VERY HIGH** — how much can it hurt, from ATR,
  stop distance, drawdown, liquidity, gap behaviour and beta.

Both are deliberately separate from the R:R the risk engine computes:
R:R is arithmetic about one trade's geometry, this is about the quality
of the underlying signal and the tradability of the instrument.

Everything is pure arithmetic over the indicator dict plus an optional
`shared.quant_metrics.profile()` dict, so it runs identically for NSE
and US candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CONVICTION_BANDS: list[tuple[float, str]] = [
    (78.0, "A"),    # take it at full size
    (62.0, "B"),    # take it, size normally
    (45.0, "C"),    # marginal — half size or skip
    (0.0, "D"),     # skip
]

RISK_BANDS: list[tuple[float, str]] = [
    (72.0, "VERY HIGH"),
    (55.0, "HIGH"),
    (35.0, "MODERATE"),
    (0.0, "LOW"),
]

# Raw setup score that maps to a 100 on the setup component. Above ~9 the
# detectors have effectively saturated, so anything higher adds nothing.
SETUP_SCORE_CEILING = 9.0

# Liquidity floor below which a swing entry is not realistically
# tradable without slippage eating the edge.
MIN_TURNOVER_INR = 2.0e7      # Rs.2 crore/day
MIN_TURNOVER_USD = 5.0e6      # $5m/day


@dataclass
class ConvictionResult:
    conviction: float               # 0-100
    conviction_grade: str           # A | B | C | D
    risk: float                     # 0-100 (higher = riskier)
    risk_grade: str                 # LOW | MODERATE | HIGH | VERY HIGH
    components: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "conviction": round(self.conviction, 1),
            "conviction_grade": self.conviction_grade,
            "risk": round(self.risk, 1),
            "risk_grade": self.risk_grade,
            "components": {k: round(v, 1) for k, v in self.components.items()},
            "notes": self.notes,
            "risk_notes": self.risk_notes,
        }


# ── Primitives ───────────────────────────────────────────────────

def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _linear(value: float | None, worst: float, best: float) -> float | None:
    """Map onto 0-100. Pass worst > best when lower values are better."""
    if value is None:
        return None
    if worst == best:
        return 50.0
    return _clamp((value - worst) / (best - worst) * 100.0)


def _blend(parts: list[tuple[float | None, float]]) -> float | None:
    got = [(s, w) for s, w in parts if s is not None]
    if not got:
        return None
    used = sum(w for _, w in got)
    return sum(s * w for s, w in got) / used if used > 0 else None


def _band(value: float, bands: list[tuple[float, str]]) -> str:
    for threshold, label in bands:
        if value >= threshold:
            return label
    return bands[-1][1]


def _num(d: dict, key: str) -> float | None:
    v = d.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Conviction ───────────────────────────────────────────────────

def _conviction(ind: dict, quant: dict, setup_score: float,
                setup_type: str) -> tuple[float, dict[str, float], list[str]]:
    notes: list[str] = []
    components: dict[str, float] = {}

    # 1. Setup strength — the scanner's own verdict, normalised.
    setup = _clamp(setup_score / SETUP_SCORE_CEILING * 100.0)
    components["setup"] = setup
    notes.append(f"{setup_type or 'NONE'} setup scored {setup_score:.1f}")

    # 2. Trend quality — stacked moving averages and 52-week position.
    trend_parts: list[tuple[float | None, float]] = []
    current = _num(ind, "current")
    sma_50 = _num(ind, "sma_50")
    sma_200 = _num(ind, "sma_200")
    ema_20 = _num(ind, "ema_20")
    if current and sma_200:
        trend_parts.append((80.0 if current > sma_200 else 20.0, 1.2))
    if sma_50 and sma_200:
        trend_parts.append((80.0 if sma_50 > sma_200 else 25.0, 1.0))
    if ema_20 and sma_50:
        trend_parts.append((70.0 if ema_20 > sma_50 else 35.0, 0.7))
    if ind.get("weekly_trend_up") is not None:
        trend_parts.append((75.0 if ind.get("weekly_trend_up") else 30.0, 0.9))
    pos = _num(quant, "range_position_pct")
    if pos is not None:
        trend_parts.append((_clamp(100.0 - abs(pos - 78.0) * 1.2), 0.9))
    trend = _blend(trend_parts)
    if trend is not None:
        components["trend"] = trend
        if trend >= 70:
            notes.append("Moving averages stacked bullishly")
        elif trend <= 35:
            notes.append("Trend structure is weak")

    # 3. Relative strength — the single most persistent swing edge.
    rs_parts: list[tuple[float | None, float]] = []
    rs_ind = _num(ind, "rel_strength")
    if rs_ind is not None:
        rs_parts.append((_linear(rs_ind, -15.0, 20.0), 1.0))
    for key, weight in (("rs_3m_pct", 1.0), ("rs_6m_pct", 0.7)):
        rs_parts.append((_linear(_num(quant, key), -18.0, 22.0), weight))
    mom = _num(quant, "momentum_12_1_pct")
    if mom is not None:
        rs_parts.append((_linear(mom, -30.0, 50.0), 0.8))
    rs = _blend(rs_parts)
    if rs is not None:
        components["relative_strength"] = rs
        if rs_ind is not None and rs_ind >= 8:
            notes.append(f"Outperforming the index by {rs_ind:.1f}pp")
        elif rs_ind is not None and rs_ind <= -8:
            notes.append(f"Lagging the index by {abs(rs_ind):.1f}pp")

    # 4. Participation — a move without volume is a move without buyers.
    vol_parts: list[tuple[float | None, float]] = []
    vr = _num(ind, "vol_ratio")
    if vr is not None:
        vol_parts.append((_linear(vr, 0.6, 2.2), 1.0))
        if vr >= 1.5:
            notes.append(f"Volume {vr:.1f}x the 20-day average")
    vt = _num(quant, "volume_trend_ratio")
    if vt is not None:
        vol_parts.append((_linear(vt, 0.7, 1.5), 0.6))
    volume = _blend(vol_parts)
    if volume is not None:
        components["volume"] = volume

    # 5. Trend strength — ADX separates a real trend from chop.
    adx_val = _num(ind, "adx")
    if adx_val is not None:
        components["trend_strength"] = _clamp(_linear(adx_val, 12.0, 38.0) or 50.0)
        if adx_val >= 25:
            notes.append(f"ADX {adx_val:.0f} — trending")
        elif adx_val <= 15:
            notes.append(f"ADX {adx_val:.0f} — choppy, no trend")

    # 6. Volatility regime — mid volatility is ideal. Too quiet means no
    #    move to capture; too wild means the stop gets hit on noise.
    atrp = _num(quant, "atr_pct")
    if atrp is None:
        atr14 = _num(ind, "atr_14")
        if atr14 and current:
            atrp = atr14 / current * 100.0
    if atrp is not None:
        components["volatility_fit"] = _clamp(100.0 - abs(atrp - 2.6) * 22.0)
        if atrp >= 6:
            notes.append(f"ATR {atrp:.1f}% of price — very wide daily range")

    weights = {
        "setup": 2.4, "trend": 1.7, "relative_strength": 1.5,
        "volume": 1.0, "trend_strength": 0.8, "volatility_fit": 0.7,
    }
    total = _blend([(components.get(k), w) for k, w in weights.items()])
    return (total if total is not None else 50.0), components, notes


# ── Risk ─────────────────────────────────────────────────────────

def _risk(ind: dict, quant: dict, *,
          entry: float, stop: float, usd: bool) -> tuple[float, list[str]]:
    notes: list[str] = []
    parts: list[tuple[float | None, float]] = []

    current = _num(ind, "current") or entry

    atrp = _num(quant, "atr_pct")
    if atrp is None:
        atr14 = _num(ind, "atr_14")
        if atr14 and current:
            atrp = atr14 / current * 100.0
    if atrp is not None:
        parts.append((_linear(atrp, 1.0, 6.5), 1.3))
        if atrp >= 5:
            notes.append(f"ATR is {atrp:.1f}% of price")

    # Stop distance: a wide stop means more rupees at risk per share and
    # a longer road back to breakeven.
    if entry > 0 and stop > 0 and stop < entry:
        stop_pct = (entry - stop) / entry * 100.0
        parts.append((_linear(stop_pct, 3.0, 15.0), 1.2))
        if stop_pct >= 12:
            notes.append(f"Stop is {stop_pct:.1f}% away")

    vol = _num(quant, "volatility_90d_pct")
    if vol is not None:
        parts.append((_linear(vol, 15.0, 70.0), 1.1))
        if vol >= 50:
            notes.append(f"Annualised volatility {vol:.0f}%")

    mdd = _num(quant, "max_drawdown_1y_pct")
    if mdd is not None:
        parts.append((_linear(mdd, 12.0, 60.0), 0.9))

    beta = _num(quant, "beta")
    if beta is not None:
        parts.append((_linear(beta, 0.6, 1.9), 0.8))
        if beta >= 1.5:
            notes.append(f"Beta {beta:.2f}")

    turnover = _num(quant, "avg_turnover")
    floor = MIN_TURNOVER_USD if usd else MIN_TURNOVER_INR
    if turnover is not None and turnover > 0:
        parts.append((_linear(turnover / floor, 25.0, 0.5), 1.0))
        if turnover < floor:
            notes.append(
                f"Thin liquidity: {'$' if usd else 'Rs.'}"
                f"{turnover/1e6:.1f}m average daily traded value"
            )

    # Buying far below the 52-week high is buying into a downtrend; the
    # setup may still be valid but the risk is objectively higher.
    dd_high = _num(quant, "drawdown_from_high_pct")
    if dd_high is not None:
        parts.append((_linear(dd_high, 5.0, 45.0), 0.7))
        if dd_high >= 35:
            notes.append(f"{dd_high:.0f}% below its 52-week high")

    if quant.get("trend_state") == "DEATH_CROSS":
        parts.append((72.0, 0.8))
        notes.append("50-DMA below 200-DMA")

    score = _blend(parts)
    return (score if score is not None else 45.0), notes


# ── Public API ───────────────────────────────────────────────────

def grade(ind: dict[str, Any],
          *,
          setup_score: float,
          setup_type: str = "",
          quant: dict[str, Any] | None = None,
          entry_price: float = 0.0,
          stop_price: float = 0.0,
          usd: bool = False) -> ConvictionResult:
    """Grade one swing candidate.

    `ind` is the dict from `signals.compute_swing_indicators()`;
    `quant` is an optional `shared.quant_metrics.profile()` dict. Missing
    inputs are skipped, never treated as zero.
    """
    ind = ind or {}
    quant = quant or {}

    conviction, components, notes = _conviction(
        ind, quant, setup_score, setup_type)
    risk, risk_notes = _risk(
        ind, quant,
        entry=entry_price or _num(ind, "current") or 0.0,
        stop=stop_price, usd=usd,
    )

    return ConvictionResult(
        conviction=conviction,
        conviction_grade=_band(round(conviction), CONVICTION_BANDS),
        risk=risk,
        risk_grade=_band(round(risk), RISK_BANDS),
        components=components,
        notes=notes,
        risk_notes=risk_notes,
    )


__all__ = [
    "CONVICTION_BANDS", "ConvictionResult", "RISK_BANDS", "grade",
]
