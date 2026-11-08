"""
modes/dashboard/theme.py
========================

Shared dashboard design system.

Since the 2026-11 redesign the dashboard speaks the same Google Material 3
language as Rupevo, its public companion site: flat white surfaces, outlined
cards, soft tonal tiles, pill buttons, light display headings and an app
shell (a fixed top app bar plus a navigation rail that becomes a bottom bar
on phones, rendered by ``nav.py``). This module is the single source of
truth for colour, type, spacing, radius and the shared component styles.
Token names from the 2026-07-30 revamp (``--risk-*``, ``--accent-2``,
``--bg-tint``, ``--accent-legacy``) are kept, so older page CSS keeps
resolving.

Usage from a page renderer::

    from modes.dashboard.theme import (
        theme_boot_script, theme_css, theme_overrides_css,
    )

    f"<head>{theme_boot_script()}"
    f"<style>{theme_css()}{PAGE_STYLE}{theme_overrides_css()}</style></head>"

Ordering matters:

* ``theme_css()``       — tokens, base element styles and the shared
  components (Rupevo card, coffee dialog, sortable headers, Ask AI). Goes
  FIRST so a page can still override anything it needs to.
* ``PAGE_STYLE``        — the page's own layout rules.
* ``theme_overrides_css()`` — the app shell plus the deliberate "make every
  page look the same" layer (headings, cards, buttons, tables, banners) and
  every dark-mode fix-up for hard-coded colours that live in inline
  ``style=`` attributes scattered through the older page renderers.

Dark mode is driven by ``document.documentElement.dataset.theme``.
``theme_boot_script()`` must be emitted inside ``<head>`` *before* any
body content so the attribute is set before first paint (no flash of
light theme).  The user's choice is persisted in
``localStorage.dashTheme``; the default follows the OS preference.
"""

from __future__ import annotations

from modes.dashboard.ai_widget import ai_widget_css
from modes.dashboard.chat_widget import chat_widget_css
from modes.dashboard.coffee import coffee_css, coffee_script
from modes.dashboard.icons import icon
from modes.dashboard.privacy import privacy_boot_script
from modes.dashboard.rupevo import rupevo_css
from modes.dashboard.table_sort import table_sort_css, table_sort_script


# ── Tokens + base ────────────────────────────────────────────────

