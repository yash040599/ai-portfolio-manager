"""US stock technical analysis for the dashboard."""

from __future__ import annotations

from functools import lru_cache
import json
import math
import os
import time
from typing import Any

from config import Config, now_ist
from modes.swing.signals import classify_setup, compute_swing_indicators


US_SCAN_CACHE_PATH = os.path.join("data", "us_scan_latest.json")
_FX_CACHE_PATH = os.path.join("data", "usdinr_rate.json")

# Live quote + FX cache (process-local, throttled).
_MIN_QUOTE_POLL_INTERVAL = 15.0
_last_quote_poll: float = 0.0
_quote_cache: dict[str, dict[str, Any]] = {}

_MIN_FX_POLL_INTERVAL = 300.0
_last_fx_poll: float = 0.0
_fx_cache: dict[str, Any] = {}

US100_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "BRK-B",
    "LLY", "AVGO", "JPM", "TSLA", "V", "UNH", "XOM", "MA", "COST",
    "WMT", "PG", "NFLX", "JNJ", "HD", "ABBV", "BAC", "KO", "PM",
    "PLTR", "CRM", "ORCL", "CSCO", "CVX", "ABT", "IBM", "MCD", "GE",
    "WFC", "MRK", "LIN", "NOW", "ACN", "T", "ISRG", "PEP", "VZ",
    "RTX", "GS", "UBER", "INTU", "DIS", "TMO", "BKNG", "AMD", "QCOM",
    "ADBE", "CAT", "TXN", "SPGI", "AMGN", "PGR", "NEE", "HON", "LOW",
    "UNP", "BLK", "PFE", "DHR", "SYK", "ETN", "TJX", "PANW", "GILD",
    "AMAT", "CMCSA", "COP", "SCHW", "BA", "DE", "ADP", "MDT", "VRTX",
    "LMT", "ADI", "CB", "C", "BMY", "SBUX", "MU", "SO", "PLD",
    "MO", "KLAC", "ICE", "ANET", "DUK", "ELV", "CI", "MCK",
    "WM", "EQIX", "SHW", "ZTS",
]

# Top 50 by approximate market-cap from US100 — the user-facing
# US50 toggle uses this so quick scans take ~half as long as US100.
US50_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "BRK-B",
    "LLY", "AVGO", "JPM", "TSLA", "V", "UNH", "XOM", "MA", "COST",
    "WMT", "PG", "NFLX", "JNJ", "HD", "ABBV", "BAC", "KO", "PM",
    "PLTR", "CRM", "ORCL", "CSCO", "CVX", "ABT", "IBM", "MCD", "GE",
    "WFC", "MRK", "LIN", "NOW", "ACN", "T", "ISRG", "PEP", "VZ",
    "RTX", "GS", "UBER", "INTU", "DIS", "TMO",
]


def analyse_us_symbol(
    symbol: str,
    ticket_amount: float | None = None,
    use_ai: bool = False,
    benchmark_candles: list[dict] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Analyse one US ticker using yfinance daily candles."""
    display_symbol = symbol.strip().upper()
    yf_symbol = _normalise_yfinance_symbol(display_symbol)
    if not yf_symbol:
        raise ValueError("symbol is required")

    if force_refresh:
        force_refresh_us_candles(display_symbol)

    ticket = _ticket(ticket_amount)
    candles = _download_daily_candles(yf_symbol)
    spy_candles = benchmark_candles or _download_daily_candles("SPY")
    result = _build_analysis(display_symbol, yf_symbol, candles, spy_candles, ticket)
    if use_ai:
        result["ai_overlay"] = _ai_overlay(result)
    return result


def analyse_us_universe(
    mode: str = "NOAI",
    ticket_amount: float | None = None,
    universe: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Analyse the US universe and persist the latest scan snapshot."""
    mode = (mode or "NOAI").upper()
    if mode not in ("NOAI", "AI"):
        mode = "NOAI"
    symbols = _build_universe(universe or getattr(Config, "US_SCAN_UNIVERSE", "US100"))
    if limit and limit > 0:
        symbols = symbols[:limit]
    ticket = _ticket(ticket_amount)
    started = now_ist().isoformat()
    benchmark = _download_daily_candles("SPY")

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for symbol in symbols:
        try:
            row = analyse_us_symbol(
                symbol,
                ticket_amount=ticket,
                benchmark_candles=benchmark,
            )
            if row["action"] != "NO_SETUP" or row["dip_signal"]["qualified"]:
                rows.append(row)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)[:180]})

    rows.sort(key=_rank_key)
    for idx, row in enumerate(rows, start=1):
        row["priority_rank"] = idx

    if mode == "AI" and rows:
        cap = max(1, int(getattr(Config, "US_AI_MAX_CANDIDATES", 5)))
        for row in rows[:cap]:
            row["ai_overlay"] = _ai_overlay(row)

    payload = {
        "ok": True,
        "mode": mode,
        "universe": universe or getattr(Config, "US_SCAN_UNIVERSE", "US100"),
        "benchmark": "SPY",
        "data_source": "yfinance",
        "started_at": started,
        "finished_at": now_ist().isoformat(),
        "ticket_amount": round(ticket, 2),
        "symbols_seen": len(symbols),
        "candidate_count": len(rows),
        "errors": errors[:20],
        "candidates": rows,
    }
    save_us_scan(payload)
    return payload


