"""
modes/dashboard/theme.py
========================

Shared dashboard design system (2026-07-30 UI revamp).

Before this module every page carried its own `:root { … }` block with
a slightly different palette, so /portfolio, /swing, /us, /tax and
/dryrun all looked like different apps.  This module is now the single
source of truth for colour, spacing, radius, shadow and the shared
component styles (cards, stat tiles, tables, buttons, chips, banners).

Usage from a page renderer::

    from modes.dashboard.theme import (
        theme_boot_script, theme_css, theme_overrides_css,
    )

    f"<head>{theme_boot_script()}"
    f"<style>{theme_css()}{PAGE_STYLE}{theme_overrides_css()}</style></head>"

Ordering matters:

* ``theme_css()``       — tokens + base element styles.  Goes FIRST so a
  page can still override anything it needs to.
* ``PAGE_STYLE``        — the page's own layout rules.
* ``theme_overrides_css()`` — the deliberate "make every page look the
  same" layer (buttons, cards, tables, nav, banners) plus every dark
  mode fix-up for hard-coded colours that live in inline ``style=``
  attributes scattered through the older page renderers.

Dark mode is driven by ``document.documentElement.dataset.theme``.
``theme_boot_script()`` must be emitted inside ``<head>`` *before* any
body content so the attribute is set before first paint (no flash of
light theme).  The user's choice is persisted in
``localStorage.dashTheme``; the default follows the OS preference.
"""

from __future__ import annotations


# ── Tokens + base ────────────────────────────────────────────────

