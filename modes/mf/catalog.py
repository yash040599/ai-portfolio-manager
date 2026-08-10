# ================================================================
# modes/mf/catalog.py
# ================================================================
# Scheme catalogue + NAV history for the mutual-fund book.
#
# Three jobs, three sources:
#   1. Coin catalogue (kite.mf_instruments) — every scheme Zerodha
#      lists, with today's NAV. This powers the "add a fund I hold
#      elsewhere" picker and prices external holdings, so a fund
#      bought at another broker is marked with the same NAV Coin
#      uses. Cached to data/mf_instruments.json (refreshed daily).
#   2. AMFI NAVAll.txt — the ISIN -> AMFI scheme-code map. Coin keys
#      funds by ISIN; the NAV-history API keys them by scheme code,
#      and AMFI is the only authoritative bridge between the two.
#   3. MFapi (api.mfapi.in) — daily NAV history, used for the
#      per-scheme charts. Free, no key, AMFI-derived.
#
# Every network call here is fail-soft: the book must still render
# from cache when the user is offline or Zerodha is down.
# ================================================================

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any

from config import Config, now_ist
from core.logger import Logger


CATALOG_PATH = os.path.join("data", "mf_instruments.json")
SCHEME_MAP_PATH = os.path.join("data", "mf_scheme_map.json")

_AMFI_NAV_ALL = "https://portal.amfiindia.com/spages/NAVAll.txt"
_MFAPI_BASE = "https://api.mfapi.in/mf"

_HTTP_TIMEOUT = 20
# AMFI's dump is ~8 MB of text; anything past 32 MB is not the file we
# asked for, so stop reading rather than buffer an unbounded response.
_MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
_SCHEME_CODE_RE = re.compile(r"^\d{1,10}$")

# In-process memo so a page render does not re-read the JSON per row.
_catalog_memo: list[dict] | None = None
_scheme_map_memo: dict[str, str] | None = None


# ── Coin scheme catalogue ───────────────────────────────────────

def _read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_json(path: str, payload: Any) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
    except OSError:
        pass


def cached_catalog() -> list[dict]:
    """Coin scheme catalogue from disk. Never contacts Zerodha."""
    global _catalog_memo
    if _catalog_memo is not None:
        return _catalog_memo
    blob = _read_json(CATALOG_PATH) or {}
    _catalog_memo = list(blob.get("schemes") or [])
    return _catalog_memo


def catalog_as_of() -> str:
    blob = _read_json(CATALOG_PATH) or {}
    return str(blob.get("fetched_at") or "")


def refresh_catalog(zerodha=None, *, log: Logger | None = None) -> list[dict]:
    """Re-download the Coin scheme catalogue and cache it.

    Returns the cached copy unchanged when Zerodha is unreachable —
    a stale catalogue is far better than an empty picker.
    """
    global _catalog_memo
    log = log or Logger("MF")

    try:
        if zerodha is None:
            from core.zerodha_client import ZerodhaClient
            zerodha = ZerodhaClient(Config, log)
            zerodha.login(interactive=False)
        schemes = zerodha.get_mf_instruments()
    except Exception as exc:  # noqa: BLE001 — offline is not fatal here
        log.warning(f"MF catalogue refresh failed: {exc}")
        return cached_catalog()

    if not schemes:
        return cached_catalog()

    _write_json(CATALOG_PATH, {
        "fetched_at": now_ist().isoformat(timespec="seconds"),
        "count": len(schemes),
        "schemes": schemes,
    })
    _catalog_memo = schemes

    # The catalogue carries a NAV for every scheme — bank it so
    # external holdings stay priced without a broker session.
    try:
        from modes.mf.persistence import cache_navs
        cache_navs(schemes)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"NAV cache write failed: {exc}")

    return schemes


def ensure_catalog(zerodha=None, *, log: Logger | None = None,
                   max_age_days: int = 1) -> list[dict]:
    """Catalogue from cache, refreshed only when stale."""
    fetched = catalog_as_of()
    if fetched:
        try:
            age = (now_ist().date()
                   - datetime.datetime.fromisoformat(fetched).date()).days
            if age <= max_age_days and cached_catalog():
                return cached_catalog()
        except ValueError:
            pass
    return refresh_catalog(zerodha, log=log)


def scheme_by_code(scheme_code: str) -> dict:
    code = (scheme_code or "").strip().upper()
    if not code:
        return {}
    for row in cached_catalog():
        if str(row.get("scheme_code") or "").upper() == code:
            return row
    return {}