def latest_us_scan() -> dict[str, Any] | None:
    """Return the latest cached US scan payload, if any."""
    if not os.path.exists(US_SCAN_CACHE_PATH):
        return None
    try:
        with open(US_SCAN_CACHE_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_us_scan(payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(US_SCAN_CACHE_PATH), exist_ok=True)
    with open(US_SCAN_CACHE_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)


def _build_analysis(
    display_symbol: str,
    yf_symbol: str,
    candles: list[dict],
    spy_candles: list[dict],
    ticket: float,
) -> dict[str, Any]:
    if len(candles) < 60:
        raise ValueError(f"not enough daily candles for {display_symbol}")
    ind = compute_swing_indicators(candles, spy_candles)
    if not ind.get("valid"):
        raise ValueError(ind.get("reason") or "not enough indicator history")

    trend_setup, trend_score, trend_reasons = classify_setup(ind)
    close = float(ind["current"])
    atr = float(ind.get("atr_14") or 0.0)
    h52 = float(ind.get("high_52w") or 0.0)
    l52 = float(ind.get("low_52w") or 0.0)
    dip_from_52w = ((h52 - close) / h52 * 100.0) if h52 > 0 else 0.0
    dip_threshold = float(getattr(Config, "SWING_DIP_PCT", 10.0))
    dip_qualified = dip_from_52w >= dip_threshold

    setup_type = trend_setup
    score = float(trend_score)
    reasons = list(trend_reasons)
    if dip_qualified:
        dip_reason = f"{dip_from_52w:.1f}% below rolling 52w high (${h52:,.2f})"
        if setup_type == "NONE":
            setup_type = "52W_DIP"
            score = round(dip_from_52w, 2)
            reasons = [dip_reason]
        else:
            score += min(2.0, dip_from_52w / 10.0)
            reasons.append(dip_reason)

    stop_distance = max(atr * 1.5, close * 0.06)
    stop = max(0.01, close - stop_distance)
    if setup_type == "52W_DIP":
        target = close * (1.0 + float(getattr(Config, "SWING_DIP_TARGET_PCT", 20.0)) / 100.0)
    else:
        target = close + (stop_distance * 2.0)
    qty = round(ticket / close, 4) if close > 0 else 0.0
    rr_ratio = ((target - close) / (close - stop)) if close > stop else 0.0
    action = _action_for(setup_type, score, qty, ind)
    pct_above_52w_low = ((close / l52 - 1.0) * 100.0) if l52 > 0 else 0.0

    warnings: list[str] = []
    if qty <= 0:
        warnings.append("Ticket amount is below the current share price")
    if ind.get("rsi", 0) >= 75:
        warnings.append("RSI is extended; avoid chasing without a fresh base")
    if close < ind.get("sma_200", 0) and setup_type != "52W_DIP":
        warnings.append("Price is below SMA-200; trend risk is elevated")

    return {
        "ok": True,
        "symbol": display_symbol,
        "stock_name": get_us_stock_name(display_symbol),
        "yf_symbol": yf_symbol,
        "exchange": "NASDAQ",
        "as_of": _date_str(candles[-1].get("date")),
        "data_source": "yfinance",
        "benchmark": "SPY",
        "ticket_amount": round(ticket, 2),
        "action": action,
        "setup_type": setup_type,
        "score": round(float(score), 2),
        "trend_signal": {
            "setup_type": trend_setup,
            "score": round(float(trend_score), 2),
            "reasons": trend_reasons,
        },
        "dip_signal": {
            "qualified": dip_qualified,
            "threshold_pct": dip_threshold,
            "dip_pct": round(dip_from_52w, 2),
        },
        "current_price": round(close, 2),
        "suggested_qty": qty,
        "position_value": round(qty * close, 2),
        "entry_price": round(close, 2),
        "stop_price": round(stop, 2),
        "target_price": round(target, 2),
        "rr_ratio": round(rr_ratio, 2),
        "indicators": {
            "ema_20": round(float(ind.get("ema_20") or 0.0), 2),
            "sma_50": round(float(ind.get("sma_50") or 0.0), 2),
            "sma_200": round(float(ind.get("sma_200") or 0.0), 2),
            "rsi": round(float(ind.get("rsi") or 0.0), 2),
            "atr_14": round(atr, 2),
            "volume_ratio": round(float(ind.get("vol_ratio") or 0.0), 2),
            "relative_strength": round(float(ind.get("rel_strength") or 0.0), 2),
            "high_52w": round(h52, 2),
            "low_52w": round(l52, 2),
            "dip_from_52w_high_pct": round(dip_from_52w, 2),
            "above_52w_low_pct": round(pct_above_52w_low, 2),
        },
        "reasons": reasons,
        "warnings": warnings,
        "ai_overlay": None,
        "priority_rank": 0,
    }


def _action_for(setup_type: str, score: float, qty: float, ind: dict) -> str:
    if setup_type == "52W_DIP":
        return "BUY_CANDIDATE" if qty > 0 else "WATCH"
    if setup_type == "NONE":
        return "NO_SETUP"
    if qty <= 0:
        return "WATCH"
    if score >= 5.0 and ind.get("current", 0) > ind.get("sma_50", 0):
        return "BUY_CANDIDATE"
    if score >= 3.0:
        return "WATCH"
    return "WAIT"


_DIP_SETUPS = {"52W_DIP", "ATH_DIP"}


def _rank_key(row: dict[str, Any]) -> tuple[int, int, float, float, str]:
    """Sort key for the unified entry recommendation table.

    Tie-break order:
      1. action_rank (BUY_CANDIDATE first, NO_SETUP last)
      2. setup_family (trend setups above 52w dips so the table
         does not get dominated by raw dip-percentage scores; the
         52w setup has a 10-30 score scale while trend setups score
         0-10, which earlier caused the top-N to be all-dip).
      3. -score (within family)
      4. -dip_pct (final tie-break)
      5. symbol (deterministic)
    """
    action_rank = {"BUY_CANDIDATE": 0, "WATCH": 1, "WAIT": 2, "NO_SETUP": 3}
    setup = (row.get("setup_type") or "").upper()
    family = 1 if setup in _DIP_SETUPS else 0
    dip = float((row.get("dip_signal") or {}).get("dip_pct") or 0.0)
    return (
        action_rank.get(row.get("action", "NO_SETUP"), 9),
        family,
        -float(row.get("score") or 0.0),
        -dip,
        row.get("symbol", ""),
    )


def _build_universe(name: str) -> list[str]:
    upper = (name or "").strip().upper()
    if upper == "US100":
        return list(US100_SYMBOLS)
    if upper == "US50":
        return list(US50_SYMBOLS)
    custom = [s.strip().upper() for s in (name or "").split(",") if s.strip()]
    return custom or list(US100_SYMBOLS)


def _ticket(value: float | None) -> float:
    ticket = float(value or getattr(Config, "US_TICKET_AMOUNT", 500.0))
    return ticket if ticket > 0 else float(getattr(Config, "US_TICKET_AMOUNT", 500.0))


def _ai_overlay(row: dict[str, Any]) -> dict[str, str]:
    try:
        from core.claude_client import ClaudeClient
        from core.logger import Logger
        log = Logger(f"USAI[{row.get('symbol', '')}]")
        claude = ClaudeClient(Config, log)
        response = claude.call(_build_ai_prompt(row))
        if response:
            return {"raw_response": response[:2000], "source": "claude_us_overlay"}
        return {"error": "Claude returned empty response"}
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _build_ai_prompt(row: dict[str, Any]) -> str:
    ind = row.get("indicators") or {}
    reasons = "; ".join(row.get("reasons") or [])
    return f"""I am reviewing a US stock swing trade. Give a concise buy-side assessment.

STOCK: {row.get('symbol')} | Setup: {row.get('setup_type')} | Action: {row.get('action')}
Price: ${row.get('current_price'):,.2f} | Stop: ${row.get('stop_price'):,.2f} | Target: ${row.get('target_price'):,.2f}
Score: {row.get('score')} | RSI: {ind.get('rsi')} | 52w dip: {ind.get('dip_from_52w_high_pct')}% | RS vs SPY: {ind.get('relative_strength')}%
Quant reasons: {reasons or 'None'}

Answer in under 350 words:
1. VERDICT: BUY / WATCH / PASS with one sentence.
2. Business quality and recent catalyst context.
3. Main risk over the next 2-8 weeks.
4. What confirmation would improve the trade.

Do not change entry, stop, target, or quantity."""


def _normalise_yfinance_symbol(symbol: str) -> str:
    return symbol.replace(".", "-").strip().upper()


# Minimum number of usable daily candles a symbol must have before
# `_build_analysis` will compute indicators on it.  Kept in module
# scope so `_download_daily_candles` can validate the same threshold
# and refuse to cache partial responses (which was the 2026-05-19
# user-reported "not enough daily candles for ORCL" — yfinance was
# rate-limited after a fresh US100 scan and returned a short df
# that got pinned in lru_cache, so every subsequent single-stock
# call kept hitting the bad cache).
_MIN_CANDLES = 60

# Order of period strings tried by `_download_daily_candles` when
# the first attempt returns fewer than `_MIN_CANDLES`.  yfinance
# occasionally throttles a single symbol and silently returns a
# 2-3 row dataframe even for blue-chip names; retrying with a
# longer window almost always unsticks it.
_PERIOD_FALLBACKS = ("18mo", "2y", "5y")


def force_refresh_us_candles(symbol: str | None = None) -> None:
    """Drop the in-memory candle cache so the next call hits yfinance.

    Pass a symbol to clear just that entry, or `None` to clear the
    whole cache.  Used by the single-stock analyse endpoint when the
    caller wants to force a fresh fetch.
    """
    if symbol is None:
        _download_daily_candles.cache_clear()
        return
    yf_sym = _normalise_yfinance_symbol(symbol)
    # lru_cache has no per-key invalidation; we simulate it by
    # snapshotting every other cached entry, clearing, and replaying.
    try:
        info = _download_daily_candles.cache_info()
    except Exception:
        return
    if info.currsize == 0:
        return
    # Simplest correct fallback: clear everything.  We only call this
    # for the active single-stock symbol so the blast radius is small.
    _download_daily_candles.cache_clear()


@lru_cache(maxsize=256)
def _download_daily_candles(symbol: str) -> list[dict]:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - depends on env
        raise RuntimeError("yfinance is required for US analysis") from exc

    last_err: Exception | None = None
    best_candles: list[dict] = []
    for period in _PERIOD_FALLBACKS:
        try:
            df = yf.download(
                symbol,
                period=period,
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
        except Exception as exc:  # pragma: no cover - network surface
            last_err = exc
            continue
        if df is None or getattr(df, "empty", True):
            last_err = ValueError(f"no yfinance candles returned for {symbol}")
            continue
        if getattr(df.columns, "nlevels", 1) > 1:
            df.columns = [col[0] for col in df.columns]
        candles: list[dict] = []
        for dt, row in df.iterrows():
            open_ = _cell(row, "Open")
            high = _cell(row, "High")
            low = _cell(row, "Low")
            close = _cell(row, "Close") or _cell(row, "Adj Close")
            volume = _cell(row, "Volume") or 0.0
            if not all(_finite(v) and v > 0 for v in (open_, high, low, close)):
                continue
            candles.append({
                "date": dt,
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume),
            })
        if len(candles) > len(best_candles):
            best_candles = candles
        if len(candles) >= _MIN_CANDLES:
            return candles
    # All periods exhausted.  Raise so lru_cache does not pin the
    # partial result — the next attempt will retry from scratch.
    if not best_candles:
        raise ValueError(
            f"no usable daily candles for {symbol}"
            + (f" ({last_err})" if last_err else "")
        )
    raise ValueError(
        f"not enough daily candles for {symbol} "
        f"(got {len(best_candles)}, need >= {_MIN_CANDLES}); "
        "yfinance may be rate-limited — try again in a minute"
    )


def _cell(row: Any, name: str) -> float:
    try:
        value = row.get(name, 0.0)
    except AttributeError:
        return 0.0
    if hasattr(value, "iloc"):
        value = value.iloc[0]
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _date_str(value: Any) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)[:10]


