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
    render_swing_detail,
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
            elif url.path == "/api/errors":
                self._serve_errors(parse_qs(url.query))
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

    def _serve_swing_detail(self, symbol: str) -> None:
        body = render_swing_detail(symbol).encode("utf-8")
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
            return scanner.scan_one(sym, swing_capital=100_000.0)

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

        # Validate exit qty + price hard. Origin: 2026-05-14 SDE
        # review found that the JS prompt for "Exit price (Rs.)"
        # sent `parseFloat("")` → NaN or `parseFloat("0")` → 0
        # straight to confirm_action(), which then wrote a CLOSED
        # position with `exit_price=0` and a catastrophic synthetic
        # P&L of `-entry_price * qty`. Cap qty at managed_qty so a
        # fat-fingered "10000" on a 50-share position can't mark
        # CLOSED with absurd P&L either.
        try:
            qty = int(data.get("qty", pos.managed_qty))
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
                    position_id, symbol, exchange, action_type, status,
                    suggested_qty, suggested_price, created_at
                ) VALUES (?, ?, ?, 'FULL_EXIT', 'PENDING', ?, ?, ?)
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

        # Get the suggested price from the action
        price = 0.0
        setup_type = ""
        if action_id:
            from modes.swing.persistence import actions_for_run, latest_run as _lr
            lr = _lr()
            if lr:
                for a in actions_for_run(int(lr["run_id"])):
                    if a.action_id == action_id:
                        price = a.suggested_price or a.live_price or 0
                        break
            from modes.swing.persistence import candidate_by_symbol as _cbs
            c = _cbs(symbol)
            if c:
                setup_type = c.setup_type
                if price <= 0:
                    price = c.close_price

        # Fallback: get live price
        if price <= 0:
            from modes.dashboard.live_quotes import get_live_quotes
            lq = get_live_quotes([symbol])
            price = lq.get(symbol, {}).get("price", 0)

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
            executed_qty=int(data.get("qty", 0)),
            executed_price=float(data.get("price", 0)),
            stop_price=float(data.get("stop", 0)),
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
            record_external_error("claude", exc, log=log)
            err = json.dumps({
                "ok": False,
                "error": f"Claude client init failed: {exc}",
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
            record_external_error("claude", payload["error"], log=log)

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
            capital = float(getattr(Config, "SWING_CAPITAL", 100_000.0))

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
            init_db, save_run, open_positions, latest_candidate_row_id_by_symbol,
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
        positions = open_positions()
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
                    record_external_error("claude", ai_overlay_payload["error"], log=log)
            except Exception as exc:
                record_external_error("claude", exc, log=log)
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
                # Re-auth succeeded — wipe any prior auth-shaped
                # toasts so they don't keep showing on the next
                # render. New errors (post-clear) still get monotonic
                # ids higher than the JS poller's last-seen, so a
                # subsequent failure will still toast.
                from core.error_sink import clear as _clear_errors
                _clear_errors()
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
                # Wipe stale auth toasts after a successful re-auth.
                from core.error_sink import clear as _clear_errors
                _clear_errors()
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
