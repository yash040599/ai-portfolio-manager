"""Dashboard HTTP server (Roadmap addendum 2026-04-23).

Serves the interactive dashboard so date-range, granularity and
verified-only filters can be changed from the webpage itself
("the script is just an entry point, all configs achieved from the
webpage directly"). Stdlib only — no Flask, no WSGI runner.

Routes:
    GET /              -> Lightweight dashboard home page.
    GET /api/data      -> JSON payload for a given filter combination.
    GET /favicon.ico   -> 204 (silences the browser).

Every API response is freshly computed from the DB; nothing is
cached server-side. The DB is local SQLite on the same box, so the
read cost is negligible vs the dev convenience.
"""

from __future__ import annotations

import datetime
import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from config import Config, now_ist
from modes.dashboard.budget_history import average_budget
from modes.dashboard.data_layer import (
    current_fy_window,
    fetch_trades,
    pending_verification_dates,
    resolve_window,
    verified_dates,
)
from modes.dashboard.day_detail import day_detail
from modes.dashboard.metrics import (
    bucketed_pnl,
    cumulative_series,
    headline_pnl,
)
from modes.dashboard.verdict import LadderRung, verdict_for


_RENDER_CACHE_TTL_SECS = 45.0
_RENDER_CACHE_LOCK = threading.Lock()
_RENDER_CACHE: dict[str, tuple[float, bytes, str]] = {}


def _cached_response(key: str) -> tuple[bytes, str] | None:
    now = time.monotonic()
    with _RENDER_CACHE_LOCK:
        item = _RENDER_CACHE.get(key)
        if not item:
            return None
        ts, body, content_type = item
        if now - ts > _RENDER_CACHE_TTL_SECS:
            _RENDER_CACHE.pop(key, None)
            return None
        return body, content_type


def _store_cached_response(key: str, body: bytes, content_type: str) -> None:
    with _RENDER_CACHE_LOCK:
        _RENDER_CACHE[key] = (time.monotonic(), body, content_type)


def _invalidate_render_cache(prefix: str | None = None) -> None:
    with _RENDER_CACHE_LOCK:
        if prefix is None:
            _RENDER_CACHE.clear()
            return
        for key in list(_RENDER_CACHE):
            if key.startswith(prefix):
                _RENDER_CACHE.pop(key, None)