# ── Stock name lookup ──────────────────────────────────────────

@lru_cache(maxsize=512)
def get_us_stock_name(symbol: str) -> str:
    """Return the long company name for a US ticker, or '' on failure.

    Cached per-process via lru_cache so repeated table renders don't
    re-hit yfinance.  Falls back to short_name then to '' so callers
    can safely render `name or symbol`.
    """
    if not symbol:
        return ""
    yf_sym = _normalise_yfinance_symbol(symbol)
    try:
        import yfinance as yf
    except ImportError:
        return ""
    try:
        ticker = yf.Ticker(yf_sym)
        fast = getattr(ticker, "fast_info", None) or {}
        name = ""
        for key in ("long_name", "longName", "shortName", "short_name"):
            try:
                value = fast[key] if isinstance(fast, dict) else getattr(fast, key, None)
            except (KeyError, AttributeError):
                value = None
            if value:
                name = str(value)
                break
        if not name:
            info = getattr(ticker, "info", None) or {}
            name = (info.get("longName") or info.get("shortName") or "").strip()
        return (name or "").strip()
    except Exception:
        return ""


# ── Live quotes ────────────────────────────────────────────────

def cached_us_live_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Return only what is already in the in-memory cache; never
    contacts yfinance.  Used during page render so the initial HTML
    response is not blocked on the network — the client-side poller
    then fetches fresh prices in the background."""
    if not symbols:
        return {}
    return {s.strip().upper(): _quote_cache.get(s.strip().upper(), {})
            for s in symbols if s and s.strip()}


def get_us_live_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Return `{symbol: {price, change_pct, as_of}}` for US tickers.

    Throttled to one upstream batch per `_MIN_QUOTE_POLL_INTERVAL`
    seconds.  Uses `yfinance.download(period='2d')` for batch close
    + prior-close so we can compute today's change %.  Symbols that
    fail (delisted, typo, network) return the last cached snapshot
    or an empty dict.
    """
    global _last_quote_poll
    if not symbols:
        return {}
    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    seen: set[str] = set()
    uniq: list[str] = []
    for s in syms:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    now = time.monotonic()
    if now - _last_quote_poll < _MIN_QUOTE_POLL_INTERVAL and _quote_cache:
        return {s: _quote_cache.get(s, {}) for s in uniq}
    try:
        import yfinance as yf
    except ImportError:
        return {s: _quote_cache.get(s, {}) for s in uniq}
    yf_map = {_normalise_yfinance_symbol(s): s for s in uniq}
    yf_syms = list(yf_map.keys())
    try:
        df = yf.download(
            yf_syms,
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
            group_by="ticker",
        )
    except Exception:
        return {s: _quote_cache.get(s, {}) for s in uniq}
    if df is None or getattr(df, "empty", True):
        return {s: _quote_cache.get(s, {}) for s in uniq}

    ts = now_ist().isoformat()
    out: dict[str, dict[str, Any]] = {}
    for yf_sym, display in yf_map.items():
        try:
            sub = df[yf_sym] if len(yf_syms) > 1 else df
        except (KeyError, ValueError):
            out[display] = _quote_cache.get(display, {})
            continue
        try:
            closes = [float(v) for v in sub["Close"].dropna().tolist()]
        except (KeyError, AttributeError, TypeError, ValueError):
            closes = []
        if not closes:
            out[display] = _quote_cache.get(display, {})
            continue
        last = closes[-1]
        prev = closes[-2] if len(closes) >= 2 else last
        change_pct = ((last / prev - 1.0) * 100.0) if prev > 0 else 0.0
        snap = {
            "price": round(last, 4),
            "change_pct": round(change_pct, 2),
            "as_of": ts,
        }
        out[display] = snap
        _quote_cache[display] = snap
    _last_quote_poll = time.monotonic()
    return out


