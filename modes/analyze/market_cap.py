# ================================================================
# modes/analyze/market_cap.py
# ================================================================
# Market-cap tier classifier (LARGE / MID / SMALL) for the
# Portfolio Analyser.
#
# SEBI/AMFI define the tiers by *rank*: LARGE = top 100 by full
# market cap, MID = 101-250, SMALL = 251+. A single stock's market
# cap doesn't reveal its rank, so we approximate the rank cut-offs
# with absolute INR thresholds derived from AMFI's latest list
# (the ~100th name sits near Rs.50,000 cr, the ~250th near
# Rs.18,000 cr). These drift slowly, so a periodic threshold tweak
# is all that's needed.
#
# Hybrid resolution order (per ANALYZE design choice 2026-06):
#   1. Live market cap from yfinance (cached) -> threshold tier.
#   2. Curated data/market_cap_tier.json seed (override/fallback).
#   3. "UNKNOWN" (never silently binned).
#
# yfinance is slow and flaky, so every successful lookup is cached
# in data/market_cap_cache.json with a timestamp; tiers are stable
# enough that a long TTL is fine.
# ================================================================

from __future__ import annotations

import datetime
import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CACHE_PATH = _PROJECT_ROOT / "data" / "market_cap_cache.json"

# Absolute thresholds in Rs. crore (approximate AMFI rank cut-offs).
_LARGE_MIN_CR = 50_000.0   # ~ rank 100
_MID_MIN_CR = 18_000.0     # ~ rank 250

# Re-fetch a symbol's market cap at most this often (tiers are stable).
_CACHE_TTL_DAYS = 30


def tier_from_mcap_cr(mcap_cr: float) -> str:
    """Map an absolute market cap (in Rs. crore) to a tier label."""
    if mcap_cr >= _LARGE_MIN_CR:
        return "LARGE"
    if mcap_cr >= _MID_MIN_CR:
        return "MID"
    return "SMALL"


# ── Cache helpers ───────────────────────────────────────────────

def _load_cache() -> dict:
    if not _CACHE_PATH.exists():
        return {}
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_PATH.write_text(
            json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8"
        )
    except OSError:
        pass


def _cache_fresh(entry: dict) -> bool:
    ts = entry.get("fetched_at")
    if not ts:
        return False
    try:
        fetched = datetime.datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return False
    age = datetime.datetime.now() - fetched
    return age.days < _CACHE_TTL_DAYS


# ── yfinance fetch ──────────────────────────────────────────────

def _fetch_mcap_cr(symbol: str) -> float | None:
    """Fetch market cap (Rs. crore) for an NSE symbol via yfinance.
    Returns None on any failure (not installed, no data, network).
    Best-effort and fail-open by design."""
    try:
        import yfinance as yf
    except ImportError:
        return None

    for suffix in (".NS", ".BO"):
        try:
            tkr = yf.Ticker(f"{symbol}{suffix}")
            mcap = None
            # fast_info is lighter and less likely to hang than .info
            fi = getattr(tkr, "fast_info", None)
            if fi is not None:
                try:
                    mcap = fi.get("market_cap") if hasattr(fi, "get") \
                        else getattr(fi, "market_cap", None)
                except Exception:
                    mcap = None
            if not mcap:
                info = getattr(tkr, "info", {}) or {}
                mcap = info.get("marketCap")
            if mcap and float(mcap) > 0:
                return float(mcap) / 1e7  # rupees -> crore
        except Exception:
            continue
    return None


# ── Public API ──────────────────────────────────────────────────

def classify_tier(
    symbol: str,
    curated_lookup: dict | None = None,
    *,
    use_live: bool = True,
) -> tuple[str, str]:
    """Resolve the cap tier for an NSE symbol.

    Returns (tier, note) where tier is LARGE / MID / SMALL / ETF /
    UNKNOWN and note explains the source. Order: live yfinance market
    cap (cached) -> curated seed -> UNKNOWN. The curated seed wins for
    ETFs (which have no meaningful single-stock market cap) and as a
    fallback whenever the live fetch is unavailable.
    """
    sym = (symbol or "").strip().upper()
    curated_lookup = curated_lookup or {}
    curated = curated_lookup.get(sym)

    # ETFs are never market-cap classified — trust the curated tag.
    if curated == "ETF":
        return "ETF", "curated seed (ETF)"

    if use_live:
        cache = _load_cache()
        entry = cache.get(sym)
        mcap_cr = None
        if isinstance(entry, dict) and _cache_fresh(entry):
            mcap_cr = entry.get("mcap_cr")
        else:
            mcap_cr = _fetch_mcap_cr(sym)
            if mcap_cr is not None:
                cache[sym] = {
                    "mcap_cr": round(mcap_cr, 1),
                    "fetched_at": datetime.datetime.now().isoformat(),
                }
                _save_cache(cache)

        if mcap_cr is not None and mcap_cr > 0:
            tier = tier_from_mcap_cr(mcap_cr)
            return tier, f"yfinance mcap ~Rs.{mcap_cr:,.0f} cr"

    # Live unavailable — fall back to the curated seed.
    if curated:
        return curated, "curated seed (live fetch unavailable)"

    return "UNKNOWN", "no live mcap and no seed entry — refresh seed"
