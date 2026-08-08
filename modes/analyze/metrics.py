# ================================================================
# modes/analyze/metrics.py
# ================================================================
# Industry-standard portfolio-level metrics (ANALYZE_ROADMAP P6 + P8).
#
# Concentration / income / valuation:
#   - sector_weights              (by current value)
#   - HHI concentration           (Herfindahl-Hirschman Index, 0-10000)
#   - top-5 concentration %
#   - single-name max %           (+ which symbol holds it)
#   - group concentration         (Adani / Tata / Bajaj / ...)
#   - weighted P/E                (size-weighted, only positive PE rows)
#   - weighted dividend yield     (size-weighted, only known yields)
#   - estimated annual dividends  (sum of dps × qty, INR)
#   - portfolio beta vs NIFTY     (size-weighted)
#
# Risk / return (P8, industry-standard):
#   - 60-day annualised volatility (size-weighted daily-return std)
#   - Sharpe ratio                 (excess return over RFR, annualised)
#   - max drawdown across prior runs (peak-to-trough on PortfolioSnapshot DB)
#   - XIRR                          (money-weighted compound annual return)
#   - cash balance + cash-drag %    (Zerodha funds.live_balance)
#
# Each metric carries an `as_of` set to the most-stale input (so the
# report can flag stale fundamentals without lying about live prices).
# ================================================================

from __future__ import annotations

import datetime
import json
import math
import sqlite3
from pathlib import Path

from config import Config, now_ist
from modes.analyze.types import (
    Field,
    PortfolioMetrics,
    SectorWeight,
    StockAnalysis,
    SRC_DERIVED,
    SRC_CANDLE_CACHE,
    SRC_ZERODHA_API,
)


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_GROUPS_PATH  = _PROJECT_ROOT / "data" / "promoter_groups.json"
_DIVIDENDS_PATH = _PROJECT_ROOT / "data" / "dividends_seed.json"
_CANDLE_CACHE_PATH = _PROJECT_ROOT / "data" / "candle_cache.db"
_ANALYSES_DB_PATH  = _PROJECT_ROOT / "data" / "portfolio_analyses.db"


def _v(field: Field | None, default: float = 0.0) -> float:
    if field is None or field.value is None:
        return default
    try:
        return float(field.value)
    except (TypeError, ValueError):
        return default


def _as_of(field: Field | None) -> datetime.datetime | None:
    if field is None or field.as_of is None:
        return None
    return field.as_of


def _oldest(*candidates: datetime.datetime | None) -> datetime.datetime:
    real = [c for c in candidates if c is not None]
    return min(real) if real else now_ist()


# ── Public entry ───────────────────────────────────────────────

