"""
modes/dashboard/home_summary.py
===============================

Aggregates every book the dashboard tracks into one payload for the
home page (2026-07-30 UI revamp).

Book boundaries — deliberately NOT double counted
-------------------------------------------------
* **India (Zerodha)** — the real Indian portfolio.  Sourced from the
  latest ``--mode analyze`` snapshot in ``data/portfolio_analyses.db``,
  which is itself read from the Zerodha demat holdings.
* **India swing open book** — a *tracking* ledger for names bought with
  the swing tool.  Those shares already sit inside the Zerodha holdings
  above, so this book is reported separately and is **never** added to
  net worth.
* **US** — the swing ``swing_positions`` rows on US exchanges.  There is
  no US broker integration, so this book *is* the US portfolio (it
  holds the MSFT / ORCL RSU lots).  It **is** added to net worth.
* **Intraday** — realised-only, from the verified intraday tax ledger.

Everything here is failure-silent: a missing DB, an expired broker
token or a dead yfinance call degrades one card, never the page.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any

from config import Config, now_ist


_TOKEN_PATH = os.path.join("data", "access_token.json")

US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA", "US"}

# Indian market hours (IST) and US regular session (ET), weekdays only.
_IN_OPEN = datetime.time(9, 15)
_IN_CLOSE = datetime.time(15, 30)
_US_OPEN = datetime.time(9, 30)
_US_CLOSE = datetime.time(16, 0)


# ── Broker auth ──────────────────────────────────────────────────

def auth_state() -> dict[str, Any]:
    """Zerodha token status, mirroring the nav auth pill logic."""
    today = now_ist().date().isoformat()
    out = {"valid": False, "token_date": "", "reason": "missing",
           "has_saved_creds": False, "user_id": "", "login_url": ""}

    api_key = str(getattr(Config, "ZERODHA_API_KEY", "") or "")
    if api_key:
        out["login_url"] = f"https://kite.trade/connect/login?api_key={api_key}&v=3"

    user_id = str(getattr(Config, "KITE_USER_ID", "") or "")
    out["has_saved_creds"] = bool(user_id and getattr(Config, "KITE_PASSWORD", ""))
    out["user_id"] = user_id

    try:
        if os.path.exists(_TOKEN_PATH):
            with open(_TOKEN_PATH, encoding="utf-8") as fh:
                saved = json.load(fh)
            out["token_date"] = str(saved.get("date") or "")
            if out["token_date"] == today:
                out["valid"] = True
                out["reason"] = "ok"
            else:
                out["reason"] = "expired"
    except Exception:
        out["reason"] = "unreadable"

    if out["valid"]:
        try:
            from core.error_sink import has_auth_invalid
            if has_auth_invalid():
                out["valid"] = False
                out["reason"] = "rejected"
        except Exception:
            pass
    return out


# ── Market clocks ────────────────────────────────────────────────

def market_state() -> dict[str, Any]:
    ist = now_ist()
    weekday = ist.weekday() < 5
    in_open = weekday and _IN_OPEN <= ist.time() <= _IN_CLOSE

    us_open = False
    us_local = ""
    try:
        from zoneinfo import ZoneInfo
        et = datetime.datetime.now(ZoneInfo("America/New_York"))
        us_local = et.strftime("%H:%M")
        us_open = et.weekday() < 5 and _US_OPEN <= et.time() <= _US_CLOSE
    except Exception:
        pass

    return {
        "ist": ist.strftime("%H:%M"),
        "ist_date": ist.strftime("%a, %d %b %Y"),
        "india_open": in_open,
        "india_label": "NSE open" if in_open else "NSE closed",
        "us_open": us_open,
        "us_local": us_local,
        "us_label": "US open" if us_open else "US closed",
    }


# ── FX ───────────────────────────────────────────────────────────

def fx_rate(*, live: bool) -> dict[str, Any]:
    """USD/INR. Falls back to the cached file, then to a Config default."""
    if live:
        try:
            from modes.dashboard.us_analysis import get_usd_inr_rate
            fx = get_usd_inr_rate()
            if float(fx.get("rate") or 0) > 0:
                return {"rate": float(fx["rate"]),
                        "as_of": str(fx.get("as_of") or ""),
                        "source": "yfinance"}
        except Exception:
            pass
    try:
        path = os.path.join("data", "usdinr_rate.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                cached = json.load(fh)
            if float(cached.get("rate") or 0) > 0:
                return {"rate": float(cached["rate"]),
                        "as_of": str(cached.get("as_of") or ""),
                        "source": "cache"}
    except Exception:
        pass
    return {"rate": float(getattr(Config, "USD_INR_FALLBACK", 0) or 0),
            "as_of": "", "source": "fallback"}


# ── India (Zerodha holdings via the analyse snapshot) ────────────

def _fv(field, default=0.0) -> float:
    if field is None or getattr(field, "value", None) is None:
        return default
    try:
        return float(field.value)
    except (TypeError, ValueError):
        return default


def india_book(*, live: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "available": False, "holdings": 0, "invested": 0.0, "current": 0.0,
        "pnl": 0.0, "pnl_pct": 0.0, "as_of": "", "mode": "", "age_days": None,
        "top": [], "sectors": [], "cash": None, "live": False,
    }
    try:
        from modes.analyze.persistence import latest_snapshot
        snap = latest_snapshot()
    except Exception:
        snap = None
    if snap is None:
        return out

    out["available"] = True
    out["mode"] = str(snap.mode or "")
    out["as_of"] = snap.timestamp.isoformat()
    try:
        out["age_days"] = (now_ist().date() - snap.timestamp.date()).days
    except Exception:
        out["age_days"] = None
    out["holdings"] = len(snap.holdings)

    m = snap.metrics
    invested = _fv(m.total_invested)
    current = _fv(m.total_current_value)
    cash = _fv(getattr(m, "cash_balance", None), default=-1.0)
    if cash >= 0:
        out["cash"] = cash

    # Optionally re-price against live quotes so the headline is not a
    # stale snapshot from the last analyse run.
    prices: dict[str, dict] = {}
    if live:
        symbols = [h.symbol for h in snap.holdings if h.symbol]
        try:
            from modes.dashboard.live_quotes import get_live_quotes
            prices = get_live_quotes(symbols[:100])
        except Exception:
            prices = {}

    rows: list[dict[str, Any]] = []
    repriced_total = 0.0
    any_live = False
    for h in snap.holdings:
        qty = _fv(h.qty)
        avg = _fv(h.avg_buy_price)
        snap_price = _fv(h.current_price)
        px = snap_price
        quote = prices.get(h.symbol) or {}
        qp = float(quote.get("price") or 0)
        if qp > 0:
            px = qp
            any_live = True
        value = qty * px if qty and px else _fv(h.current_value)
        cost = qty * avg if qty and avg else _fv(h.invested_value)
        repriced_total += value
        rows.append({
            "symbol": h.symbol,
            "qty": qty,
            "avg": avg,
            "price": px,
            "value": value,
            "pnl": value - cost,
            "pnl_pct": ((value / cost - 1) * 100) if cost > 0 else 0.0,
            "sector": str(getattr(h.sector, "value", "") or "") if h.sector else "",
            "change_pct": float(quote.get("change_pct") or 0),
            # Snapshot prices are still a real mark, so India rows are
            # always "priced" even when the broker is unreachable.
            "priced": px > 0,
        })

    if any_live and repriced_total > 0:
        current = repriced_total
        out["live"] = True

    out["invested"] = invested
    out["current"] = current
    out["pnl"] = current - invested
    out["pnl_pct"] = ((current / invested - 1) * 100) if invested > 0 else 0.0

    rows.sort(key=lambda r: r["value"], reverse=True)
    out["top"] = rows[:6]
    total = sum(r["value"] for r in rows) or 1.0
    buckets: dict[str, float] = {}
    for r in rows:
        buckets[r["sector"] or "OTHER"] = buckets.get(r["sector"] or "OTHER", 0.0) + r["value"]
    out["sectors"] = sorted(
        ({"sector": k, "weight_pct": v / total * 100, "value": v}
         for k, v in buckets.items()),
        key=lambda d: d["weight_pct"], reverse=True,
    )[:8]
    return out


# ── Swing books ──────────────────────────────────────────────────

def _position_rows(positions, quotes: dict[str, dict]) -> tuple[list[dict], float, float]:
    """Value an open book. Rows without a live quote fall back to entry
    price and are flagged `priced=False` so the UI can show a dash
    rather than a meaningless zero P&L."""
    rows: list[dict] = []
    invested = 0.0
    current = 0.0
    for p in positions:
        qty = float(p.managed_qty or 0)
        entry = float(p.entry_price or 0)
        quote = quotes.get(p.symbol) or {}
        quoted = float(quote.get("price") or 0)
        px = quoted or entry
        cost = qty * entry
        value = qty * px
        invested += cost
        current += value
        rows.append({
            "symbol": p.symbol,
            "exchange": p.exchange,
            "qty": qty,
            "entry": entry,
            "price": px,
            "value": value,
            "pnl": value - cost,
            "pnl_pct": ((px / entry - 1) * 100) if entry > 0 else 0.0,
            "live": bool(quoted),
            "priced": bool(quoted),
            "entry_date": p.entry_date,
        })
    rows.sort(key=lambda r: r["value"], reverse=True)
    return rows, invested, current


def swing_india_book(*, live: bool) -> dict[str, Any]:
    """Tracking book only — these shares are already inside `india_book`."""
    out = {"positions": 0, "invested": 0.0, "current": 0.0, "unrealised": 0.0,
           "unrealised_pct": 0.0, "realised_net": 0.0, "closed": 0,
           "watchlist": 0, "pending_actions": 0, "rows": [], "live": False,
           "last_run": "", "last_run_mode": ""}
    try:
        from modes.swing.persistence import (
            actions_for_run, get_watchlist, latest_run, open_positions,
            realised_pnl_summary,
        )
    except Exception:
        return out

    try:
        positions = open_positions(exchange="NSE")
    except Exception:
        positions = []
    try:
        out["watchlist"] = len(get_watchlist(exchange="NSE"))
    except Exception:
        pass
    try:
        pnl = realised_pnl_summary(exchange="NSE")
        out["realised_net"] = float(pnl.get("net_pnl") or 0)
        out["closed"] = int(pnl.get("count") or 0)
    except Exception:
        pass

    quotes: dict[str, dict] = {}
    symbols = [p.symbol for p in positions if p.symbol]
    if symbols:
        try:
            if live:
                from modes.dashboard.live_quotes import get_live_quotes
                quotes = get_live_quotes(symbols)
            else:
                from modes.dashboard.live_quotes import cached_live_quotes
                quotes = cached_live_quotes(symbols)
        except Exception:
            quotes = {}

    rows, invested, current = _position_rows(positions, quotes)
    out["positions"] = len(positions)
    out["rows"] = rows[:6]
    out["invested"] = invested
    out["current"] = current
    out["unrealised"] = current - invested
    out["unrealised_pct"] = ((current / invested - 1) * 100) if invested > 0 else 0.0
    out["live"] = any(r["live"] for r in rows)

    try:
        run = latest_run()
        if run:
            out["last_run"] = str(run.get("finished_at") or "")[:16].replace("T", " ")
            out["last_run_mode"] = str(run.get("mode") or "")
            acts = actions_for_run(int(run["run_id"]))
            out["pending_actions"] = sum(
                1 for a in acts
                if a.status == "PENDING" and a.action_type == "ENTRY")
    except Exception:
        pass
    return out


# ── Mutual funds (Coin + externally-held) ────────────────────────

def mf_book(*, live: bool) -> dict[str, Any]:
    """Mutual funds: Coin holdings plus funds tracked at other brokers.

    `live` only controls whether Coin is re-fetched. A fund is always
    marked to its last published NAV — there is no intraday price to
    upgrade to, which is why `nav_as_of` travels with the money.
    """
    out: dict[str, Any] = {
        "available": False, "schemes": 0, "invested": 0.0, "current": 0.0,
        "pnl": 0.0, "pnl_pct": 0.0, "nav_as_of": "", "rows": [],
        "monthly_sip": 0.0, "active_sips": 0, "paused_sips": 0,
        "external_count": 0, "unpriced": 0,
    }
    try:
        from modes.mf.book import build_book
        book = build_book(live=live)
    except Exception:
        return out

    if not book.holdings:
        return out

    out["available"] = True
    out["schemes"] = len(book.schemes)
    out["invested"] = book.invested_value
    out["current"] = book.current_value
    out["pnl"] = book.pnl
    out["pnl_pct"] = book.pnl_pct
    out["nav_as_of"] = book.nav_as_of
    out["monthly_sip"] = book.monthly_sip_outflow
    out["active_sips"] = len(book.active_sips)
    out["paused_sips"] = len(book.paused_sips)
    out["external_count"] = sum(1 for h in book.holdings
                                if h.source != "COIN")
    out["unpriced"] = book.unpriced_count
    out["rows"] = [
        {
            "fund": s.fund,
            "scheme_code": s.scheme_code,
            "units": s.units,
            "value": s.current_value,
            "pnl": s.pnl,
            "pnl_pct": s.pnl_pct,
            "priced": s.nav > 0,
            "brokers": len(s.brokers),
        }
        for s in book.schemes[:6]
    ]
    return out


def us_book(*, live: bool) -> dict[str, Any]:
    """US positions — this IS the US portfolio (RSU lots + swing buys)."""
    out = {"positions": 0, "invested_usd": 0.0, "current_usd": 0.0,
           "pnl_usd": 0.0, "pnl_pct": 0.0, "realised_usd": 0.0, "closed": 0,
           "watchlist": 0, "rows": [], "live": False}
    try:
        from modes.swing.persistence import (
            get_watchlist, open_positions, realised_pnl_summary,
        )
    except Exception:
        return out

    try:
        positions = [p for p in open_positions()
                     if (p.exchange or "").upper() in US_EXCHANGES]
    except Exception:
        positions = []
    try:
        out["watchlist"] = len([w for w in get_watchlist()
                                if (w.exchange or "").upper() in US_EXCHANGES])
    except Exception:
        pass
    for ex in US_EXCHANGES:
        try:
            row = realised_pnl_summary(exchange=ex)
        except Exception:
            continue
        out["realised_usd"] += float(row.get("net_pnl") or 0)
        out["closed"] += int(row.get("count") or 0)

    quotes: dict[str, dict] = {}
    symbols = [p.symbol for p in positions if p.symbol]
    if symbols:
        try:
            if live:
                from modes.dashboard.us_analysis import get_us_live_quotes
                quotes = get_us_live_quotes(symbols)
            else:
                from modes.dashboard.us_analysis import cached_us_live_quotes
                quotes = cached_us_live_quotes(symbols)
        except Exception:
            quotes = {}

    rows, invested, current = _position_rows(positions, quotes)
    out["positions"] = len(positions)
    out["rows"] = rows[:6]
    out["invested_usd"] = invested
    out["current_usd"] = current
    out["pnl_usd"] = current - invested
    out["pnl_pct"] = ((current / invested - 1) * 100) if invested > 0 else 0.0
    out["live"] = any(r["live"] for r in rows)
    return out


# ── Intraday (realised only) ─────────────────────────────────────

def intraday_book() -> dict[str, Any]:
    out = {"net_pnl": 0.0, "gross_pnl": 0.0, "charges": 0.0, "trades": 0,
           "days": 0, "window": "", "best_day": None, "worst_day": None}
    try:
        from modes.dashboard.data_layer import current_fy_window, fetch_trades
        from modes.dashboard.metrics import headline_pnl
        d_from, d_to = current_fy_window()
        trades = fetch_trades(d_from, d_to, include_provisional=True)
        head = headline_pnl(trades)
        out.update({
            "net_pnl": head.net_pnl,
            "gross_pnl": head.gross_pnl,
            "charges": head.total_charges,
            "trades": head.trade_count,
            "days": head.trading_days,
            "window": f"{d_from} to {d_to}",
            "best_day": head.best_day,
            "worst_day": head.worst_day,
        })
    except Exception:
        pass
    return out


# ── Top-level ────────────────────────────────────────────────────

def build_summary(*, live: bool = False) -> dict[str, Any]:
    """One payload for the home page. `live=False` never calls a broker."""
    fx = fx_rate(live=live)
    rate = float(fx.get("rate") or 0)

    india = india_book(live=live)
    swing_in = swing_india_book(live=live)
    us = us_book(live=live)
    mf = mf_book(live=live)
    intraday = intraday_book()

    us_inr = us["current_usd"] * rate if rate > 0 else 0.0
    india_inr = india["current"]
    mf_inr = mf["current"]
    # Coin units are not in the demat equity list, so adding the fund
    # book here cannot double-count anything in `india_book`.
    net_worth = india_inr + mf_inr + us_inr

    india_cost = india["invested"]
    us_cost_inr = us["invested_usd"] * rate if rate > 0 else 0.0
    total_cost = india_cost + mf["invested"] + us_cost_inr
    total_pnl = net_worth - total_cost

    return {
        "generated_at": now_ist().isoformat(timespec="seconds"),
        "live": live,
        "auth": auth_state(),
        "market": market_state(),
        "fx": fx,
        "india": india,
        "swing_india": swing_in,
        "us": us,
        "mf": mf,
        "intraday": intraday,
        "totals": {
            "net_worth_inr": net_worth,
            "india_inr": india_inr,
            "mf_inr": mf_inr,
            "us_inr": us_inr,
            "invested_inr": total_cost,
            "unrealised_inr": total_pnl,
            "unrealised_pct": ((net_worth / total_cost - 1) * 100)
                              if total_cost > 0 else 0.0,
            "india_share_pct": (india_inr / net_worth * 100) if net_worth > 0 else 0.0,
            "mf_share_pct": (mf_inr / net_worth * 100) if net_worth > 0 else 0.0,
            "us_share_pct": (us_inr / net_worth * 100) if net_worth > 0 else 0.0,
            "realised_inr": (swing_in["realised_net"]
                             + intraday["net_pnl"]
                             + (us["realised_usd"] * rate if rate > 0 else 0.0)),
        },
    }


__all__ = ["auth_state", "build_summary", "market_state", "mf_book"]
