"""Shared dashboard app shell: a top app bar and a navigation rail.

The layout mirrors Rupevo, the dashboard's public companion site. The app
bar carries the brand, the page's own status pills (`after_links`), and the
screen-privacy, theme and "Buy me a coffee" controls. The rail lists every
dashboard page plus Rupevo; below 840px it becomes a bottom bar that scrolls
sideways. Both are fixed-position, so the markup works wherever a page puts
it in <body>; `theme.py` pads the body whenever the shell is present.
"""

from __future__ import annotations

import html

from modes.dashboard.coffee import coffee_button_html
from modes.dashboard.icons import icon
from modes.dashboard.privacy import privacy_toggle_html
from modes.dashboard.rupevo import rupevo_nav_link
from modes.dashboard.theme import theme_toggle_html


# (label, href, icon, tooltip)
NAV_ITEMS = [
    ("Home", "/", "home", "Overview of every book"),
    ("Portfolio", "/portfolio", "portfolio", "Zerodha holdings, gaps and analyse runs"),
    ("Funds", "/mf", "funds", "Mutual funds: Coin plus funds held at other brokers"),
    ("Swing", "/swing", "india", "Indian swing scan, watchlist and open book"),
    ("US", "/us", "us", "US long-term portfolio and investment ideas"),
    ("Intraday", "/trading", "pulse", "Live intraday P&L"),
    ("Dry run", "/dryrun", "flask", "Per-strategy dry-run P&L"),
    ("Tax", "/tax", "tax", "Realised P&L and tax projection"),
    ("Theory", "/theory/project", "book", "The project story, evidence and research roadmap"),
]

# Pages still pass a label (not a path) as `here`; normalise those.
_LABEL_ALIASES = {
    "home": "/",
    "portfolio": "/portfolio",
    "mutual funds": "/mf",
    "funds": "/mf",
    "mf": "/mf",
    "swing": "/swing",
    "us": "/us",
    "trading": "/trading",
    "trading (live p&l)": "/trading",
    "intraday": "/trading",
    "dry run": "/dryrun",
    "dryrun": "/dryrun",
    "tax": "/tax",
    "theory": "/theory/project",
}


def _active(here: str, href: str) -> bool:
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
    """Render the app bar and navigation rail with `here` marked current.

    `after_links` holds page status pills (auth, FX rate, currency switch,
    docs picker); they sit in the app bar before the shared controls.
    """
    status = f'<div class="appbar-status">{after_links}</div>' if after_links else ""
    appbar = (
        '<header class="appbar">'
        '<a class="brand" href="/" title="Portfolio HQ: overview of every book">'
        '<span class="brand-mark" aria-hidden="true">P</span>'
        '<span class="brand-text">Portfolio HQ</span></a>'
        '<span class="appbar-spacer"></span>'
        + status
        + privacy_toggle_html()
        + theme_toggle_html()
        + coffee_button_html()
        + "</header>"
    )
    items = []
    for label, href, glyph, hint in NAV_ITEMS:
        current = ' aria-current="page"' if _active(here, href) else ""
        items.append(
            f'<a class="nav-item" href="{href}" title="{html.escape(hint)}"{current}>'
            f'<span class="nav-icon">{icon(glyph)}</span>'
            f'<span class="nav-label">{html.escape(label)}</span></a>'
        )
    rail = (
        '<nav class="navbar" aria-label="Dashboard pages">'
        + "".join(items)
        + '<span class="nav-sep" aria-hidden="true"></span>'
        + rupevo_nav_link()
        + "</nav>"
    )
    return appbar + rail


def is_shell(fragment: str) -> bool:
    """True when `fragment` starts with the shell from `render_topnav`."""
    return fragment.lstrip().startswith('<header class="appbar"')


__all__ = ["NAV_ITEMS", "is_shell", "render_topnav"]