def theme_css() -> str:
    """Design tokens and base element styles (emit before page CSS)."""
    return r"""
:root {
  color-scheme: light;
  /* Surfaces */
  --bg:            #ffffff;
  --bg-tint:       none;
  --card:          #ffffff;
  --card-2:        #f8fafd;
  --soft:          #f0f4f9;
  --soft-2:        #e1e7ef;
  --input-bg:      #ffffff;
  --line:          #e3e3e3;
  --line-strong:   #c4c7c5;

  /* Text */
  --fg:            #1f1f1f;
  --fg-2:          #444746;
  --muted:         #5f6368;
  --faint:         #80868b;

  /* Accent: --accent-2 equals --accent, so legacy gradients render flat */
  --accent:        #0b57d0;
  --accent-2:      #0b57d0;
  --accent-fg:     #ffffff;
  --accent-soft:   #e8f0fe;
  --accent-line:   #a8c7fa;
  --tonal:         #d3e3fd;
  --on-tonal:      #041e49;
  --nav-active:    #c2e7ff;
  --on-nav-active: #001d35;

  /* Semantics */
  --pos:           #137333;
  --pos-bg:        #e6f4ea;
  --pos-line:      #ceead6;
  --neg:           #c5221f;
  --neg-bg:        #fce8e6;
  --neg-line:      #fad2cf;
  --warn-fg:       #8f4f00;
  --warn-bg:       #fef7e0;
  --warn-line:     #fde293;
  --risk-fg:       #c5221f;
  --risk-bg:       #fce8e6;
  --risk-line:     #fad2cf;
  --info-fg:       #0b57d0;
  --info-bg:       #e8f0fe;
  --info-line:     #d2e3fc;

  /* Legacy alias kept so old page CSS keeps resolving */
  --accent-legacy: #1f1f1f;

  /* Shape */
  --radius:        16px;
  --radius-md:     12px;
  --radius-sm:     8px;
  --radius-pill:   999px;
  --shadow-sm:     0 1px 2px rgba(60,64,67,.3), 0 1px 3px 1px rgba(60,64,67,.15);
  --shadow-md:     0 1px 3px rgba(60,64,67,.3), 0 4px 8px 3px rgba(60,64,67,.15);
  --shadow-lg:     0 2px 3px rgba(60,64,67,.3), 0 6px 10px 4px rgba(60,64,67,.15);
  --ring:          0 0 0 2px var(--accent);

  /* Type (Google Sans where installed, the system UI font elsewhere) */
  --font: "Google Sans Text", "Google Sans", Roboto, system-ui, -apple-system,
          BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  --font-display: "Google Sans", "Google Sans Display", Roboto, system-ui,
          -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  --mono: "Google Sans Mono", "Roboto Mono", ui-monospace, "SF Mono",
          "Cascadia Mono", Menlo, Consolas, monospace;

  /* App shell (nav.py) */
  --wrap:          1240px;
  --appbar-h:      64px;
  --rail-w:        88px;
  --navbar-h:      64px;
  --gutter:        24px;
}

html[data-theme="dark"] {
  color-scheme: dark;
  --bg:            #131314;
  --bg-tint:       none;
  --card:          #1b1c1d;
  --card-2:        #1e1f20;
  --soft:          #282a2c;
  --soft-2:        #37393b;
  --input-bg:      #1b1c1d;
  --line:          #3c4043;
  --line-strong:   #5f6368;

  --fg:            #e3e3e3;
  --fg-2:          #c4c7c5;
  --muted:         #9aa0a6;
  --faint:         #80868b;

  --accent:        #a8c7fa;
  --accent-2:      #a8c7fa;
  --accent-fg:     #062e6f;
  --accent-soft:   rgba(168,199,250,.14);
  --accent-line:   rgba(168,199,250,.4);
  --tonal:         #004a77;
  --on-tonal:      #c2e7ff;
  --nav-active:    #004a77;
  --on-nav-active: #c2e7ff;

  --pos:           #81c995;
  --pos-bg:        rgba(129,201,149,.14);
  --pos-line:      rgba(129,201,149,.35);
  --neg:           #f28b82;
  --neg-bg:        rgba(242,139,130,.14);
  --neg-line:      rgba(242,139,130,.35);
  --warn-fg:       #fdd663;
  --warn-bg:       rgba(253,214,99,.12);
  --warn-line:     rgba(253,214,99,.32);
  --risk-fg:       #f28b82;
  --risk-bg:       rgba(242,139,130,.14);
  --risk-line:     rgba(242,139,130,.35);
  --info-fg:       #a8c7fa;
  --info-bg:       rgba(168,199,250,.12);
  --info-line:     rgba(168,199,250,.32);

  --accent-legacy: #e3e3e3;

  --shadow-sm:     0 1px 2px rgba(0,0,0,.5), 0 1px 3px 1px rgba(0,0,0,.3);
  --shadow-md:     0 1px 3px rgba(0,0,0,.5), 0 4px 8px 3px rgba(0,0,0,.3);
  --shadow-lg:     0 2px 3px rgba(0,0,0,.5), 0 6px 10px 4px rgba(0,0,0,.35);
}

* { box-sizing: border-box; }

html { -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
html, body { min-height: 100%; }

body {
  margin: 0;
  padding: 24px 20px 48px;
  font-family: var(--font);
  font-size: 14px;
  line-height: 1.45;
  color: var(--fg);
  background: var(--bg);
  -webkit-font-smoothing: antialiased;
  -webkit-tap-highlight-color: transparent;
}

h1, h2, h3 { font-family: var(--font-display); }
a { color: var(--accent); text-underline-offset: 2px; }
::selection { background: var(--tonal); color: var(--on-tonal); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
svg.ic { display: block; flex: none; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
           overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }

/* Scrollbars (WebKit) */
::-webkit-scrollbar { width: 11px; height: 11px; }
::-webkit-scrollbar-thumb {
  background: var(--line-strong); border-radius: 8px;
  border: 3px solid transparent; background-clip: content-box;
}
::-webkit-scrollbar-thumb:hover { background: var(--muted); background-clip: content-box; }
::-webkit-scrollbar-track { background: transparent; }

/* ── Shared components ─────────────────────────────────────── */

.t-grid { display: grid; gap: 16px; }
.t-row  { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }

.t-card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 20px 24px;
}

.t-section-title {
  display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 12px;
  margin: 28px 0 12px; font-family: var(--font-display);
  font-size: 18px; line-height: 24px; font-weight: 500; color: var(--fg);
}
.t-section-title .hint {
  font-family: var(--font); font-size: 12.5px; font-weight: 400; color: var(--muted);
}

.t-chip {
  display: inline-flex; align-items: center; gap: 6px; min-height: 26px;
  padding: 3px 10px; border-radius: var(--radius-sm);
  font-size: 12.5px; line-height: 18px; font-weight: 500;
  border: 1px solid var(--line); background: transparent; color: var(--fg-2);
  white-space: nowrap; text-decoration: none;
}
.t-chip.pos  { background: var(--pos-bg);  color: var(--pos);     border-color: transparent; }
.t-chip.neg  { background: var(--neg-bg);  color: var(--neg);     border-color: transparent; }
.t-chip.warn { background: var(--warn-bg); color: var(--warn-fg); border-color: transparent; }
.t-chip.info { background: var(--info-bg); color: var(--info-fg); border-color: transparent; }
.t-chip .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; flex: none; }
.t-chip .dot.live { animation: t-pulse 1.8s ease-in-out infinite; }
@keyframes t-pulse { 0%,100% { opacity: 1; } 50% { opacity: .25; } }
/* A status chip beside a page title (e.g. "AI run", "HELD") */
h1 .title-chip { margin-left: 8px; vertical-align: middle; font-family: var(--font); }

.pos { color: var(--pos); }
.neg { color: var(--neg); }
.muted { color: var(--muted); }
.num { font-variant-numeric: tabular-nums; }

/* Page and card heads, as on Rupevo */
.page-head { margin: 4px 0 20px; }
.eyebrow { margin-bottom: 4px; font-size: 12px; line-height: 16px; font-weight: 500;
           letter-spacing: .02em; color: var(--muted); }
.page-head h1 { margin: 0; font-size: 28px; line-height: 36px; font-weight: 400;
                letter-spacing: 0; color: var(--fg); }
.page-head .lede { margin: 6px 0 0; max-width: 760px; color: var(--fg-2);
                   font-size: 15px; line-height: 22px; }
.card-head { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 12px;
             margin-bottom: 12px; }
.card-head h2 { margin: 0; font-size: 18px; line-height: 24px; font-weight: 500;
                letter-spacing: 0; text-transform: none; color: var(--fg); }
.card-head .hint { font-size: 12.5px; color: var(--muted); }
.card-head .spacer { flex: 1; }

/* Icon and text buttons */
.icon-btn {
  flex: none; width: 40px; height: 40px; display: inline-grid; place-items: center;
  padding: 0; border: 0; border-radius: 50%; background: transparent;
  color: var(--fg-2); cursor: pointer; font: inherit;
  transition: background-color .15s ease, color .15s ease;
}
.icon-btn:hover { background: var(--soft); color: var(--fg); }
.icon-btn[aria-pressed="true"] { background: var(--tonal); color: var(--on-tonal); }
.text-btn {
  font: inherit; font-size: 13px; font-weight: 500; line-height: 20px;
  display: inline-flex; align-items: center; gap: 6px; min-height: 32px;
  padding: 6px 12px; border: 0; border-radius: var(--radius-pill);
  background: none; color: var(--accent); cursor: pointer; text-decoration: none;
}
.text-btn:hover { background: var(--accent-soft); }
.text-btn[disabled] { opacity: .5; cursor: default; background: none; }

/* Material 3 switch: <label class="switch"><input type="checkbox"> Label</label> */
.switch { display: inline-flex; align-items: center; gap: 8px; font-size: 13px;
          color: var(--fg-2); cursor: pointer; user-select: none; }
.switch input {
  appearance: none; -webkit-appearance: none; position: relative; flex: none;
  width: 36px; height: 20px; margin: 0; border-radius: 999px; cursor: pointer;
  border: 2px solid var(--line-strong); background: var(--soft-2);
  transition: background-color .15s ease, border-color .15s ease;
}
.switch input::before {
  content: ""; position: absolute; top: 2px; left: 2px; width: 12px; height: 12px;
  border-radius: 50%; background: var(--muted);
  transition: transform .15s ease, background-color .15s ease;
}
.switch input:checked { background: var(--accent); border-color: var(--accent); }
.switch input:checked::before { transform: translateX(16px); background: var(--accent-fg); }

/* Skeleton shimmer for values that load after first paint */
.t-skel {
  display: inline-block; min-width: 68px; height: 1em;
  border-radius: 5px; vertical-align: middle;
  background: linear-gradient(90deg, var(--soft) 25%, var(--soft-2) 37%, var(--soft) 63%);
  background-size: 400% 100%;
  animation: t-shimmer 1.3s ease infinite;
}
@keyframes t-shimmer { 0% { background-position: 100% 50%; } 100% { background-position: 0 50%; } }

@media (prefers-reduced-motion: reduce) {
  * { animation-duration: .001ms !important; animation-iteration-count: 1 !important;
      transition-duration: .001ms !important; }
}
""" + rupevo_css() + coffee_css() + table_sort_css() + chat_widget_css() + ai_widget_css()


