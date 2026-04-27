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
from Dashboard.budget_history import average_budget
from Dashboard.data_layer import (
    current_fy_window,
    fetch_trades,
    pending_verification_dates,
    resolve_window,
    verified_dates,
)
from Dashboard.day_detail import day_detail
from Dashboard.metrics import (
    bucketed_pnl,
    cumulative_series,
    headline_pnl,
)
from Dashboard.render_html import build_payload, render_shell
from Dashboard.theory_page import render_theory_page
from Dashboard.verdict import LadderRung, verdict_for


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
                self._serve_shell()
            elif url.path == "/theory" or url.path == "/theory/":
                # Redirect to default theory page so the dropdown reflects state.
                from Dashboard.theory_page import DEFAULT_PAGE
                self.send_response(302)
                self.send_header("Location", f"/theory/{DEFAULT_PAGE}")
                self.end_headers()
            elif url.path.startswith("/theory/"):
                slug = url.path[len("/theory/"):].strip("/")
                self._serve_theory(slug)
            elif url.path == "/api/data":
                self._serve_api(parse_qs(url.query))
            elif url.path == "/api/day":
                self._serve_day(parse_qs(url.query))
            else:
                self.send_error(404, "Not found")
        except Exception as exc:  # noqa: BLE001 — surface to browser
            sys.stderr.write(f"[dashboard] ERROR: {exc!r}\n")
            self.send_error(500, f"Server error: {exc}")

    # — Endpoints —

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
