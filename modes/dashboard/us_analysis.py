"""US stock technical analysis for the dashboard."""

from __future__ import annotations

import datetime
from functools import lru_cache
import json
import math
import os
import time
from typing import Any

import requests

from config import Config, now_ist
from modes.dashboard import us_config
from shared.candle_cache import CandleCache
from modes.swing.signals import classify_setup, compute_swing_indicators


US_SCAN_CACHE_PATH = os.path.join("data", "us_scan_latest.json")
# 2026-05-19: keep the immediately previous scan on disk so the
# "what changed since last scan" card on /us can diff the latest
# scan vs the one before it, mirroring the Indian Swing diff card.
US_SCAN_PRIOR_PATH = os.path.join("data", "us_scan_prior.json")
# 2026-06-02: persist single-stock AI overlays keyed by symbol so the
# US detail page is STICKY — once you "Analyse with AI" for ORCL, the
# overlay survives navigating away and back (mirrors the Indian Swing
# `ai_overlay_json` persistence, but file-backed because US has no
# per-symbol SQLite table).
US_AI_OVERLAY_PATH = os.path.join("data", "us_ai_overlays.json")
_FX_CACHE_PATH = os.path.join("data", "usdinr_rate.json")

# Live quote + FX cache (process-local).
#
# Per-symbol TTL replaces the old shared "no more than one batch
# every 15 s" throttle. Reason: the dashboard now polls three
# tiers (open=15 s, watch=30 s, reco=60 s) in a cascade — under
# the old shared throttle, the watch + reco fetches that fire
# 250-500 ms after the open fetch always returned cached because
# the global throttle hadn't elapsed. End result: watchlist and
# recommendation P&L stopped updating after the first cascade.
#
# Now each cached snapshot carries an `as_of_mono` monotonic
# timestamp and the helper rebuilds the upstream batch from
# "symbols whose cache is older than `_QUOTE_SOFT_TTL` seconds".
# Tiers stay independent and yfinance still gets one batched call
# per tier instead of N single-symbol calls.
_QUOTE_SOFT_TTL = 12.0  # accept cached snapshot if <12 s old
_quote_cache: dict[str, dict[str, Any]] = {}

_MIN_FX_POLL_INTERVAL = 300.0
_last_fx_poll: float = 0.0
_fx_cache: dict[str, Any] = {}