# ── USD / INR ──────────────────────────────────────────────────

def get_usd_inr_rate(force_refresh: bool = False) -> dict[str, Any]:
    """Return the latest USD->INR conversion rate.

    Cached in-process for `_MIN_FX_POLL_INTERVAL` seconds and
    persisted on disk so cold starts don't show a zero badge.
    Returns `{rate, as_of, source}` even on upstream failure
    (falls back to disk, then to a hard-coded sentinel of 0).
    """
    global _last_fx_poll, _fx_cache
    now = time.monotonic()
    if (not force_refresh and _fx_cache
            and now - _last_fx_poll < _MIN_FX_POLL_INTERVAL):
        return dict(_fx_cache)
    fetched: dict[str, Any] | None = None
    try:
        import yfinance as yf
        df = yf.download(
            "USDINR=X",
            period="5d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if df is not None and not df.empty:
            if getattr(df.columns, "nlevels", 1) > 1:
                df.columns = [c[0] for c in df.columns]
            closes = [float(v) for v in df["Close"].dropna().tolist()]
            if closes:
                fetched = {
                    "rate": round(closes[-1], 4),
                    "as_of": now_ist().isoformat(),
                    "source": "yfinance:USDINR=X",
                }
    except Exception:
        fetched = None
    if fetched:
        _fx_cache = fetched
        _last_fx_poll = time.monotonic()
        try:
            os.makedirs(os.path.dirname(_FX_CACHE_PATH), exist_ok=True)
            with open(_FX_CACHE_PATH, "w", encoding="utf-8") as handle:
                json.dump(fetched, handle, indent=2)
        except OSError:
            pass
        return dict(fetched)
    # Disk fallback
    if os.path.exists(_FX_CACHE_PATH):
        try:
            with open(_FX_CACHE_PATH, encoding="utf-8") as handle:
                disk = json.load(handle)
            if isinstance(disk, dict) and disk.get("rate"):
                _fx_cache = disk
                return dict(disk)
        except (OSError, json.JSONDecodeError):
            pass
    return {"rate": 0.0, "as_of": "", "source": "unavailable"}


# ── Health-check builder for US detail page ────────────────────

def build_us_health_checks(row: dict[str, Any],
                            live_price: float | None = None
                            ) -> list[tuple[str, str, str, bool]]:
    """Return a list of (name, explanation, value, passed) tuples.

    Mirrors the Indian-swing detail page health table so the US
    detail page can render the same kvtable / checklist UI.
    """
    ind = row.get("indicators") or {}
    close = float(live_price or row.get("current_price") or 0.0)
    sma_200 = float(ind.get("sma_200") or 0.0)
    sma_50 = float(ind.get("sma_50") or 0.0)
    ema_20 = float(ind.get("ema_20") or 0.0)
    rsi = float(ind.get("rsi") or 0.0)
    vol_ratio = float(ind.get("volume_ratio") or 0.0)
    rs = float(ind.get("relative_strength") or 0.0)
    atr = float(ind.get("atr_14") or 0.0)
    h52 = float(ind.get("high_52w") or 0.0)
    entry = float(row.get("entry_price") or close)
    stop = float(row.get("stop_price") or 0.0)
    target = float(row.get("target_price") or 0.0)
    rr = float(row.get("rr_ratio") or 0.0)
    setup = (row.get("setup_type") or "").upper()

    checks: list[tuple[str, str, str, bool]] = []

    checks.append((
        "Long-term trend (200-day)",
        "Is the stock above its 200-day average?",
        f"Price ${close:,.2f} vs avg ${sma_200:,.2f}",
        close > sma_200 if sma_200 > 0 else False,
    ))
    checks.append((
        "Medium-term trend (50-day)",
        "Is the stock above its 50-day average?",
        f"Price ${close:,.2f} vs avg ${sma_50:,.2f}",
        close > sma_50 if sma_50 > 0 else False,
    ))
    checks.append((
        "Short-term trend (20-day)",
        "Is the stock above its recent 20-day trend line?",
        f"Price ${close:,.2f} vs trend ${ema_20:,.2f}",
        close > ema_20 if ema_20 > 0 else False,
    ))
    stacked = (ema_20 > sma_50 > sma_200) if (sma_50 > 0 and sma_200 > 0) else False
    checks.append((
        "All trends aligned",
        "Are the short, medium, and long-term trends all pointing up?",
        "Yes — all aligned" if stacked else "No — mixed",
        stacked,
    ))
    if 30 <= rsi <= 70:
        rsi_desc = f"{rsi:.0f} — healthy zone"
        rsi_ok = True
    elif rsi > 70:
        rsi_desc = f"{rsi:.0f} — may be overbought"
        rsi_ok = False
    else:
        rsi_desc = f"{rsi:.0f} — oversold (risky)"
        rsi_ok = False
    checks.append((
        "Buying/selling pressure",
        "RSI sweet spot for buying is 30-70.",
        rsi_desc,
        rsi_ok,
    ))
    checks.append((
        "Trading activity",
        "Is volume above the recent average?",
        f"{vol_ratio:.2f}x normal volume",
        vol_ratio >= 1.0,
    ))
    checks.append((
        "Beating the market?",
        "Is this stock outperforming SPY over the last 60 days?",
        f"{rs:+.1f}% vs SPY",
        rs > 0,
    ))
    checks.append((
        "Risk vs reward",
        "For every $1 risked, how much could we gain? Target >= 2x.",
        f"{rr:.2f}x (entry ${entry:,.2f} -> target ${target:,.2f}, stop ${stop:,.2f})",
        rr >= 2.0,
    ))
    if atr > 0 and entry > stop > 0:
        stop_atr = (entry - stop) / atr
        checks.append((
            "Stop-loss safety margin",
            "Is the stop far enough from normal daily swings (1x-3x ATR)?",
            f"{stop_atr:.2f}x daily swing range",
            1.0 <= stop_atr <= 3.0,
        ))
    if h52 > 0:
        pct_from_high = ((close / h52) - 1.0) * 100.0
        if pct_from_high > -5:
            pos_desc = f"{pct_from_high:+.1f}% from 52w high — near the top"
        elif pct_from_high > -20:
            pos_desc = f"{pct_from_high:+.1f}% from 52w high — reasonable range"
        else:
            pos_desc = f"{pct_from_high:+.1f}% from 52w high — significantly below"
        checks.append((
            "Where in its yearly range?",
            "How far is the stock from its 52w high?",
            pos_desc,
            pct_from_high > -20 or setup == "52W_DIP",
        ))
    if ema_20 > 0:
        ext = ((close / ema_20) - 1.0) * 100.0
        checks.append((
            "Not too far from trend",
            "Is the stock extended above its trend? Avoid chasing.",
            f"{ext:+.1f}% from trend line",
            abs(ext) < 8.0,
        ))
    return checks


__all__ = [
    "analyse_us_symbol",
    "analyse_us_universe",
    "build_us_health_checks",
    "cached_us_live_quotes",
    "force_refresh_us_candles",
    "get_us_live_quotes",
    "get_us_stock_name",
    "get_usd_inr_rate",
    "latest_us_scan",
]