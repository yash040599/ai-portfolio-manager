# ================================================================
# modes/analyze/enrich_noai.py
# ================================================================
# Deterministic NoAI enrichment for the Portfolio Analyser.
#
# Takes Zerodha holdings + reference seed files + cached candles and
# returns a list of fully-populated `StockAnalysis` records (every
# NoAI field stamped with `source` and `as_of`).
#
# AI fields (`ai_thesis_long_term`, `ai_qualitative_risks`, ...) are
# left as None here \u2014 enrich_ai.py adds those when --ai is set.
#
# This module is the ONE place the analyser hits Zerodha live; the
# dashboard never imports it. Failure-isolation: any single field
# failure marks that field as `Field.missing(note=...)` and continues
# to the next \u2014 the report renders the holes honestly.
# ================================================================

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from config              import Config, now_ist
from core.logger         import Logger
from core.zerodha_client import ZerodhaClient
from modes.analyze.recommendation_rules import apply_rules
from modes.analyze.types import (
    Field,
    StockAnalysis,
    SRC_CANDLE_CACHE,
    SRC_DERIVED,
    SRC_DIVIDENDS,
    SRC_FUNDAMENTALS,
    SRC_MISSING,
    SRC_SECTOR_MAP,
    SRC_ZERODHA_API,
)


# ── Reference data loaders (cached at import-time) ──────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR     = _PROJECT_ROOT / "data"

_FUNDAMENTALS_PATH    = _DATA_DIR / "fundamentals_seed.json"
_DIVIDENDS_PATH       = _DATA_DIR / "dividends_seed.json"
_BENCHMARK_PATH       = _DATA_DIR / "benchmark_sector_weights.json"
_CANDIDATES_PATH      = _DATA_DIR / "analyse_candidates.json"
_GROUPS_PATH          = _DATA_DIR / "promoter_groups.json"


def _load_seed(path: Path) -> tuple[dict, datetime.datetime]:
    """Returns (data_dict_excluding_meta, file_mtime_as_datetime).
    Returns ({}, now) when the file is missing."""
    if not path.exists():
        return {}, now_ist()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}, now_ist()
    data = {k: v for k, v in raw.items() if k != "_meta"}
    mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
    return data, mtime


def load_reference_data() -> dict:
    """Loads all hand-curated seed files. Returns a dict with the
    seed dicts and their file mtimes so callers can stamp `as_of`.
    Re-reading the JSON on every call is fine \u2014 these are tiny."""
    fundamentals, fund_mtime = _load_seed(_FUNDAMENTALS_PATH)
    dividends,    div_mtime  = _load_seed(_DIVIDENDS_PATH)
    benchmark,    bench_mtime = _load_seed(_BENCHMARK_PATH)
    candidates,   cand_mtime = _load_seed(_CANDIDATES_PATH)
    groups,       grp_mtime  = _load_seed(_GROUPS_PATH)
    return {
        "fundamentals":           fundamentals,
        "fundamentals_as_of":     fund_mtime,
        "dividends":              dividends,
        "dividends_as_of":        div_mtime,
        "benchmark":              benchmark,
        "benchmark_as_of":        bench_mtime,
        "candidates":             candidates,
        "candidates_as_of":       cand_mtime,
        "promoter_groups":        groups,
        "promoter_groups_as_of":  grp_mtime,
    }


# ── Sector lookup ───────────────────────────────────────────────

def _sector_for(symbol: str) -> str:
    """Pulls sector from the trade-mode SECTOR_MAP. Imported lazily
    so this module doesn't drag the trade-mode tree into analyse-mode
    callers (the trade-mode runtime is large)."""
    try:
        from modes.trade.stock_scanner import SECTOR_MAP
        return SECTOR_MAP.get(symbol, "OTHER")
    except Exception:
        return "OTHER"


# Industry is not currently maintained as a separate dict; we treat
# it as a refinement of sector. When the project later splits sector
# into (sector, industry), this helper becomes the migration point.
def _industry_for(symbol: str, sector: str) -> str:
    return sector


# ── Long-term technical helpers ────────────────────────────────

def _sma(closes: list[float], period: int) -> float:
    """Simple Moving Average over the last `period` closes."""
    if len(closes) < period:
        return 0.0
    window = closes[-period:]
    return sum(window) / period


