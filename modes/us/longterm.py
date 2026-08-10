"""Long-term investment scorecard for US equities.

Mirrors `modes/analyze/scoring.py` in shape — score orthogonal factor
pillars 0-100, weight into a composite, map onto a rating band — but the
pillars are chosen for a **buy-and-hold** horizon rather than a swing.

Why these pillars
-----------------
Each one is a factor with published long-horizon evidence, not a chart
pattern:

Quality & profitability  24  Novy-Marx (2013): gross profitability is the
                             single most durable cross-sectional quality
                             signal. ROE/ROA/margins/FCF conversion.
Growth durability        17  Compounding needs the business to grow, and
                             to keep growing — level plus consistency.
Valuation                18  Fama-French HML, judged against the SECTOR
                             median. Software at 30x and a bank at 12x
                             can both be fair; an absolute cutoff cannot
                             express that.
Long-horizon momentum    16  Jegadeesh-Titman 12-1 momentum, the most
                             replicated anomaly there is, plus a 200-DMA
                             trend filter to avoid value traps.
Financial strength       13  Piotroski-style solvency. What stops a
                             compounder becoming a permanent loss.
Risk & drawdown          12  Frazzini-Pedersen betting-against-beta:
                             lower-volatility names have historically
                             delivered better risk-adjusted long-run
                             returns.

Deliberately absent: ATR stops, R-multiples and price targets. Those are
trade constructs — they assume a planned exit in weeks. A long-term
holding is exited when the thesis breaks (quality decays, valuation runs
far ahead of fundamentals), which is what `rating` and `valuation_band`
express instead.

Missing inputs never score zero. An uncovered pillar is dropped and the
remaining weights are renormalised, so a company with no EV/EBITDA is
not punished for Yahoo's coverage gap; `coverage_pct` reports how much
of the model actually had data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shared.quant_metrics import (
    annualised_volatility_pct, max_drawdown_pct, momentum_12_1_pct,
    period_return_pct, sma,
)


# Composite 0-100 -> rating. Vocabulary is deliberately about
# accumulation rather than entry timing.
RATING_BANDS: list[tuple[float, str]] = [
    (76.0, "HIGH CONVICTION"),
    (62.0, "ACCUMULATE"),
    (46.0, "NEUTRAL"),
    (34.0, "WEAK"),
    (0.0, "AVOID"),
]

RISK_BANDS: list[tuple[float, str]] = [
    (72.0, "VERY HIGH"),
    (55.0, "HIGH"),
    (35.0, "MODERATE"),
    (0.0, "LOW"),
]

PILLAR_WEIGHTS: dict[str, float] = {
    "quality": 24.0,
    "valuation": 18.0,
    "growth": 17.0,
    "momentum": 16.0,
    "strength": 13.0,
    "risk": 12.0,
}

# US sector P/E medians. A software name on 32x and a bank on 12x are
# both fairly valued for what they are, so valuation is always scored
# relative to the sector rather than against one absolute number.
SECTOR_PE_MEDIAN: dict[str, float] = {
    "Technology": 30.0,
    "Communication Services": 20.0,
    "Healthcare": 22.0,
    "Financial Services": 14.0,
    "Consumer Cyclical": 22.0,
    "Consumer Defensive": 21.0,
    "Industrials": 22.0,
    "Energy": 13.0,
    "Utilities": 19.0,
    "Real Estate": 30.0,
    "Basic Materials": 17.0,
}
DEFAULT_PE_MEDIAN = 22.0


@dataclass
class Pillar:
    name: str
    score: float
    weight: float
    covered: bool
    drivers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "score": round(self.score, 1),
                "weight": self.weight, "covered": self.covered,
                "drivers": self.drivers}


@dataclass
class LongTermScore:
    composite: float
    rating: str
    risk_score: float
    risk_grade: str
    conviction: str
    coverage_pct: float
    valuation_band: str
    pillars: list[Pillar]
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
            "valuation_band": self.valuation_band,
            "pillars": [p.to_dict() for p in self.pillars],
            "risk_drivers": self.risk_drivers,
            "summary": self.summary,
        }


# ── Primitives ──────────────────────────────────────────────────

def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _linear(value: float | None, worst: float, best: float) -> float | None:
    """Map onto 0-100. Pass worst > best when lower is better."""
    if value is None:
        return None
    if worst == best:
        return None
    return _clamp((value - worst) / (best - worst) * 100.0)


def _blend(parts: list[tuple[float | None, float]]) -> tuple[float | None, float]:
    """Weighted mean over the parts that have data.

    Returns (score, covered_weight_fraction) so a pillar can report how
    much of itself was actually measurable.
    """
    total = sum(w for _, w in parts)
    got = [(v, w) for v, w in parts if v is not None]
    if not got or total <= 0:
        return None, 0.0
    used = sum(w for _, w in got)
    return sum(v * w for v, w in got) / used, used / total


def _band(score: float, bands: list[tuple[float, str]]) -> str:
    for floor, label in bands:
        if score >= floor:
            return label
    return bands[-1][1]


# ── Pillars ─────────────────────────────────────────────────────

def _quality(f: dict) -> Pillar:
    roe = f.get("roe_pct")
    gross = f.get("gross_margin_pct")
    oper = f.get("operating_margin_pct")
    fcfm = f.get("fcf_margin_pct")
    roa = f.get("roa_pct")

    score, cov = _blend([
        (_linear(roe, 0.0, 35.0), 0.28),
        (_linear(gross, 15.0, 70.0), 0.24),
        (_linear(oper, 0.0, 30.0), 0.20),
        (_linear(fcfm, 0.0, 25.0), 0.18),
        (_linear(roa, 0.0, 18.0), 0.10),
    ])
    drivers = []
    if roe is not None:
        drivers.append(f"ROE {roe:.1f}%")
    if gross is not None:
        drivers.append(f"gross margin {gross:.1f}%")
    if fcfm is not None:
        drivers.append(f"FCF margin {fcfm:.1f}%")
    return Pillar("Quality & profitability", score or 0.0,
                  PILLAR_WEIGHTS["quality"], score is not None, drivers)


def _valuation(f: dict) -> Pillar:
    sector = str(f.get("sector") or "")
    median_pe = SECTOR_PE_MEDIAN.get(sector, DEFAULT_PE_MEDIAN)
    pe = f.get("trailing_pe") or f.get("forward_pe")
    ev = f.get("ev_to_ebitda")
    fcfy = f.get("fcf_yield_pct")
    pb = f.get("price_to_book")

    # Relative P/E: 0.6x the sector median scores full marks, 2.2x zero.
    rel_pe = (pe / median_pe) if (pe and pe > 0 and median_pe > 0) else None
    score, cov = _blend([
        (_linear(rel_pe, 2.2, 0.6), 0.40),
        (_linear(ev, 28.0, 8.0), 0.22),
        (_linear(fcfy, 0.0, 8.0), 0.26),
        (_linear(pb, 12.0, 1.0), 0.12),
    ])

    drivers = []
    if pe:
        drivers.append(f"P/E {pe:.1f} vs {sector or 'market'} median {median_pe:.0f}")
    if fcfy is not None:
        drivers.append(f"FCF yield {fcfy:.1f}%")
    if ev is not None:
        drivers.append(f"EV/EBITDA {ev:.1f}")
    return Pillar("Valuation", score or 0.0, PILLAR_WEIGHTS["valuation"],
                  score is not None, drivers)


def _growth(f: dict) -> Pillar:
    rev = f.get("revenue_growth_pct")
    eps = f.get("earnings_growth_pct")
    score, cov = _blend([
        (_linear(rev, -5.0, 25.0), 0.55),
        (_linear(eps, -10.0, 30.0), 0.45),
    ])
    drivers = []
    if rev is not None:
        drivers.append(f"revenue growth {rev:+.1f}%")
    if eps is not None:
        drivers.append(f"earnings growth {eps:+.1f}%")
    return Pillar("Growth durability", score or 0.0, PILLAR_WEIGHTS["growth"],
                  score is not None, drivers)


def _momentum(closes: list[float], bench: list[float] | None) -> Pillar:
    mom = momentum_12_1_pct(closes)
    r12 = period_return_pct(closes, 252)
    bench_12 = period_return_pct(bench, 252) if bench else None
    rel = (r12 - bench_12) if (r12 is not None and bench_12 is not None) else None
    sma200 = sma(closes, 200)
    last = closes[-1] if closes else None
    above = (last > sma200) if (sma200 and last) else None

    trend_score = None if above is None else (100.0 if above else 25.0)
    score, cov = _blend([
        (_linear(mom, -25.0, 45.0), 0.45),
        (_linear(rel, -25.0, 30.0), 0.25),
        (trend_score, 0.30),
    ])
    drivers = []
    if mom is not None:
        drivers.append(f"12-1 momentum {mom:+.1f}%")
    if rel is not None:
        drivers.append(f"{rel:+.1f}pp vs SPY over 12m")
    if above is not None:
        drivers.append("above 200-DMA" if above else "below 200-DMA")
    return Pillar("Long-horizon momentum", score or 0.0,
                  PILLAR_WEIGHTS["momentum"], score is not None, drivers)


def _strength(f: dict) -> Pillar:
    dte = f.get("debt_to_equity")
    cr = f.get("current_ratio")
    cash = f.get("total_cash")
    debt = f.get("total_debt")
    net_cash = None
    if cash is not None and debt is not None:
        net_cash = 100.0 if cash >= debt else 40.0

    score, cov = _blend([
        (_linear(dte, 2.5, 0.1), 0.45),
        (_linear(cr, 0.7, 2.5), 0.30),
        (net_cash, 0.25),
    ])
    drivers = []
    if dte is not None:
        drivers.append(f"debt/equity {dte:.2f}x")
    if cr is not None:
        drivers.append(f"current ratio {cr:.2f}")
    if net_cash == 100.0:
        drivers.append("net cash positive")
    return Pillar("Financial strength", score or 0.0,
                  PILLAR_WEIGHTS["strength"], score is not None, drivers)


def _risk_pillar(closes: list[float], f: dict) -> tuple[Pillar, float, list[str]]:
    """Returns (pillar, risk_score, risk_drivers).

    The pillar rewards low risk; `risk_score` is the inverse and is
    reported separately because "should I own it" and "how much can it
    hurt" are different questions.
    """
    vol = annualised_volatility_pct(closes, window=252)
    dd = max_drawdown_pct(closes, window=756)
    beta = f.get("beta")

    score, cov = _blend([
        (_linear(vol, 55.0, 15.0), 0.40),
        (_linear(dd, 65.0, 15.0), 0.35),
        (_linear(beta, 2.0, 0.6), 0.25),
    ])
    drivers = []
    risk_drivers = []
    if vol is not None:
        drivers.append(f"volatility {vol:.1f}%")
        if vol > 40:
            risk_drivers.append(f"High volatility ({vol:.0f}% annualised)")
    if dd is not None:
        drivers.append(f"max drawdown {dd:.1f}%")
        if dd > 45:
            risk_drivers.append(f"Has fallen {dd:.0f}% peak-to-trough")
    if beta is not None:
        drivers.append(f"beta {beta:.2f}")
        if beta > 1.5:
            risk_drivers.append(f"Beta {beta:.2f} — amplifies market moves")

    pillar = Pillar("Risk & drawdown", score or 0.0, PILLAR_WEIGHTS["risk"],
                    score is not None, drivers)
    risk_score = 100.0 - (score if score is not None else 50.0)
    return pillar, risk_score, risk_drivers


# ── Composite ───────────────────────────────────────────────────

def _valuation_band(f: dict) -> str:
    pe = f.get("trailing_pe") or f.get("forward_pe")
    median = SECTOR_PE_MEDIAN.get(str(f.get("sector") or ""), DEFAULT_PE_MEDIAN)
    if not pe or pe <= 0 or median <= 0:
        return "UNKNOWN"
    rel = pe / median
    if rel <= 0.8:
        return "CHEAP vs sector"
    if rel <= 1.2:
        return "FAIR vs sector"
    if rel <= 1.8:
        return "RICH vs sector"
    return "EXPENSIVE vs sector"


def score_long_term(fundamentals: dict, closes: list[float],
                    bench_closes: list[float] | None = None) -> LongTermScore:
    """Score one US company for a buy-and-hold horizon."""
    f = fundamentals or {}
    closes = list(closes or [])

    pillars = [
        _quality(f),
        _valuation(f),
        _growth(f),
        _momentum(closes, bench_closes),
        _strength(f),
    ]
    risk_pillar, risk_score, risk_drivers = _risk_pillar(closes, f)
    pillars.append(risk_pillar)

    covered = [p for p in pillars if p.covered]
    total_weight = sum(p.weight for p in covered)
    composite = (sum(p.score * p.weight for p in covered) / total_weight
                 if total_weight > 0 else 0.0)
    coverage = (total_weight / sum(PILLAR_WEIGHTS.values())) * 100.0

    rating = _band(composite, RATING_BANDS)
    # A thin model must not masquerade as a strong call.
    if coverage < 55.0 and rating in ("HIGH CONVICTION", "ACCUMULATE"):
        rating = "NEUTRAL"
        risk_drivers.append("Rating capped — under half the model had data")

    conviction = ("High" if coverage >= 80 and composite >= 62 else
                  "Medium" if coverage >= 60 else "Low")

    top = max(covered, key=lambda p: p.score, default=None)
    weak = min(covered, key=lambda p: p.score, default=None)
    bits = []
    if top:
        bits.append(f"strongest on {top.name.lower()} ({top.score:.0f})")
    if weak and weak is not top:
        bits.append(f"weakest on {weak.name.lower()} ({weak.score:.0f})")
    summary = f"{rating} at {composite:.0f}/100" + (
        " — " + ", ".join(bits) if bits else "")

    return LongTermScore(
        composite=composite,
        rating=rating,
        risk_score=risk_score,
        risk_grade=_band(risk_score, RISK_BANDS),
        conviction=conviction,
        coverage_pct=coverage,
        valuation_band=_valuation_band(f),
        pillars=pillars,
        risk_drivers=risk_drivers,
        summary=summary,
    )


def action_for(score: LongTermScore, *, owned: bool = False) -> str:
    """Map a score onto a long-term action.

    Long-horizon vocabulary on purpose: there is no BUY_CANDIDATE or
    WAIT here because this book is not entered and exited on setups.
    """
    if score.rating in ("HIGH CONVICTION", "ACCUMULATE"):
        return "ACCUMULATE"
    if score.rating == "NEUTRAL":
        return "HOLD" if owned else "WATCH"
    if score.rating == "WEAK":
        return "REVIEW" if owned else "WATCH"
    return "TRIM" if owned else "AVOID"


__all__ = [
    "score_long_term", "action_for", "LongTermScore", "Pillar",
    "RATING_BANDS", "RISK_BANDS", "PILLAR_WEIGHTS", "SECTOR_PE_MEDIAN",
]
