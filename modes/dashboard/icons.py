"""Line icons for the dashboard shell, drawn on Rupevo's 24x24 grid.

The shared names (home, india, us, funds, tax, sun, moon, coffee, refresh,
info, spark, external, chevron-down) reuse Rupevo's `site/assets/js/icons.js`
paths so both products read as one family; the rest are drawn to match:
1.8px strokes in the text colour, round caps and joins.
"""

from __future__ import annotations

import html


_PATHS = {
    "home": '<path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z"/>',
    "portfolio": ('<rect x="3.5" y="7.5" width="17" height="12" rx="2"/>'
                  '<path d="M9 7.5V6a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 6v1.5M3.5 12.5h17"/>'),
    "india": '<path d="M4 17 9.5 11.5l3.5 3.5L20 8"/><path d="M14.5 8H20v5.5"/>',
    "us": ('<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.3 3.6 5.1 3.6 8.5'
           's-1.2 6.2-3.6 8.5c-2.4-2.3-3.6-5.1-3.6-8.5S9.6 5.8 12 3.5z"/>'),
    "funds": '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5V12l6 6"/>',
    "pulse": '<path d="M3.5 12h4l2.5-6.5 4 13 2.5-6.5h4"/>',
    "flask": ('<path d="M9.5 3.5h5M10.5 3.5v6L5 18.6a1.3 1.3 0 0 0 1.1 1.9h11.8a1.3 1.3 0 0 0 1.1-1.9'
              'L13.5 9.5v-6"/><path d="M7.6 14.5h8.8"/>'),
    "tax": ('<path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 20z"/>'
            '<path d="M14 3.5v4h4M10 12h5M10 15.5h5"/>'),
    "book": ('<path d="M12 6.5c-1.8-1.3-4.3-2-7.5-2v13c3.2 0 5.7.7 7.5 2 1.8-1.3 4.3-2 7.5-2v-13'
             'c-3.2 0-5.7.7-7.5 2z"/><path d="M12 6.5v13"/>'),
    "sun": ('<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2'
            'M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>'),
    "moon": '<path d="M19.5 14.5A7.5 7.5 0 1 1 9.5 4.5a6 6 0 0 0 10 10z"/>',
    "eye": ('<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/>'
            '<circle cx="12" cy="12" r="3"/>'),
    "eye-off": ('<path d="M10.6 5.6c.5-.1.9-.1 1.4-.1 6 0 9.5 6.5 9.5 6.5a17 17 0 0 1-2.9 3.7'
                'M6.6 6.6C4 8.3 2.5 12 2.5 12S6 18.5 12 18.5c1.9 0 3.6-.6 5-1.5"/>'
                '<path d="M9.9 9.9a3 3 0 0 0 4.2 4.2M3.5 3.5l17 17"/>'),
    "coffee": ('<path d="M4.5 10h11v3.5a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5z"/>'
               '<path d="M15.5 11.5H17a2.5 2.5 0 0 1 0 5h-1.6M8 4.5c-.7.8-.7 1.7 0 2.5'
               'M11.5 4.5c-.7.8-.7 1.7 0 2.5"/>'),
    "refresh": '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3L19.5 9"/><path d="M19.5 4.5V9H15"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.2"/>',
    "spark": '<path d="M12 3.5 13.8 9l5.7 1.5-5.7 1.6L12 17.5l-1.8-5.4-5.7-1.6L10.2 9z"/>',
    "external": ('<path d="M13.5 4.5H19.5v6M19.5 4.5 11 13M17.5 14v4.5a1 1 0 0 1-1 1h-11'
                 'a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1H10"/>'),
    "chevron-down": '<path d="m6 9.5 6 6 6-6"/>',
}


def icon(name: str, *, size: int = 24, cls: str = "") -> str:
    """Decorative SVG markup for `name` (hidden from assistive technology)."""
    classes = f"ic ic-{name}" + (f" {cls}" if cls else "")
    return (
        f'<svg class="{html.escape(classes)}" viewBox="0 0 24 24" width="{size}" '
        f'height="{size}" aria-hidden="true" focusable="false" fill="none" '
        'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{_PATHS[name]}</svg>'
    )


__all__ = ["icon"]
