"""US company fundamentals for the long-term scorer.

Sourced from yfinance `Ticker.info`, cached to `data/us_fundamentals.json`.

Cached aggressively (default 14 days) because fundamentals only move on
quarterly reporting, while a universe scan touches ~100 symbols and
`.info` is one slow HTTP roundtrip each. Without the cache a scan would
take minutes and hammer Yahoo.

Every field is normalised to a single convention here so the scorer
never has to guess:
  * ratios like margins / ROE arrive as fractions -> stored as percent
  * `debtToEquity` arrives as a percentage -> stored as a plain ratio
  * anything missing or non-finite becomes ``None``, never 0.0, so a
    gap in coverage is never mistaken for a bad reading
"""

from __future__ import annotations

import datetime
import json
import math
import os
from typing import Any

from config import now_ist
from core.logger import Logger


CACHE_PATH = os.path.join("data", "us_fundamentals.json")
DEFAULT_MAX_AGE_DAYS = 14

_memo: dict[str, dict] | None = None


# ── Normalisation helpers ───────────────────────────────────────

def _num(value: Any) -> float | None:
    """Finite float or None. Zero is preserved; junk is dropped."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _pct_from_fraction(value: Any) -> float | None:
    """0.34 -> 34.0. Values already in percent are left alone."""
    v = _num(value)
    if v is None:
        return None
    return v * 100.0 if abs(v) <= 1.5 else v


def _dividend_yield_pct(value: Any) -> float | None:
    """yfinance has shipped this as both a fraction and a percent.

    Anything at or below 0.25 is read as a fraction (a 25%-yielding
    common stock is not a real case), everything else as percent.
    """
    v = _num(value)
    if v is None or v < 0:
        return None
    pct = v * 100.0 if v <= 0.25 else v
    return min(pct, 25.0)


def _statement_value(frame, *labels: str) -> float | None:
    """Most recent value of the first matching row in a yfinance frame."""
    if frame is None:
        return None
    try:
        if frame.empty:
            return None
        for label in labels:
            if label in frame.index:
                return _num(frame.loc[label].iloc[0])
    except Exception:  # noqa: BLE001 — shape varies by ticker
        return None
    return None


def _free_cash_flow(ticker) -> tuple[float | None, float | None]:
    """(free cash flow, revenue) from the cash-flow / income statements.

    `info["freeCashflow"]` is not trustworthy — for MSFT it reports
    ~16.5bn against a reported 67bn, a 4x understatement that would
    quietly wreck FCF yield and FCF margin. The statements agree with
    the filings, so they win.
    """
    try:
        cashflow = ticker.cashflow
    except Exception:  # noqa: BLE001
        return None, None

    fcf = _statement_value(cashflow, "Free Cash Flow")
    if fcf is None:
        ocf = _statement_value(cashflow, "Operating Cash Flow",
                               "Total Cash From Operating Activities")
        capex = _statement_value(cashflow, "Capital Expenditure",
                                 "Capital Expenditures")
        if ocf is not None and capex is not None:
            fcf = ocf - abs(capex)

    revenue = None
    try:
        revenue = _statement_value(ticker.financials, "Total Revenue")
    except Exception:  # noqa: BLE001
        revenue = None
    return fcf, revenue


# ── Cache ───────────────────────────────────────────────────────

def _read_cache() -> dict[str, dict]:
    global _memo
    if _memo is not None:
        return _memo
    try:
        with open(CACHE_PATH, encoding="utf-8") as fh:
            blob = json.load(fh)
        _memo = dict(blob.get("symbols") or {})
    except (OSError, ValueError):
        _memo = {}
    return _memo


def _write_cache(data: dict[str, dict]) -> None:
    global _memo
    _memo = data
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"updated_at": now_ist().isoformat(timespec="seconds"),
                       "count": len(data), "symbols": data},
                      fh, indent=2, default=str)
    except OSError:
        pass


def cached(symbol: str) -> dict:
    return _read_cache().get((symbol or "").strip().upper(), {})


def _is_fresh(row: dict, max_age_days: int) -> bool:
    stamp = str(row.get("fetched_at") or "")
    if not stamp:
        return False
    try:
        age = (now_ist().date()
               - datetime.datetime.fromisoformat(stamp).date()).days
    except ValueError:
        return False
    return age <= max_age_days


# ── Fetch ───────────────────────────────────────────────────────

def fetch(symbol: str) -> dict:
    """Pull one company's fundamentals. Returns {} on any failure."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return {}
    try:
        import yfinance as yf
        ticker = yf.Ticker(sym)
        info = ticker.info or {}
    except Exception:  # noqa: BLE001 — a missing profile is not fatal
        return {}
    if not info:
        return {}

    debt_to_equity = _num(info.get("debtToEquity"))
    if debt_to_equity is not None:
        # yfinance reports this as a percentage (29.1 means 0.29x).
        debt_to_equity = debt_to_equity / 100.0

    market_cap = _num(info.get("marketCap"))
    stmt_fcf, stmt_revenue = _free_cash_flow(ticker)
    fcf = stmt_fcf if stmt_fcf is not None else _num(info.get("freeCashflow"))
    revenue = stmt_revenue if stmt_revenue is not None else _num(info.get("totalRevenue"))

    return {
        "symbol": sym,
        "fetched_at": now_ist().isoformat(timespec="seconds"),
        "name": str(info.get("longName") or info.get("shortName") or ""),
        "sector": str(info.get("sector") or ""),
        "industry": str(info.get("industry") or ""),
        # Valuation
        "trailing_pe": _num(info.get("trailingPE")),
        "forward_pe": _num(info.get("forwardPE")),
        "price_to_book": _num(info.get("priceToBook")),
        "ev_to_ebitda": _num(info.get("enterpriseToEbitda")),
        "fcf_yield_pct": (fcf / market_cap * 100.0)
                         if (fcf and market_cap and market_cap > 0) else None,
        "free_cash_flow": fcf,
        "fcf_source": "statement" if stmt_fcf is not None else "info",
        "dividend_yield_pct": _dividend_yield_pct(info.get("dividendYield")),
        "payout_ratio_pct": _pct_from_fraction(info.get("payoutRatio")),
        # Quality
        "roe_pct": _pct_from_fraction(info.get("returnOnEquity")),
        "roa_pct": _pct_from_fraction(info.get("returnOnAssets")),
        "gross_margin_pct": _pct_from_fraction(info.get("grossMargins")),
        "operating_margin_pct": _pct_from_fraction(info.get("operatingMargins")),
        "net_margin_pct": _pct_from_fraction(info.get("profitMargins")),
        "fcf_margin_pct": (fcf / revenue * 100.0)
                          if (fcf and revenue and revenue > 0) else None,
        # Balance sheet
        "debt_to_equity": debt_to_equity,
        "current_ratio": _num(info.get("currentRatio")),
        "total_debt": _num(info.get("totalDebt")),
        "total_cash": _num(info.get("totalCash")),
        # Growth
        "revenue_growth_pct": _pct_from_fraction(info.get("revenueGrowth")),
        "earnings_growth_pct": _pct_from_fraction(info.get("earningsGrowth")),
        # Context
        "market_cap": market_cap,
        "beta": _num(info.get("beta")),
    }


def ensure(symbols: list[str], *, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
           log: Logger | None = None) -> dict[str, dict]:
    """Refresh stale symbols and return the full cache slice.

    Failures are cached as an empty-but-stamped row so a delisted or
    unsupported ticker is not retried on every scan.
    """
    log = log or Logger("US")
    data = dict(_read_cache())
    fetched = skipped = failed = 0

    for raw in symbols:
        sym = (raw or "").strip().upper()
        if not sym:
            continue
        if _is_fresh(data.get(sym) or {}, max_age_days):
            skipped += 1
            continue
        row = fetch(sym)
        if row:
            data[sym] = row
            fetched += 1
        else:
            data[sym] = {"symbol": sym,
                         "fetched_at": now_ist().isoformat(timespec="seconds"),
                         "unavailable": True}
            failed += 1

    if fetched or failed:
        _write_cache(data)
        log.info(f"US fundamentals: {fetched} fetched, {skipped} fresh, "
                 f"{failed} unavailable")
    return {s.strip().upper(): data.get(s.strip().upper(), {})
            for s in symbols if s}


__all__ = ["CACHE_PATH", "cached", "fetch", "ensure"]