def _parse_int(val: str | None) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _parse_float(val: str | None, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ── Post-login redirect helpers ───────────────────────────────────

_ALLOWED_NEXT = {"/", "/login", "/portfolio", "/mf", "/swing", "/us", "/trading",
                 "/dryrun", "/tax"}


def _safe_next(raw: str | None) -> str:
    """Whitelist the post-login landing page.

    The value arrives from a form field, so echoing it straight into a
    `Location:` header would be an open redirect (and, with a stray
    CR/LF, a response-splitting bug).  Only known in-app paths are
    accepted; anything else falls back to /login.
    """
    candidate = (raw or "").strip()
    return candidate if candidate in _ALLOWED_NEXT else "/login"


def _login_target(next_path: str, ok: bool, err: str) -> str:
    """Build the 303 Location for a login attempt (query-escaped)."""
    flag = "ok=1" if ok else "err=" + quote(str(err or "Login failed")[:200],
                                           safe="")
    sep = "&" if "?" in next_path else "?"
    return f"{next_path}{sep}{flag}"


# ── Payload assembly (shared between / and /api/data) ─────────────

def _ladder_from_config() -> list[LadderRung]:
    raw = getattr(Config, "CAPITAL_LADDER", None) or []
    rungs: list[LadderRung] = []
    for row in raw:
        try:
            rungs.append(LadderRung(
                budget            = int(row["budget"]),
                win_rate_min      = float(row["win_rate_min"]),
                profit_factor_min = float(row["profit_factor_min"]),
                max_dd_pct        = float(row["max_dd_pct"]),
                weeks_required    = int(row["weeks_required"]),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    rungs.sort(key=lambda r: r.budget)
    return rungs


def compute_payload(
    *,
    date_from: str | None,
    date_to: str | None,
    granularity: str,
    include_provisional: bool,
) -> dict:
    """Resolve the window, fetch from DB, build the SPA JSON payload."""
    from modes.dashboard.render_html import build_payload

    if granularity not in ("daily", "weekly", "monthly"):
        granularity = "daily"

    d_from, d_to = resolve_window(date_from=date_from, date_to=date_to)

    trades   = fetch_trades(d_from, d_to, include_provisional=include_provisional)
    verified = verified_dates(d_from, d_to)
    pending  = pending_verification_dates(d_from, d_to)
    headline = headline_pnl(trades)
    ladder   = _ladder_from_config()

    fallback_budget = float(getattr(Config, "MAX_BUDGET_INR", 0) or 0)
    # Use real per-day budget recorded in trading reports; fall back
    # to Config when a day's report is missing.
    budget_avg = average_budget(verified or [d_from], fallback_budget)
    verdict = verdict_for(headline, ladder=ladder, budget=int(round(budget_avg)))

    bucketed   = bucketed_pnl(trades, granularity)
    cumulative = cumulative_series(trades)

    # Roadmap D13 / V2 #246 — strategy-version overlay (boundary days
    # where the bot's git SHA changed). Read-only; failure-silent.
    from modes.dashboard.strategy_versions import strategy_shas, boundaries
    overlay_enabled = bool(getattr(Config, "DASHBOARD_STRATEGY_VERSION_OVERLAY", True))
    sv_boundaries: list[dict] = []
    if overlay_enabled:
        try:
            sv_boundaries = boundaries(strategy_shas(d_from, d_to))
        except Exception:  # never let dashboard fall over for an overlay
            sv_boundaries = []

    return build_payload(
        date_from           = d_from,
        date_to             = d_to,
        granularity         = granularity,
        headline            = headline,
        verdict             = verdict,
        budget              = budget_avg,
        verified_day_count  = len(verified),
        pending_dates       = pending,
        bucketed            = bucketed,
        cumulative          = cumulative,
        include_provisional = include_provisional,
        strategy_boundaries = sv_boundaries,
        strategy_overlay_enabled = overlay_enabled,
    )


# ── Handler ───────────────────────────────────────────────────────

class _DashboardHandler(BaseHTTPRequestHandler):
    """Serves the SPA shell + JSON. Only GET; localhost-bound only."""

    # Quieter logs — default access log spams every /api/data poll.
    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        sys.stderr.write("[dashboard] " + (fmt % args) + "\n")

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        url = urlparse(self.path)
        if url.path == "/favicon.ico":
            self.send_response(204); self.end_headers(); return

        try:
            if url.path == "/":
                self._serve_home(parse_qs(url.query))
            elif url.path == "/home" or url.path == "/home/":
                self._serve_home(parse_qs(url.query))
            elif url.path == "/portfolio" or url.path == "/portfolio/":
                self._serve_portfolio()
            elif url.path.startswith("/portfolio/"):
                # /portfolio/<symbol> drill-down (D26 + D29).
                symbol = url.path[len("/portfolio/"):].strip("/")
                self._serve_portfolio_drilldown(symbol)
            elif url.path == "/mf" or url.path == "/mf/":
                self._serve_mf()
            elif url.path == "/api/mf/sections":
                self._serve_mf_sections(parse_qs(url.query))
            elif url.path == "/api/mf/search":
                self._serve_mf_search(parse_qs(url.query))
            elif url.path == "/api/mf/nav_history":
                self._serve_mf_nav_history(parse_qs(url.query))
            elif url.path == "/trading" or url.path == "/trading/":
                self._serve_shell()
            elif url.path == "/login" or url.path == "/login/":
                self._serve_login()
            elif url.path == "/api/home/summary":
                self._serve_home_summary(parse_qs(url.query))
            elif url.path == "/api/run_status":
                self._serve_run_status()
            elif url.path == "/api/portfolio/sections":
                self._serve_portfolio_sections()
            elif url.path == "/api/stock_chart":
                self._serve_stock_chart(parse_qs(url.query))
            elif url.path == "/theory" or url.path == "/theory/":
                # Redirect to default theory page so the dropdown reflects state.
                from modes.dashboard.theory_page import DEFAULT_PAGE
                self.send_response(302)
                self.send_header("Location", f"/theory/{DEFAULT_PAGE}")
                self.end_headers()
            elif url.path.startswith("/theory/"):
                slug = url.path[len("/theory/"):].strip("/")
                self._serve_theory(slug)
            elif url.path == "/tax" or url.path == "/tax/":
                self._serve_tax(parse_qs(url.query))
            elif url.path == "/api/tax":
                self._serve_tax_api(parse_qs(url.query))
            elif url.path == "/swing" or url.path == "/swing/":
                self._serve_swing()
            elif url.path == "/us" or url.path == "/us/":
                self._serve_us()
            elif url.path == "/api/us/sections":
                self._serve_us_sections()
            elif url.path == "/api/us/run":
                self._serve_us_run(parse_qs(url.query))
            elif url.path == "/api/us/analyse":
                self._serve_us_analyse(parse_qs(url.query))
            elif url.path == "/api/us/changes_since":
                self._serve_us_changes_since()
            elif url.path == "/api/us/sectors":
                self._serve_us_sectors()
            elif url.path == "/api/us/compare":
                self._serve_us_compare(parse_qs(url.query))
            elif url.path.startswith("/us/"):
                sym = url.path[len("/us/"):].strip("/")
                self._serve_us_detail(sym)
            elif url.path.startswith("/swing/") and not url.path.startswith("/swing/api"):
                # /swing/<symbol> detail page
                sym = url.path[len("/swing/"):].strip("/")
                self._serve_swing_detail(sym)
            elif url.path == "/api/swing/data":
                self._serve_swing_data()
            elif url.path == "/api/swing/run_status":
                self._serve_swing_run_status()
            elif url.path == "/api/swing/compare":
                self._serve_swing_compare(parse_qs(url.query))
            elif url.path == "/api/swing/sectors":
                self._serve_swing_sectors()
            elif url.path == "/api/swing/changes_since":
                self._serve_swing_changes_since()
            elif url.path == "/api/live_prices":
                self._serve_live_prices(parse_qs(url.query))
            elif url.path == "/api/us/live_prices":
                self._serve_us_live_prices(parse_qs(url.query))
            elif url.path == "/api/fx/usdinr":
                self._serve_fx_usdinr(parse_qs(url.query))
            elif url.path == "/api/errors":
                self._serve_errors(parse_qs(url.query))
            elif url.path == "/api/data":
                self._serve_api(parse_qs(url.query))
            elif url.path == "/api/day":
                self._serve_day(parse_qs(url.query))
            elif url.path == "/api/ai/status":
                self._serve_ai_status()
            elif url.path == "/dryrun" or url.path == "/dryrun/":
                self._serve_dryrun()
            else:
                self.send_error(404, "Not found")
        except (ConnectionAbortedError, ConnectionResetError,
                BrokenPipeError):
            # Client navigated away mid-response (browser refresh,
            # tab close).  Nothing to recover, nothing to log loudly.
            pass
        except Exception as exc:  # noqa: BLE001 — surface to browser
            sys.stderr.write(f"[dashboard] ERROR: {exc!r}\n")
            try:
                self.send_error(500, f"Server error: {exc}")
            except (ConnectionAbortedError, ConnectionResetError,
                    BrokenPipeError):
                pass

    def _serve_home(self, qs: dict[str, list[str]] | None = None) -> None:
        from modes.dashboard.home_page import render_home_page
        qs = qs or {}
        login_ok = bool(qs.get("ok"))
        login_err = (qs.get("err") or [""])[0]
        body = render_home_page(login_ok=login_ok, login_err=login_err)
        self._write_html(body.encode("utf-8"))

    def _serve_home_summary(self, qs: dict[str, list[str]]) -> None:
        """GET /api/home/summary[?live=1] — aggregated book summary for
        the home page.  `live=1` is the only path that touches Zerodha /
        yfinance; both are already rate-limited in their own modules.
        Never 500s: a failure returns the error string so the page can
        show a banner and keep the last snapshot on screen."""
        from modes.dashboard.home_summary import build_summary
        live = (qs.get("live") or ["0"])[0] in ("1", "true", "yes")
        try:
            payload = build_summary(live=live)
        except Exception as exc:  # noqa: BLE001 — degrade, don't blank
            sys.stderr.write(f"[dashboard] home summary failed: {exc!r}\n")
            self._write_json(
                json.dumps({"error": str(exc)[:300]}).encode("utf-8"),
                status=503,
            )
            return
        self._write_json(json.dumps(payload, default=str).encode("utf-8"))

    def _serve_dryrun(self) -> None:
        from modes.dashboard.dryrun_page import render_dryrun_page
        self._serve_cached_html("page:dryrun", render_dryrun_page)

    def _write_html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _write_json(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_cached_html(self, key: str, render) -> None:
        cached = _cached_response(key)
        if cached:
            self._write_html(cached[0])
            return
        body = render().encode("utf-8")
        _store_cached_response(key, body, "text/html; charset=utf-8")
        self._write_html(body)

    def _serve_cached_json(self, key: str, render) -> None:
        cached = _cached_response(key)
        if cached:
            self._write_json(cached[0])
            return
        body = render().encode("utf-8")
        _store_cached_response(key, body, "application/json; charset=utf-8")
        self._write_json(body)

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        _invalidate_render_cache()
        try:
            if url.path == "/api/analyse_run":
                self._serve_analyse_run(parse_qs(url.query))
            elif url.path == "/api/login_submit":
                self._serve_login_submit()
            elif url.path == "/api/login_assisted":
                self._serve_login_assisted()
            elif url.path == "/api/swing/run":
                self._serve_swing_run(parse_qs(url.query))
            elif url.path.startswith("/api/swing/actions/") and url.path.endswith("/confirm"):
                self._serve_swing_action_confirm(url.path)
            elif url.path.startswith("/api/swing/actions/") and url.path.endswith("/skip"):
                self._serve_swing_action_skip(url.path)
            elif url.path.startswith("/api/swing/positions/") and url.path.endswith("/edit"):
                self._serve_swing_position_edit(url.path)
            elif url.path.startswith("/api/swing/positions/") and url.path.endswith("/exit"):
                self._serve_swing_position_exit(url.path)
            elif url.path == "/api/swing/positions/add":
                self._serve_swing_position_add()
            elif url.path == "/api/mf/external/add":
                self._serve_mf_external_add()
            elif url.path.startswith("/api/mf/external/") and url.path.endswith("/edit"):
                self._serve_mf_external_edit(url.path)
            elif url.path.startswith("/api/mf/external/") and url.path.endswith("/remove"):
                self._serve_mf_external_remove(url.path)
            elif url.path == "/api/us/positions/add":
                self._serve_swing_position_add(
                    default_exchange="NASDAQ",
                    source="DASHBOARD_US_MANUAL_ADD",
                    notes="Manual Add+ from US page",
                )
            elif url.path.startswith("/api/us/positions/") and url.path.endswith("/edit"):
                self._serve_swing_position_edit(url.path)
            elif url.path.startswith("/api/us/positions/") and url.path.endswith("/exit"):
                self._serve_swing_position_exit(url.path, exchange_filter=None)
            elif url.path == "/api/us/watchlist/add":
                self._serve_us_watchlist_add()
            elif url.path.startswith("/api/us/watchlist/") and url.path.endswith("/promote"):
                self._serve_swing_watchlist_promote(url.path)
            elif url.path.startswith("/api/us/watchlist/") and url.path.endswith("/remove"):
                self._serve_swing_watchlist_remove(url.path)
            elif url.path == "/api/swing/watchlist/add":
                self._serve_swing_watchlist_add()
            elif url.path.startswith("/api/swing/watchlist/") and url.path.endswith("/promote"):
                self._serve_swing_watchlist_promote(url.path)
            elif url.path.startswith("/api/swing/watchlist/") and url.path.endswith("/remove"):
                self._serve_swing_watchlist_remove(url.path)
            elif url.path.startswith("/api/swing/ai_analyse/"):
                self._serve_swing_ai_analyse_single(url.path)
            elif url.path == "/api/swing/analyse_one":
                self._serve_swing_analyse_one(parse_qs(url.query))
            elif url.path == "/api/ai/switch":
                self._serve_ai_switch()
            elif url.path == "/api/chat/prompt":
                self._serve_chat_prompt()
            else:
                self.send_error(404, "Not found")
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[dashboard] POST ERROR: {exc!r}\n")
            self.send_error(500, f"Server error: {exc}")

    # — Endpoints —

    def _serve_portfolio(self) -> None:
        from modes.dashboard.portfolio_page import render_portfolio_page
        self._serve_cached_html("page:portfolio", render_portfolio_page)

    def _serve_portfolio_sections(self) -> None:
        from modes.dashboard.portfolio_page import render_portfolio_sections_json
        self._serve_cached_json(
            "json:portfolio:sections",
            render_portfolio_sections_json,
        )

    def _serve_portfolio_drilldown(self, symbol: str) -> None:
        from modes.dashboard.portfolio_page import render_stock_drilldown
        body = render_stock_drilldown(symbol).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ── Mutual funds ────────────────────────────────────────────

    def _serve_mf(self) -> None:
        from modes.dashboard.mf_page import render_mf_page
        self._serve_cached_html("page:mf", render_mf_page)

    def _serve_mf_sections(self, qs: dict[str, list[str]]) -> None:
        """GET /api/mf/sections[?live=1].

        `live=1` is the only path that calls Coin; the cached path is
        what the page renders on first paint, so a missing Zerodha
        session never blocks the book.
        """
        from modes.dashboard.mf_page import render_mf_sections_json
        live = (qs.get("live") or ["0"])[0] in ("1", "true", "yes")
        if live:
            try:
                body = render_mf_sections_json(live=True).encode("utf-8")
            except Exception as exc:  # noqa: BLE001 — degrade to cache
                sys.stderr.write(f"[dashboard] mf live sections failed: {exc!r}\n")
                self._write_json(
                    json.dumps({"ok": False, "error": str(exc)[:300]}).encode("utf-8"),
                    status=503,
                )
                return
            # The sync rewrote the stored book; drop renders built on the old one.
            _invalidate_render_cache("json:mf")
            _invalidate_render_cache("page:mf")
            self._write_json(body)
            return
        self._serve_cached_json(
            "json:mf:sections",
            lambda: render_mf_sections_json(live=False),
        )

    def _serve_mf_search(self, qs: dict[str, list[str]]) -> None:
        from modes.dashboard.mf_page import render_mf_search_json
        query = (qs.get("q") or [""])[0][:120]
        self._write_json(render_mf_search_json(query).encode("utf-8"))

    def _serve_mf_nav_history(self, qs: dict[str, list[str]]) -> None:
        from modes.dashboard.mf_page import render_mf_nav_history_json
        scheme = (qs.get("scheme") or [""])[0][:32]
        days = _parse_int((qs.get("days") or [""])[0]) or 365
        body = render_mf_nav_history_json(scheme, days=max(30, min(days, 3650)))
        self._write_json(body.encode("utf-8"))

    def _mf_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        # Manual-entry forms are tiny; refuse anything that isn't.
        if length > 64 * 1024:
            raise ValueError("Request body too large")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    def _serve_mf_external_add(self) -> None:
        """POST /api/mf/external/add — track a fund held outside Coin."""
        from modes.mf.persistence import add_external_holding
        try:
            data = self._mf_json_body()
            holding_id = add_external_holding(
                scheme_code=str(data.get("scheme_code") or ""),
                fund=str(data.get("fund") or "")[:200],
                units=float(data.get("units") or 0),
                avg_nav=float(data.get("avg_nav") or 0),
                broker=str(data.get("broker") or "Other")[:80],
                folio=str(data.get("folio") or "")[:60],
                notes=str(data.get("notes") or "")[:300],
            )
        except (TypeError, ValueError) as exc:
            self._write_json(
                json.dumps({"ok": False, "error": str(exc)[:200]}).encode("utf-8"),
                status=400,
            )
            return
        _invalidate_render_cache("json:mf")
        self._write_json(
            json.dumps({"ok": True, "holding_id": holding_id}).encode("utf-8"))
    def _serve_mf_external_edit(self, path: str) -> None:
        """POST /api/mf/external/<id>/edit — correct units or average NAV."""
        from modes.mf.persistence import edit_external_holding
        parts = path.strip("/").split("/")
        try:
            holding_id = int(parts[3])
            data = self._mf_json_body()
            ok = edit_external_holding(
                holding_id=holding_id,
                units=float(data.get("units") or 0),
                avg_nav=float(data.get("avg_nav") or 0),
                broker=(str(data["broker"])[:80] if "broker" in data else None),
                folio=(str(data["folio"])[:60] if "folio" in data else None),
                notes=(str(data["notes"])[:300] if "notes" in data else None),
            )
        except (IndexError, TypeError, ValueError) as exc:
            self._write_json(
                json.dumps({"ok": False, "error": str(exc)[:200]}).encode("utf-8"),
                status=400,
            )
            return
        _invalidate_render_cache("json:mf")
        self._write_json(json.dumps({"ok": ok}).encode("utf-8"),
                         status=200 if ok else 404)

    def _serve_mf_external_remove(self, path: str) -> None:
        """POST /api/mf/external/<id>/remove — stop tracking a fund."""
        from modes.mf.persistence import remove_external_holding
        parts = path.strip("/").split("/")
        try:
            holding_id = int(parts[3])
        except (IndexError, ValueError):
            self._write_json(
                json.dumps({"ok": False, "error": "Invalid holding id"}).encode("utf-8"),
                status=400,
            )
            return
        ok = remove_external_holding(holding_id)
        _invalidate_render_cache("json:mf")
        self._write_json(json.dumps({"ok": ok}).encode("utf-8"),
                         status=200 if ok else 404)

    def _serve_login(self) -> None:
        from modes.dashboard.portfolio_page import render_login_page
        qs = parse_qs(urlparse(self.path).query)
        ok = bool(qs.get("ok"))
        err = (qs.get("err") or [""])[0]
        body = render_login_page(ok=ok, err=err).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_run_status(self) -> None:
        from modes.dashboard.portfolio_page import render_status_json
        raw = render_status_json()
        try:
            status = json.loads(raw).get("status")
            if status in ("DONE", "FAILED"):
                _invalidate_render_cache("page:portfolio")
                _invalidate_render_cache("json:portfolio")
        except Exception:
            pass
        body = raw.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_stock_chart(self, qs: dict[str, list[str]]) -> None:
        from modes.dashboard.portfolio_page import render_stock_chart_json
        symbol = (qs.get("symbol") or [""])[0]
        try:
            lookback = int((qs.get("lookback") or ["365"])[0])
        except (TypeError, ValueError):
            lookback = 365
        body = render_stock_chart_json(symbol, lookback_days=lookback).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_analyse_run(self, qs: dict[str, list[str]]) -> None:
        from modes.dashboard.portfolio_actions import submit_run
        mode = (qs.get("mode") or ["NOAI"])[0].upper()
        if mode not in ("NOAI", "AI"):
            mode = "NOAI"
        scope = (qs.get("scope") or ["all"])[0]
        job = submit_run(mode=mode, scope=scope)
        body = json.dumps({
            "job_id": job.job_id,
            "status": job.status,
            "mode":   job.mode,
            "scope":  job.scope,
        }).encode("utf-8")
        self.send_response(202)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # — Swing endpoints (D31) —

    def _serve_swing(self) -> None:
        from modes.dashboard.swing_page import render_swing_page
        self._serve_cached_html("page:swing", render_swing_page)

    def _serve_us(self) -> None:
        from modes.dashboard.us_page import render_us_page
        self._serve_cached_html("page:us", render_us_page)

    def _serve_us_sections(self) -> None:
        from modes.dashboard.us_page import render_us_sections_json
        self._serve_cached_json("json:us:sections", render_us_sections_json)

    def _serve_us_detail(self, symbol: str) -> None:
        from modes.dashboard.us_page import render_us_detail
        body = render_us_detail(symbol).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_us_run(self, qs: dict[str, list[str]]) -> None:
        mode = (qs.get("mode") or ["NOAI"])[0].upper()
        ticket = _parse_float((qs.get("ticket") or [""])[0], 0.0)
        universe = (qs.get("universe") or [""])[0].strip().upper() or None
        limit = _parse_int((qs.get("limit") or [None])[0]) or 0
        try:
            from modes.dashboard.us_analysis import analyse_us_universe
            payload = analyse_us_universe(
                mode=mode,
                ticket_amount=ticket,
                universe=universe,
                limit=limit,
            )
            _invalidate_render_cache("page:us")
            _invalidate_render_cache("json:us")
            status = 200
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)[:300]}
            status = 500
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_us_analyse(self, qs: dict[str, list[str]]) -> None:
        symbol = (qs.get("symbol") or [""])[0].strip().upper()
        ticket = _parse_float((qs.get("ticket") or [""])[0], 0.0)
        use_ai = (qs.get("ai") or ["0"])[0] in ("1", "true", "True", "yes")
        force_refresh = (qs.get("force") or ["1"])[0] in (
            "1", "true", "True", "yes")
        if not symbol:
            body = json.dumps({"ok": False, "error": "missing symbol"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            from modes.dashboard.us_analysis import analyse_us_symbol
            # Manual single-stock analyse defaults to a fresh fetch —
            # the user typed the ticker expecting current data, and
            # the lru_cache may otherwise pin a short response from a
            # throttled batch (origin: 2026-05-19 ORCL report).  Compare
            # can pass force=0 to keep side-by-side checks responsive.
            payload = analyse_us_symbol(
                symbol,
                ticket_amount=ticket,
                use_ai=use_ai,
                force_refresh=force_refresh,
            )
            status = 200
            # Persist the single-stock AI overlay so the US detail
            # page is sticky across navigation (origin 2026-06-02:
            # ORCL AI section went blank on revisit).
            if use_ai and isinstance(payload, dict):
                overlay = payload.get("ai_overlay")
                if isinstance(overlay, dict) and not overlay.get("error"):
                    try:
                        from modes.dashboard.us_analysis import save_us_ai_overlay
                        save_us_ai_overlay(symbol, overlay)
                    except Exception:
                        pass
        except Exception as exc:
            payload = {"ok": False, "symbol": symbol, "error": str(exc)[:300]}
            status = 500
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_detail(self, symbol: str) -> None:
        from modes.dashboard.swing_page import render_swing_detail
        body = render_swing_detail(symbol).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_data(self) -> None:
        from modes.dashboard.swing_page import render_swing_data_json
        body = render_swing_data_json().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_run_status(self) -> None:
        from modes.dashboard.swing_page import render_swing_status_json
        raw = render_swing_status_json()
        try:
            status = json.loads(raw).get("status")
            if status in ("DONE", "FAILED"):
                _invalidate_render_cache("page:swing")
        except Exception:
            pass
        body = raw.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_compare(self, qs: dict[str, list[str]]) -> None:
        """GET /api/swing/compare?symbols=A,B,C,D
           GET /api/swing/compare?sector=BANKING

        Compare up to 4 NSE symbols side-by-side. Either an explicit
        comma-separated list, or auto-pick the top 4 in a sector
        (`?sector=BANK`). Returns the matrix as JSON; the dashboard
        renders the table client-side. Roadmap S45.

        Auth-shaped Zerodha errors surface via the usual
        `core.error_sink` toast path because `scan_one()` calls
        `zerodha.login(interactive=False)` which raises (post-S42)
        on a stale token.
        """
        from modes.swing.compare import (
            MAX_COMPARE_STOCKS, normalise_sector, top_n_in_sector,
            compare_symbols,
        )
        from modes.swing.scanner import SwingScanner
        from core.zerodha_client import ZerodhaClient
        from core.logger import Logger
        from core.error_sink import record_external_error

        symbols_param = (qs.get("symbols") or [""])[0].strip()
        sector_param = (qs.get("sector") or [""])[0].strip()

        chosen_sector = ""
        if sector_param:
            chosen_sector = normalise_sector(sector_param)
            symbols = top_n_in_sector(chosen_sector, n=MAX_COMPARE_STOCKS)
        else:
            symbols = [s for s in symbols_param.split(",") if s.strip()]

        if not symbols:
            err = json.dumps({
                "ok": False,
                "error": ("No symbols. Pass ?symbols=A,B,C,D or ?sector=NAME "
                          "(e.g. BANKING / IT / PHARMA / AUTO / FMCG)."),
            }).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        log = Logger("SwingCompare")
        try:
            zerodha = ZerodhaClient(Config, log)
            zerodha.login(interactive=False)
        except Exception as exc:
            record_external_error("zerodha", exc, log=log)
            err = json.dumps({
                "ok": False,
                "error": f"Zerodha login failed: {exc}",
            }).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        scanner = SwingScanner(Config, zerodha, log)

        def _scan_one(sym: str):
            ticket = float(getattr(Config, "SWING_TICKET_AMOUNT", 20_000.0))
            return scanner.scan_one(sym, swing_capital=ticket)

        result = compare_symbols(symbols, scan_one=_scan_one,
                                 sector=chosen_sector)

        # Serialise the dataclasses for the JSON wire.
        rows_json = [{
            "label": r.label,
            "values": r.values,
            "winner_idx": r.winner_idx,
            "winners_idx": r.winners_idx,
            "direction": r.direction,
            "explain": r.explain,
        } for r in result.rows]
        body = json.dumps({
            "ok": True,
            "symbols": result.symbols,
            "sector": result.sector,
            "rows": rows_json,
            "winner_overall": result.winner_overall(),
            "win_counts": [result.win_count(i)
                           for i in range(len(result.symbols))],
            "notes": result.notes,
        }, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_sectors(self) -> None:
        """GET /api/swing/sectors — list all known SECTOR_MAP keys.
        Used by the dashboard's compare-sector dropdown."""
        from modes.swing.compare import list_known_sectors
        body = json.dumps({"sectors": list_known_sectors()}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_changes_since(self) -> None:
        """GET /api/swing/changes_since — diff the latest full-scan
        run vs the most recent prior trading-day full-scan run.

        Returns the dict shape from
        `persistence.diff_latest_vs_prior_day` plus a `prior_run_age`
        plain-English label for the UI ("yesterday's scan",
        "last Friday's scan", "scan from 2026-05-09").

        Same gates as the recommendations table: snapshots and
        SEARCH_BOX runs are skipped on both ends. When the latest
        run is identical to yesterday's, the helper walks further
        back so the user always gets a meaningful "last big change"
        report.
        """
        from modes.swing.persistence import diff_latest_vs_prior_day
        diff = diff_latest_vs_prior_day() or {}
        # Compute the human-friendly age label server-side so the
        # JS doesn't have to re-parse dates with Date() (which is
        # unreliable with bare YYYY-MM-DD strings across browsers).
        age_label = ""
        try:
            import datetime as _dt
            cur_d = diff.get("current_run_date") or ""
            prior_d = diff.get("prior_run_date") or ""
            prior_fin = diff.get("prior_run_finished_at") or ""
            if cur_d and prior_d:
                cur = _dt.date.fromisoformat(cur_d)
                prior = _dt.date.fromisoformat(prior_d)
                delta_days = (cur - prior).days
                # Include the scan time for clarity
                scan_time = prior_fin[11:16] if len(prior_fin) > 15 else ""
                time_suffix = f" at {scan_time}" if scan_time else ""
                if delta_days == 0:
                    age_label = f"earlier today ({prior_d}{time_suffix})"
                elif delta_days == 1:
                    age_label = f"yesterday ({prior_d}{time_suffix})"
                elif 1 < delta_days <= 4:
                    age_label = (f"{prior.strftime('%A').lower()}'s "
                                 f"scan ({prior_d}{time_suffix})")
                else:
                    age_label = f"scan from {prior_d}{time_suffix}"
        except Exception:
            age_label = ""
        diff["prior_run_age_label"] = age_label
        body = json.dumps(diff, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_us_sectors(self) -> None:
        """GET /api/us/sectors — list US sector keys for the
        compare-stocks dropdown on /us. Mirrors
        `/api/swing/sectors`. Origin: 2026-05-19 user asked for
        the /us compare card to look and behave exactly like
        /swing — including the dynamic-sector dropdown."""
        from modes.dashboard.us_compare import list_known_sectors
        body = json.dumps({"sectors": list_known_sectors()}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _serve_us_compare(self, qs: dict[str, list[str]]) -> None:
        """GET /api/us/compare?symbols=A,B,C,D
           GET /api/us/compare?sector=MEGACAP_TECH

        Side-by-side comparison of up to 4 US tickers. Returns the
        SAME payload shape as `/api/swing/compare` (rows,
        winner_idx, winners_idx, win_counts, winner_overall,
        sector, notes) so the existing JS renderer
        (`_renderCompareResult`) works without per-product
        branches. Roadmap: 2026-05-19 compare-card alignment.
        """
        from modes.dashboard.us_compare import (
            MAX_COMPARE_STOCKS, normalise_sector, top_n_in_sector,
            compare_symbols,
        )
        from modes.dashboard.us_analysis import analyse_us_symbol

        symbols_param = (qs.get("symbols") or [""])[0].strip()
        sector_param = (qs.get("sector") or [""])[0].strip()

        chosen_sector = ""
        if sector_param:
            chosen_sector = normalise_sector(sector_param)
            symbols = top_n_in_sector(chosen_sector, n=MAX_COMPARE_STOCKS)
        else:
            symbols = [s for s in symbols_param.split(",") if s.strip()]

        if not symbols:
            err = json.dumps({
                "ok": False,
                "error": ("No symbols. Pass ?symbols=A,B,C,D or "
                          "?sector=NAME (e.g. MEGACAP_TECH / "
                          "SEMICONDUCTORS / BANKS)."),
            }).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        def _one(sym: str) -> dict:
            # Use the existing single-stock analyser. AI overlay
            # is intentionally off — compare table is a structural
            # head-to-head, not a Claude call.
            return analyse_us_symbol(sym, use_ai=False)

        try:
            result = compare_symbols(
                symbols, analyse_one=_one, sector=chosen_sector,
            )
        except Exception as exc:  # noqa: BLE001
            err = json.dumps({
                "ok": False,
                "error": f"Compare failed: {exc}",
            }).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        rows_json = [{
            "label": r.label,
            "values": r.values,
            "winner_idx": r.winner_idx,
            "winners_idx": r.winners_idx,
            "direction": r.direction,
            "explain": r.explain,
        } for r in result.rows]
        body = json.dumps({
            "ok": True,
            "symbols": result.symbols,
            "sector": result.sector,
            "rows": rows_json,
            "winner_overall": result.winner_overall(),
            "win_counts": [result.win_count(i)
                           for i in range(len(result.symbols))],
            "notes": result.notes,
        }, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_us_changes_since(self) -> None:
        """GET /api/us/changes_since — diff the latest /us scan
        against the immediately prior scan. Same payload shape as
        `/api/swing/changes_since` so the dashboard JS renderer is
        shared (positions/scores/setup are equivalent across the
        two products). Origin: 2026-05-19 user asked for the same
        "what changed" card on /us that /swing already has."""
        from modes.dashboard.us_analysis import us_scan_diff
        diff = us_scan_diff() or {}
        # Build the human-friendly "yesterday's scan" label client-
        # side mirror of the swing implementation.
        age_label = ""
        try:
            import datetime as _dt
            cur_d = diff.get("current_run_date") or ""
            prior_d = diff.get("prior_run_date") or ""
            prior_fin = diff.get("prior_run_finished_at") or ""
            if cur_d and prior_d:
                cur = _dt.date.fromisoformat(cur_d)
                prior = _dt.date.fromisoformat(prior_d)
                delta_days = (cur - prior).days
                scan_time = prior_fin[11:16] if len(prior_fin) > 15 else ""
                time_suffix = f" at {scan_time}" if scan_time else ""
                if delta_days == 0:
                    age_label = f"earlier today ({prior_d}{time_suffix})"
                elif delta_days == 1:
                    age_label = f"yesterday ({prior_d}{time_suffix})"
                elif 1 < delta_days <= 4:
                    age_label = (f"{prior.strftime('%A').lower()}'s "
                                 f"scan ({prior_d}{time_suffix})")
                else:
                    age_label = f"scan from {prior_d}{time_suffix}"
        except Exception:
            age_label = ""
        diff["prior_run_age_label"] = age_label
        body = json.dumps(diff, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_live_prices(self, qs: dict[str, list[str]]) -> None:
        """GET /api/live_prices?symbols=A,B,C — returns
        `{symbol: {price, change_pct, as_of}}` for the requested
        NSE symbols. Backed by `modes/dashboard/live_quotes.py`
        which is already rate-limited (5 s minimum between Zerodha
        polls) and falls back to cached values when the broker is
        unreachable. Origin: 2026-05-14 user feedback "I don't see
        the live prices being refreshed on /portfolio and /swing".
        The shared JS poller on both pages calls this endpoint
        every 5 s and updates only the price-bearing cells.
        """
        from modes.dashboard.live_quotes import get_live_quotes
        raw = (qs.get("symbols") or [""])[0]
        # Comma-separated; strip + de-dup; cap at 100 to keep the
        # broker call sane on a wide /portfolio page.
        symbols = []
        seen: set[str] = set()
        for s in raw.split(","):
            sym = s.strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                symbols.append(sym)
            if len(symbols) >= 100:
                break

        quotes = get_live_quotes(symbols) if symbols else {}
        # Ensure every requested symbol has a key, even when the
        # broker returned nothing — the JS poller can then keep its
        # DOM cells unchanged for missing symbols rather than
        # erasing them.
        out = {sym: quotes.get(sym, {}) for sym in symbols}
        body = json.dumps({
            "quotes": out,
            "ts": now_ist().isoformat(),
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_us_live_prices(self, qs: dict[str, list[str]]) -> None:
        """GET /api/us/live_prices?symbols=A,B,C - returns
        `{symbol: {price, change_pct, as_of}}` for US tickers via
        the throttled yfinance poller in `us_analysis`. The /us
        page polls this every 15 s so price/P&L cells update
        without a full reload."""
        from modes.dashboard.us_analysis import get_us_live_quotes
        raw = (qs.get("symbols") or [""])[0]
        symbols: list[str] = []
        seen: set[str] = set()
        for s in raw.split(","):
            sym = s.strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                symbols.append(sym)
            if len(symbols) >= 100:
                break
        quotes = get_us_live_quotes(symbols) if symbols else {}
        out = {sym: quotes.get(sym, {}) for sym in symbols}
        body = json.dumps({
            "quotes": out,
            "ts": now_ist().isoformat(),
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_fx_usdinr(self, qs: dict[str, list[str]]) -> None:
        """GET /api/fx/usdinr - returns the cached USD/INR rate.

        Refreshes upstream at most once every five minutes (handled
        in `get_usd_inr_rate`); the dashboard polls this every five
        minutes to keep the currency toggle accurate."""
        from modes.dashboard.us_analysis import get_usd_inr_rate
        force = (qs.get("refresh") or ["0"])[0] in ("1", "true", "True")
        fx = get_usd_inr_rate(force_refresh=force)
        body = json.dumps(fx, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_errors(self, qs: dict[str, list[str]]) -> None:
        """GET /api/errors?since=<id>[&max_age_secs=N][&init=1]
        — returns external-API errors recorded in `core.error_sink`
        with id > since (and optionally newer than `max_age_secs`).

        Used by the top-right toast widget on every page; polled
        every 5 s. The widget tracks its own `lastSeenId` in
        `localStorage` (added 2026-05-14) so reloading or navigating
        between pages doesn't replay every prior toast.

        `?init=1` (added 2026-05-14): returns just the current max
        id with an empty errors list. The JS poller calls this on
        first-ever load (no localStorage value) so it can bookmark
        the high-water mark without surfacing pre-existing errors
        as toasts. Origin: laptop-sleep-resume on 2026-05-14
        produced ~20 stale Zerodha network errors that re-spawned
        on every page navigation because the JS-only `window` var
        died with each navigation.
        """
        from core.error_sink import get_errors_since, current_max_id
        since = 0
        try:
            since = int((qs.get("since") or ["0"])[0])
        except (TypeError, ValueError):
            since = 0
        is_init = (qs.get("init") or ["0"])[0] in ("1", "true", "yes")
        max_age_secs: float | None = None
        try:
            raw_age = (qs.get("max_age_secs") or [""])[0]
            if raw_age:
                max_age_secs = float(raw_age)
        except (TypeError, ValueError):
            max_age_secs = None
        if is_init:
            errors: list = []
            max_id = current_max_id()
        else:
            errors = get_errors_since(since, max_age_secs=max_age_secs)
            max_id = current_max_id()
        body = json.dumps({
            "errors": errors,
            "max_id": max_id,
            "ts": now_ist().isoformat(),
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_run(self, qs: dict[str, list[str]]) -> None:
        from modes.dashboard.swing_actions import submit_swing_run
        mode = (qs.get("mode") or ["NOAI"])[0].upper()
        if mode not in ("NOAI", "AI"):
            mode = "NOAI"
        capital = _parse_float((qs.get("capital") or ["0"])[0], 0.0)
        if capital <= 0:
            capital = float(getattr(Config, "SWING_TICKET_AMOUNT", 20_000.0))
        job = submit_swing_run(mode=mode, trigger_source="DASHBOARD_BUTTON",
                               swing_capital=capital)
        body = json.dumps({
            "job_id": job.job_id,
            "status": job.status,
            "mode":   job.mode,
        }).encode("utf-8")
        self.send_response(202)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_action_confirm(self, path: str) -> None:
        # /api/swing/actions/<id>/confirm
        parts = path.strip("/").split("/")
        try:
            action_id = int(parts[3])
        except (IndexError, ValueError):
            self.send_error(400, "Invalid action ID")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(raw)

        # Validate inputs hard. The dashboard JS prompts string-typed
        # values that can come back as NaN / 0 / negative if the user
        # fat-fingers. Without this guard a confirmed entry would
        # write `entry_price=0` or `executed_qty=0` into
        # `swing_positions`, breaking every downstream P&L / live-MTM
        # calc. Origin: 2026-05-14 SDE review of the confirm/exit
        # data flow.
        try:
            qty = int(data.get("qty", 0))
            price = float(data.get("price", 0))
            stop = float(data.get("stop", 0))
        except (TypeError, ValueError):
            err = json.dumps({"ok": False,
                              "error": "qty/price/stop must be numeric"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return
        # math.isnan-safe check: NaN comparisons are always False so
        # `not (qty > 0)` catches NaN, 0, and negative qty in one go.
        if not (qty > 0) or not (price > 0):
            err = json.dumps({
                "ok": False,
                "error": "qty and price must be positive numbers",
            }).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        from modes.swing.persistence import confirm_action
        result = confirm_action(
            action_id=action_id,
            executed_qty=qty,
            executed_price=price,
            source="DASHBOARD",
            confirmed_stop=stop,
        )
        body = json.dumps({"ok": result is not None}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_action_skip(self, path: str) -> None:
        parts = path.strip("/").split("/")
        try:
            action_id = int(parts[3])
        except (IndexError, ValueError):
            self.send_error(400, "Invalid action ID")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(raw)
        from modes.swing.persistence import skip_action
        ok = skip_action(action_id, data.get("reason", ""))
        body = json.dumps({"ok": ok}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_position_exit(self, path: str,
                                   exchange_filter: str | None = "NSE") -> None:
        # /api/swing/positions/<id>/exit — creates a FULL_EXIT action + confirms it
        parts = path.strip("/").split("/")
        try:
            pos_id = int(parts[3])
        except (IndexError, ValueError):
            self.send_error(400, "Invalid position ID")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(raw)

        from modes.swing.persistence import open_positions, confirm_action, _connect, _ensure_schema
        from config import now_ist as _now

        # Find the position
        positions = open_positions(exchange=exchange_filter)
        pos = next((p for p in positions if p.position_id == pos_id), None)
        if not pos:
            body = json.dumps({"ok": False, "error": "Position not found"}).encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Validate exit qty + price hard. Origin: 2026-05-14 SDE
        # review found that the JS prompt for "Exit price (Rs.)"
        # sent `parseFloat("")` → NaN or `parseFloat("0")` → 0
        # straight to confirm_action(), which then wrote a CLOSED
        # position with `exit_price=0` and a catastrophic synthetic
        # P&L of `-entry_price * qty`. Cap qty at managed_qty so a
        # fat-fingered "10000" on a 50-share position can't mark
        # CLOSED with absurd P&L either.
        try:
            qty = float(data.get("qty", pos.managed_qty))
            price = float(data.get("price", 0))
        except (TypeError, ValueError):
            err = json.dumps({"ok": False,
                              "error": "qty/price must be numeric"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return
        if not (qty > 0) or not (price > 0):
            err = json.dumps({
                "ok": False,
                "error": "qty and price must be positive numbers",
            }).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return
        if qty > pos.managed_qty:
            err = json.dumps({
                "ok": False,
                "error": (f"qty {qty} exceeds managed_qty "
                          f"{pos.managed_qty} for position {pos_id}"),
            }).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        # Create an exit action and immediately confirm it
        ts = _now().isoformat()
        from modes.swing.persistence import DB_PATH
        with _connect(DB_PATH) as conn:
            _ensure_schema(conn)
            cur = conn.execute("""
                INSERT INTO swing_actions (
                    run_id, position_id, symbol, exchange, action_type, status,
                    suggested_qty, suggested_price, created_at
                ) VALUES (0, ?, ?, ?, 'FULL_EXIT', 'PENDING', ?, ?, ?)
            """, (pos_id, pos.symbol, pos.exchange, qty, price, ts))
            exit_action_id = int(cur.lastrowid or 0)

        result = confirm_action(
            action_id=exit_action_id,
            executed_qty=qty,
            executed_price=price,
            source="DASHBOARD",
        )
        body = json.dumps({"ok": result is not None}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_position_add(
        self,
        default_exchange: str = "NSE",
        source: str = "DASHBOARD_MANUAL_ADD",
        notes: str = "Manual Add+ from swing detail/search",
    ) -> None:
        """POST /api/swing/positions/add — manual Add+ into open book."""
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(raw)

        symbol = (data.get("symbol") or "").strip().upper()
        exchange = (data.get("exchange") or default_exchange).strip().upper()
        if not exchange:
            exchange = default_exchange
        try:
            # float so US fractional shares survive; NSE callers
            # send whole numbers, so this is a no-op for them.
            qty = float(data.get("qty", 0))
            price = float(data.get("price", 0))
            stop = float(data.get("stop", 0) or 0)
            target = float(data.get("target", 0) or 0)
        except (TypeError, ValueError):
            err = json.dumps({
                "ok": False,
                "error": "symbol, qty, and price are required; qty/price must be numeric",
            }).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        if not symbol:
            err = json.dumps({"ok": False, "error": "No symbol"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return
        if qty <= 0 or price <= 0:
            err = json.dumps({
                "ok": False,
                "error": "qty and price must be positive numbers",
            }).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        from modes.swing.persistence import add_manual_position
        pos = add_manual_position(
            symbol=symbol,
            executed_qty=qty,
            executed_price=price,
            stop_price=stop,
            target_price=target,
            exchange=exchange,
            source=source,
            notes=notes,
        )
        body = json.dumps({
            "ok": pos is not None,
            "position_id": pos.position_id if pos else 0,
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_position_edit(self, path: str) -> None:
        """POST /api/swing/positions/<id>/edit — update qty/avg cost."""
        parts = path.strip("/").split("/")
        try:
            pos_id = int(parts[3])
        except (IndexError, ValueError):
            self.send_error(400, "Invalid position ID")
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(raw)
        try:
            qty = float(data.get("qty", 0))
            price = float(data.get("price", 0))
            stop = float(data.get("stop", 0) or 0)
            target = float(data.get("target", 0) or 0)
        except (TypeError, ValueError):
            err = json.dumps({
                "ok": False,
                "error": "qty and average price are required numeric values",
            }).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return
        if qty <= 0 or price <= 0:
            err = json.dumps({
                "ok": False,
                "error": "qty and average price must be positive numbers",
            }).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        from modes.swing.persistence import edit_position
        pos = edit_position(
            position_id=pos_id,
            managed_qty=qty,
            entry_price=price,
            stop_price=stop,
            target_price=target,
        )
        body = json.dumps({
            "ok": pos is not None,
            "position_id": pos.position_id if pos else 0,
        }).encode("utf-8")
        self.send_response(200 if pos else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── Watchlist endpoints ─────────────────────────────────────

    def _serve_swing_watchlist_add(self) -> None:
        """POST /api/swing/watchlist/add — add a stock to watchlist."""
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(raw)
        action_id = int(data.get("action_id", 0))
        symbol = (data.get("symbol") or "").strip().upper()

        if not symbol:
            body = json.dumps({"ok": False, "error": "No symbol"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Watchlist entry price MUST come from a live Zerodha quote —
        # no candidate-row / cached-close fallback. After-hours or
        # missing-session adds were the 2026-05-18 SUNPHARMA bug
        # (added via individual search, no Zerodha session, no
        # candidate, so the row landed with added_price = 0).
        # Retry the live fetch a few times before giving up so a
        # transient rate-limit / network blip doesn't fail the add.
        #
        # We call `ZerodhaClient.get_quotes` directly (NOT the
        # dashboard's `get_live_quotes` wrapper) because that
        # wrapper has a 5s rate-limit that returns cached snapshots —
        # for a freshly-searched symbol the cache is empty and the
        # wrapper would short-circuit to {} on every retry.
        price = 0.0
        setup_type = ""

        # Setup type is metadata only — pull from the latest candidate
        # row if one exists, but never let it influence the price.
        from modes.swing.persistence import candidate_by_symbol as _cbs
        c = _cbs(symbol)
        if c:
            setup_type = c.setup_type or ""

        # ── Live Zerodha LTP with retries ──
        last_err: str = ""
        try:
            from core.zerodha_client import ZerodhaClient
            from core.logger import Logger as _Logger
            zerodha = ZerodhaClient(Config, _Logger("WatchlistAdd"))
            zerodha.login(interactive=False)
            for attempt in range(3):
                try:
                    raw = zerodha.get_quotes(
                        [{"symbol": symbol, "exchange": "NSE"}]
                    )
                    q = raw.get(f"NSE:{symbol}") if isinstance(raw, dict) else None
                    if isinstance(q, dict):
                        price = float(q.get("last_price", 0) or 0)
                    if price > 0:
                        break
                    last_err = "Zerodha returned no live price"
                except Exception as exc:  # noqa: BLE001 — surface to client
                    last_err = str(exc)
                time.sleep(0.6 * (attempt + 1))  # 0.6s, 1.2s back-off
        except Exception as exc:  # noqa: BLE001 — login / construction failure
            last_err = f"Zerodha session unavailable: {exc}"

        if price <= 0:
            body = json.dumps({
                "ok": False,
                "error": (
                    f"Could not fetch live Zerodha price for {symbol} "
                    f"after 3 attempts. {last_err or ''} "
                    "Confirm Zerodha is logged in and the market data "
                    "feed is reachable, then retry."
                ).strip(),
            }).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        from modes.swing.persistence import add_to_watchlist
        wid = add_to_watchlist(
            symbol=symbol, price=price,
            setup_type=setup_type, action_id=action_id,
        )
        body = json.dumps({"ok": True, "watchlist_id": wid}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_us_watchlist_add(self) -> None:
        """POST /api/us/watchlist/add — add a US stock to watchlist.

        2026-05-19: rewritten to mirror the Indian swing rule —
        watchlist entry price MUST come from a live yfinance / Yahoo
        quote, no candidate-row / cached-close fallback. Retry is
        the ONLY fallback. The old fallback silently inserted
        `added_price = 0` whenever yfinance was rate-limited or the
        client posted no price, which was the SUNPHARMA-equivalent
        bug for US tickers.
        """
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(raw)
        symbol = (data.get("symbol") or "").strip().upper()
        if not symbol:
            body = json.dumps({"ok": False, "error": "No symbol"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        setup_type = (data.get("setup_type") or "").strip().upper()
        exchange = (data.get("exchange") or "NASDAQ").strip().upper()

        # ── Live yfinance price with retries ──
        from modes.dashboard.us_analysis import fetch_us_live_price_now
        price = 0.0
        last_err = ""
        for attempt in range(3):
            try:
                price = fetch_us_live_price_now(symbol)
                if price > 0:
                    break
                last_err = "yfinance/Yahoo returned no live price"
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
            time.sleep(0.6 * (attempt + 1))  # 0.6s, 1.2s back-off

        if price <= 0:
            body = json.dumps({
                "ok": False,
                "error": (
                    f"Could not fetch live price for {symbol} after 3 "
                    f"attempts. {last_err or ''} "
                    "yfinance may be rate-limited — wait a moment and "
                    "try again."
                ).strip(),
            }).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        from modes.swing.persistence import add_to_watchlist
        wid = add_to_watchlist(
            symbol=symbol,
            price=price,
            setup_type=setup_type,
            exchange=exchange,
            notes="US dashboard watchlist",
        )
        body = json.dumps({"ok": True, "watchlist_id": wid}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_watchlist_promote(self, path: str) -> None:
        """POST /api/swing/watchlist/<id>/promote — move to open book."""
        parts = path.strip("/").split("/")
        try:
            wid = int(parts[3])
        except (IndexError, ValueError):
            self.send_error(400, "Invalid watchlist ID")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(raw)
        from modes.swing.persistence import promote_watchlist_to_position
        result = promote_watchlist_to_position(
            watchlist_id=wid,
            executed_qty=float(data.get("qty", 0)),
            executed_price=float(data.get("price", 0)),
            stop_price=float(data.get("stop", 0)),
            target_price=float(data.get("target", 0) or 0),
        )
        body = json.dumps({"ok": result is not None}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_watchlist_remove(self, path: str) -> None:
        """POST /api/swing/watchlist/<id>/remove — remove from watchlist."""
        parts = path.strip("/").split("/")
        try:
            wid = int(parts[3])
        except (IndexError, ValueError):
            self.send_error(400, "Invalid watchlist ID")
            return
        from modes.swing.persistence import remove_from_watchlist
        ok = remove_from_watchlist(wid)
        body = json.dumps({"ok": ok}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_ai_analyse_single(self, path: str) -> None:
        """POST /api/swing/ai_analyse/<SYMBOL>

        Runs the swing AI overlay against ONE candidate and persists
        the response to that row's `ai_overlay_json`. Returns the
        full overlay payload so the dashboard can render the result
        without a second round-trip. Costs exactly one Claude call
        (~Rs.{CLAUDE_COST_PER_CALL} on Pro). Roadmap S37.

        Failure modes (all surfaced as 4xx + JSON error):
          * Symbol unknown        -> 404
          * No prior swing scan   -> 404 (run the scan first)
          * Claude raises (auth,
            rate-limit, etc.)     -> 502 + error toast via the
                                     usual `core.error_sink` path
        """
        from urllib.parse import unquote
        sym = unquote(path[len("/api/swing/ai_analyse/"):]).strip("/").upper()
        if not sym:
            err = json.dumps({"ok": False, "error": "missing symbol"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        # Lazy imports — keep the GET hot-path lean.
        from modes.swing.persistence import (
            candidate_by_symbol, latest_candidate_row_id_by_symbol,
            update_candidate_ai_overlay,
        )
        from modes.swing.ai_overlay import analyse_single_candidate
        from core.claude_client import ClaudeClient
        from core.logger import Logger

        cand = candidate_by_symbol(sym)
        if cand is None:
            err = json.dumps({
                "ok": False,
                "error": f"No swing candidate found for {sym} — run a scan first.",
            }).encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        row_id = latest_candidate_row_id_by_symbol(sym)
        log = Logger(f"SwingAI[{sym}]")
        try:
            claude = ClaudeClient(Config, log)
        except Exception as exc:
            from core.error_sink import record_external_error
            record_external_error("ai", exc, log=log)
            err = json.dumps({
                "ok": False,
                "error": f"AI client init failed: {exc}",
            }).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        overlay_json = analyse_single_candidate(cand, claude, log)
        # Persist back to the row so a page reload shows the same
        # text that was just rendered.
        if row_id is not None:
            try:
                update_candidate_ai_overlay(row_id, overlay_json)
            except Exception as exc:
                # Persistence failure shouldn't block the response —
                # the overlay text is already in `overlay_json` and
                # we're returning it inline. Log + toast quietly.
                from core.error_sink import record_external_error
                record_external_error("dashboard", exc, log=log)

        # Surface Claude failures as a top-right toast too. The
        # `analyse_single_candidate` helper writes
        # `{"error": "..."}` into `overlay_json` on failure.
        try:
            payload = json.loads(overlay_json or "{}")
        except Exception:
            payload = {}
        if payload.get("error"):
            from core.error_sink import record_external_error
            record_external_error("ai", payload["error"], log=log)

        body = json.dumps({
            "ok": not payload.get("error"),
            "symbol": sym,
            "overlay": payload,
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_analyse_one(self, qs: dict[str, list[str]]) -> None:
        """POST /api/swing/analyse_one?symbol=SBIN&ai=1&capital=100000

        Single-stock search-box flow (S38). Runs the full per-stock
        pipeline against ONE ticker, optionally chains the per-stock
        AI overlay (~Rs.{CLAUDE_COST_PER_CALL}), persists the result
        as a one-row swing_runs entry + a PENDING ENTRY action so
        the page's Done/Skip buttons work just like a recommendation
        from a full scan.

        Returns the candidate JSON + an `action_id` (when accepted)
        + the AI overlay payload if `ai=1`. Errors land in the
        usual `core.error_sink` toast surface.
        """
        symbol = (qs.get("symbol") or [""])[0].strip().upper()
        ai_flag = (qs.get("ai") or ["0"])[0] in ("1", "true", "True", "yes")
        try:
            capital = float((qs.get("capital") or ["0"])[0])
        except (TypeError, ValueError):
            capital = 0.0
        if capital <= 0:
            capital = float(getattr(Config, "SWING_TICKET_AMOUNT", 20_000.0))

        if not symbol:
            err = json.dumps({"ok": False, "error": "missing symbol"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        # Lazy imports.
        from core.logger import Logger
        from core.zerodha_client import ZerodhaClient
        from modes.swing.scanner import SwingScanner
        from modes.swing.persistence import (
            save_run, open_positions, latest_candidate_row_id_by_symbol,
            update_candidate_ai_overlay,
        )
        from modes.swing.types import SwingRunResult
        from modes.swing.ai_overlay import analyse_single_candidate
        from core.claude_client import ClaudeClient
        from core.error_sink import record_external_error

        log = Logger(f"SwingAnalyseOne[{symbol}]")

        try:
            zerodha = ZerodhaClient(Config, log)
            zerodha.login(interactive=False)
        except Exception as exc:
            record_external_error("zerodha", exc, log=log)
            err = json.dumps({
                "ok": False,
                "error": f"Zerodha login failed: {exc}",
            }).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        scanner = SwingScanner(Config, zerodha, log)
        positions = open_positions(exchange="NSE")
        existing = [{
            "symbol": p.symbol,
            "risk_rupees": (p.entry_price - p.stop_price) * p.managed_qty,
            "position_value": p.entry_price * p.managed_qty,
            "sector": "",
        } for p in positions]

        try:
            cand, action = scanner.scan_one(
                symbol,
                swing_capital=capital,
                existing_positions=existing,
            )
        except Exception as exc:
            record_external_error("zerodha", exc, log=log)
            err = json.dumps({
                "ok": False,
                "error": f"scan_one failed for {symbol}: {exc}",
            }).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        # Optional AI overlay — runs only when explicitly requested
        # AND the candidate was scoreable (no point burning a Claude
        # call on a "Not enough daily history" reject).
        ai_overlay_payload: dict | None = None
        if ai_flag and cand.setup_type != "NONE":
            try:
                claude = ClaudeClient(Config, log)
                overlay_json = analyse_single_candidate(cand, claude, log)
                try:
                    ai_overlay_payload = json.loads(overlay_json or "{}")
                except Exception:
                    ai_overlay_payload = {"error": "invalid AI overlay JSON"}
                if ai_overlay_payload.get("error"):
                    record_external_error("ai", ai_overlay_payload["error"], log=log)
            except Exception as exc:
                record_external_error("ai", exc, log=log)
                ai_overlay_payload = {"error": str(exc)[:200]}

        # AI overlay carry-forward (S48, 2026-05-14): when the user
        # didn't request AI for THIS analyse, copy any cached good
        # overlay from a prior run onto the freshly-saved candidate
        # row so the detail page surfaces the existing analysis. The
        # pre-S48 search-box flow created a fresh candidate row with
        # `ai_overlay_json=""` for every search; the detail page's
        # `candidate_by_symbol()` then picked the new row (newest
        # ACCEPTED) and showed "no AI" even though the symbol had a
        # 2161-byte overlay from yesterday's full-scan AI run. Same
        # carry-forward logic that `SwingManager.run()` uses for the
        # universe scan path.
        if ai_overlay_payload is None and cand.status == "ACCEPTED":
            try:
                from modes.swing.persistence import (
                    latest_ai_overlay_for_symbol,
                )
                cached = latest_ai_overlay_for_symbol(cand.symbol)
                if cached:
                    cand.ai_overlay_json = cached[0]
                    log.info(
                        f"AI overlay carry-forward: {cand.symbol} "
                        f"inherits cached overlay from {cached[1][:16]}"
                    )
            except Exception as exc:
                log.warning(f"AI carry-forward failed: {exc}")

        # Persist as a one-stock run so Done/Skip work and the
        # detail page picks up the candidate via candidate_by_symbol().
        ts = now_ist().isoformat()
        result = SwingRunResult(
            started_at=ts,
            finished_at=ts,
            mode="AI" if ai_flag else "NOAI",
            universe="SINGLE",
            run_for_date=now_ist().date().isoformat(),
            trigger_source="SEARCH_BOX",
            candidates=[cand],
            actions=[action] if action else [],
            positions=positions,
            notes=f"single-stock analyse: {symbol}",
        )
        try:
            run_id = save_run(result)
        except Exception as exc:
            log.warning(f"Persist single-stock run failed: {exc}")
            run_id = 0

        # Persist the AI overlay onto the freshly-saved candidate row
        # (if AI was requested) so the detail page renders the same
        # text without a re-fetch.
        action_id = None
        if run_id and ai_overlay_payload:
            row_id = latest_candidate_row_id_by_symbol(cand.symbol)
            if row_id is not None:
                try:
                    update_candidate_ai_overlay(
                        row_id, json.dumps(ai_overlay_payload))
                except Exception:
                    pass
        # Pull the action_id (now persisted) for the Done button.
        if run_id and action is not None:
            from modes.swing.persistence import _connect, _ensure_schema, DB_PATH
            try:
                with _connect(DB_PATH) as conn:
                    _ensure_schema(conn)
                    row = conn.execute(
                        "SELECT action_id FROM swing_actions "
                        "WHERE run_id=? AND symbol=? "
                        "ORDER BY action_id DESC LIMIT 1",
                        (run_id, cand.symbol),
                    ).fetchone()
                    if row:
                        action_id = int(row["action_id"])
            except Exception:
                action_id = None

        body = json.dumps({
            "ok": True,
            "symbol": cand.symbol,
            "candidate": cand.to_dict(),
            "action_id": action_id,
            "ai_overlay": ai_overlay_payload,
            "run_id": run_id,
        }, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_ai_status(self) -> None:
        """GET /api/ai/status — returns current AI provider config."""
        import json as _json
        ai_plan = Config.ai()
        body = _json.dumps({
            "provider": Config.AI_PROVIDER,
            "plan": Config.AI_PLAN,
            "model": ai_plan["model"],
            "cost": ai_plan["cost_inr_approx"],
            "note": ai_plan["note"],
            "free_tier": ai_plan.get("free_tier"),
            "all_options": Config.ai_providers_summary(),
        }).encode()
        self._write_json(body)

    def _serve_ai_switch(self) -> None:
        """POST /api/ai/switch — switch AI provider and/or plan at runtime.

        Body (JSON): {"provider": "gemini"|"gpt"|"claude", "plan": "free"|"pro"|"max"}
        Both fields are optional; omitted fields keep current value.
        Returns the new active config as JSON.
        """
        import json as _json
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = _json.loads(raw) if raw.strip() else {}
        except _json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        valid_providers = list(Config._AI_PROVIDER_TABLE)
        valid_plans = ["basic", "detailed", "full"]

        new_provider = data.get("provider", "").lower().strip()
        new_plan = data.get("plan", "").lower().strip()

        if new_provider and new_provider not in valid_providers:
            body = _json.dumps({"ok": False, "error": f"Invalid provider: {new_provider}. Valid: {valid_providers}"}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if new_plan and new_plan not in valid_plans:
            body = _json.dumps({"ok": False, "error": f"Invalid plan: {new_plan}. Valid: {valid_plans}"}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if new_provider:
            Config.AI_PROVIDER = new_provider
        if new_plan:
            Config.AI_PLAN = new_plan
            Config.CLAUDE_PLAN = new_plan  # keep legacy alias in sync

        # Build a cost warning if switching TO a paid provider
        ai_plan = Config.ai()
        cost_warning = None
        has_free = bool(ai_plan.get("free_tier"))
        if not has_free:
            cost_warning = (
                f"⚠ {Config.AI_PROVIDER.upper()} is a PAID provider. "
                f"Estimated cost: {ai_plan['cost_inr_approx']}. "
                f"Make sure you have credits loaded."
            )
        body = _json.dumps({
            "ok": True,
            "provider": Config.AI_PROVIDER,
            "plan": Config.AI_PLAN,
            "model": ai_plan["model"],
            "cost": ai_plan["cost_inr_approx"],
            "note": ai_plan["note"],
            "free_tier": ai_plan.get("free_tier"),
            "cost_warning": cost_warning,
            "all_options": Config.ai_providers_summary(),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_chat_prompt(self) -> None:
        """POST /api/chat/prompt — build a copy-paste LLM prompt from
        the user's personal data for a given page scope/symbol.

        Body (JSON): {"scope": str, "symbol": str, "question": str}
        Returns: {"ok": true, "prompt": str} or {"ok": false, "error": str}
        """
        import json as _json
        from modes.dashboard.chat_widget import build_chat_prompt
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = _json.loads(raw) if raw.strip() else {}
        except _json.JSONDecodeError:
            self._write_json(
                _json.dumps({"ok": False, "error": "Invalid JSON"}).encode(),
                status=400)
            return
        scope = str(data.get("scope", "")).strip()
        symbol = str(data.get("symbol", "")).strip()
        question = str(data.get("question", ""))
        try:
            prompt = build_chat_prompt(scope, symbol, question)
        except ValueError as exc:
            self._write_json(
                _json.dumps({"ok": False, "error": str(exc)}).encode(),
                status=400)
            return
        self._write_json(_json.dumps({"ok": True, "prompt": prompt}).encode())

    def _serve_login_submit(self) -> None:
        # Read form-urlencoded body; extract redirect_url; exchange
        # for an access token via core.zerodha_client. Always redirect
        # back to the submitting page so the auth pill re-renders.
        from urllib.parse import parse_qs as _pqs, urlparse as _up
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        form = _pqs(raw)
        redirect_url = (form.get("redirect_url") or [""])[0]
        next_path = _safe_next((form.get("next") or [""])[0])

        request_token = ""
        if redirect_url:
            try:
                q = _pqs(_up(redirect_url).query)
                request_token = (q.get("request_token") or [""])[0]
            except Exception:
                request_token = ""

        ok = False
        err = ""
        if request_token:
            try:
                from config import Config
                from core.logger import Logger
                from core.zerodha_client import ZerodhaClient
                client = ZerodhaClient(Config, Logger("DashboardLogin"))
                # `_kite` is None until login() runs; we recreate the
                # KiteConnect handle ourselves to use the helper.
                from kiteconnect import KiteConnect
                client._kite = KiteConnect(api_key=Config.ZERODHA_API_KEY)
                client._exchange_and_save(request_token)
                ok = True
                # Re-auth succeeded — wipe any prior auth-shaped
                # toasts so they don't keep showing on the next
                # render. New errors (post-clear) still get monotonic
                # ids higher than the JS poller's last-seen, so a
                # subsequent failure will still toast.
                from core.error_sink import clear as _clear_errors
                _clear_errors()
            except Exception as exc:
                err = str(exc)[:200]
        else:
            err = "No request_token found in the pasted URL"

        # 303 redirect with a one-shot query flag for the page to read.
        self.send_response(303)
        self.send_header("Location", _login_target(next_path, ok, err))
        self.end_headers()

    def _serve_login_assisted(self) -> None:
        """Assisted login: password from .env, OTP from form field."""
        from urllib.parse import parse_qs as _pqs
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        form = _pqs(raw)
        otp = (form.get("otp") or [""])[0].strip()
        next_path = _safe_next((form.get("next") or [""])[0])

        ok = False
        err = ""
        if otp:
            try:
                from config import Config
                from core.logger import Logger
                from core.zerodha_client import ZerodhaClient
                client = ZerodhaClient(Config, Logger("DashboardAssistedLogin"))
                client.login_assisted_with_otp(otp)
                ok = True
                # Wipe stale auth toasts after a successful re-auth.
                from core.error_sink import clear as _clear_errors
                _clear_errors()
            except Exception as exc:
                err = str(exc)[:200]
        else:
            err = "No OTP provided"

        self.send_response(303)
        self.send_header("Location", _login_target(next_path, ok, err))
        self.end_headers()

    def _serve_shell(self) -> None:
        from modes.dashboard.render_html import render_shell
        d_from, d_to = current_fy_window()
        payload = compute_payload(
            date_from=d_from, date_to=d_to,
            granularity="daily", include_provisional=True,
        )
        body = render_shell(payload, server_mode=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_theory(self, slug: str = "") -> None:
        from modes.dashboard.theory_page import render_theory_page
        body = render_theory_page(slug or "statistics").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # — /tax —
    def _serve_tax(self, qs: dict[str, list[str]]) -> None:
        from modes.dashboard.tax_page import render_tax_page_v2
        fy_start = _parse_int(qs.get("fy", [None])[0])
        other_income = _parse_float(qs.get("other_income", ["0"])[0], 0.0)
        is_salaried = (qs.get("is_salaried", ["1"])[0] != "0")
        body = render_tax_page_v2(
            other_income=other_income,
            fy_start=fy_start,
            is_salaried=is_salaried,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_tax_api(self, qs: dict[str, list[str]]) -> None:
        from modes.dashboard.tax_page import render_tax_api
        fy_start = _parse_int(qs.get("fy", [None])[0])
        other_income = _parse_float(qs.get("other_income", ["0"])[0], 0.0)
        is_salaried = (qs.get("is_salaried", ["1"])[0] != "0")
        payload = render_tax_api(
            other_income=other_income,
            fy_start=fy_start,
            is_salaried=is_salaried,
        )
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_api(self, qs: dict[str, list[str]]) -> None:
        date_from = (qs.get("from") or [None])[0] or None
        date_to   = (qs.get("to")   or [None])[0] or None
        granularity = (qs.get("granularity") or ["daily"])[0]
        verified = (qs.get("verified") or ["all"])[0]
        include_provisional = (verified != "verified")

        # Validate ISO dates if supplied (avoid raising 500s mid-render).
        for label, val in (("from", date_from), ("to", date_to)):
            if val:
                try:
                    datetime.date.fromisoformat(val)
                except ValueError:
                    self.send_error(400, f"Invalid {label} date: {val!r}")
                    return

        payload = compute_payload(
            date_from=date_from, date_to=date_to,
            granularity=granularity,
            include_provisional=include_provisional,
        )
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_day(self, qs: dict[str, list[str]]) -> None:
        date = (qs.get("date") or [None])[0]
        if not date:
            self.send_error(400, "Missing 'date' query param.")
            return
        verified = (qs.get("verified") or ["all"])[0]
        include_provisional = (verified != "verified")
        try:
            payload = day_detail(date, include_provisional=include_provisional)
        except ValueError as exc:
            self.send_error(400, f"Invalid date: {exc}")
            return
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


# ── Entrypoint ────────────────────────────────────────────────────

def serve(host: str = "127.0.0.1", port: int = 0,
          *, open_browser: bool = True) -> int:
    """Start the dashboard server (blocking).

    `port=0` lets the OS pick a free port — printed on startup so the
    user knows where to connect. Always binds localhost only; this is
    a personal tool that surfaces tax-grade P&L, not a public service.
    """
    server = ThreadingHTTPServer((host, port), _DashboardHandler)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}/"
    print(f"[dashboard] serving on {url}  (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] shutting down…")
    finally:
        server.server_close()
    return 0


__all__ = ["serve", "compute_payload"]