def search_catalog(query: str, limit: int = 25) -> list[dict]:
    """Substring search over the Coin catalogue for the add-fund picker."""
    q = (query or "").strip().lower()
    if len(q) < 2:
        return []
    terms = [t for t in q.split() if t]
    out: list[dict] = []
    for row in cached_catalog():
        haystack = (f"{row.get('name', '')} {row.get('amc', '')} "
                    f"{row.get('scheme_code', '')}").lower()
        if all(t in haystack for t in terms):
            out.append({
                "scheme_code": row.get("scheme_code", ""),
                "name": row.get("name", ""),
                "amc": row.get("amc", ""),
                "plan": row.get("plan", ""),
                "scheme_type": row.get("scheme_type", ""),
                "nav": row.get("nav", 0),
                "nav_date": row.get("nav_date", ""),
            })
            if len(out) >= limit:
                break
    out.sort(key=lambda r: str(r.get("name") or ""))
    return out


# ── AMFI ISIN -> scheme-code map ────────────────────────────────

def _download_text(url: str) -> str:
    """GET a text file with a hard timeout and a size cap."""
    import requests

    with requests.get(url, timeout=_HTTP_TIMEOUT, stream=True) as resp:
        resp.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_DOWNLOAD_BYTES:
                raise ValueError("response larger than the expected AMFI dump")
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def refresh_scheme_map(*, log: Logger | None = None) -> dict[str, str]:
    """Rebuild the ISIN -> AMFI scheme-code map from NAVAll.txt.

    Layout is semicolon-separated with AMC/category headers mixed in:
      Scheme Code;ISIN Growth;ISIN Reinvest;Scheme Name;NAV;Date
    """
    global _scheme_map_memo
    log = log or Logger("MF")
    try:
        text = _download_text(_AMFI_NAV_ALL)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"AMFI scheme map refresh failed: {exc}")
        return cached_scheme_map()

    mapping: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) < 6:
            continue
        code = parts[0].strip()
        if not _SCHEME_CODE_RE.match(code):
            continue
        for isin in (parts[1].strip().upper(), parts[2].strip().upper()):
            if _ISIN_RE.match(isin):
                mapping.setdefault(isin, code)

    if not mapping:
        return cached_scheme_map()

    _write_json(SCHEME_MAP_PATH, {
        "fetched_at": now_ist().isoformat(timespec="seconds"),
        "count": len(mapping),
        "isin_to_scheme_code": mapping,
    })
    _scheme_map_memo = mapping
    return mapping


def cached_scheme_map() -> dict[str, str]:
    global _scheme_map_memo
    if _scheme_map_memo is not None:
        return _scheme_map_memo
    blob = _read_json(SCHEME_MAP_PATH) or {}
    _scheme_map_memo = dict(blob.get("isin_to_scheme_code") or {})
    return _scheme_map_memo


def amfi_code_for_isin(isin: str, *, allow_refresh: bool = True) -> str:
    key = (isin or "").strip().upper()
    if not _ISIN_RE.match(key):
        return ""
    mapping = cached_scheme_map()
    if key in mapping:
        return mapping[key]
    if allow_refresh and not mapping:
        mapping = refresh_scheme_map()
    return mapping.get(key, "")


# ── NAV history (MFapi) ─────────────────────────────────────────

def nav_history(isin: str, *, days: int = 365) -> dict:
    """Daily NAV series for one scheme, oldest first.

    Returns {"ok", "scheme_name", "points": [{"date", "nav"}, ...]}.
    Charting is a nice-to-have, so every failure degrades to ok=False
    rather than raising into a page render.
    """
    import requests

    code = amfi_code_for_isin(isin)
    if not code:
        return {"ok": False, "error": "No AMFI scheme code for this ISIN",
                "points": [], "scheme_name": ""}
    # Belt-and-braces: the code is interpolated into a URL path.
    if not _SCHEME_CODE_RE.match(code):
        return {"ok": False, "error": "Invalid scheme code", "points": [],
                "scheme_name": ""}

    try:
        resp = requests.get(f"{_MFAPI_BASE}/{code}", timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        blob = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200], "points": [],
                "scheme_name": ""}

    cutoff = now_ist().date() - datetime.timedelta(days=max(1, days))
    points: list[dict] = []
    for row in (blob.get("data") or []):
        raw_date = str(row.get("date") or "")
        try:
            day, month, year = raw_date.split("-")
            stamp = f"{year}-{month}-{day}"
            if datetime.date.fromisoformat(stamp) < cutoff:
                continue
            points.append({"date": stamp, "nav": float(row.get("nav") or 0)})
        except (ValueError, AttributeError):
            continue

    points.sort(key=lambda p: p["date"])
    meta = blob.get("meta") or {}
    return {
        "ok": bool(points),
        "scheme_name": str(meta.get("scheme_name") or ""),
        "scheme_category": str(meta.get("scheme_category") or ""),
        "fund_house": str(meta.get("fund_house") or ""),
        "amfi_code": code,
        "points": points,
    }


__all__ = [
    "CATALOG_PATH", "SCHEME_MAP_PATH",
    "cached_catalog", "catalog_as_of", "refresh_catalog", "ensure_catalog",
    "scheme_by_code", "search_catalog",
    "refresh_scheme_map", "cached_scheme_map", "amfi_code_for_isin",
    "nav_history",
]
