"""
modes/analyze/scoring.py
========================

Multi-factor scorecard for a single holding (2026-07-31).

Before this module the analyser had one 7-branch if/elif tree over
``pnl_pct`` / ``rsi`` / ``above_sma_200``. That is three inputs deciding
a long-term recommendation, and it could not express *degree* — a name
scraping past a threshold got the same verdict as one clearing it by a
mile.

This replaces it with the structure equity research desks actually use:
score a set of orthogonal **factor pillars** 0-100, weight them into a
composite, and map the composite onto a rating band. Separately score
**risk**, because "should I own it" and "how much can it hurt me" are
two different questions that a single number cannot answer.

Pillars (weights sum to 100)
----------------------------
=====================  ===  ===================================================
Trend                   22  Where price sits vs its own structure
Momentum                24  Is it working, and better than the index
Risk-adjusted return    14  Sharpe / Sortino — return per unit of pain
Quality & stability     14  Volatility, drawdown, beta, cap tier
Valuation & income      14  P/E vs sector, dividend yield
Position context        12  Your cost basis, weight, conviction drift
=====================  ===  ===================================================

Missing inputs never score zero — a pillar with no data is dropped and
the remaining weights are renormalised, so a stock with no P/E seed is
not silently punished for it. `coverage_pct` reports how much of the
model actually had data.

Ratings are the standard five-point sell-side scale so they mean what a
reader expects: STRONG BUY / BUY / HOLD / REDUCE / SELL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Rating bands (composite 0-100) ───────────────────────────────

RATING_BANDS: list[tuple[float, str]] = [
    (78.0, "STRONG BUY"),
    (62.0, "BUY"),
    (45.0, "HOLD"),
    (32.0, "REDUCE"),
    (0.0, "SELL"),
]

# ── Risk bands (risk score 0-100, higher = riskier) ──────────────

RISK_BANDS: list[tuple[float, str]] = [
    (72.0, "VERY HIGH"),
    (55.0, "HIGH"),
    (35.0, "MODERATE"),
    (0.0, "LOW"),
]

PILLAR_WEIGHTS: dict[str, float] = {
    "trend": 22.0,
    "momentum": 24.0,
    "risk_adjusted": 14.0,
    "quality": 14.0,
    "valuation": 14.0,
    "position": 12.0,
}

# Sector P/E medians for the Indian market — a bank on 14x and an FMCG
# name on 55x are both "fairly valued" for their sector, so an absolute
# P/E cutoff would be meaningless. Rough NIFTY-sector medians; refresh
# alongside data/fundamentals_seed.json.
SECTOR_PE_MEDIAN: dict[str, float] = {
    "BANKING": 15.0, "FINANCE": 20.0, "IT": 26.0, "PHARMA": 30.0,
    "AUTO": 24.0, "ENERGY": 14.0, "METALS": 12.0, "FMCG": 45.0,
    "INFRA": 22.0, "TELECOM": 40.0, "CAPGOODS": 45.0, "OTHER": 25.0,
}
DEFAULT_PE_MEDIAN = 25.0


@dataclass
class PillarScore:
    """One factor pillar: its 0-100 score plus the evidence behind it."""
    name: str
    score: float
    weight: float
    covered: bool
    drivers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "score": round(self.score, 1),
            "weight": self.weight, "covered": self.covered,
            "drivers": self.drivers,
        }


@dataclass
class Scorecard:
    """Full result of scoring one holding."""
    composite: float            # 0-100
    rating: str                 # STRONG BUY .. SELL
    risk_score: float           # 0-100 (higher = riskier)
    risk_grade: str             # LOW .. VERY HIGH
    conviction: str             # Low | Medium | High
    coverage_pct: float         # how much of the model had data
    pillars: list[PillarScore]
    risk_drivers: list[str]
    summary: str

    def to_dict(self) -> dict:
        return {
            "composite": round(self.composite, 1),
            "rating": self.rating,
            "risk_score": round(self.risk_score, 1),
            "risk_grade": self.risk_grade,
            "conviction": self.conviction,
            "coverage_pct": round(self.coverage_pct, 1),
            "pillars": [p.to_dict() for p in self.pillars],
            "risk_drivers": self.risk_drivers,
            "summary": self.summary,
        }


# ── Small scoring primitives ─────────────────────────────────────

def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _linear(value: float | None, worst: float, best: float) -> float | None:
    """Map `value` onto 0-100 between `worst` and `best`.

    Works in both directions: pass worst > best for metrics where lower
    is better (volatility, drawdown).
    """
    if value is None:
        return None
    if worst == best:
        return 50.0
    return _clamp((value - worst) / (best - worst) * 100.0)


def _blend(parts: list[tuple[float | None, float]]) -> tuple[float | None, float]:
    """Weighted mean over (score, weight) pairs, skipping None scores.

    Returns (score, covered_weight_fraction). Renormalising over only the
    available inputs is what stops a missing data point from acting like
    a zero.
    """
    total_w = sum(w for _, w in parts)
    got = [(s, w) for s, w in parts if s is not None]
    if not got or total_w <= 0:
        return None, 0.0
    used_w = sum(w for _, w in got)
    return sum(s * w for s, w in got) / used_w, used_w / total_w


def _band(value: float, bands: list[tuple[float, str]]) -> str:
    for threshold, label in bands:
        if value >= threshold:
            return label
    return bands[-1][1]


# ── Pillars ──────────────────────────────────────────────────────

def _trend_pillar(m: dict) -> PillarScore:
    drivers: list[str] = []
    parts: list[tuple[float | None, float]] = []

    state = m.get("trend_state")
    if state == "GOLDEN_CROSS":
        parts.append((80.0, 1.0))
        drivers.append("50-DMA above 200-DMA (golden cross)")
    elif state == "DEATH_CROSS":
        parts.append((20.0, 1.0))
        drivers.append("50-DMA below 200-DMA (death cross)")

    above200 = m.get("above_sma_200")
    if above200 is not None:
        parts.append((78.0 if above200 else 22.0, 1.0))
        drivers.append("Above 200-DMA" if above200 else "Below 200-DMA")

    above50 = m.get("above_sma_50")
    if above50 is not None:
        parts.append((70.0 if above50 else 30.0, 0.6))

    pos = m.get("range_position_pct")
    if pos is not None:
        # Mid-to-upper range is healthy; the very top is extended, the
        # bottom is a downtrend. Peak the curve around 80.
        score = 100.0 - abs(pos - 80.0) * 1.15
        parts.append((_clamp(score), 1.0))
        drivers.append(f"{pos:.0f}th percentile of 52-week range")

    dd = m.get("drawdown_from_high_pct")
    if dd is not None:
        parts.append((_linear(dd, 45.0, 0.0), 0.8))

    score, coverage = _blend(parts)
    return PillarScore("trend", score if score is not None else 50.0,
                       PILLAR_WEIGHTS["trend"], coverage > 0, drivers)


def _momentum_pillar(m: dict) -> PillarScore:
    drivers: list[str] = []
    parts: list[tuple[float | None, float]] = []

    mom = m.get("momentum_12_1_pct")
    if mom is not None:
        parts.append((_linear(mom, -35.0, 55.0), 1.4))
        drivers.append(f"12-1 momentum {mom:+.1f}%")

    for key, _label, weight in (("return_3m_pct", "3M", 0.9),
                               ("return_6m_pct", "6M", 0.8)):
        val = m.get(key)
        if val is not None:
            parts.append((_linear(val, -25.0, 35.0), weight))

    rs3 = m.get("rs_3m_pct")
    if rs3 is not None:
        parts.append((_linear(rs3, -20.0, 25.0), 1.2))
        drivers.append(f"{rs3:+.1f}pp vs index over 3M")

    rs12 = m.get("rs_12m_pct")
    if rs12 is not None:
        parts.append((_linear(rs12, -30.0, 40.0), 0.9))

    rsi = m.get("rsi_daily")
    if rsi is not None:
        # 55-65 is the sweet spot: trending without being stretched.
        score = 100.0 - abs(rsi - 60.0) * 2.2
        parts.append((_clamp(score), 0.7))
        if rsi >= 70:
            drivers.append(f"RSI {rsi:.0f} — overbought")
        elif rsi <= 35:
            drivers.append(f"RSI {rsi:.0f} — oversold")

    vt = m.get("volume_trend_ratio")
    if vt is not None:
        parts.append((_linear(vt, 0.6, 1.6), 0.5))
        if vt >= 1.25:
            drivers.append(f"Volume {vt:.2f}x its 60-day average")

    score, coverage = _blend(parts)
    return PillarScore("momentum", score if score is not None else 50.0,
                       PILLAR_WEIGHTS["momentum"], coverage > 0, drivers)


def _risk_adjusted_pillar(m: dict) -> PillarScore:
    drivers: list[str] = []
    parts: list[tuple[float | None, float]] = []

    sharpe = m.get("sharpe_1y")
    if sharpe is not None:
        parts.append((_linear(sharpe, -1.0, 2.5), 1.2))
        drivers.append(f"Sharpe {sharpe:.2f}")

    sortino = m.get("sortino_1y")
    if sortino is not None:
        parts.append((_linear(sortino, -1.0, 3.5), 1.0))

    up = m.get("up_capture_pct")
    down = m.get("down_capture_pct")
    if up is not None and down is not None:
        parts.append((_linear(up - down, -40.0, 60.0), 0.9))
        drivers.append(f"Capture {up:.0f}% up / {down:.0f}% down")

    score, coverage = _blend(parts)
    return PillarScore("risk_adjusted", score if score is not None else 50.0,
                       PILLAR_WEIGHTS["risk_adjusted"], coverage > 0, drivers)


def _quality_pillar(m: dict) -> PillarScore:
    drivers: list[str] = []
    parts: list[tuple[float | None, float]] = []

    vol = m.get("volatility_90d_pct")
    if vol is not None:
        parts.append((_linear(vol, 65.0, 15.0), 1.2))
        if vol >= 45:
            drivers.append(f"Annualised volatility {vol:.0f}%")

    mdd = m.get("max_drawdown_1y_pct")
    if mdd is not None:
        parts.append((_linear(mdd, 55.0, 10.0), 1.0))
        if mdd >= 35:
            drivers.append(f"1-year max drawdown {mdd:.0f}%")

    beta = m.get("beta")
    if beta is not None:
        # Reward ~1.0; punish both a high-beta cannon and a dead stock.
        parts.append((_clamp(100.0 - abs(beta - 0.95) * 65.0), 0.8))
        drivers.append(f"Beta {beta:.2f}")

    tier = (m.get("market_cap_tier") or "").upper()
    tier_score = {"LARGE": 85.0, "ETF": 80.0, "MID": 58.0, "SMALL": 32.0}.get(tier)
    if tier_score is not None:
        parts.append((tier_score, 0.9))
        drivers.append(f"{tier.title()}-cap")

    turnover = m.get("avg_turnover")
    if turnover is not None and turnover > 0:
        # Rs.1cr/day is thin, Rs.100cr/day is deeply liquid.
        parts.append((_linear(min(turnover, 1e9) / 1e7, 1.0, 100.0), 0.7))
        if turnover < 2e7:
            drivers.append("Thin traded value (<Rs.2cr/day)")

    score, coverage = _blend(parts)
    return PillarScore("quality", score if score is not None else 50.0,
                       PILLAR_WEIGHTS["quality"], coverage > 0, drivers)


def _valuation_pillar(m: dict) -> PillarScore:
    drivers: list[str] = []
    parts: list[tuple[float | None, float]] = []

    pe = m.get("pe")
    if pe is not None and pe > 0:
        median = SECTOR_PE_MEDIAN.get((m.get("sector") or "").upper(),
                                      DEFAULT_PE_MEDIAN)
        premium = (pe / median - 1.0) * 100.0
        parts.append((_linear(premium, 90.0, -40.0), 1.3))
        drivers.append(
            f"P/E {pe:.1f} vs {median:.0f} sector median ({premium:+.0f}%)"
        )

    dy = m.get("dividend_yield_pct")
    if dy is not None:
        parts.append((_linear(dy, 0.0, 4.0), 0.7))
        if dy >= 2.0:
            drivers.append(f"Dividend yield {dy:.1f}%")

    score, coverage = _blend(parts)
    return PillarScore("valuation", score if score is not None else 50.0,
                       PILLAR_WEIGHTS["valuation"], coverage > 0, drivers)


def _position_pillar(m: dict) -> PillarScore:
    """How this name sits *inside your book* — not a view on the company.

    A great business at a 30% portfolio weight is still a problem, and a
    position already up 120% deserves a different answer than the same
    business bought yesterday.
    """
    drivers: list[str] = []
    parts: list[tuple[float | None, float]] = []

    weight = m.get("weight_pct")
    if weight is not None:
        # 3-8% is a healthy conviction position; above 15% is a risk.
        if weight <= 8.0:
            parts.append((75.0, 1.0))
        else:
            parts.append((_linear(weight, 30.0, 8.0), 1.0))
        if weight >= 15.0:
            drivers.append(f"{weight:.1f}% of the book — concentrated")

    pnl = m.get("pnl_pct")
    if pnl is not None:
        # Mild positive P&L scores best: a big winner may be extended and
        # a big loser needs a decision, not a shrug.
        if pnl < 0:
            parts.append((_linear(pnl, -50.0, 0.0) * 0.7 + 25.0, 0.9))
            if pnl <= -20:
                drivers.append(f"Down {pnl:.0f}% on cost")
        else:
            parts.append((_clamp(85.0 - max(0.0, pnl - 60.0) * 0.5), 0.9))
            if pnl >= 60:
                drivers.append(f"Up {pnl:.0f}% on cost — consider trimming")

    score, coverage = _blend(parts)
    return PillarScore("position", score if score is not None else 50.0,
                       PILLAR_WEIGHTS["position"], coverage > 0, drivers)


# ── Risk score ───────────────────────────────────────────────────

def _risk(m: dict) -> tuple[float, list[str]]:
    """0-100 where higher = more capital at risk. Independent of the
    rating: a low-risk stock can still be a SELL."""
    drivers: list[str] = []
    parts: list[tuple[float | None, float]] = []

    vol = m.get("volatility_90d_pct")
    if vol is not None:
        parts.append((_linear(vol, 12.0, 70.0), 1.4))
        if vol >= 45:
            drivers.append(f"Volatility {vol:.0f}% annualised")

    mdd = m.get("max_drawdown_1y_pct")
    if mdd is not None:
        parts.append((_linear(mdd, 8.0, 60.0), 1.2))
        if mdd >= 40:
            drivers.append(f"Fell {mdd:.0f}% peak-to-trough in the last year")

    beta = m.get("beta")
    if beta is not None:
        parts.append((_linear(beta, 0.5, 1.9), 1.0))
        if beta >= 1.4:
            drivers.append(f"High beta {beta:.2f}")

    down = m.get("down_capture_pct")
    if down is not None:
        parts.append((_linear(down, 50.0, 140.0), 0.9))
        if down >= 115:
            drivers.append(f"Captures {down:.0f}% of index downside")

    tier = (m.get("market_cap_tier") or "").upper()
    tier_risk = {"LARGE": 20.0, "ETF": 22.0, "MID": 55.0, "SMALL": 85.0}.get(tier)
    if tier_risk is not None:
        parts.append((tier_risk, 1.0))

    turnover = m.get("avg_turnover")
    if turnover is not None and turnover > 0:
        parts.append((_linear(min(turnover, 1e9) / 1e7, 100.0, 1.0), 0.8))

    weight = m.get("weight_pct")
    if weight is not None:
        parts.append((_linear(weight, 3.0, 25.0), 1.1))
        if weight >= 20:
            drivers.append(f"Single-name concentration {weight:.1f}%")

    state = m.get("trend_state")
    if state == "DEATH_CROSS":
        parts.append((70.0, 0.7))

    score, _ = _blend(parts)
    return (score if score is not None else 45.0), drivers


# ── Public API ───────────────────────────────────────────────────

def score(metrics: dict[str, Any]) -> Scorecard:
    """Score one holding.

    `metrics` is a flat dict — everything from
    `shared.quant_metrics.profile()` plus the analyser's own inputs:
    ``rsi_daily``, ``pe``, ``dividend_yield_pct``, ``sector``,
    ``market_cap_tier``, ``weight_pct``, ``pnl_pct``. Every key is
    optional.
    """
    pillars = [
        _trend_pillar(metrics),
        _momentum_pillar(metrics),
        _risk_adjusted_pillar(metrics),
        _quality_pillar(metrics),
        _valuation_pillar(metrics),
        _position_pillar(metrics),
    ]

    covered = [p for p in pillars if p.covered]
    total_w = sum(p.weight for p in covered)
    composite = (sum(p.score * p.weight for p in covered) / total_w
                 if total_w > 0 else 50.0)
    coverage_pct = total_w / sum(PILLAR_WEIGHTS.values()) * 100.0

    risk_score, risk_drivers = _risk(metrics)

    # Band on the *displayed* (rounded) value so a composite of 61.96
    # never renders as "62/100 (HOLD)" while the band table says 62 is a
    # BUY. Readers compare the number to the table; make them agree.
    rating = _band(round(composite), RATING_BANDS)
    risk_grade = _band(round(risk_score), RISK_BANDS)

    # Conviction reflects how much of the model actually had data and how
    # far the composite is from the fence — a 46 with half the inputs
    # missing is not a confident HOLD.
    distance = abs(composite - 50.0)
    if coverage_pct >= 80 and distance >= 20:
        conviction = "High"
    elif coverage_pct >= 55 and distance >= 8:
        conviction = "Medium"
    else:
        conviction = "Low"

    top = sorted(covered, key=lambda p: p.score * p.weight, reverse=True)
    best = top[0].name if top else "n/a"
    worst = top[-1].name if top else "n/a"
    summary = (
        f"Composite {composite:.0f}/100 ({rating}), risk {risk_score:.0f}/100 "
        f"({risk_grade}). Strongest pillar: {best}; weakest: {worst}. "
        f"Model coverage {coverage_pct:.0f}%."
    )

    return Scorecard(
        composite=composite, rating=rating,
        risk_score=risk_score, risk_grade=risk_grade,
        conviction=conviction, coverage_pct=coverage_pct,
        pillars=pillars, risk_drivers=risk_drivers, summary=summary,
    )


__all__ = [
    "PILLAR_WEIGHTS", "PillarScore", "RATING_BANDS", "RISK_BANDS",
    "Scorecard", "SECTOR_PE_MEDIAN", "score",
]
