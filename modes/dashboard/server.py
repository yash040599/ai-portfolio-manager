"""Dashboard HTTP server (Roadmap addendum 2026-04-23).

Serves the interactive dashboard so date-range, granularity and
verified-only filters can be changed from the webpage itself
("the script is just an entry point, all configs achieved from the
webpage directly"). Stdlib only — no Flask, no WSGI runner.

Routes:
    GET /              -> HTML shell with the initial payload inlined.
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
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from config import Config
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
from modes.dashboard.render_html import build_payload, render_shell
from modes.dashboard.portfolio_page import (
    render_login_page,
    render_portfolio_page,
    render_status_json,
    render_stock_chart_json,
    render_stock_drilldown,
)
from modes.dashboard.portfolio_actions import submit_run
from modes.dashboard.theory_page import render_theory_page
from modes.dashboard.tax_page import render_tax_api, render_tax_page_v2
from modes.dashboard.verdict import LadderRung, verdict_for
from modes.dashboard.swing_page import (
    render_swing_page, render_swing_data_json, render_swing_status_json,
)
from modes.dashboard.swing_actions import submit_swing_run


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
                # D24 (2026-05-12): default landing page is now the
                # tool-wide Portfolio Analyser. The legacy intraday
                # P&L SPA moved to /trading.
                self.send_response(302)
                self.send_header("Location", "/portfolio")
                self.end_headers()
            elif url.path == "/portfolio" or url.path == "/portfolio/":
                self._serve_portfolio()
            elif url.path.startswith("/portfolio/"):
                # /portfolio/<symbol> drill-down (D26 + D29).
                symbol = url.path[len("/portfolio/"):].strip("/")
                self._serve_portfolio_drilldown(symbol)
            elif url.path == "/trading" or url.path == "/trading/":
                self._serve_shell()
            elif url.path == "/login" or url.path == "/login/":
                self._serve_login()
            elif url.path == "/api/run_status":
                self._serve_run_status()
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
            elif url.path == "/api/swing/data":
                self._serve_swing_data()
            elif url.path == "/api/swing/run_status":
                self._serve_swing_run_status()
            elif url.path == "/api/data":
                self._serve_api(parse_qs(url.query))
            elif url.path == "/api/day":
                self._serve_day(parse_qs(url.query))
            else:
                self.send_error(404, "Not found")
        except Exception as exc:  # noqa: BLE001 — surface to browser
            sys.stderr.write(f"[dashboard] ERROR: {exc!r}\n")
            self.send_error(500, f"Server error: {exc}")

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
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
            elif url.path.startswith("/api/swing/positions/") and url.path.endswith("/exit"):
                self._serve_swing_position_exit(url.path)
            else:
                self.send_error(404, "Not found")
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[dashboard] POST ERROR: {exc!r}\n")
            self.send_error(500, f"Server error: {exc}")

    # — Endpoints —

    def _serve_portfolio(self) -> None:
        body = render_portfolio_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_portfolio_drilldown(self, symbol: str) -> None:
        body = render_stock_drilldown(symbol).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_login(self) -> None:
        body = render_login_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_run_status(self) -> None:
        body = render_status_json().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_stock_chart(self, qs: dict[str, list[str]]) -> None:
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
        body = render_swing_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_data(self) -> None:
        body = render_swing_data_json().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_run_status(self) -> None:
        body = render_swing_status_json().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_swing_run(self, qs: dict[str, list[str]]) -> None:
        mode = (qs.get("mode") or ["NOAI"])[0].upper()
        if mode not in ("NOAI", "AI"):
            mode = "NOAI"
        capital = _parse_float((qs.get("capital") or ["0"])[0], 0.0)
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
        from modes.swing.persistence import confirm_action
        result = confirm_action(
            action_id=action_id,
            executed_qty=int(data.get("qty", 0)),
            executed_price=float(data.get("price", 0)),
            source="DASHBOARD",
            confirmed_stop=float(data.get("stop", 0)),
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

    def _serve_swing_position_exit(self, path: str) -> None:
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
        from modes.swing.types import SwingAction, ACTION_FULL_EXIT, STATUS_PENDING
        from config import now_ist as _now

        # Find the position
        positions = open_positions()
        pos = next((p for p in positions if p.position_id == pos_id), None)
        if not pos:
            body = json.dumps({"ok": False, "error": "Position not found"}).encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Create an exit action and immediately confirm it
        ts = _now().isoformat()
        from modes.swing.persistence import DB_PATH
        with _connect(DB_PATH) as conn:
            _ensure_schema(conn)
            cur = conn.execute("""
                INSERT INTO swing_actions (
                    position_id, symbol, exchange, action_type, status,
                    suggested_qty, suggested_price, created_at
                ) VALUES (?, ?, ?, 'FULL_EXIT', 'PENDING', ?, ?, ?)
            """, (pos_id, pos.symbol, pos.exchange,
                  int(data.get("qty", pos.managed_qty)),
                  float(data.get("price", 0)), ts))
            exit_action_id = int(cur.lastrowid or 0)

        result = confirm_action(
            action_id=exit_action_id,
            executed_qty=int(data.get("qty", pos.managed_qty)),
            executed_price=float(data.get("price", 0)),
            source="DASHBOARD",
        )
        body = json.dumps({"ok": result is not None}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_login_submit(self) -> None:
        # Read form-urlencoded body; extract redirect_url; exchange
        # for an access token via core.zerodha_client. Always redirect
        # back to /login so the page re-renders the auth pill.
        from urllib.parse import parse_qs as _pqs, urlparse as _up
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        form = _pqs(raw)
        redirect_url = (form.get("redirect_url") or [""])[0]

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
            except Exception as exc:
                err = str(exc)[:200]

        # 303 redirect with a one-shot query flag for the page to read.
        target = "/login?ok=1" if ok else f"/login?err={err}"
        self.send_response(303)
        self.send_header("Location", target)
        self.end_headers()

    def _serve_login_assisted(self) -> None:
        """Assisted login: password from .env, OTP from form field."""
        from urllib.parse import parse_qs as _pqs
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        form = _pqs(raw)
        otp = (form.get("otp") or [""])[0].strip()

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
            except Exception as exc:
                err = str(exc)[:200]
        else:
            err = "No OTP provided"

        target = "/login?ok=1" if ok else f"/login?err={err}"
        self.send_response(303)
        self.send_header("Location", target)
        self.end_headers()

    def _serve_shell(self) -> None:
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
        body = render_theory_page(slug or "statistics").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # — /tax —
    def _serve_tax(self, qs: dict[str, list[str]]) -> None:
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