# ── App shell + cross-page polish + dark-mode fix-ups ────────────

def theme_overrides_css() -> str:
    """App shell and component polish applied AFTER each page's own CSS.

    Three jobs:
      1. Lay out the app shell from ``nav.py``: a fixed top app bar and a
         navigation rail (a bottom bar on phones). The body is padded only
         when the shell is present (``body:has(.appbar)``), which also
         outranks every page's own ``body { padding }`` rule.
      2. Give every legacy page the same headings / cards / buttons /
         tables / banners so the dashboard reads as one product.
      3. Neutralise hard-coded light-mode colours (`background: white`,
         `#f7f8fa`, `#1c1f23`, `#cfd9eb`, …) that older renderers bake
         into inline `style=` attributes, which would otherwise stay
         white-on-white in dark mode.  Inline styles can only be beaten
         with `!important`, hence its use below — it is confined to this
         block on purpose.
    """
    return r"""
/* ── App shell (rendered by modes/dashboard/nav.py) ──────────── */
html:has(.appbar) { scroll-padding-top: calc(var(--appbar-h) + 16px); }
body:has(.appbar) {
  padding: calc(var(--appbar-h) + 8px) var(--gutter) 48px calc(var(--rail-w) + var(--gutter));
}
.appbar {
  position: fixed; top: 0; left: 0; right: 0; z-index: 50;
  display: flex; align-items: center; gap: 4px;
  height: var(--appbar-h); padding: 0 16px 0 23px;
  background: var(--bg); border-bottom: 1px solid transparent;
  font-family: var(--font); font-size: 14px; line-height: 20px; color: var(--fg);
  transition: border-color .2s ease;
}
.appbar.scrolled { border-bottom-color: var(--line); }
.appbar .brand {
  display: inline-flex; align-items: center; gap: 10px; flex: none;
  padding: 6px 12px 6px 6px; border-radius: var(--radius-pill);
  color: var(--fg); text-decoration: none; white-space: nowrap;
  font-family: var(--font-display); font-size: 20px; font-weight: 500; letter-spacing: -.01em;
}
.appbar .brand:hover { background: var(--soft); }
.brand-mark {
  width: 30px; height: 30px; border-radius: 10px; display: inline-grid; place-items: center;
  flex: none; background: var(--accent); color: var(--accent-fg);
  font-family: var(--font-display); font-size: 16px; font-weight: 700; letter-spacing: 0;
}
.appbar-spacer { flex: 1; min-width: 8px; }
.appbar-status { display: flex; align-items: center; gap: 8px; min-width: 0; margin-right: 4px; }
.privacy-toggle .ic-eye-off { display: none; }
html[data-privacy="on"] .privacy-toggle .ic-eye { display: none; }
html[data-privacy="on"] .privacy-toggle .ic-eye-off { display: block; }
.theme-sun, .theme-moon { display: grid; }
html[data-theme="dark"] .theme-sun { display: none; }
html:not([data-theme="dark"]) .theme-moon { display: none; }

/* Page status pills passed in as `after_links` */
.appbar .auth, .appbar .research {
  display: inline-flex; align-items: center; gap: 4px; min-height: 32px; padding: 0 12px;
  border: 0; border-radius: var(--radius-sm); font-size: 13px; font-weight: 500;
  text-decoration: none; white-space: nowrap;
}
.appbar .auth strong { font-weight: 500; }
.appbar .auth.ok  { background: var(--pos-bg); color: var(--pos); }
.appbar .auth.bad { background: var(--neg-bg); color: var(--neg); }
.appbar .auth:hover { box-shadow: inset 0 0 0 1px currentColor; }
.appbar .research { max-width: 280px; overflow: hidden; text-overflow: ellipsis;
                    background: var(--info-bg); color: var(--info-fg); }
.appbar .fx-badge {
  font-size: 12.5px; padding: 4px 10px; border: 0; border-radius: var(--radius-pill);
  background: var(--soft); color: var(--fg-2); white-space: nowrap;
}
.appbar .fx-badge strong { font-weight: 500; color: var(--fg); }
.appbar .cur-toggle {
  display: inline-flex; align-items: center; gap: 2px; padding: 2px; margin: 0 2px;
  border-radius: var(--radius-pill); border: 1px solid var(--line-strong);
  background: transparent; cursor: pointer; font: inherit; overflow: visible;
}
.appbar .cur-pill {
  min-width: 34px; padding: 2px 8px; border-radius: var(--radius-pill); line-height: 18px;
  font-size: 12.5px; font-weight: 600; color: var(--muted); text-align: center;
}
.appbar .cur-pill.active { background: var(--tonal); color: var(--on-tonal); }
.appbar .docs-pick { display: flex; align-items: center; gap: 8px; min-width: 0; }
.appbar .docs-pick label { margin: 0; font-size: 12.5px; color: var(--muted);
                           text-transform: none; letter-spacing: 0; }
.appbar .docs-pick select {
  width: 240px; max-width: 100%; padding: 6px 12px; font-size: 13px;
  border-radius: var(--radius-pill) !important; cursor: pointer;
}

/* Navigation: a rail on wide screens, a bottom bar on phones */
.navbar { position: fixed; z-index: 45; display: flex; font-family: var(--font); }
.nav-item {
  display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 0;
  color: var(--fg-2); text-decoration: none; font-size: 12px; line-height: 16px; font-weight: 500;
}
.nav-item:hover { color: var(--fg); }
.nav-icon { display: grid; place-items: center; width: 56px; height: 32px; border-radius: 16px;
            transition: background-color .2s ease; }
.nav-item:hover .nav-icon { background: var(--soft-2); }
.nav-item[aria-current="page"] { color: var(--fg); font-weight: 700; }
.nav-item[aria-current="page"] .nav-icon { background: var(--nav-active); color: var(--on-nav-active); }
.nav-label { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nav-sep { flex: none; width: 32px; height: 1px; background: var(--line); }

@media (min-width: 840px) {
  .navbar {
    top: var(--appbar-h); bottom: 0; left: 0; width: var(--rail-w);
    flex-direction: column; align-items: center; gap: 12px; padding: 8px 0 16px;
    background: var(--bg); overflow-y: auto; scrollbar-width: none;
  }
  .navbar::-webkit-scrollbar { display: none; }
  .nav-item { width: 100%; }
}

/* ── Layout ──────────────────────────────────────────────────── */
.wrap { max-width: var(--wrap); margin: 0 auto; }
/* Some pages emit the AI banner or a card as direct <body> children while
   the content lives inside .wrap — centre them so nothing looks off-axis. */
body > .ai-widget,
body > .chat-widget,
body > .banner,
body > .rv-card { max-width: var(--wrap); margin-left: auto; margin-right: auto; }

/* ── Type: light display headings, sentence-case section titles ── */
h1, h1.page-title {
  font-family: var(--font-display); font-size: 28px; line-height: 36px;
  font-weight: 400; letter-spacing: 0; margin: 4px 0 6px; color: var(--fg);
}
h2 {
  font-family: var(--font-display); font-size: 18px; line-height: 24px;
  font-weight: 500; letter-spacing: 0; text-transform: none; color: var(--fg);
}
.sub { color: var(--muted); }

/* ── Cards: outlined, flat ───────────────────────────────────── */
.card, .chart-card, .card-mini, .summary-card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: none;
}
.card { padding: 20px 24px; }
.card > h2:first-child, .card > h3:first-child { margin-top: 0; }

/* Chart titles: sentence case, like Rupevo's card heads */
.chart-card h3, .chart-grid .chart-card h3, .chart-card .title {
  font-family: var(--font-display); font-size: 14px; line-height: 20px; font-weight: 500;
  letter-spacing: 0; text-transform: none; color: var(--fg);
}

/* Summary tiles (HQ, funds): soft tonal surfaces like Rupevo's */
.kpi {
  background: var(--soft); border: 0; border-radius: var(--radius-md);
  box-shadow: none; padding: 14px 16px;
}
.kpi.accent { background: var(--accent-soft); }
.k-label {
  display: flex; align-items: center; gap: 6px; font-size: 13px; line-height: 18px;
  font-weight: 500; letter-spacing: 0; text-transform: none; color: var(--fg-2);
}
.k-value {
  font-family: var(--font-display); font-size: 24px; line-height: 32px; font-weight: 400;
  letter-spacing: 0; margin: 4px 0 2px; font-variant-numeric: tabular-nums;
}
.k-foot { font-size: 12.5px; color: var(--muted); font-variant-numeric: tabular-nums; }

/* ── Buttons: Material pills ─────────────────────────────────── */
button.action, a.action, .btn {
  font: inherit; font-size: 13.5px; font-weight: 500; line-height: 20px; letter-spacing: .01em;
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  min-height: 36px; padding: 7px 20px;
  border-radius: var(--radius-pill); border: 1px solid transparent;
  background: var(--accent); color: var(--accent-fg) !important;
  cursor: pointer; text-decoration: none; box-shadow: none;
  transition: box-shadow .15s ease, background-color .15s ease;
}
button.action:hover, a.action:hover, .btn:hover { box-shadow: var(--shadow-sm); }
button.action:active, a.action:active, .btn:active { box-shadow: none; }
button.action[disabled], .btn[disabled] {
  opacity: .38; cursor: not-allowed; box-shadow: none;
}
button.action.alt, a.action.alt, .btn.alt {
  background: transparent !important;
  color: var(--accent) !important;
  border-color: var(--line-strong) !important;
}
button.action.alt:hover, a.action.alt:hover, .btn.alt:hover {
  background: var(--accent-soft) !important; box-shadow: none;
}
/* Row actions stay compact so tables keep their density. */
table button.action, table a.action, table .btn {
  min-height: 28px; padding: 3px 12px; font-size: 12.5px;
}
.controls button { border-radius: var(--radius-pill) !important; padding: 7px 20px; font-weight: 500; }

/* ── Form controls (incl. inline-styled legacy inputs) ───────── */
input[type="text"], input[type="number"], input[type="search"], input[type="email"],
input[type="password"], input[type="date"], select, textarea {
  background-color: var(--input-bg) !important;
  color: var(--fg) !important;
  border: 1px solid var(--line-strong) !important;
  border-radius: var(--radius-sm) !important;
  font: inherit;
  padding: 7px 12px;
}
input::placeholder, textarea::placeholder { color: var(--muted); opacity: 1; }
input:focus-visible, select:focus-visible, textarea:focus-visible {
  outline: none; border-color: var(--accent) !important;
  box-shadow: inset 0 0 0 1px var(--accent);
}
input[type="checkbox"], input[type="radio"] { accent-color: var(--accent); }

/* ── Tables: hairline rows, quiet sentence-case headers ──────── */
table.holdings, table.kvtable, table.grid {
  font-variant-numeric: tabular-nums;
}
table.holdings th {
  color: var(--muted) !important;
  font-size: 12px; font-weight: 500; letter-spacing: 0; text-transform: none;
  padding: 10px;
  border-bottom: 1px solid var(--line) !important;
  background: var(--card);
  position: sticky; top: 0; z-index: 1;
}
/* Pin sticky headers under the fixed app bar, not behind it. */
body:has(.appbar) table.holdings th { top: var(--appbar-h); }
table.holdings td { padding: 8px 10px; border-bottom: 1px solid var(--line) !important; }
table.holdings tr:hover td { background: var(--card-2) !important; }
table.kvtable td { border-bottom: 1px solid var(--line) !important; }

/* Every table header and small label in sentence case, as on Rupevo */
th { text-transform: none !important; letter-spacing: 0 !important; }
table.data-table th, .day-detail th {
  font-size: 12px; font-weight: 500; color: var(--muted); border-bottom-width: 1px;
}
.metric { background: var(--soft); border: 0; border-radius: var(--radius-md); padding: 14px 16px; }
.metric-label, .controls label, .summary-col .col-title, .history-strip .tile .when {
  font-size: 12.5px; font-weight: 500; letter-spacing: 0; text-transform: none; color: var(--fg-2);
}
.metric-value { font-family: var(--font-display); font-size: 22px; line-height: 28px; font-weight: 400; }

/* Wide tables (entry recommendations, open book) carry 12-15 columns and
   cannot compress below ~1100px without the cells colliding. Without a
   scroll container they burst out of the card instead, which is worse on
   small screens. Wrap those in .table-scroll. */
.table-scroll {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  /* Bleed to the card edges so the scrollbar is not inset awkwardly. */
  margin-inline: -24px;
  padding-inline: 24px;
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

/* ── Banners / flags: tonal, borderless ──────────────────────── */
.banner { border-radius: var(--radius-md); border: 0 !important; }
.banner.info { background: var(--info-bg) !important; color: var(--fg) !important; }
.banner.warn { background: var(--warn-bg) !important; color: var(--warn-fg) !important; }
.banner.error { background: var(--neg-bg) !important; color: var(--neg) !important; }
.flag { border-radius: var(--radius-md); }
.flag.INFO { background: var(--info-bg) !important; color: var(--fg) !important; }
.flag.WARN { background: var(--warn-bg) !important; color: var(--warn-fg) !important; }
.flag.RISK { background: var(--risk-bg) !important; color: var(--risk-fg) !important; }

code, pre { background: var(--soft) !important; color: var(--fg-2) !important;
            border-radius: 6px; }

hr { border: none; border-top: 1px solid var(--line); }

/* Fixed overlays that must clear the app bar */
#err-toast-host { top: calc(var(--appbar-h) + 8px) !important; }
body:has(.appbar) .story-contents { top: calc(var(--appbar-h) + 16px); }

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

/* Charts read these via getComputedStyle in page scripts */
.chart-card canvas { max-height: 300px; }

/* ── Phones and small tablets: bottom navigation bar ─────────── */
@media (max-width: 839px) {
  :root { --appbar-h: 56px; --gutter: 16px; }
  body:has(.appbar) {
    padding: calc(var(--appbar-h) + 8px) var(--gutter)
             calc(var(--navbar-h) + 24px + env(safe-area-inset-bottom, 0px));
  }
  .navbar {
    left: 0; right: 0; bottom: 0; height: calc(var(--navbar-h) + env(safe-area-inset-bottom, 0px));
    padding: 6px 4px env(safe-area-inset-bottom, 0px); align-items: flex-start;
    background: var(--card-2); border-top: 1px solid var(--line);
    overflow-x: auto; scrollbar-width: none;
  }
  .navbar::-webkit-scrollbar { display: none; }
  /* Ten destinations do not fit a phone: the bar scrolls sideways. */
  .nav-item { flex: 1 0 64px; }
  .nav-sep { display: none; }
  .appbar { padding: 0 4px 0 8px; gap: 2px; }
  .appbar .brand { font-size: 18px; padding-right: 8px; }
  .appbar .research, .appbar .fx-badge, .appbar .docs-pick label { display: none; }
  .appbar .docs-pick select { width: 170px; }
  h1, h1.page-title { font-size: 24px; line-height: 32px; }
  .card { padding: 16px; }
  .table-scroll { margin-inline: -16px; padding-inline: 16px; }
}
@media (max-width: 480px) {
  .appbar .brand-text { display: none; }
  .appbar .docs-pick select { width: 140px; }
}
"""