_US_CACHE_EXCHANGE = "US"
_US_CACHE_INTERVAL = "day"
_US_CACHE_LOOKBACK_DAYS = 900
_US_CACHE_FRESH_DAYS = 5

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
    """Analyse one US ticker using cached/Yahoo daily candles."""
    display_symbol = symbol.strip().upper()
    yf_symbol = _normalise_yfinance_symbol(display_symbol)
    if not yf_symbol:
        raise ValueError("symbol is required")

    if force_refresh:
        force_refresh_us_candles(display_symbol)

    ticket = _ticket(ticket_amount)
    candles = _download_daily_candles(yf_symbol, force_refresh)
    spy_candles = benchmark_candles or _download_daily_candles("SPY", force_refresh)
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
    symbols = _build_universe(universe or us_config.US_SCAN_UNIVERSE)
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
        cap = max(1, int(us_config.US_AI_MAX_CANDIDATES))
        for row in rows[:cap]:
            row["ai_overlay"] = _ai_overlay(row)

    payload = {
        "ok": True,
        "mode": mode,
        "universe": universe or us_config.US_SCAN_UNIVERSE,
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
    """Persist a US scan snapshot, rolling the prior snapshot to
    `US_SCAN_PRIOR_PATH` first so the dashboard can show a
    "what changed since last scan" diff (mirrors the Indian Swing
    `diff_latest_vs_prior_day` behaviour, but file-backed because
    US scans are JSON-only — no per-run SQLite table)."""
    os.makedirs(os.path.dirname(US_SCAN_CACHE_PATH), exist_ok=True)
    # Roll latest → prior BEFORE writing the new latest, but only
    # if the existing latest belongs to a different scan (different
    # `finished_at`). Re-saving the same scan must not clobber the
    # genuine prior, otherwise the diff would silently become a
    # no-op against an identical snapshot.
    try:
        existing = latest_us_scan() or {}
        new_finished = payload.get("finished_at") or ""
        old_finished = existing.get("finished_at") or ""
        if existing and old_finished and old_finished != new_finished:
            with open(US_SCAN_PRIOR_PATH, "w", encoding="utf-8") as fh:
                json.dump(existing, fh, indent=2, default=str)
    except Exception:
        # Diffing is a nice-to-have; never let a snapshot-roll
        # failure block writing the fresh scan.
        pass
    with open(US_SCAN_CACHE_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)


def save_us_ai_overlay(symbol: str, overlay: dict[str, Any]) -> None:
    """Persist a single-stock US AI overlay keyed by symbol so the
    detail page can re-render it on the next visit (sticky AI).

    Overlays carrying an `error` are NOT saved — a transient AI
    failure must not overwrite a previously good analysis."""
    sym = (symbol or "").strip().upper()
    if not sym or not isinstance(overlay, dict) or overlay.get("error"):
        return
    os.makedirs(os.path.dirname(US_AI_OVERLAY_PATH), exist_ok=True)
    store: dict[str, Any] = {}
    if os.path.exists(US_AI_OVERLAY_PATH):
        try:
            with open(US_AI_OVERLAY_PATH, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                store = loaded
        except (OSError, json.JSONDecodeError):
            store = {}
    store[sym] = {"overlay": overlay, "saved_at": now_ist().isoformat()}
    try:
        with open(US_AI_OVERLAY_PATH, "w", encoding="utf-8") as fh:
            json.dump(store, fh, indent=2, default=str)
    except OSError:
        pass


def latest_us_ai_overlay(
    symbol: str, max_age_days: int = 365,
) -> tuple[dict[str, Any], str] | None:
    """Return `(overlay, saved_at_iso)` for the most recent saved US
    AI overlay for `symbol`, or None if missing / older than
    `max_age_days`."""
    sym = (symbol or "").strip().upper()
    if not sym or not os.path.exists(US_AI_OVERLAY_PATH):
        return None
    try:
        with open(US_AI_OVERLAY_PATH, encoding="utf-8") as fh:
            store = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(store, dict):
        return None
    entry = store.get(sym)
    if not isinstance(entry, dict):
        return None
    overlay = entry.get("overlay")
    saved_at = str(entry.get("saved_at") or "")
    if not isinstance(overlay, dict):
        return None
    if max_age_days and saved_at:
        try:
            ts = datetime.datetime.fromisoformat(saved_at.split(".")[0])
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)
            age_days = (datetime.datetime.now() - ts).days
            if age_days > max_age_days:
                return None
        except (ValueError, TypeError):
            pass
    return overlay, saved_at


def latest_us_scan_prior() -> dict[str, Any] | None:
    """Return the most recent prior US scan snapshot (the one that
    was the "latest" before the current `latest_us_scan()` was
    written). Returns None if no prior snapshot exists yet — the
    `/us` diff card renders an "only one scan on file" message."""
    if not os.path.exists(US_SCAN_PRIOR_PATH):
        return None
    try:
        with open(US_SCAN_PRIOR_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def us_scan_diff(rank_move_threshold: int = 3) -> dict[str, Any]:
    """Compare the latest US scan vs the most recent prior scan and
    return the same shape used by the Indian Swing
    `/api/swing/changes_since` endpoint so the /us page can render
    an identical "what changed" card.

    The diff is computed over `candidates` only (the rows the user
    sees in Entry Recommendations). New entries / drops / rank
    movers are sorted by rank, ascending in magnitude.
    """
    latest = latest_us_scan() or {}
    prior = latest_us_scan_prior() or {}

    def _rank_map(scan: dict) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for c in (scan.get("candidates") or []):
            sym = str(c.get("symbol") or "").strip().upper()
            if not sym:
                continue
            try:
                rank = int(c.get("priority_rank") or 0)
            except (TypeError, ValueError):
                rank = 0
            try:
                score = float(c.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            out[sym] = {
                "symbol": sym,
                "rank": rank,
                "score": score,
                "setup_type": c.get("setup_type") or "",
            }
        return out

    latest_map = _rank_map(latest)
    prior_map = _rank_map(prior)

    if not latest:
        return {
            "current_run_id": None,
            "current_run_date": "",
            "current_run_finished_at": "",
            "prior_run_id": None,
            "prior_run_date": "",
            "prior_run_finished_at": "",
            "compared_to_latest": True,
            "skipped_runs": 0,
            "new_entries": [],
            "dropped": [],
            "rank_movers": [],
            "summary": "no US scans on file yet",
        }

    new_entries = sorted(
        [v for s, v in latest_map.items() if s not in prior_map],
        key=lambda d: d["rank"] or 9_999,
    )
    dropped = sorted(
        [
            {
                "symbol": s,
                "prior_rank": v["rank"],
                "prior_score": v["score"],
                "prior_setup_type": v["setup_type"],
                "now_status": "MISSING",
            }
            for s, v in prior_map.items() if s not in latest_map
        ],
        key=lambda d: d["prior_rank"] or 9_999,
    )
    rank_movers: list[dict[str, Any]] = []
    for s, v in latest_map.items():
        if s not in prior_map:
            continue
        pr = prior_map[s]["rank"]
        nr = v["rank"]
        if pr <= 0 or nr <= 0:
            continue
        delta = pr - nr  # +ve = moved up
        if abs(delta) >= int(rank_move_threshold):
            rank_movers.append({
                "symbol": s,
                "prior_rank": pr,
                "new_rank": nr,
                "delta": delta,
                "score_delta": round(v["score"] - prior_map[s]["score"], 2),
            })
    rank_movers.sort(key=lambda d: -abs(d["delta"]))

    bits: list[str] = []
    if new_entries: bits.append(f"{len(new_entries)} new")
    if dropped: bits.append(f"{len(dropped)} dropped")
    if rank_movers:
        bits.append(
            f"{len(rank_movers)} rank mover"
            + ("s" if len(rank_movers) != 1 else "")
        )
    summary = " · ".join(bits) if bits else "no notable changes"

    return {
        # Mirror the swing diff payload keys so the JS renderer
        # can be shared verbatim. We synthesise "run_id" from the
        # finished_at timestamp string (good enough for "different
        # scan" detection on the client).
        "current_run_id": latest.get("finished_at") or "",
        "current_run_date": (latest.get("finished_at") or "")[:10],
        "current_run_finished_at": latest.get("finished_at") or "",
        "prior_run_id": (prior.get("finished_at") or "") if prior else None,
        "prior_run_date": ((prior.get("finished_at") or "")[:10]
                           if prior else None),
        "prior_run_finished_at": prior.get("finished_at") if prior else None,
        "compared_to_latest": True,
        "skipped_runs": 0,
        "new_entries": new_entries,
        "dropped": dropped,
        "rank_movers": rank_movers,
        "summary": summary,
    }


def fetch_us_live_price_now(symbol: str) -> float:
    """Direct yfinance/Yahoo call for the freshest available price
    of a US ticker. Bypasses the throttled `_quote_cache` so retries
    actually hit the network and never silently return stale data.

    Used by the watchlist-add endpoint to enforce the same rule as
    Indian swing adds: the watchlist entry price MUST come from a
    live quote, no candidate / cached-close fallback. Returns 0 if
    every attempt fails — the caller is expected to surface an
    error to the user instead of inserting a zero-price row.

    Tries (in order):
      1. yfinance `Ticker.fast_info["last_price"]` (one HTTP roundtrip).
      2. Yahoo `chart` API for `range=1d&interval=1m` last finite close.
    """
    if not symbol:
        return 0.0
    yf_sym = _normalise_yfinance_symbol(symbol)

    # 1. yfinance fast_info — pulls just the live quote, not history.
    try:
        import yfinance as yf
        ticker = yf.Ticker(yf_sym)
        fast = getattr(ticker, "fast_info", None)
        if fast is not None:
            for key in ("last_price", "lastPrice", "regular_market_price",
                        "regularMarketPrice"):
                try:
                    raw = fast[key] if isinstance(fast, dict) else getattr(fast, key, None)
                except (KeyError, AttributeError):
                    raw = None
                try:
                    price = float(raw or 0)
                except (TypeError, ValueError):
                    price = 0.0
                if price > 0 and _finite(price):
                    return round(price, 4)
    except Exception:
        pass

    # 2. Yahoo chart API — most recent 1-minute close.
    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}",
            params={"range": "1d", "interval": "1m"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        result = ((payload.get("chart") or {}).get("result") or [{}])[0]
        meta = result.get("meta") or {}
        for key in ("regularMarketPrice", "previousClose"):
            try:
                price = float(meta.get(key) or 0)
            except (TypeError, ValueError):
                price = 0.0
            if price > 0 and _finite(price):
                return round(price, 4)
        closes = (((result.get("indicators") or {}).get("quote")
                   or [{}])[0].get("close") or [])
        for value in reversed(closes):
            try:
                price = float(value or 0)
            except (TypeError, ValueError):
                continue
            if price > 0 and _finite(price):
                return round(price, 4)
    except Exception:
        pass

    return 0.0


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
    ticket = float(value or us_config.US_TICKET_AMOUNT)
    return ticket if ticket > 0 else float(us_config.US_TICKET_AMOUNT)


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
def _download_daily_candles(symbol: str, force_network: bool = False) -> list[dict]:
    symbol = _normalise_yfinance_symbol(symbol)
    last_err: Exception | None = None
    best_candles: list[dict] = []

    cached = [] if force_network else _read_cached_us_candles(symbol)
    if len(cached) >= _MIN_CANDLES:
        return cached

    # Prefer Yahoo's public chart JSON endpoint over yfinance for
    # historical candles.  It avoids yfinance's cookie/crumb layer and
    # is less likely to return a throttled 5-row dataframe for SPY.
    for period in _PERIOD_FALLBACKS:
        try:
            candles = _download_yahoo_chart_candles(symbol, period)
        except Exception as exc:  # pragma: no cover - network surface
            last_err = exc
            continue
        if len(candles) > len(best_candles):
            best_candles = candles
        if len(candles) >= _MIN_CANDLES:
            _store_cached_us_candles(symbol, candles)
            return candles

    # yfinance remains as a secondary provider because it is already a
    # dependency for names, live quotes, and FX.
    for period in _PERIOD_FALLBACKS:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - depends on env
            last_err = RuntimeError("yfinance is required for US analysis")
            break
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
            _store_cached_us_candles(symbol, candles)
            return candles

    stale_cached = cached or _read_cached_us_candles(symbol, max_stale_days=None)
    if len(stale_cached) >= _MIN_CANDLES:
        return stale_cached

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
        "Yahoo/yfinance may be rate-limited and no local cache is available"
    )


def _read_cached_us_candles(
    symbol: str,
    max_stale_days: int | None = _US_CACHE_FRESH_DAYS,
) -> list[dict]:
    to_date = now_ist().date()
    from_date = to_date - datetime.timedelta(days=_US_CACHE_LOOKBACK_DAYS)
    try:
        candles = CandleCache().get_cached_candles(
            symbol,
            _US_CACHE_EXCHANGE,
            _US_CACHE_INTERVAL,
            from_date,
            to_date,
        )
    except Exception:
        return []
    if max_stale_days is None or not candles:
        return candles
    last_date = _candle_date(candles[-1])
    if last_date is None:
        return []
    if (to_date - last_date).days > max_stale_days:
        return []
    return candles


def _store_cached_us_candles(symbol: str, candles: list[dict]) -> None:
    try:
        CandleCache().store_candles(
            symbol,
            _US_CACHE_EXCHANGE,
            _US_CACHE_INTERVAL,
            candles,
        )
    except Exception:
        pass


def _download_yahoo_chart_candles(symbol: str, period: str) -> list[dict]:
    response = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": period, "interval": "1d", "events": "history"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=12,
    )
    response.raise_for_status()
    payload = response.json()
    chart = payload.get("chart") or {}
    error = chart.get("error")
    if error:
        raise ValueError(error.get("description") or str(error))
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"no Yahoo chart candles returned for {symbol}")
    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quotes = indicators.get("quote") or []
    quote = quotes[0] if quotes else {}
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    candles: list[dict] = []
    for idx, ts in enumerate(timestamps):
        try:
            open_ = float(opens[idx])
            high = float(highs[idx])
            low = float(lows[idx])
            close = float(closes[idx])
        except (IndexError, TypeError, ValueError):
            continue
        try:
            volume = float(volumes[idx] or 0.0)
        except (IndexError, TypeError, ValueError):
            volume = 0.0
        if not all(_finite(v) and v > 0 for v in (open_, high, low, close)):
            continue
        candles.append({
            "date": datetime.datetime.fromtimestamp(
                int(ts), tz=datetime.timezone.utc).date(),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
    return candles


def _candle_date(candle: dict) -> datetime.date | None:
    value = candle.get("date")
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


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
    re-hit upstream providers.  Uses Yahoo Search first because the
    yfinance info path can fail when Yahoo rejects its crumb/cookie.
    """
    if not symbol:
        return ""
    yf_sym = _normalise_yfinance_symbol(symbol)
    try:
        response = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": yf_sym, "quotesCount": 6, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        if response.ok:
            quotes = (response.json().get("quotes") or [])
            for quote in quotes:
                if str(quote.get("symbol") or "").upper() == yf_sym:
                    name = (quote.get("longname") or quote.get("shortname") or "")
                    if name:
                        return str(name).strip()
    except Exception:
        pass
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
    then fetches fresh prices in the background.

    Strips the internal `_mono` TTL tracker so the public payload
    stays clean (price / change_pct / as_of)."""
    if not symbols:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for raw in symbols:
        if not raw or not raw.strip():
            continue
        sym = raw.strip().upper()
        cached = _quote_cache.get(sym) or {}
        out[sym] = {k: v for k, v in cached.items() if k != "_mono"}
    return out


def get_us_live_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Return `{symbol: {price, change_pct, as_of}}` for US tickers.

    Per-symbol TTL: a cached snapshot <`_QUOTE_SOFT_TTL` s old is
    returned without an upstream call. Anything older (or missing)
    is re-fetched in one batched yfinance call.

    2026-05-19 rewrite: previous version used
    `yfinance.download(period='5d', interval='1d')`, which returns
    DAILY candles. The last candle is yesterday's close once the
    US session ends — so the dashboard tables stopped moving even
    with live polling on, even during US market hours. We now go
    straight to the Yahoo `chart` API with `interval=1m` and use
    `meta.regularMarketPrice` (live tick) or the last finite 1-min
    close as the live price, with `meta.previousClose` for change %.
    Falls back to `yfinance.download(interval='1m')` if the chart
    API errors out.
    """
    if not symbols:
        return {}
    syms = [s.strip().upper() for s in symbols if s and s.strip()]
    seen: set[str] = set()
    uniq: list[str] = []
    for s in syms:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    if not uniq:
        return {}

    # Per-symbol TTL: pull from cache, mark stale ones for re-fetch.
    now_mono = time.monotonic()
    out: dict[str, dict[str, Any]] = {}
    stale: list[str] = []
    for sym in uniq:
        cached = _quote_cache.get(sym)
        if cached and (now_mono - float(cached.get("_mono", 0.0))
                       < _QUOTE_SOFT_TTL):
            # Strip internal _mono key when returning to caller.
            out[sym] = {k: v for k, v in cached.items() if k != "_mono"}
        else:
            stale.append(sym)

    if not stale:
        return out

    ts = now_ist().isoformat()
    yf_map = {_normalise_yfinance_symbol(s): s for s in stale}

    # Per-symbol chart-API call (1m interval = true intraday). This
    # is the source the detail page already uses successfully via
    # `fetch_us_live_price_now`. Batched yfinance.download for
    # 1m interval is unreliable across multiple symbols (Yahoo
    # sometimes truncates), so we loop. With a small per-tier
    # symbol set (open: ~5, watch: ~10) this is cheap.
    fetched_any = False
    for yf_sym, display in yf_map.items():
        snap = _fetch_us_intraday_snapshot(yf_sym, ts)
        if snap:
            snap["_mono"] = now_mono
            _quote_cache[display] = snap
            out[display] = {k: v for k, v in snap.items() if k != "_mono"}
            fetched_any = True
        else:
            # Keep whatever was cached previously (may be empty).
            cached = _quote_cache.get(display)
            if cached:
                out[display] = {k: v for k, v in cached.items() if k != "_mono"}
            else:
                out[display] = {}

    # Yahoo chart 1m can rate-limit on very-fresh repeats. As a
    # safety net, if NOTHING came back from the per-symbol loop,
    # fall back to a single batched yfinance.download(interval='1m').
    if not fetched_any:
        try:
            import yfinance as yf
            df = yf.download(
                list(yf_map.keys()),
                period="1d",
                interval="1m",
                auto_adjust=False,
                progress=False,
                threads=False,
                group_by="ticker",
            )
            for yf_sym, display in yf_map.items():
                try:
                    sub = df[yf_sym] if len(yf_map) > 1 else df
                except (KeyError, ValueError):
                    continue
                try:
                    closes = [float(v) for v in sub["Close"].dropna().tolist()]
                except (KeyError, AttributeError, TypeError, ValueError):
                    closes = []
                if not closes:
                    continue
                last = closes[-1]
                prev = closes[0] if len(closes) >= 2 else last
                change_pct = ((last / prev - 1.0) * 100.0) if prev > 0 else 0.0
                snap = {
                    "price": round(last, 4),
                    "change_pct": round(change_pct, 2),
                    "as_of": ts,
                    "_mono": now_mono,
                }
                _quote_cache[display] = snap
                out[display] = {k: v for k, v in snap.items() if k != "_mono"}
        except Exception:
            pass

    return out


def _fetch_us_intraday_snapshot(yf_sym: str, ts: str) -> dict[str, Any] | None:
    """Single Yahoo `chart` API call with 1m interval. Returns
    {price, change_pct, as_of} or None on any error. Used by
    `get_us_live_quotes` per-symbol so a 15-stock watchlist still
    fans out without a 15 s shared throttle in the way."""
    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}",
            params={"range": "1d", "interval": "1m"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=6,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None
    result = ((payload.get("chart") or {}).get("result") or [{}])[0]
    meta = result.get("meta") or {}
    # Prefer the LIVE tick from `regularMarketPrice` — this is the
    # exact value Yahoo Finance's web UI shows in real time.
    price = 0.0
    for key in ("regularMarketPrice", "chartPreviousClose"):
        try:
            v = float(meta.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
        if v > 0 and _finite(v):
            price = v
            break
    # Last finite 1m close as fallback.
    if price <= 0:
        closes = (((result.get("indicators") or {}).get("quote") or [{}])[0]
                  .get("close") or [])
        for value in reversed(closes):
            try:
                v = float(value or 0.0)
            except (TypeError, ValueError):
                continue
            if v > 0 and _finite(v):
                price = v
                break
    if price <= 0:
        return None
    try:
        prev = float(meta.get("previousClose") or 0.0)
    except (TypeError, ValueError):
        prev = 0.0
    change_pct = ((price / prev - 1.0) * 100.0) if prev > 0 else 0.0
    return {
        "price": round(price, 4),
        "change_pct": round(change_pct, 2),
        "as_of": ts,
    }


# ── USD / INR ──────────────────────────────────────────────────

def cached_usd_inr_rate() -> dict[str, Any]:
    """Return the cached USD->INR rate without touching the network."""
    if _fx_cache and _fx_cache.get("rate"):
        return dict(_fx_cache)
    if os.path.exists(_FX_CACHE_PATH):
        try:
            with open(_FX_CACHE_PATH, encoding="utf-8") as handle:
                disk = json.load(handle)
            if isinstance(disk, dict) and disk.get("rate"):
                _fx_cache.update(disk)
                return dict(disk)
        except (OSError, json.JSONDecodeError):
            pass
    return {"rate": 0.0, "as_of": "", "source": "unavailable"}


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