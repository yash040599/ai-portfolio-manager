"""Shared dashboard navigation chrome."""

from __future__ import annotations

import html

from modes.dashboard.theme import theme_toggle_html


# (label, href, tooltip)
# "Home" is deliberately absent — the `Portfolio HQ` brand on the left
# is the home link, so a separate pill would be a second button to the
# same page.
NAV_ITEMS = [
    ("Portfolio", "/portfolio", "Zerodha holdings, gaps and analyse runs"),
    ("Mutual Funds", "/mf", "Coin funds plus funds held at other brokers"),
    ("Swing", "/swing", "Indian swing scan, watchlist and open book"),
    ("US", "/us", "US long-term portfolio and investment ideas"),
    ("Intraday", "/trading", "Live intraday P&L"),
    ("Dry Run", "/dryrun", "Per-strategy dry-run P&L"),
    ("Tax", "/tax", "Realised P&L and tax projection"),
    ("Theory", "/theory/statistics", "Strategy notes and statistics"),
]

# Pages still pass a label (not a path) as `here`; normalise those.
_LABEL_ALIASES = {
    "home": "/",
    "portfolio": "/portfolio",
    "mutual funds": "/mf",
    "mf": "/mf",
    "swing": "/swing",
    "us": "/us",
    "trading": "/trading",
    "trading (live p&l)": "/trading",
    "intraday": "/trading",
    "dry run": "/dryrun",
    "dryrun": "/dryrun",
    "tax": "/tax",
    "theory": "/theory/statistics",
}


def _active(here: str, label: str, href: str) -> bool:
    marker = (here or "").strip().lower()
    if not marker:
        return False
    marker = _LABEL_ALIASES.get(marker, marker)
    lhref = href.lower()
    if marker == lhref:
        return True
    # "/" must never prefix-match, otherwise Home lights up on every page.
    if lhref != "/" and marker.startswith(lhref + "/"):
        return True
    if href.startswith("/theory/") and marker.startswith("/theory/"):
        return True
    return False


def render_topnav(here: str = "", *, after_links: str = "") -> str:
    """Render a full dashboard nav plus a Back control and theme switch."""
    at_home = _active(here, "Home", "/")
    brand_cls = "nav-brand here" if at_home else "nav-brand"
    brand_extra = ' aria-current="page"' if at_home else ''
    parts = [
        '<button class="nav-back" type="button" title="Go back" '
        'onclick="if (window.history.length > 1) { window.history.back(); } '
        'else { window.location.href=\'/\'; }">&#8592; Back</button>',
        f'<a class="{brand_cls}" href="/"{brand_extra} '
        'title="Home — overview of every book">'
        '<span class="dot"></span>Portfolio HQ</a>',
        '<span class="nav-links">',
    ]
    for label, href, hint in NAV_ITEMS:
        if _active(here, label, href):
            parts.append(
                f'<span class="here" aria-current="page" '
                f'title="{html.escape(hint)}">{html.escape(label)}</span>'
            )
        else:
            parts.append(
                f'<a href="{href}" title="{html.escape(hint)}">'
                f'{html.escape(label)}</a>'
            )
    parts.append("</span>")
    return (
        '<nav class="topnav">'
        + "".join(parts)
        + '<span class="spacer"></span>'
        + after_links
        + theme_toggle_html()
        + '</nav>'
    )


def topnav_css() -> str:
    """Structural nav CSS only — colours come from `theme.py`."""
    return """
nav.topnav { display: flex; gap: 8px; align-items: center;
             padding: 8px 12px; background: var(--card);
             border: 1px solid var(--line); border-radius: 14px;
             margin-bottom: 18px; font-size: 14px; flex-wrap: wrap; }
nav.topnav .nav-brand { display: inline-flex; align-items: center; gap: 7px;
             font-weight: 700; font-size: 13.5px; letter-spacing: -.01em;
             padding: 0 8px 0 4px; color: var(--fg); white-space: nowrap;
             text-decoration: none; }
nav.topnav .nav-brand .dot { width: 9px; height: 9px; border-radius: 50%;
             background: linear-gradient(135deg, var(--accent), var(--accent-2)); }
nav.topnav .nav-links { display: flex; gap: 2px; align-items: center;
             flex-wrap: wrap; }
nav.topnav a,
nav.topnav button.nav-back { color: var(--fg); text-decoration: none;
                             font-weight: 500; }
nav.topnav button.nav-back { font: inherit; padding: 5px 12px;
                             border: 1px solid var(--line);
                             border-radius: 999px; background: var(--card);
                             color: var(--fg); cursor: pointer; }
nav.topnav button.nav-back:hover { background: var(--soft); }
nav.topnav .here { color: var(--accent); cursor: default; }
nav.topnav a.nav-brand.here { color: var(--accent); }
nav.topnav .sep { display: none; }
nav.topnav .spacer { flex: 1; }
/* The brand is the only Home affordance now, so it stays visible at
   every width; the nav wraps instead. */
"""


__all__ = ["NAV_ITEMS", "render_topnav", "topnav_css"]