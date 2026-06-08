"""Lightweight dashboard home page.

This page is intentionally cheap: no broker calls, no yfinance calls,
no portfolio analysis DB reads, and no polling JavaScript.  It exists
so opening the dashboard does not automatically load the heaviest page.
"""

from __future__ import annotations

import html

from modes.dashboard.nav import render_topnav, topnav_css


_PAGES = [
    ("Portfolio", "/portfolio", "Holdings, gaps, actions, and analyse runs."),
    ("Swing", "/swing", "Indian swing scan, watchlist, and open book."),
    ("US", "/us", "US swing scan, watchlist, FX toggle, and open book."),
    ("Trading", "/trading", "Live intraday P&L dashboard."),
    ("Dry Run", "/dryrun", "Per-strategy dry-run P&L and statistics."),
    ("Tax", "/tax", "Tax and realised P&L views."),
    ("Theory", "/theory/statistics", "Strategy notes and statistics."),
]


def render_home_page() -> str:
    cards = []
    for title, href, desc in _PAGES:
        cards.append(
            '<a class="tile" href="{href}">'
            '<strong>{title}</strong>'
            '<span>{desc}</span>'
            '</a>'.format(
                href=html.escape(href),
                title=html.escape(title),
                desc=html.escape(desc),
            )
        )
    body = "".join([
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Dashboard Home</title><style>",
        _STYLE,
        topnav_css(),
        "</style></head><body>",
        render_topnav("/"),
        "<main class='wrap'>",
        "<h1>Dashboard</h1>",
        "<p class='sub'>Open only the tool you need. This page does not start scans, live price polling, or analysis jobs.</p>",
        "<section class='grid'>",
        *cards,
        "</section>",
        "</main></body></html>",
    ])
    return body


_STYLE = """
:root { --bg: #fafbfc; --fg: #1c1f23; --muted: #6a7280;
        --card: #ffffff; --line: #e5e7eb; --soft: #f4f6f8; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px; background: var(--bg); color: var(--fg);
       font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }
.wrap { max-width: 980px; margin: 0 auto; }
h1 { margin: 0 0 6px; font-size: 24px; }
.sub { margin: 0 0 18px; color: var(--muted); font-size: 14px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 12px; }
.tile { display: block; padding: 14px 15px; min-height: 96px;
        border: 1px solid var(--line); border-radius: 8px;
        background: var(--card); color: var(--fg); text-decoration: none; }
.tile:hover { background: var(--soft); }
.tile strong { display: block; margin-bottom: 8px; font-size: 15px; }
.tile span { display: block; color: var(--muted); font-size: 13px; line-height: 1.45; }
"""


__all__ = ["render_home_page"]