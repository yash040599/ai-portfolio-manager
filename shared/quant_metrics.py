"""
shared/quant_metrics.py
=======================

Industry-standard quantitative metrics over a daily OHLCV candle list.

Everything here is **pure arithmetic** — no API calls, no imports from
`modes/`, no side effects — so both the portfolio analyser
(`modes/analyze/`) and the swing scanner (`modes/swing/`) can share one
implementation instead of each growing its own half-correct copy.

Conventions
-----------
* Candles are dicts with ``open/high/low/close/volume`` keys, **oldest
  first** (the shape Kite's ``historical_data()`` and yfinance both
  produce after normalisation).
* Every function degrades gracefully: not enough history returns
  ``None`` rather than raising or silently inventing a number. Callers
  must treat ``None`` as "unknown", never as zero.
* Percentages are returned as percent (12.3 means +12.3%), not fractions.
* "Annualised" uses 252 trading days.

Why these metrics
-----------------
They are the standard toolkit an equity research desk or a factor model
uses to describe a single name: trend, momentum, risk-adjusted return,
downside behaviour, benchmark sensitivity and tradability. See
``modes/analyze/scoring.py`` for how they roll up into a rating.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

TRADING_DAYS = 252

# Standard lookbacks in trading days.
LOOKBACK_1M = 21
LOOKBACK_3M = 63
LOOKBACK_6M = 126
LOOKBACK_12M = 252


# ── Extraction helpers ───────────────────────────────────────────

def closes_of(candles: Sequence[dict]) -> list[float]:
    out: list[float] = []
    for c in candles or []:
        try:
            v = float(c.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out.append(v)
    return out


def _series(candles_or_closes: Any) -> list[float]:
    """Accept either a candle list or an already-extracted close list."""
    if not candles_or_closes:
        return []
    first = candles_or_closes[0]
    if isinstance(first, dict):
        return closes_of(candles_or_closes)
    out: list[float] = []
    for v in candles_or_closes:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            out.append(f)
    return out


def daily_returns(closes: Sequence[float]) -> list[float]:
    """Simple (not log) daily returns as fractions."""
    series = _series(closes)
    return [
        series[i] / series[i - 1] - 1.0
        for i in range(1, len(series))
        if series[i - 1] > 0
    ]


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: Sequence[float]) -> float:
    """Sample standard deviation (n-1). Zero for < 2 points."""
    if len(xs) < 2:
        return 0.0
    mu = _mean(xs)
    var = sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(max(0.0, var))


# ── Return / momentum ────────────────────────────────────────────

def period_return_pct(closes: Sequence[float], lookback: int) -> float | None:
    """Total return over the last `lookback` bars, in percent."""
    series = _series(closes)
    if len(series) <= lookback or lookback <= 0:
        return None
    past = series[-(lookback + 1)]
    if past <= 0:
        return None
    return (series[-1] / past - 1.0) * 100.0


def momentum_12_1_pct(closes: Sequence[float]) -> float | None:
    """Classic academic momentum: the 12-month return **excluding** the
    most recent month.

    Skipping the last month strips out short-term mean reversion, which
    is why every published momentum factor (Jegadeesh-Titman, AQR,
    MSCI) defines it this way rather than as a plain 12-month return.
    """
    series = _series(closes)
    if len(series) <= LOOKBACK_12M:
        return None
    start = series[-(LOOKBACK_12M + 1)]
    end = series[-(LOOKBACK_1M + 1)]
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def relative_strength_pct(closes: Sequence[float],
                          bench_closes: Sequence[float],
                          lookback: int = LOOKBACK_3M) -> float | None:
    """Excess return vs the benchmark over `lookback` bars (percentage
    points). Positive = outperforming."""
    stock = period_return_pct(closes, lookback)
    bench = period_return_pct(bench_closes, lookback)
    if stock is None or bench is None:
        return None
    return stock - bench


# ── Risk ─────────────────────────────────────────────────────────

def annualised_volatility_pct(closes: Sequence[float],
                              window: int = LOOKBACK_3M) -> float | None:
    """Annualised standard deviation of daily returns, in percent."""
    series = _series(closes)
    if len(series) < 21:
        return None
    rets = daily_returns(series[-(window + 1):])
    if len(rets) < 20:
        return None
    return _stdev(rets) * math.sqrt(TRADING_DAYS) * 100.0


def max_drawdown_pct(closes: Sequence[float],
                     window: int = LOOKBACK_12M) -> float | None:
    """Worst peak-to-trough decline over `window` bars, as a positive
    percent (18.4 means the name fell 18.4% from a running peak)."""
    series = _series(closes)
    if len(series) < 20:
        return None
    window_series = series[-window:] if len(series) > window else series
    peak = window_series[0]
    worst = 0.0
    for px in window_series:
        if px > peak:
            peak = px
        if peak > 0:
            dd = (peak - px) / peak * 100.0
            if dd > worst:
                worst = dd
    return worst


def drawdown_from_high_pct(closes: Sequence[float],
                           window: int = LOOKBACK_12M) -> float | None:
    """How far below the `window` high the latest close sits, as a
    positive percent. 0 = at a new high."""
    series = _series(closes)
    if not series:
        return None
    window_series = series[-window:] if len(series) > window else series
    high = max(window_series)
    if high <= 0:
        return None
    return max(0.0, (high - series[-1]) / high * 100.0)


def sharpe_ratio(closes: Sequence[float],
                 risk_free_pct: float = 6.5,
                 window: int = LOOKBACK_12M) -> float | None:
    """Annualised Sharpe ratio.

    `risk_free_pct` is the annual risk-free rate — default 6.5% is a
    reasonable Indian 10y G-Sec proxy; pass ~4.5% for US names.
    """
    series = _series(closes)
    rets = daily_returns(series[-(window + 1):]) if len(series) > 40 else []
    if len(rets) < 40:
        return None
    sd = _stdev(rets)
    if sd <= 0:
        return None
    rf_daily = risk_free_pct / 100.0 / TRADING_DAYS
    return (_mean(rets) - rf_daily) / sd * math.sqrt(TRADING_DAYS)


def sortino_ratio(closes: Sequence[float],
                  risk_free_pct: float = 6.5,
                  window: int = LOOKBACK_12M) -> float | None:
    """Annualised Sortino ratio — Sharpe but penalising only downside
    deviation, which is what an investor actually cares about."""
    series = _series(closes)
    rets = daily_returns(series[-(window + 1):]) if len(series) > 40 else []
    if len(rets) < 40:
        return None
    rf_daily = risk_free_pct / 100.0 / TRADING_DAYS
    downside = [r - rf_daily for r in rets if r < rf_daily]
    if len(downside) < 5:
        return None
    dd = math.sqrt(sum(d ** 2 for d in downside) / len(rets))
    if dd <= 0:
        return None
    return (_mean(rets) - rf_daily) / dd * math.sqrt(TRADING_DAYS)


# ── Benchmark sensitivity ────────────────────────────────────────

def beta_and_correlation(closes: Sequence[float],
                         bench_closes: Sequence[float],
                         window: int = LOOKBACK_12M
                         ) -> tuple[float | None, float | None]:
    """(beta, correlation) of the stock against the benchmark.

    Both series are truncated to the same length from the right, which
    assumes aligned trading calendars — true for NSE-vs-NIFTY and
    US-vs-SPY, and the mis-alignment from the odd holiday is immaterial
    at a 252-bar window.
    """
    s = _series(closes)
    b = _series(bench_closes)
    n = min(len(s), len(b), window + 1)
    if n < 60:
        return None, None
    sr = daily_returns(s[-n:])
    br = daily_returns(b[-n:])
    m = min(len(sr), len(br))
    if m < 40:
        return None, None
    sr, br = sr[-m:], br[-m:]

    sd_s, sd_b = _stdev(sr), _stdev(br)
    if sd_b <= 0:
        return None, None
    mu_s, mu_b = _mean(sr), _mean(br)
    cov = sum((sr[i] - mu_s) * (br[i] - mu_b) for i in range(m)) / (m - 1)
    beta = cov / (sd_b ** 2)
    corr = (cov / (sd_s * sd_b)) if sd_s > 0 else None
    return beta, corr


def up_down_capture(closes: Sequence[float],
                    bench_closes: Sequence[float],
                    window: int = LOOKBACK_12M
                    ) -> tuple[float | None, float | None]:
    """(up_capture_pct, down_capture_pct) vs the benchmark.

    Up capture > 100 means the name gains more than the index on index-up
    days; down capture < 100 means it falls less on index-down days. The
    ideal profile is high up / low down — a single number beta cannot
    express that asymmetry, which is why fund factsheets always print
    both.
    """
    s = _series(closes)
    b = _series(bench_closes)
    n = min(len(s), len(b), window + 1)
    if n < 60:
        return None, None
    sr = daily_returns(s[-n:])
    br = daily_returns(b[-n:])
    m = min(len(sr), len(br))
    if m < 40:
        return None, None
    sr, br = sr[-m:], br[-m:]

    up_s = [sr[i] for i in range(m) if br[i] > 0]
    up_b = [br[i] for i in range(m) if br[i] > 0]
    dn_s = [sr[i] for i in range(m) if br[i] < 0]
    dn_b = [br[i] for i in range(m) if br[i] < 0]

    up = (_mean(up_s) / _mean(up_b) * 100.0) if up_b and _mean(up_b) != 0 else None
    dn = (_mean(dn_s) / _mean(dn_b) * 100.0) if dn_b and _mean(dn_b) != 0 else None
    return up, dn


# ── Trend structure ──────────────────────────────────────────────

def sma(closes: Sequence[float], period: int) -> float | None:
    series = _series(closes)
    if len(series) < period or period <= 0:
        return None
    return sum(series[-period:]) / period


def trend_state(closes: Sequence[float]) -> dict[str, Any]:
    """Moving-average structure in one dict.

    Returns ``{state, sma_50, sma_200, above_sma_50, above_sma_200,
    days_since_cross}``. `state` is one of:
      GOLDEN_CROSS  — 50 above 200 (long-term uptrend)
      DEATH_CROSS   — 50 below 200 (long-term downtrend)
      UNKNOWN       — fewer than 200 bars
    `days_since_cross` counts bars since the 50/200 relationship last
    flipped; a *fresh* golden cross is a very different signal from one
    that is 300 bars old and already extended.
    """
    series = _series(closes)
    out: dict[str, Any] = {
        "state": "UNKNOWN", "sma_50": None, "sma_200": None,
        "above_sma_50": None, "above_sma_200": None,
        "days_since_cross": None,
    }
    s50 = sma(series, 50)
    s200 = sma(series, 200)
    out["sma_50"] = s50
    out["sma_200"] = s200
    if s50 is None:
        return out
    out["above_sma_50"] = series[-1] > s50
    if s200 is None:
        return out
    out["above_sma_200"] = series[-1] > s200
    out["state"] = "GOLDEN_CROSS" if s50 > s200 else "DEATH_CROSS"

    # Walk backwards recomputing both SMAs until the sign flips.
    current_above = s50 > s200
    for back in range(1, min(len(series) - 200, 250)):
        window = series[:-back]
        p50 = sma(window, 50)
        p200 = sma(window, 200)
        if p50 is None or p200 is None:
            break
        if (p50 > p200) != current_above:
            out["days_since_cross"] = back
            break
    return out


def range_position_pct(current: float, low: float, high: float) -> float | None:
    """Where `current` sits inside [low, high] as 0-100.

    100 = at the 52-week high, 0 = at the low. A far more comparable
    number across names than "x% below the high".
    """
    if high <= low or current <= 0:
        return None
    return max(0.0, min(100.0, (current - low) / (high - low) * 100.0))


# ── Tradability ──────────────────────────────────────────────────

def atr(candles: Sequence[dict], period: int = 14) -> float | None:
    """Wilder's Average True Range in price units."""
    if not candles or len(candles) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        try:
            high = float(candles[i]["high"])
            low = float(candles[i]["low"])
            prev_close = float(candles[i - 1]["close"])
        except (KeyError, TypeError, ValueError):
            continue
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def atr_pct(candles: Sequence[dict], period: int = 14) -> float | None:
    """ATR as a percent of the latest close — volatility on a scale that
    is comparable between a Rs.100 and a Rs.10,000 stock."""
    a = atr(candles, period)
    closes = closes_of(candles)
    if a is None or not closes or closes[-1] <= 0:
        return None
    return a / closes[-1] * 100.0