# ── Boot script (must be in <head>, before body paint) ───────────

def theme_boot_script() -> str:
    """Sets `data-theme` before first paint and exposes `toggleTheme()`.

    Also ships the shared table sorting and the coffee dialog to every page.
    """
    return privacy_boot_script() + table_sort_script() + coffee_script() + r"""
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

  // The fixed app bar gets a hairline once the page scrolls under it.
  var appbar = null;
  function markScrolled() {
    appbar = appbar || document.querySelector('.appbar');
    if (appbar) appbar.classList.toggle('scrolled', window.scrollY > 4);
  }
  window.addEventListener('scroll', markScrolled, { passive: true });
  document.addEventListener('DOMContentLoaded', markScrolled);

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
    // The token spans several lines; a canvas font string must not.
    var font = css.getPropertyValue('--font').replace(/\s+/g, ' ').trim();
    if (fg) Chart.defaults.color = fg;
    if (grid) Chart.defaults.borderColor = grid;
    if (font) Chart.defaults.font.family = font;
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
    """Sun/moon icon button for the app bar (Rupevo's control)."""
    return (
        '<button class="icon-btn theme-toggle" type="button" onclick="toggleTheme()" '
        'title="Switch between light and dark theme" '
        'aria-label="Toggle colour theme">'
        f'<span class="theme-sun">{icon("sun")}</span>'
        f'<span class="theme-moon">{icon("moon")}</span>'
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
