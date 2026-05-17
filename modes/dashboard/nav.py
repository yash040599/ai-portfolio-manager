"""Shared dashboard navigation chrome."""

from __future__ import annotations

import html


NAV_ITEMS = [
    ("Portfolio", "/portfolio"),
    ("Swing", "/swing"),
    ("Trading (Live P&L)", "/trading"),
    ("Chan", "/chan"),
    ("Tax", "/tax"),
    ("Theory", "/theory/statistics"),
]


def _active(here: str, label: str, href: str) -> bool:
    marker = (here or "").strip().lower()
    if not marker:
        return False
    if marker == label.lower() or marker == href.lower():
        return True
    if marker.startswith(href.lower() + "/"):
        return True
    if href.startswith("/theory/") and marker.startswith("/theory/"):
        return True
    return label.lower().startswith(marker)


def render_topnav(here: str = "", *, after_links: str = "") -> str:
    """Render a full dashboard nav plus a browser Back control."""
    parts = [
        '<button class="nav-back" type="button" '
        'onclick="if (window.history.length > 1) { window.history.back(); } '
        'else { window.location.href=\'/portfolio\'; }">Back</button>'
    ]
    for label, href in NAV_ITEMS:
        parts.append('<span class="sep">&middot;</span>')
        if _active(here, label, href):
            parts.append(f'<span class="here">{html.escape(label)}</span>')
        else:
            parts.append(f'<a href="{href}">{html.escape(label)}</a>')
    return (
        '<nav class="topnav">'
        + "".join(parts)
        + '<span class="spacer"></span>'
        + after_links
        + '</nav>'
    )


def topnav_css() -> str:
    return """
nav.topnav { display: flex; gap: 14px; align-items: center;
             padding: 10px 16px; background: var(--card);
             border: 1px solid var(--line); border-radius: 8px;
             margin-bottom: 18px; font-size: 14px; flex-wrap: wrap; }
nav.topnav a,
nav.topnav button.nav-back { color: var(--fg); text-decoration: none;
                             font-weight: 500; }
nav.topnav a:hover { text-decoration: underline; }
nav.topnav button.nav-back { font: inherit; padding: 4px 9px;
                             border: 1px solid var(--line);
                             border-radius: 5px; background: white;
                             cursor: pointer; }
nav.topnav button.nav-back:hover { background: var(--soft); }
nav.topnav .here { color: var(--muted); cursor: default; }
nav.topnav .sep { color: var(--muted); }
nav.topnav .spacer { flex: 1; }
"""


__all__ = ["NAV_ITEMS", "render_topnav", "topnav_css"]