def avg_daily_turnover(candles: Sequence[dict], window: int = 20) -> float | None:
    """Mean daily traded value (price x volume) over `window` bars, in
    the instrument's own currency. The practical liquidity test: can you
    get in and out without moving the price?"""
    if not candles:
        return None
    rows = candles[-window:] if len(candles) > window else candles
    values: list[float] = []
    for c in rows:
        try:
            close = float(c.get("close") or 0)
            vol = float(c.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if close > 0 and vol > 0:
            values.append(close * vol)
    if not values:
        return None
    return sum(values) / len(values)


def volume_trend_ratio(candles: Sequence[dict],
                       fast: int = 20, slow: int = 60) -> float | None:
    """Recent average volume divided by the longer-run average.

    > 1 means participation is picking up, which is what confirms a
    trend; < 1 on a rally is the classic distribution warning.
    """
    if not candles or len(candles) < slow:
        return None
    vols: list[float] = []
    for c in candles:
        try:
            vols.append(float(c.get("volume") or 0))
        except (TypeError, ValueError):
            vols.append(0.0)
    fast_avg = _mean(vols[-fast:])
    slow_avg = _mean(vols[-slow:])
    if slow_avg <= 0:
        return None
    return fast_avg / slow_avg


# ── Aggregate ────────────────────────────────────────────────────

def profile(candles: Sequence[dict],
            bench_candles: Sequence[dict] | None = None,
            *,
            risk_free_pct: float = 6.5) -> dict[str, Any]:
    """Compute the whole metric set in one pass.

    Returns a flat dict of ``metric -> value | None``. Callers persist it
    verbatim, so keys are stable API — add, never rename.
    """
    closes = closes_of(candles)
    bench_closes = closes_of(bench_candles) if bench_candles else []

    highs = [float(c["high"]) for c in candles if c.get("high")] if candles else []
    lows = [float(c["low"]) for c in candles if c.get("low")] if candles else []
    win = min(LOOKBACK_12M, len(highs)) if highs else 0
    high_52w = max(highs[-win:]) if win else None
    low_52w = min(lows[-win:]) if win else None

    beta, corr = beta_and_correlation(closes, bench_closes) if bench_closes else (None, None)
    up_cap, down_cap = up_down_capture(closes, bench_closes) if bench_closes else (None, None)
    trend = trend_state(closes)

    return {
        "bars": len(closes),
        # Returns
        "return_1m_pct": period_return_pct(closes, LOOKBACK_1M),
        "return_3m_pct": period_return_pct(closes, LOOKBACK_3M),
        "return_6m_pct": period_return_pct(closes, LOOKBACK_6M),
        "return_12m_pct": period_return_pct(closes, LOOKBACK_12M),
        "momentum_12_1_pct": momentum_12_1_pct(closes),
        # Relative strength
        "rs_1m_pct": relative_strength_pct(closes, bench_closes, LOOKBACK_1M) if bench_closes else None,
        "rs_3m_pct": relative_strength_pct(closes, bench_closes, LOOKBACK_3M) if bench_closes else None,
        "rs_6m_pct": relative_strength_pct(closes, bench_closes, LOOKBACK_6M) if bench_closes else None,
        "rs_12m_pct": relative_strength_pct(closes, bench_closes, LOOKBACK_12M) if bench_closes else None,
        # Risk
        "volatility_30d_pct": annualised_volatility_pct(closes, LOOKBACK_1M + 9),
        "volatility_90d_pct": annualised_volatility_pct(closes, LOOKBACK_3M),
        "max_drawdown_1y_pct": max_drawdown_pct(closes, LOOKBACK_12M),
        "drawdown_from_high_pct": drawdown_from_high_pct(closes, LOOKBACK_12M),
        "sharpe_1y": sharpe_ratio(closes, risk_free_pct, LOOKBACK_12M),
        "sortino_1y": sortino_ratio(closes, risk_free_pct, LOOKBACK_12M),
        # Benchmark
        "beta": beta,
        "correlation": corr,
        "up_capture_pct": up_cap,
        "down_capture_pct": down_cap,
        # Trend
        "trend_state": trend["state"],
        "sma_50": trend["sma_50"],
        "sma_200": trend["sma_200"],
        "above_sma_50": trend["above_sma_50"],
        "above_sma_200": trend["above_sma_200"],
        "days_since_ma_cross": trend["days_since_cross"],
        "high_52w": high_52w,
        "low_52w": low_52w,
        "range_position_pct": (range_position_pct(closes[-1], low_52w, high_52w)
                               if closes and high_52w and low_52w else None),
        # Tradability
        "atr_pct": atr_pct(candles, 14),
        "avg_turnover": avg_daily_turnover(candles, 20),
        "volume_trend_ratio": volume_trend_ratio(candles),
    }


__all__ = [
    "TRADING_DAYS", "LOOKBACK_1M", "LOOKBACK_3M", "LOOKBACK_6M", "LOOKBACK_12M",
    "annualised_volatility_pct", "atr", "atr_pct", "avg_daily_turnover",
    "beta_and_correlation", "closes_of", "daily_returns",
    "drawdown_from_high_pct", "max_drawdown_pct", "momentum_12_1_pct",
    "period_return_pct", "profile", "range_position_pct",
    "relative_strength_pct", "sharpe_ratio", "sma", "sortino_ratio",
    "trend_state", "up_down_capture", "volume_trend_ratio",
]