def theme_css() -> str:
    """Design tokens and base element styles (emit before page CSS)."""
    return r"""
:root {
  color-scheme: light;
  /* Surfaces */
  --bg:            #eef2f9;
  --bg-tint:       radial-gradient(1200px 600px at 12% -8%, #dfe9ff 0%, rgba(223,233,255,0) 55%),
                   radial-gradient(1000px 520px at 92% 0%, #e6f6ef 0%, rgba(230,246,239,0) 52%);
  --card:          #ffffff;
  --card-2:        #fbfcfe;
  --soft:          #f1f5f9;
  --input-bg:      #ffffff;
  --line:          #e2e8f0;
  --line-strong:   #cbd5e1;

  /* Text */
  --fg:            #0f172a;
  --fg-2:          #334155;
  --muted:         #64748b;
  --faint:         #94a3b8;

  /* Brand / accent */
  --accent:        #2f5fe0;
  --accent-2:      #6d5ae0;
  --accent-fg:     #ffffff;
  --accent-soft:   #e8efff;
  --accent-line:   #c5d7ff;

  /* Semantics */
  --pos:           #0b8a5b;
  --pos-bg:        #e4f7ef;
  --pos-line:      #b6e6d2;
  --neg:           #d0342c;
  --neg-bg:        #fdecea;
  --neg-line:      #f6c5c0;
  --warn-fg:       #a5610a;
  --warn-bg:       #fff6e5;
  --warn-line:     #f6d7a3;
  --risk-fg:       #b91c1c;
  --risk-bg:       #fdecec;
  --risk-line:     #f4c0c0;
  --info-fg:       #1d4ed8;
  --info-bg:       #eaf1ff;
  --info-line:     #c9daff;

  /* Legacy aliases kept so old page CSS keeps resolving */
  --accent-legacy: #1c1f23;

  /* Shape */
  --radius:        14px;
  --radius-sm:     9px;
  --radius-pill:   999px;
  --shadow-sm:     0 1px 2px rgba(15,23,42,.05), 0 1px 3px rgba(15,23,42,.06);
  --shadow-md:     0 6px 18px -6px rgba(15,23,42,.16), 0 2px 6px rgba(15,23,42,.06);
  --shadow-lg:     0 18px 42px -18px rgba(15,23,42,.35);
  --ring:          0 0 0 3px rgba(47,95,224,.22);

  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
          "Helvetica Neue", Arial, sans-serif;
  --mono: "SF Mono", ui-monospace, "Cascadia Mono", Menlo, Consolas, monospace;
}

html[data-theme="dark"] {
  color-scheme: dark;
  --bg:            #0a1020;
  --bg-tint:       radial-gradient(1200px 600px at 12% -8%, #16244a 0%, rgba(22,36,74,0) 55%),
                   radial-gradient(1000px 520px at 92% 0%, #10312a 0%, rgba(16,49,42,0) 52%);
  --card:          #121a2b;
  --card-2:        #162034;
  --soft:          #1a2438;
  --input-bg:      #161f33;
  --line:          #253149;
  --line-strong:   #33425f;

  --fg:            #e6edf8;
  --fg-2:          #c6d2e4;
  --muted:         #93a3bb;
  --faint:         #6f819b;

  --accent:        #6f9bff;
  --accent-2:      #a08cff;
  --accent-fg:     #08122a;
  --accent-soft:   #1a2947;
  --accent-line:   #2e4472;

  --pos:           #34d39f;
  --pos-bg:        #0f2e26;
  --pos-line:      #1d5544;
  --neg:           #ff7b72;
  --neg-bg:        #331a1c;
  --neg-line:      #5f2b2b;
  --warn-fg:       #f0b45e;
  --warn-bg:       #33260f;
  --warn-line:     #5c451c;
  --risk-fg:       #ff8b84;
  --risk-bg:       #331a1c;
  --risk-line:     #5f2b2b;
  --info-fg:       #93b4ff;
  --info-bg:       #182444;
  --info-line:     #2c3f6b;

  --accent-legacy: #e6edf8;

  --shadow-sm:     0 1px 2px rgba(0,0,0,.4);
  --shadow-md:     0 8px 22px -8px rgba(0,0,0,.65);
  --shadow-lg:     0 22px 48px -20px rgba(0,0,0,.8);
  --ring:          0 0 0 3px rgba(111,155,255,.28);
}

* { box-sizing: border-box; }

html, body { min-height: 100%; }

body {
  margin: 0;
  padding: 22px 20px 48px;
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.5;
  color: var(--fg);
  background-color: var(--bg);
  background-image: var(--bg-tint);
  background-attachment: fixed;
  background-repeat: no-repeat;
  -webkit-font-smoothing: antialiased;
}

a { color: var(--accent); }

::selection { background: var(--accent-soft); color: var(--fg); }

:focus-visible {
  outline: none;
  box-shadow: var(--ring);
  border-radius: var(--radius-sm);
}

/* Scrollbars (WebKit) */
::-webkit-scrollbar { width: 11px; height: 11px; }
::-webkit-scrollbar-thumb {
  background: var(--line-strong); border-radius: 8px;
  border: 3px solid transparent; background-clip: content-box;
}
::-webkit-scrollbar-thumb:hover { background: var(--muted); background-clip: content-box; }
::-webkit-scrollbar-track { background: transparent; }

/* ── Shared components ─────────────────────────────────────── */

.t-grid { display: grid; gap: 14px; }
.t-row  { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }

.t-card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px;
}

.t-section-title {
  display: flex; align-items: baseline; gap: 10px;
  font-size: 12px; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted);
  margin: 26px 0 10px;
}
.t-section-title .hint {
  font-size: 11px; font-weight: 500; letter-spacing: 0;
  text-transform: none; color: var(--faint);
}

.t-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 10px; border-radius: var(--radius-pill);
  font-size: 11.5px; font-weight: 600; line-height: 1.6;
  border: 1px solid var(--line); background: var(--soft); color: var(--fg-2);
  white-space: nowrap;
}
.t-chip.pos  { background: var(--pos-bg);  color: var(--pos);      border-color: var(--pos-line); }
.t-chip.neg  { background: var(--neg-bg);  color: var(--neg);      border-color: var(--neg-line); }
.t-chip.warn { background: var(--warn-bg); color: var(--warn-fg);  border-color: var(--warn-line); }
.t-chip.info { background: var(--info-bg); color: var(--info-fg);  border-color: var(--info-line); }
.t-chip .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.t-chip .dot.live { animation: t-pulse 1.8s ease-in-out infinite; }
@keyframes t-pulse { 0%,100% { opacity: 1; } 50% { opacity: .25; } }

.pos { color: var(--pos); }
.neg { color: var(--neg); }
.muted { color: var(--muted); }
.num { font-variant-numeric: tabular-nums; }

/* Skeleton shimmer for values that load after first paint */
.t-skel {
  display: inline-block; min-width: 68px; height: 1em;
  border-radius: 5px; vertical-align: middle;
  background: linear-gradient(90deg, var(--soft) 25%, var(--line) 37%, var(--soft) 63%);
  background-size: 400% 100%;
  animation: t-shimmer 1.3s ease infinite;
}
@keyframes t-shimmer { 0% { background-position: 100% 50%; } 100% { background-position: 0 50%; } }

@media (prefers-reduced-motion: reduce) {
  * { animation-duration: .001ms !important; animation-iteration-count: 1 !important;
      transition-duration: .001ms !important; }
}
"""


