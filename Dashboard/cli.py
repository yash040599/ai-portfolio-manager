"""Dashboard CLI (Roadmap D1, expanded 2026-04-23).

Entry point invoked via `python main.py --mode dashboard`. The script
is intentionally thin — once launched, the webpage itself is the
config surface (date range, granularity, verified-only toggle).

Modes:
  default                   Start a local HTTP server + open the browser.
  --no-open                 Write a static HTML snapshot to disk only.
  --text                    Print the legacy plain-text dashboard.

Window flags (also adjustable from the webpage in server mode):
  --days N                  Last N calendar days.
  --from / --to             Explicit ISO window.
  Default                   Current Indian FY (Apr 1 → Mar 31).

Source-of-truth flags:
  --verified-only           Tax-grade view; pending rows excluded.
  Default                   Include pending rows as PROVISIONAL.

Server flags:
  --port N                  Bind to a fixed port (0 = OS-allocated).
  --host H                  Bind interface (default 127.0.0.1).
"""

from __future__ import annotations

import argparse
import datetime
import sys

from config import Config
from Dashboard.budget_history import average_budget
from Dashboard.data_layer import (
    fetch_trades,
    pending_verification_dates,
    resolve_window,
    verified_dates,
)
from Dashboard.metrics import (
    bucketed_pnl,
    cumulative_series,
    headline_pnl,
)
from Dashboard.render_text import render as render_text
from Dashboard.verdict import LadderRung, verdict_for


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dashboard",
        description="AI Portfolio Manager — profitability dashboard (read-only).",
    )
    p.add_argument("--days", type=int, default=None,
                   help="Last N calendar days. Default: current Indian FY.")
    p.add_argument("--from", dest="date_from", default=None,
                   help="Window start (ISO YYYY-MM-DD).")
    p.add_argument("--to", dest="date_to", default=None,
                   help="Window end (ISO YYYY-MM-DD).")
    p.add_argument("--verified-only", action="store_true",
                   help="Restrict to sheet-verified rows (tax-grade only).")
    p.add_argument("--text", action="store_true",
                   help="Plain-text dashboard to stdout (no HTML, no server).")
    p.add_argument("--no-open", action="store_true",
                   help="Write static HTML snapshot to disk; do not start the server.")
    p.add_argument("--port", type=int, default=0,
                   help="Server port. 0 = OS-allocated. Ignored with --text/--no-open.")
    p.add_argument("--host", default="127.0.0.1",
                   help="Server bind interface (default 127.0.0.1).")
    return p


def _ladder_from_config(cfg) -> list[LadderRung]:
    raw = getattr(cfg, "CAPITAL_LADDER", None) or []
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


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    today = datetime.date.today()
    if args.date_to:
        try:
            entered = datetime.date.fromisoformat(args.date_to)
            if entered > today:
                print(
                    f"  warning: --to {args.date_to} is in the future; "
                    f"clamping to today ({today.isoformat()}).",
                    file=sys.stderr,
                )
        except ValueError:
            print(f"  error: --to is not a valid ISO date: {args.date_to!r}",
                  file=sys.stderr)
            return 2

    # Default path: launch the interactive server and open the browser.
    # CLI flags only seed the *initial* view — the webpage takes over.
    if not args.text and not args.no_open:
        from Dashboard.server import serve
        return serve(host=args.host, port=args.port, open_browser=True)

    # --text and --no-open both need a one-shot resolved window.
    date_from, date_to = resolve_window(
        days       = args.days,
        date_from  = args.date_from,
        date_to    = args.date_to,
        today      = today,
    )

    include_provisional = not args.verified_only
    trades   = fetch_trades(date_from, date_to,
                            include_provisional=include_provisional)
    verified = verified_dates(date_from, date_to)
    pending  = pending_verification_dates(date_from, date_to)
    headline = headline_pnl(trades)
    ladder   = _ladder_from_config(Config)

    fallback = float(getattr(Config, "MAX_BUDGET_INR", 0) or 0)
    budget_avg = average_budget(verified or [date_from], fallback)
    budget_int = int(round(budget_avg))
    verdict_result = verdict_for(headline, ladder=ladder, budget=budget_int)

    if args.text:
        print(render_text(
            date_from           = date_from,
            date_to             = date_to,
            trading_day_count   = headline.trading_days,
            verified_day_count  = len(verified),
            pending_dates       = pending,
            headline            = headline,
            verdict             = verdict_result,
            budget              = budget_int,
            include_provisional = include_provisional,
        ))
        return 0

    # --no-open: write static HTML snapshot.
    from Dashboard.render_html import build_payload, render_shell, write_and_maybe_open
    from Dashboard.strategy_versions import strategy_shas, boundaries
    overlay_enabled = bool(getattr(Config, "DASHBOARD_STRATEGY_VERSION_OVERLAY", True))
    sv_boundaries: list[dict] = []
    if overlay_enabled:
        try:
            sv_boundaries = boundaries(strategy_shas(date_from, date_to))
        except Exception:
            sv_boundaries = []
    payload = build_payload(
        date_from           = date_from,
        date_to             = date_to,
        granularity         = "daily",
        headline            = headline,
        verdict             = verdict_result,
        budget              = budget_avg,
        verified_day_count  = len(verified),
        pending_dates       = pending,
        bucketed            = bucketed_pnl(trades, "daily"),
        cumulative          = cumulative_series(trades),
        include_provisional = include_provisional,
        strategy_boundaries = sv_boundaries,
        strategy_overlay_enabled = overlay_enabled,
    )
    html_str = render_shell(payload, server_mode=False)
    out_path = write_and_maybe_open(html_str, date_to=date_to, open_browser=False)
    print(f"Dashboard snapshot written to {out_path}")
    print("Tip: re-run without --no-open for the interactive (server) view.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