def _rsi_daily(closes: list[float], period: int = 14) -> float:
    """Wilder-smoothed RSI on daily closes. Returns 50.0 on
    insufficient data so the rule engine treats it as neutral."""
    if len(closes) < period + 1:
        return 50.0
    deltas  = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains   = [max(0.0, d) for d in deltas[:period]]
    losses  = [max(0.0, -d) for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for d in deltas[period:]:
        avg_gain = (avg_gain * (period - 1) + max(0.0, d)) / period
        avg_loss = (avg_loss * (period - 1) + max(0.0, -d)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _beta_vs_nifty(stock_closes: list[float], nifty_closes: list[float]) -> float:
    """Rolling 250-day beta = cov(stock, nifty) / var(nifty).
    Returns 1.0 on insufficient data so portfolio aggregations still work."""
    n = min(len(stock_closes), len(nifty_closes))
    if n < 30:
        return 1.0
    sc = stock_closes[-n:]
    nc = nifty_closes[-n:]
    sr = [(sc[i] - sc[i - 1]) / sc[i - 1] for i in range(1, n) if sc[i - 1] > 0]
    nr = [(nc[i] - nc[i - 1]) / nc[i - 1] for i in range(1, n) if nc[i - 1] > 0]
    if len(sr) < 30 or len(nr) < 30 or len(sr) != len(nr):
        # Fall back to truncating to common length when one side dropped a row.
        m = min(len(sr), len(nr))
        sr, nr = sr[-m:], nr[-m:]
        if m < 30:
            return 1.0
    mean_s = sum(sr) / len(sr)
    mean_n = sum(nr) / len(nr)
    cov = sum((sr[i] - mean_s) * (nr[i] - mean_n) for i in range(len(sr))) / len(sr)
    var = sum((nr[i] - mean_n) ** 2 for i in range(len(nr))) / len(nr)
    if var == 0:
        return 1.0
    return cov / var


# ── Public entry ───────────────────────────────────────────────

def enrich_holdings(
    holdings: list[dict],
    *,
    zerodha: ZerodhaClient,
    log: Logger,
    cfg: type[Config] | None = None,
) -> list[StockAnalysis]:
    """Run the NoAI enrichment over a list of Zerodha holdings dicts
    (as returned by `ZerodhaClient.get_holdings()`). Returns a list
    of `StockAnalysis` records. Order is preserved."""
    cfg = cfg or Config

    if not holdings:
        return []

    refs = load_reference_data()
    log.info(
        f"Reference data loaded "
        f"(fundamentals: {len(refs['fundamentals'])}, "
        f"dividends: {len(refs['dividends'])}, "
        f"benchmark sectors: {len(refs['benchmark'])})"
    )

    # ── 1. Live quotes (single batch call) ─────────────────────
    log.info("Fetching live quotes from Zerodha (single batch call)...")
    quote_lookup: dict[str, dict] = {}
    quote_as_of = now_ist()
    try:
        raw_quotes = zerodha.get_quotes_safe(holdings) or {}
        quote_lookup = {k: v for k, v in raw_quotes.items() if isinstance(v, dict)}
        log.success(f"Got {len(quote_lookup)} live quotes")
    except Exception as e:
        log.warning(f"Live quote batch failed: {e} \u2014 using cached prices from holdings")

    # ── 2. NIFTY daily candles for beta (one fetch per run) ─────
    nifty_closes: list[float] = []
    try:
        one_year_ago = now_ist().date() - datetime.timedelta(days=400)
        nifty_hist = zerodha.get_historical(
            symbol="NIFTY 50", exchange="NSE",
            from_date=one_year_ago, to_date=now_ist().date(),
            interval="day",
        )
        nifty_closes = [float(c.get("close", 0)) for c in nifty_hist if c.get("close")]
        log.info(f"NIFTY daily history: {len(nifty_closes)} candles")
    except Exception as e:
        log.warning(f"NIFTY history fetch failed: {e} \u2014 beta will fall back to 1.0")

    # ── 3. Per-stock enrichment ────────────────────────────────
    results: list[StockAnalysis] = []
    for h in holdings:
        symbol   = h["symbol"]
        exchange = h.get("exchange", "NSE")
        try:
            stock = _enrich_one(
                h, exchange=exchange,
                quote=quote_lookup.get(f"{exchange}:{symbol}", {}),
                quote_as_of=quote_as_of,
                refs=refs,
                zerodha=zerodha,
                nifty_closes=nifty_closes,
                log=log,
            )
            apply_rules(stock)
            results.append(stock)
        except Exception as e:
            log.warning(f"Failed to enrich {symbol}: {e} \u2014 skipping")

    # ── 4. Compute weight-in-portfolio (derived) ───────────────
    total_value = sum(_v(s.current_value) for s in results)
    if total_value > 0:
        ts = now_ist()
        for s in results:
            cv = _v(s.current_value)
            s.weight_in_portfolio_pct = Field(
                value=round(cv / total_value * 100, 2),
                source=SRC_DERIVED, as_of=ts,
            )

    log.success(f"Enriched {len(results)} stocks (NoAI)")
    return results


# ── Per-stock enrichment ────────────────────────────────────────

def _enrich_one(
    h: dict,
    *,
    exchange: str,
    quote: dict,
    quote_as_of: datetime.datetime,
    refs: dict,
    zerodha: ZerodhaClient,
    nifty_closes: list[float],
    log: Logger,
) -> StockAnalysis:
    """Build one fully-enriched StockAnalysis. Each field's source +
    as_of is set explicitly here; never silently default to now."""
    symbol = h["symbol"]
    qty    = int(h.get("quantity", 0))
    avg    = float(h.get("avg_buy_price", 0) or 0)

    # ── Live price + P&L ──
    if quote:
        last = float(quote.get("last_price", h.get("current_price", 0)) or 0)
        price_src = SRC_ZERODHA_API
        price_at  = quote_as_of
    else:
        last = float(h.get("current_price", 0) or 0)
        price_src = SRC_ZERODHA_API
        price_at  = quote_as_of - datetime.timedelta(minutes=1)  # mark slightly stale

    invested = qty * avg
    current  = qty * last
    pnl      = current - invested
    pnl_pct  = (pnl / invested * 100) if invested > 0 else 0.0

    field_position = lambda v, src=SRC_ZERODHA_API, at=quote_as_of: Field(
        value=v, source=src, as_of=at,
    )

    qty_f         = field_position(qty)
    avg_f         = field_position(round(avg, 2))
    current_f     = Field(value=round(last, 2), source=price_src, as_of=price_at)
    invested_f    = Field(value=round(invested, 2), source=SRC_DERIVED, as_of=price_at)
    current_val_f = Field(value=round(current, 2),  source=SRC_DERIVED, as_of=price_at)
    pnl_f         = Field(value=round(pnl, 2),      source=SRC_DERIVED, as_of=price_at)
    pnl_pct_f     = Field(value=round(pnl_pct, 2),  source=SRC_DERIVED, as_of=price_at)

    # ── 1y daily candles for technicals + 52w + beta ──
    daily_candles: list[dict] = []
    try:
        one_year_ago = now_ist().date() - datetime.timedelta(days=400)
        daily_candles = zerodha.get_historical(
            symbol=symbol, exchange=exchange,
            from_date=one_year_ago, to_date=now_ist().date(),
            interval="day",
        ) or []
    except Exception as e:
        log.debug(f"Daily history failed for {symbol}: {e}")

    closes = [float(c.get("close", 0)) for c in daily_candles if c.get("close")]
    highs  = [float(c.get("high",  0)) for c in daily_candles if c.get("high")]
    lows   = [float(c.get("low",   0)) for c in daily_candles if c.get("low")]
    last_candle_date = (
        daily_candles[-1].get("date") if daily_candles else None
    )
    candle_at = (
        last_candle_date if isinstance(last_candle_date, datetime.datetime)
        else now_ist()
    )

    # 52-week extremes (use the actual last 252 trading days when available).
    window = 252 if len(highs) >= 252 else len(highs)
    if window > 0:
        high_52w = max(highs[-window:])
        low_52w  = min(lows[-window:])
        high_52w_f = Field(value=round(high_52w, 2), source=SRC_CANDLE_CACHE, as_of=candle_at)
        low_52w_f  = Field(value=round(low_52w, 2),  source=SRC_CANDLE_CACHE, as_of=candle_at)
        pct_from_high = ((last - high_52w) / high_52w * 100) if high_52w > 0 else 0.0
        pct_from_high_f = Field(
            value=round(pct_from_high, 2), source=SRC_DERIVED, as_of=candle_at,
        )
    else:
        high_52w_f = Field.missing(note="no daily candles")
        low_52w_f  = Field.missing(note="no daily candles")
        pct_from_high_f = Field.missing(note="no daily candles")

    # SMA-50 / SMA-200.
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    sma50_f = (Field(value=round(sma50, 2), source=SRC_CANDLE_CACHE, as_of=candle_at)
               if sma50 > 0 else Field.missing(note="<50 candles"))
    sma200_f = (Field(value=round(sma200, 2), source=SRC_CANDLE_CACHE, as_of=candle_at)
                if sma200 > 0 else Field.missing(note="<200 candles"))
    above_sma200 = (last > sma200) if sma200 > 0 else True
    above_sma200_f = (Field(value=above_sma200, source=SRC_DERIVED, as_of=candle_at)
                      if sma200 > 0 else Field.missing(note="<200 candles"))

    # RSI daily.
    rsi = _rsi_daily(closes, 14)
    rsi_f = Field(value=round(rsi, 1), source=SRC_CANDLE_CACHE, as_of=candle_at)

    # Beta vs NIFTY.
    if nifty_closes and closes:
        beta = _beta_vs_nifty(closes, nifty_closes)
        beta_f = Field(value=round(beta, 2), source=SRC_DERIVED, as_of=candle_at)
    else:
        beta_f = Field.missing(note="NIFTY history missing")

    # Sector / industry from static map.
    sector = _sector_for(symbol)
    sector_f = Field(value=sector, source=SRC_SECTOR_MAP,
                     as_of=now_ist(), note="trade-mode SECTOR_MAP")
    industry_f = Field(value=_industry_for(symbol, sector),
                       source=SRC_SECTOR_MAP, as_of=now_ist())

    # Dividend yield TTM.
    dps = refs["dividends"].get(symbol)
    if dps is not None and last > 0:
        yield_pct = float(dps) / last * 100
        div_yield_f = Field(
            value=round(yield_pct, 2),
            source=SRC_DIVIDENDS,
            as_of=refs["dividends_as_of"],
            note=f"DPS Rs.{dps:.2f} / price Rs.{last:.2f}",
        )
    else:
        div_yield_f = Field.missing(note="no dividend seed entry")

    # Weighted P/E (per-stock TTM PE).
    fund = refs["fundamentals"].get(symbol)
    if isinstance(fund, dict) and fund.get("pe") is not None:
        pe_f = Field(
            value=float(fund["pe"]),
            source=SRC_FUNDAMENTALS,
            as_of=refs["fundamentals_as_of"],
            note=fund.get("source_note", ""),
        )
    else:
        pe_f = Field.missing(
            note="no fundamentals seed entry" if fund is None
            else (fund.get("source_note") or "PE not applicable"),
        )

    # Build the record. Rule fields are set later by apply_rules().
    placeholder = Field(value=None, source=SRC_RULE_ENGINE_PLACEHOLDER, as_of=now_ist())
    return StockAnalysis(
        symbol=symbol,
        exchange=exchange,
        qty=qty_f,
        avg_buy_price=avg_f,
        current_price=current_f,
        invested_value=invested_f,
        current_value=current_val_f,
        pnl=pnl_f,
        pnl_pct=pnl_pct_f,
        high_52w=high_52w_f,
        low_52w=low_52w_f,
        sector=sector_f,
        industry=industry_f,
        beta_vs_nifty=beta_f,
        dividend_yield_ttm=div_yield_f,
        weighted_pe=pe_f,
        sma_50=sma50_f,
        sma_200=sma200_f,
        rsi_daily=rsi_f,
        above_sma_200=above_sma200_f,
        price_vs_high_52w_pct=pct_from_high_f,
        rule_action=placeholder,
        rule_conviction=placeholder,
        rule_horizon=placeholder,
        rule_target_price=placeholder,
        rule_reasoning=placeholder,
    )


# Sentinel source label for rule fields BEFORE the rule engine runs.
# apply_rules() overwrites every rule_* field with proper source tags;
# this just keeps the dataclass slot non-None during construction.
SRC_RULE_ENGINE_PLACEHOLDER = "rule_pending"


def _v(field: Field | None, default: float = 0.0) -> float:
    if field is None or field.value is None:
        return default
    try:
        return float(field.value)
    except (TypeError, ValueError):
        return default