# ── Deliberate cross-page polish + dark-mode fix-ups ─────────────

def theme_overrides_css() -> str:
    """Component polish applied AFTER each page's own CSS.

    Two jobs:
      1. Give every legacy page the same buttons / cards / tables /
         banners so the dashboard reads as one product.
      2. Neutralise hard-coded light-mode colours (`background: white`,
         `#f7f8fa`, `#1c1f23`, `#cfd9eb`, …) that older renderers bake
         into inline `style=` attributes, which would otherwise stay
         white-on-white in dark mode.  Inline styles can only be beaten
         with `!important`, hence its use below — it is confined to this
         block on purpose.
    """
    return r"""
/* ── Layout shells ───────────────────────────────────────────── */
.wrap { max-width: 1240px; margin: 0 auto; }
/* Some pages emit the nav / AI banner as direct <body> children while
   the content lives inside .wrap — centre them so nothing looks
   off-axis. */
body > nav.topnav,
body > .ai-widget,
body > .chat-widget,
body > .banner { max-width: 1240px; margin-left: auto; margin-right: auto; }

h1, h1.page-title {
  font-size: 25px; font-weight: 700; letter-spacing: -.015em;
  margin: 0 0 4px; color: var(--fg);
}
.sub { color: var(--muted); }

/* ── Cards ───────────────────────────────────────────────────── */
.card, .chart-card, .card-mini {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}
.card { padding: 16px 18px; }

/* ── Buttons ─────────────────────────────────────────────────── */
button.action, .btn {
  font: inherit; font-weight: 600; font-size: 13px;
  padding: 8px 14px; border-radius: var(--radius-sm);
  border: 1px solid transparent;
  background: linear-gradient(180deg, var(--accent), color-mix(in srgb, var(--accent) 86%, #000));
  color: var(--accent-fg) !important;
  cursor: pointer; transition: transform .06s ease, box-shadow .15s ease, filter .15s ease;
  box-shadow: var(--shadow-sm);
}
button.action:hover, .btn:hover { filter: brightness(1.06); box-shadow: var(--shadow-md); }
button.action:active, .btn:active { transform: translateY(1px); }
button.action[disabled], .btn[disabled] {
  opacity: .5; cursor: not-allowed; box-shadow: none; filter: none;
}
button.action.alt, .btn.alt {
  background: var(--card) !important;
  color: var(--fg) !important;
  border-color: var(--line-strong) !important;
}
button.action.alt:hover, .btn.alt:hover { background: var(--soft) !important; }

/* ── Form controls (incl. inline-styled legacy inputs) ───────── */
input[type="text"], input[type="number"], input[type="search"],
input[type="password"], input[type="date"], select, textarea {
  background: var(--input-bg) !important;
  color: var(--fg) !important;
  border: 1px solid var(--line-strong) !important;
  border-radius: var(--radius-sm) !important;
  font: inherit;
  padding: 7px 10px;
}
input::placeholder, textarea::placeholder { color: var(--faint); }
input:focus-visible, select:focus-visible, textarea:focus-visible {
  border-color: var(--accent) !important; box-shadow: var(--ring);
}

/* ── Tables ──────────────────────────────────────────────────── */
table.holdings, table.kvtable, table.grid {
  font-variant-numeric: tabular-nums;
}
table.holdings th {
  color: var(--muted) !important;
  border-bottom: 1px solid var(--line) !important;
  background: var(--card-2);
  position: sticky; top: 0; z-index: 1;
}
html[data-theme="dark"] table th { background: var(--card-2); }
table.holdings td { border-bottom: 1px solid var(--line) !important; }
table.holdings tr:hover td { background: var(--soft) !important; }
table.kvtable td { border-bottom: 1px dashed var(--line) !important; }

/* Wide tables (entry recommendations, open book) carry 12-15 columns and
   cannot compress below ~1100px without the cells colliding. Without a
   scroll container they burst out of the card instead, which is worse on
   small screens. Wrap those in .table-scroll. */
.table-scroll {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  /* Bleed to the card edges so the scrollbar is not inset awkwardly. */
  margin-inline: -18px;
  padding-inline: 18px;
}
.table-scroll > table.holdings { min-width: 1100px; }
.table-scroll > table.holdings th,
.table-scroll > table.holdings td { white-space: nowrap; }
/* Reason is free text and the only column that should wrap; capping it
   stops one long reason from stretching the whole table. A cell that
   keeps `nowrap` here overflows its box and the next cell paints on
   top of it, so every free-text column must carry `class="reason"`. */
.table-scroll > table.holdings td.reason {
  white-space: normal; overflow-wrap: anywhere;
  min-width: 180px; max-width: 260px;
}
/* A horizontal scroll container also traps vertical sticky positioning,
   so drop the sticky header rather than have it pin to the wrong box. */
.table-scroll > table.holdings th { position: static; }

/* ── Banners / flags ─────────────────────────────────────────── */
.banner { border-radius: var(--radius-sm); }
.banner.info { background: var(--info-bg) !important; border: 1px solid var(--info-line) !important;
               color: var(--fg) !important; }
.banner.warn { background: var(--warn-bg) !important; border: 1px solid var(--warn-line) !important;
               color: var(--warn-fg) !important; }
.banner.error { background: var(--risk-bg) !important; border: 1px solid var(--risk-line) !important;
                color: var(--risk-fg) !important; }
.flag.INFO { background: var(--info-bg) !important; color: var(--fg) !important; }
.flag.WARN { background: var(--warn-bg) !important; color: var(--warn-fg) !important; }
.flag.RISK { background: var(--risk-bg) !important; color: var(--risk-fg) !important; }

code, pre { background: var(--soft) !important; color: var(--fg-2) !important;
            border-radius: 5px; }

hr { border: none; border-top: 1px solid var(--line); }

/* ── Nav (rendered by modes/dashboard/nav.py) ────────────────── */
nav.topnav {
  position: sticky; top: 8px; z-index: 40;
  background: color-mix(in srgb, var(--card) 96%, transparent) !important;
  backdrop-filter: saturate(1.6) blur(12px);
  -webkit-backdrop-filter: saturate(1.6) blur(12px);
  border: 1px solid var(--line) !important;
  border-radius: var(--radius) !important;
  box-shadow: var(--shadow-md);
  padding: 8px 12px !important;
  gap: 4px !important;
}
nav.topnav .sep { display: none; }
nav.topnav a {
  padding: 6px 11px; border-radius: var(--radius-pill);
  color: var(--fg-2) !important; font-weight: 600; font-size: 13px;
  text-decoration: none !important; white-space: nowrap;
  transition: background .12s ease, color .12s ease;
}
nav.topnav a:hover { background: var(--soft); color: var(--fg) !important; }
nav.topnav .here {
  padding: 6px 11px; border-radius: var(--radius-pill);
  background: var(--accent-soft) !important; color: var(--accent) !important;
  font-weight: 700; font-size: 13px; white-space: nowrap;
  border: 1px solid var(--accent-line);
}
nav.topnav button.nav-back {
  background: var(--card) !important; color: var(--fg) !important;
  border: 1px solid var(--line-strong) !important;
  border-radius: var(--radius-pill) !important;
  padding: 5px 12px !important; font-weight: 600; font-size: 13px;
}
nav.topnav button.nav-back:hover { background: var(--soft) !important; }
nav.topnav .auth { font-weight: 600; border: 1px solid transparent; }
nav.topnav .auth.ok  { background: var(--pos-bg) !important; color: var(--pos) !important;
                       border-color: var(--pos-line) !important; }
nav.topnav .auth.bad { background: var(--risk-bg) !important; color: var(--risk-fg) !important;
                       border-color: var(--risk-line) !important; }
nav.topnav .research { background: var(--info-bg) !important; color: var(--info-fg) !important;
                       border-color: var(--info-line) !important; }
nav.topnav .fx-badge {
  font-size: 12px; padding: 4px 10px; border-radius: var(--radius-pill);
  background: var(--soft); color: var(--fg-2); border: 1px solid var(--line);
}

/* Theme switch */
button.theme-toggle {
  font: inherit; font-size: 15px; line-height: 1; cursor: pointer;
  width: 32px; height: 32px; display: inline-flex;
  align-items: center; justify-content: center;
  border-radius: var(--radius-pill);
  border: 1px solid var(--line-strong);
  background: var(--card); color: var(--fg);
}
button.theme-toggle:hover { background: var(--soft); }
html[data-theme="dark"] .theme-toggle-sun { display: none; }
html:not([data-theme="dark"]) .theme-toggle-moon { display: none; }

/* ── Dark-mode rescues for hard-coded light colours ──────────── */
html[data-theme="dark"] .tile,
html[data-theme="dark"] .history-strip .tile,
html[data-theme="dark"] .sugg-grid .card-mini {
  background: var(--card) !important; border-color: var(--line) !important;
}
html[data-theme="dark"] .tile:hover { background: var(--soft) !important; }

/* Inline `style="…"` swatches used by the older renderers.  Grouped by
   the semantic they were standing in for, so a dark reader gets the
   same meaning rather than white-on-white. */
html[data-theme="dark"] [style*="background:#fff"],
html[data-theme="dark"] [style*="background: #fff"],
html[data-theme="dark"] [style*="background:white"],
html[data-theme="dark"] [style*="background: white"],
html[data-theme="dark"] [style*="#fafbfc"],
html[data-theme="dark"] [style*="#f7f8fa"],
html[data-theme="dark"] [style*="#f6f8fa"] {
  background: var(--card) !important;
}
html[data-theme="dark"] [style*="#e6f4ea"] {
  background: var(--pos-bg) !important; color: var(--pos) !important;
}
html[data-theme="dark"] [style*="#fff4cc"],
html[data-theme="dark"] [style*="#fff4e0"],
html[data-theme="dark"] [style*="#fff8e1"] {
  background: var(--warn-bg) !important; color: var(--warn-fg) !important;
  border-color: var(--warn-line) !important;
}
html[data-theme="dark"] [style*="#fde8e8"],
html[data-theme="dark"] [style*="#fdecec"],
html[data-theme="dark"] [style*="#fff0f0"] {
  background: var(--risk-bg) !important; color: var(--risk-fg) !important;
}
html[data-theme="dark"] [style*="#eef4ff"],
html[data-theme="dark"] [style*="#eef2ff"],
html[data-theme="dark"] [style*="#eef3ff"] {
  background: var(--info-bg) !important; color: var(--fg) !important;
}
html[data-theme="dark"] [style*="color:#1c1f23"],
html[data-theme="dark"] [style*="color: #1c1f23"],
html[data-theme="dark"] [style*="color:#5d6b82"],
html[data-theme="dark"] [style*="color:#666"],
html[data-theme="dark"] [style*="color:#555"] { color: var(--muted) !important; }
html[data-theme="dark"] [style*="border:1px solid #cfd9eb"],
html[data-theme="dark"] [style*="border:1px solid #e5e7eb"],
html[data-theme="dark"] [style*="border:1px solid #ccc"],
html[data-theme="dark"] [style*="border:1px solid #d0d7de"],
html[data-theme="dark"] [style*="border-top:1px solid #e5e7eb"] {
  border-color: var(--line) !important;
}

/* Page-CSS classes that bake in a light palette. */
html[data-theme="dark"] .pill.ok,
html[data-theme="dark"] .chip.ok { background: var(--pos-bg); color: var(--pos); }
html[data-theme="dark"] .pill.warn { background: var(--warn-bg); color: var(--warn-fg); }
html[data-theme="dark"] .pill.neg { background: var(--neg-bg); color: var(--neg); }
html[data-theme="dark"] .rebate-note { background: var(--pos-bg); color: var(--pos); }
html[data-theme="dark"] .big-value,
html[data-theme="dark"] .big-label,
html[data-theme="dark"] .big-sub { color: var(--warn-fg); }
html[data-theme="dark"] .copy:hover { background: var(--soft); }
html[data-theme="dark"] .copy.copied { background: var(--pos-bg); color: var(--pos); }
html[data-theme="dark"] .doc-section blockquote,
html[data-theme="dark"] .doc-section .math-block { background: var(--soft); color: var(--fg); }
html[data-theme="dark"] .doc-section table.md-table th { background: var(--card-2); }
html[data-theme="dark"] .doc-section table.md-table tr:hover td { background: var(--soft); }
html[data-theme="dark"] .flag.INFO { background: var(--info-bg) !important; }
html[data-theme="dark"] .banner-cta { background: var(--accent); color: var(--accent-fg); }

html[data-theme="dark"] .sectorbar { background: var(--accent) !important; }
html[data-theme="dark"] img:not([src*=".svg"]) { filter: brightness(.92); }

input[type="checkbox"], input[type="radio"] { accent-color: var(--accent); }

/* Charts read these via getComputedStyle in page scripts */
.chart-card canvas { max-height: 300px; }

@media (max-width: 820px) {
  body { padding: 14px 12px 40px; }
  nav.topnav { position: static; }
  h1, h1.page-title { font-size: 21px; }
  /* The nav is a flex row whose link group defaults to min-width:auto, so
     it refused to shrink below its max-content width and alone forced
     ~500px of horizontal page scroll even after the tables were fixed.
     `display:flex` is restated here because pages that predate nav.py's
     stylesheet (swing) render .nav-links as a plain block, where
     flex-wrap would silently do nothing. */
  nav.topnav { flex-wrap: wrap; row-gap: 6px; }
  nav.topnav .nav-links {
    display: flex; flex-wrap: wrap; row-gap: 6px;
    min-width: 0; max-width: 100%;
  }
  nav.topnav .spacer { flex-basis: 100%; height: 0; }
  .card { padding: 12px 12px; }
  .table-scroll { margin-inline: -12px; padding-inline: 12px; }
}
"""