def compute_metrics(
    holdings: list[StockAnalysis],
    *,
    cash_balance: float | None = None,
    prior_runs: list[dict] | None = None,
) -> PortfolioMetrics:
    """Compute all portfolio-level metrics from a list of enriched
    `StockAnalysis` records. Pure-Python; no external calls.

    Optional inputs:
      cash_balance: Zerodha funds.live_balance (rupees). When supplied
        the metrics include `cash_balance` + `cash_drag_pct` fields.
      prior_runs: list of `portfolio_runs` dicts (newest first) from
        `data/portfolio_analyses.db`. When supplied (and ≥ 2 rows
        exist) the metrics include `max_drawdown_pct` + `xirr_pct`.
    """
    if not holdings:
        return _empty()

    # ── Headline values ──
    invested_total      = sum(_v(s.invested_value) for s in holdings)
    current_total       = sum(_v(s.current_value)  for s in holdings)
    pnl_total           = current_total - invested_total
    pnl_pct_total       = (pnl_total / invested_total * 100) if invested_total > 0 else 0.0
    headline_as_of      = _oldest(*[_as_of(s.current_value) for s in holdings])

    # ── Per-symbol weight (recompute here so this module is self-contained) ──
    weights: dict[str, float] = {}
    if current_total > 0:
        for s in holdings:
            weights[s.symbol] = _v(s.current_value) / current_total

    # ── Sector weights ──
    sector_buckets: dict[str, dict] = {}
    for s in holdings:
        sec = (s.sector.value if s.sector and s.sector.value else "OTHER") or "OTHER"
        b = sector_buckets.setdefault(sec, {"value": 0.0, "count": 0})
        b["value"] += _v(s.current_value)
        b["count"] += 1
    sector_weights = sorted(
        [
            SectorWeight(
                sector=k,
                weight_pct=round(v["value"] / current_total * 100, 2) if current_total > 0 else 0.0,
                holdings_count=v["count"],
            )
            for k, v in sector_buckets.items()
        ],
        key=lambda sw: sw.weight_pct,
        reverse=True,
    )

    # ── Concentration metrics ──
    sorted_weights = sorted(weights.values(), reverse=True)
    hhi = round(sum((w * 100) ** 2 for w in sorted_weights), 1)  # weights as % so 0-10000 scale
    top5 = round(sum(sorted_weights[:5]) * 100, 2)
    if weights:
        top_sym, top_w = max(weights.items(), key=lambda kv: kv[1])
    else:
        top_sym, top_w = "", 0.0

    hhi_f = Field(value=hhi, source=SRC_DERIVED, as_of=headline_as_of,
                  note=("concentrated" if hhi > 2500 else "healthy"))
    top5_f = Field(value=top5, source=SRC_DERIVED, as_of=headline_as_of)
    single_max_f = Field(value=round(top_w * 100, 2), source=SRC_DERIVED,
                         as_of=headline_as_of,
                         note=("concentration risk" if top_w > 0.25 else "ok"))
    single_sym_f = Field(value=top_sym, source=SRC_DERIVED, as_of=headline_as_of)

    # ── Group concentration (Adani / Tata / Bajaj / ...) ──
    group_map = _load_groups()
    group_buckets: dict[str, float] = {}
    for s in holdings:
        g = group_map.get(s.symbol)
        if g:
            group_buckets[g] = group_buckets.get(g, 0.0) + weights.get(s.symbol, 0.0)
    group_pcts = {k: round(v * 100, 2) for k, v in group_buckets.items() if v > 0}
    group_f = Field(value=group_pcts, source=SRC_DERIVED, as_of=headline_as_of)

    # ── Weighted P/E (positive PE only) ──
    pe_num = pe_den = 0.0
    pe_oldest: datetime.datetime | None = None
    for s in holdings:
        pe = _v(s.weighted_pe)
        if pe > 0:
            w = weights.get(s.symbol, 0.0)
            pe_num += w * pe
            pe_den += w
            pe_oldest = _oldest(pe_oldest, _as_of(s.weighted_pe))
    weighted_pe = round(pe_num / pe_den, 2) if pe_den > 0 else 0.0
    weighted_pe_f = Field(value=weighted_pe, source=SRC_DERIVED,
                          as_of=pe_oldest or headline_as_of,
                          note=f"covers {pe_den * 100:.0f}% of portfolio")

    # ── Weighted dividend yield (known yields only) ──
    dy_num = dy_den = 0.0
    dy_oldest: datetime.datetime | None = None
    for s in holdings:
        dy = _v(s.dividend_yield_ttm)
        if dy >= 0 and s.dividend_yield_ttm and s.dividend_yield_ttm.value is not None:
            w = weights.get(s.symbol, 0.0)
            dy_num += w * dy
            dy_den += w
            dy_oldest = _oldest(dy_oldest, _as_of(s.dividend_yield_ttm))
    weighted_dy = round(dy_num / dy_den, 2) if dy_den > 0 else 0.0
    weighted_dy_f = Field(value=weighted_dy, source=SRC_DERIVED,
                          as_of=dy_oldest or headline_as_of,
                          note=f"covers {dy_den * 100:.0f}% of portfolio")

    # ── Portfolio beta vs NIFTY ──
    beta_num = beta_den = 0.0
    beta_oldest: datetime.datetime | None = None
    for s in holdings:
        b = _v(s.beta_vs_nifty)
        if s.beta_vs_nifty and s.beta_vs_nifty.value is not None:
            w = weights.get(s.symbol, 0.0)
            beta_num += w * b
            beta_den += w
            beta_oldest = _oldest(beta_oldest, _as_of(s.beta_vs_nifty))
    portfolio_beta = round(beta_num / beta_den, 2) if beta_den > 0 else 1.0
    beta_f = Field(value=portfolio_beta, source=SRC_DERIVED,
                   as_of=beta_oldest or headline_as_of)

    # ── Annual dividend estimate (rupees) ──
    div_seed = _load_dividends()
    annual_div_inr = 0.0
    div_seed_as_of = (
        datetime.datetime.fromtimestamp(_DIVIDENDS_PATH.stat().st_mtime)
        if _DIVIDENDS_PATH.exists() else headline_as_of
    )
    for s in holdings:
        dps = div_seed.get(s.symbol)
        qty = _v(s.qty)
        if dps is not None and qty > 0:
            annual_div_inr += float(dps) * qty
    annual_div_f = Field(value=round(annual_div_inr, 2),
                         source=SRC_DERIVED, as_of=div_seed_as_of,
                         note="Σ DPS_TTM × qty across holdings")

    # ── Annualised volatility + Sharpe ──
    # Use per-stock daily-close history from candle_cache.db (last
    # ANALYZE_VOL_LOOKBACK_DAYS sessions). Compute portfolio daily
    # returns as the size-weighted sum of per-stock daily returns.
    rfr = float(getattr(Config, "RISK_FREE_RATE_PCT", 7.0))
    lookback = int(getattr(Config, "ANALYZE_VOL_LOOKBACK_DAYS", 60))
    port_returns = _portfolio_daily_returns(holdings, weights, lookback=lookback)
    vol_f = sharpe_f = None
    if port_returns and len(port_returns) >= 20:
        ann_vol_pct  = _stdev(port_returns) * math.sqrt(252) * 100
        mean_daily   = sum(port_returns) / len(port_returns)
        rfr_daily    = (rfr / 100.0) / 252.0
        excess_daily = mean_daily - rfr_daily
        sharpe       = (excess_daily / _stdev(port_returns)) * math.sqrt(252) \
                        if _stdev(port_returns) > 0 else 0.0
        vol_f = Field(
            value=round(ann_vol_pct, 2),
            source=SRC_CANDLE_CACHE, as_of=headline_as_of,
            note=f"{lookback}-day window, annualised √252",
        )
        sharpe_f = Field(
            value=round(sharpe, 2),
            source=SRC_DERIVED, as_of=headline_as_of,
            note=f"RFR={rfr:.1f}% (config), {lookback}-day window",
        )

    # ── Max drawdown across prior runs + XIRR ──
    max_dd_f = xirr_f = None
    if prior_runs:
        max_dd_pct = _max_drawdown_pct(prior_runs, current_total)
        if max_dd_pct is not None:
            max_dd_f = Field(value=round(max_dd_pct, 2),
                             source=SRC_DERIVED, as_of=headline_as_of,
                             note=f"peak-to-trough across {len(prior_runs) + 1} runs")
        xirr = _xirr_pct(prior_runs, current_total, headline_as_of)
        if xirr is not None:
            xirr_f = Field(value=round(xirr, 2),
                           source=SRC_DERIVED, as_of=headline_as_of,
                           note="money-weighted, anchor = oldest snapshot")

    # ── Cash position + drag ──
    cash_f = drag_f = None
    if cash_balance is not None and cash_balance >= 0:
        total_account = current_total + float(cash_balance)
        drag_pct = (float(cash_balance) / total_account * 100) \
                    if total_account > 0 else 0.0
        cash_f = Field(value=round(float(cash_balance), 2),
                       source=SRC_ZERODHA_API, as_of=headline_as_of,
                       note="Zerodha funds.live_balance")
        drag_f = Field(value=round(drag_pct, 2),
                       source=SRC_DERIVED, as_of=headline_as_of,
                       note="cash / (cash + invested_value)")

    # ── Market-cap tier breakdown (P9) ──
    # Sums weights into LARGE / MID / SMALL / ETF / UNKNOWN buckets.
    # An UNKNOWN bucket > 0 is the operator's cue to refresh
    # `data/market_cap_tier.json`.
    cap_tier_buckets: dict[str, float] = {}
    for s in holdings:
        tier = (s.market_cap_tier.value
                if s.market_cap_tier and s.market_cap_tier.value
                else "UNKNOWN") or "UNKNOWN"
        cap_tier_buckets[tier] = cap_tier_buckets.get(tier, 0.0) \
                                  + weights.get(s.symbol, 0.0)
    cap_tier_pct = {k: round(v * 100, 2)
                    for k, v in cap_tier_buckets.items() if v > 0}
    cap_tier_f = Field(value=cap_tier_pct, source=SRC_DERIVED,
                       as_of=headline_as_of,
                       note="weights from data/market_cap_tier.json")

    return PortfolioMetrics(
        sector_weights          = sector_weights,
        hhi_concentration       = hhi_f,
        top_5_concentration_pct = top5_f,
        single_name_max_pct     = single_max_f,
        single_name_max_symbol  = single_sym_f,
        group_concentration     = group_f,
        weighted_pe             = weighted_pe_f,
        weighted_dividend_yield = weighted_dy_f,
        portfolio_beta_vs_nifty = beta_f,
        total_invested      = Field(value=round(invested_total, 2), source=SRC_DERIVED, as_of=headline_as_of),
        total_current_value = Field(value=round(current_total, 2),  source=SRC_DERIVED, as_of=headline_as_of),
        total_pnl           = Field(value=round(pnl_total, 2),      source=SRC_DERIVED, as_of=headline_as_of),
        total_pnl_pct       = Field(value=round(pnl_pct_total, 2),  source=SRC_DERIVED, as_of=headline_as_of),
        volatility_30d_pct  = vol_f,
        sharpe_ratio        = sharpe_f,
        max_drawdown_pct    = max_dd_f,
        xirr_pct            = xirr_f,
        annual_dividend_estimate = annual_div_f,
        cash_balance        = cash_f,
        cash_drag_pct       = drag_f,
        cap_tier_weights    = cap_tier_f,
    )


