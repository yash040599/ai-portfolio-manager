# ================================================================
# modes/dashboard/live_quotes.py
# ================================================================
# Shared read-only Zerodha quote polling layer (Dashboard D30).
#
# Batches visible symbols, respects rate limits, stamps as_of,
# returns current prices for dashboard display overlay.
# Does NOT re-run analysis or place orders.
# ================================================================

from __future__ import annotations

import json
import os
import time
from typing import Any

from config import Config, now_ist
from core.logger import Logger
from core.zerodha_client import ZerodhaClient


# Rate-limit: minimum seconds between quote batches
_MIN_POLL_INTERVAL = 5.0
_last_poll_time = 0.0
_cached_quotes: dict[str, dict] = {}


def get_live_quotes(symbols: list[str],
                    exchange: str = "NSE") -> dict[str, dict]:
    """Fetch live quotes for a list of symbols.

    Returns {symbol: {price, as_of, change_pct}} or cached values
    when called faster than the rate limit. Returns empty dicts for
    symbols that fail.
    """
    global _last_poll_time, _cached_quotes

    if not symbols:
        return {}

    now = time.monotonic()
    if now - _last_poll_time < _MIN_POLL_INTERVAL and _cached_quotes:
        return {s: _cached_quotes.get(s, {}) for s in symbols}

    try:
        # Check if we have a valid Zerodha token today
        token_path = os.path.join("data", "access_token.json")
        if not os.path.exists(token_path):
            return {s: _cached_quotes.get(s, {}) for s in symbols}

        with open(token_path, encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("date") != str(now_ist().date()):
            return {s: _cached_quotes.get(s, {}) for s in symbols}

        log = Logger("LiveQuotes")
        zerodha = ZerodhaClient(Config, log)
        zerodha.login(interactive=False)

        # `ZerodhaClient.get_quotes()` expects a list of
        # `{"symbol": ..., "exchange": ...}` dicts (NOT a list of
        # "EXCHANGE:SYMBOL" strings). Passing strings here was the
        # 2026-05-14 source of the user-visible "string indices
        # must be integers, not 'str'" toast — the inner code did
        # `s["exchange"]` on each entry, which silently does string
        # indexing on a string and raises that exact TypeError.
        stocks = [{"symbol": s, "exchange": exchange} for s in symbols]
        raw = zerodha.get_quotes(stocks)

        ts = now_ist().isoformat()
        result: dict[str, dict] = {}
        for s in symbols:
            key = f"{exchange}:{s}"
            q = raw.get(key) if isinstance(raw, dict) else None
            if isinstance(q, dict):
                ltp = q.get("last_price", 0) or 0
                ohlc = q.get("ohlc")
                if isinstance(ohlc, dict):
                    prev_close = ohlc.get("close", ltp) or ltp
                else:
                    prev_close = ltp
                change_pct = ((ltp / prev_close - 1) * 100
                              if prev_close > 0 else 0)
                result[s] = {
                    "price": ltp,
                    "as_of": ts,
                    "change_pct": round(change_pct, 2),
                    "volume": q.get("volume", 0) or 0,
                }
                _cached_quotes[s] = result[s]
            else:
                # Sparse Kite response (illiquid name, paused, etc.)
                # — keep whatever was cached and move on quietly.
                result[s] = _cached_quotes.get(s, {})

        _last_poll_time = time.monotonic()
        return result

    except Exception as exc:
        # Record so the dashboard surfaces a top-right toast instead
        # of the previous silent fall-through to cached values. Auth-
        # shaped Zerodha errors ("Incorrect `api_key` or `access_token`")
        # ALSO invalidate the saved token file inside `record_external_error`,
        # which flips the auth pill to "Re-login" on the next render —
        # the user no longer has to guess why prices stopped updating.
        from core.error_sink import record_external_error
        record_external_error("zerodha", exc, log=Logger("LiveQuotes"))
        return {s: _cached_quotes.get(s, {}) for s in symbols}