# ── Boot script (must be in <head>, before body paint) ───────────

def theme_boot_script() -> str:
    """Sets `data-theme` before first paint and exposes `toggleTheme()`."""
    return r"""
<script>
(function () {
  var KEY = 'dashTheme';
  function apply(mode) {
    document.documentElement.setAttribute('data-theme', mode);
  }
  function preferred() {
    try {
      var saved = window.localStorage.getItem(KEY);
      if (saved === 'dark' || saved === 'light') return saved;
    } catch (e) { /* private mode */ }
    try {
      if (window.matchMedia &&
          window.matchMedia('(prefers-color-scheme: dark)').matches) return 'dark';
    } catch (e) { /* older browsers */ }
    return 'light';
  }
  apply(preferred());
  window.toggleTheme = function () {
    var next = document.documentElement.getAttribute('data-theme') === 'dark'
      ? 'light' : 'dark';
    apply(next);
    try { window.localStorage.setItem(KEY, next); } catch (e) {}
    applyChartTheme();
    // Let any page-specific chart code restyle itself too.
    window.dispatchEvent(new CustomEvent('dashthemechange', { detail: next }));
  };
  window.isDarkTheme = function () {
    return document.documentElement.getAttribute('data-theme') === 'dark';
  };

  // Chart.js reads its label/grid colours from `Chart.defaults`, which
  // default to near-black — unreadable on the dark surface. Sync them
  // with the tokens before any page script builds a chart (this
  // listener registers first because the theme script lives in <head>),
  // and again whenever the user flips the theme.
  function applyChartTheme() {
    if (typeof Chart === 'undefined') return;
    var css = getComputedStyle(document.documentElement);
    var fg = css.getPropertyValue('--fg-2').trim();
    var grid = css.getPropertyValue('--line').trim();
    if (fg) Chart.defaults.color = fg;
    if (grid) Chart.defaults.borderColor = grid;
    try {
      var live = Chart.instances || {};
      Object.keys(live).forEach(function (k) {
        var c = live[k];
        if (c && typeof c.update === 'function') c.update('none');
      });
    } catch (e) { /* chart teardown races — never block the toggle */ }
  }
  window.applyChartTheme = applyChartTheme;
  document.addEventListener('DOMContentLoaded', applyChartTheme);
})();
</script>
"""


def theme_toggle_html() -> str:
    """Small sun/moon button — drop into the nav's `after_links`."""
    return (
        '<button class="theme-toggle" type="button" onclick="toggleTheme()" '
        'title="Switch between light and dark theme" '
        'aria-label="Toggle colour theme">'
        '<span class="theme-toggle-sun" aria-hidden="true">&#9788;</span>'
        '<span class="theme-toggle-moon" aria-hidden="true">&#9789;</span>'
        '</button>'
    )


def head_common(title: str, *, chartjs: bool = False,
                extra_style: str = "") -> str:
    """Full `<head>` for a themed page.

    `extra_style` is the page's own CSS; it is sandwiched between the
    token layer and the override layer (see module docstring).
    """
    chart = ('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/'
             'dist/chart.umd.min.js"></script>') if chartjs else ""
    import html as _html
    return (
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_html.escape(title)}</title>"
        + theme_boot_script()
        + chart
        + "<style>"
        + theme_css()
        + extra_style
        + theme_overrides_css()
        + "</style>"
        "</head>"
    )


__all__ = [
    "head_common",
    "theme_boot_script",
    "theme_css",
    "theme_overrides_css",
    "theme_toggle_html",
]