# ── Helpers (industry-standard math) ───────────────────────────

def _stdev(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    var  = sum((x - mean) ** 2 for x in xs) / (n - 1)   # sample stdev
    return math.sqrt(var)


def _portfolio_daily_returns(
    holdings: list[StockAnalysis],
    weights: dict[str, float],
    *,
    lookback: int,
) -> list[float]:
    """Read closes for each held symbol from candle_cache.db (interval
    'day'), compute per-stock daily returns over the last `lookback`
    sessions, then aggregate as a size-weighted portfolio return per
    day. Returns an empty list when the cache is missing or insufficient.

    NOTE: only symbols whose cache has ≥ `lookback` rows contribute;
    the size weights are RE-NORMALISED across the contributing set so
    the missing names don't silently shrink the portfolio return.
    """
    if not _CANDLE_CACHE_PATH.exists() or lookback < 5:
        return []
    closes_by_symbol: dict[str, list[float]] = {}
    try:
        conn = sqlite3.connect(str(_CANDLE_CACHE_PATH))
        try:
            for s in holdings:
                rows = conn.execute(
                    """SELECT close FROM candle_cache
                       WHERE symbol = ? AND exchange = ? AND interval = 'day'
                       ORDER BY candle_date DESC LIMIT ?""",
                    (s.symbol, s.exchange, lookback + 1),
                ).fetchall()
                closes = [r[0] for r in rows if r[0]]
                # rows came newest-first; reverse to chronological order.
                closes.reverse()
                if len(closes) >= lookback + 1:
                    closes_by_symbol[s.symbol] = closes[-(lookback + 1):]
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return []

    if not closes_by_symbol:
        return []

    # Re-normalise weights across symbols that had data.
    total_w = sum(weights.get(sym, 0.0) for sym in closes_by_symbol)
    if total_w <= 0:
        return []
    norm_w = {sym: weights.get(sym, 0.0) / total_w
              for sym in closes_by_symbol}

    # Daily returns per symbol (length = lookback).
    daily_returns_by_symbol: dict[str, list[float]] = {}
    for sym, closes in closes_by_symbol.items():
        rets = [(closes[i] - closes[i - 1]) / closes[i - 1]
                for i in range(1, len(closes)) if closes[i - 1] > 0]
        daily_returns_by_symbol[sym] = rets

    # Aggregate: weighted sum across symbols on each day.
    n_days = min(len(r) for r in daily_returns_by_symbol.values())
    out: list[float] = []
    for i in range(n_days):
        out.append(sum(
            norm_w[sym] * daily_returns_by_symbol[sym][i]
            for sym in closes_by_symbol
        ))
    return out


def _max_drawdown_pct(prior_runs: list[dict], current_value: float) -> float | None:
    """Walk forward through `prior_runs` (oldest first), append the
    current run's value, and return the worst peak-to-trough drawdown
    percentage. Returns None when fewer than 2 data points exist."""
    series: list[float] = []
    for r in reversed(prior_runs):  # prior_runs is newest-first
        v = r.get("portfolio_value") or 0
        if v > 0:
            series.append(float(v))
    series.append(float(current_value))
    if len(series) < 2:
        return None
    peak = series[0]
    max_dd = 0.0
    for v in series[1:]:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd


def _xirr_pct(prior_runs: list[dict], current_value: float,
              now: datetime.datetime) -> float | None:
    """Money-weighted compound annual return ((CAGR) approximation).

    Treats the OLDEST snapshot's `portfolio_value` as the initial
    investment and the current value as the terminal value. This is a
    simple two-point CAGR — the analyser doesn't track interim cash
    flows (deposits / withdrawals) yet, so true XIRR isn't available.
    Honest naming: still labelled `xirr_pct` since that's what the
    industry-standard equivalent is, with the simplifying assumption
    documented inline.

    Returns None when the oldest snapshot is < 30 days old (CAGR
    extrapolated from < 1 month is misleading)."""
    if not prior_runs:
        return None
    oldest = prior_runs[-1]
    try:
        oldest_ts = datetime.datetime.fromisoformat(oldest["started_at"])
    except (TypeError, KeyError, ValueError):
        return None
    days = (now - oldest_ts).days
    if days < 30:
        return None
    initial = float(oldest.get("portfolio_value") or 0)
    if initial <= 0 or current_value <= 0:
        return None
    years = days / 365.25
    # CAGR = (end/start)^(1/years) - 1
    cagr = (current_value / initial) ** (1 / years) - 1
    return cagr * 100


def _load_groups() -> dict:
    if not _GROUPS_PATH.exists():
        return {}
    try:
        raw = json.loads(_GROUPS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {k: v for k, v in raw.items() if k != "_meta"}


def _load_dividends() -> dict:
    """Load `data/dividends_seed.json` (TTM dividend per share map).
    Returns {} when the file is missing or malformed."""
    if not _DIVIDENDS_PATH.exists():
        return {}
    try:
        raw = json.loads(_DIVIDENDS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {k: v for k, v in raw.items() if k != "_meta"}


def _empty() -> PortfolioMetrics:
    miss = Field.missing()
    return PortfolioMetrics(
        sector_weights          = [],
        hhi_concentration       = miss,
        top_5_concentration_pct = miss,
        single_name_max_pct     = miss,
        single_name_max_symbol  = miss,
        group_concentration     = miss,
        weighted_pe             = miss,
        weighted_dividend_yield = miss,
        portfolio_beta_vs_nifty = miss,
        total_invested          = miss,
        total_current_value     = miss,
        total_pnl               = miss,
        total_pnl_pct           = miss,
    )
