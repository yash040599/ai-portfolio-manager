"""Lazy NSE stock-name lookup.

Loads `{symbol: company_name}` from the Zerodha Kite instruments
dump (one network call) and caches it to disk so subsequent
dashboard renders are instant.  Returns '' for any unknown symbol
or when Zerodha auth is unavailable, so callers can safely render
`name or symbol` without conditional checks.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

_CACHE_PATH = os.path.join("data", "nse_stock_names.json")
_MAX_AGE_SECS = 7 * 24 * 60 * 60  # 1 week

_memory: dict[str, str] | None = None
_last_load: float = 0.0


def get_nse_stock_name(symbol: str) -> str:
    """Return the company name for an NSE symbol, or '' on failure."""
    if not symbol:
        return ""
    sym = symbol.strip().upper()
    table = _load_table()
    return table.get(sym, "")


def _load_table() -> dict[str, str]:
    global _memory, _last_load
    if _memory is not None and (time.time() - _last_load) < 300:
        return _memory
    # Disk cache.
    if os.path.exists(_CACHE_PATH):
        try:
            age = time.time() - os.path.getmtime(_CACHE_PATH)
            with open(_CACHE_PATH, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and data:
                _memory = {k.upper(): str(v) for k, v in data.items()}
                _last_load = time.time()
                if age < _MAX_AGE_SECS:
                    return _memory
        except (OSError, json.JSONDecodeError):
            pass
    # Refresh from Zerodha (best effort).
    fresh = _refresh_from_kite()
    if fresh:
        _memory = fresh
        _last_load = time.time()
        try:
            os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
            with open(_CACHE_PATH, "w", encoding="utf-8") as handle:
                json.dump(fresh, handle, indent=0)
        except OSError:
            pass
        return fresh
    return _memory or {}


def _refresh_from_kite() -> dict[str, str]:
    """Build `{symbol: name}` from Kite instruments.  Returns {} on
    any failure (no auth, offline, etc.)."""
    try:
        token_path = os.path.join("data", "access_token.json")
        if not os.path.exists(token_path):
            return {}
        from config import Config, now_ist
        with open(token_path, encoding="utf-8") as handle:
            saved = json.load(handle)
        if saved.get("date") != str(now_ist().date()):
            return {}
        from core.logger import Logger
        from core.zerodha_client import ZerodhaClient
        zerodha = ZerodhaClient(Config, Logger("StockNames"))
        zerodha.login(interactive=False)
        rows: list[Any] = zerodha._kite.instruments("NSE")
    except Exception:
        return {}
    out: dict[str, str] = {}
    for row in rows:
        try:
            sym = str(row.get("tradingsymbol") or "").strip().upper()
            name = str(row.get("name") or "").strip()
            seg = str(row.get("segment") or "").upper()
            inst = str(row.get("instrument_type") or "").upper()
        except AttributeError:
            continue
        if not sym or not name:
            continue
        # Only equity (EQ) cash-market rows.
        if seg != "NSE" or inst != "EQ":
            continue
        # Prefer the longer name when duplicates exist.
        prev = out.get(sym, "")
        if len(name) > len(prev):
            out[sym] = name
    return out


__all__ = ["get_nse_stock_name"]
