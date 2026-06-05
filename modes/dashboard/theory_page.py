"""Theory dashboard pages — one route per source doc.

Routes:
    /theory                     -> redirects to /theory/statistics
    /theory/statistics          -> docs/TRADE_STATISTICS.md (+ live stats summary card)
    /theory/trade-strategy         -> docs/TRADE_STRATEGY.md
    /theory/evolution           -> docs/TRADE_EVOLUTION.md
    /theory/tax-guide           -> docs/TRADE_TAX_GUIDE.md (regulatory reference for the /tax page)
    /theory/options-guide       -> docs/OPTIONS_GUIDE.md (plain-English options primer)
    /theory/options-roadmap     -> docs/OPTIONS_ROADMAP.md (options mode build plan)
    /theory/next-ideas          -> docs/TRADE_NEXT_IDEAS.md (intraday & options research)

UI: shared shell with a "Docs" dropdown selector at top-right for
quick switching, plus a "Dashboard (Live P&L)" link back to home.
The statistics page injects an industry-standard summary card at the
top showing theoretical-vs-live numbers side-by-side.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from modes.dashboard.live_stats import LiveStats, compute_live_stats
from modes.dashboard.nav import render_topnav, topnav_css


# `__file__` lives at modes/dashboard/theory_page.py — three .parent
# hops to reach the workspace root that holds `docs/`. The earlier
# two-hop version was correct only when the dashboard lived under
# `services/dashboard/`; preserved here as a comment so a future
# move catches the regression in review.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DOCS_DIR = _PROJECT_ROOT / "docs"


# ── Page registry ────────────────────────────────────────────────

# slug -> (label, source filename, includes_summary_card)
PAGES: dict[str, tuple[str, str, bool]] = {
    "statistics":  ("Statistical Analysis",             "TRADE_STATISTICS.md", True),
    "trade-strategy": ("Trade Strategy — Complete Reference", "TRADE_STRATEGY.md",         False),
    "evolution":   ("Strategy Evolution",               "TRADE_EVOLUTION.md",  False),
    "tax-guide":   ("Tax Guide (India — Intraday)",     "TRADE_TAX_GUIDE.md",           False),
    "options-guide": ("Options Trading Guide",          "OPTIONS_GUIDE.md",             False),
    "options-roadmap": ("Options Roadmap",              "OPTIONS_ROADMAP.md",           False),
    "next-ideas":  ("Next Ideas — Intraday & Options",  "TRADE_NEXT_IDEAS.md",          False),
}

DEFAULT_PAGE = "statistics"


# ── Minimal Markdown -> HTML ─────────────────────────────────────

_INLINE_RE_CODE   = re.compile(r"`([^`]+)`")
_INLINE_RE_BOLD   = re.compile(r"\*\*([^*]+)\*\*")
_INLINE_RE_ITAL   = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_INLINE_RE_LINK   = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HEADING_RE       = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_SEP_RE     = re.compile(r"^\s*\|?\s*[:\-\| ]+\s*\|?\s*$")
_LIST_RE          = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
_BLOCKQUOTE_RE    = re.compile(r"^>\s?(.*)$")
_CODE_FENCE_RE    = re.compile(r"^```")
_HTML_COMMENT_RE  = re.compile(r"<!--.*?-->", re.DOTALL)
_MATH_BLOCK_RE    = re.compile(r"^\s*\$\$(.+?)\$\$\s*$", re.DOTALL)
_MATH_INLINE_RE   = re.compile(r"(?<!\\)\$([^$\n]+?)(?<!\\)\$")
# Standard markdown escapes we honour: \| \$ \* \_ \` \\ \# \[ \] \( \)
_MD_ESCAPE_RE     = re.compile(r"\\([|$*_`\\#\[\]()<>])")


def _inline(text: str) -> str:
    """Render inline markdown after HTML-escaping the raw text."""
    out = html.escape(text, quote=False)
    out = _INLINE_RE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    # Inline math: stash raw TeX in a data-tex attribute; KaTeX renders client-side.
    # The regex above already skips \$ so authors can write a literal dollar sign.
    out = _MATH_INLINE_RE.sub(
        lambda m: f'<span class="math-inline" data-tex="{html.escape(m.group(1), quote=True)}"></span>',
        out,
    )
    out = _INLINE_RE_LINK.sub(
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>',
        out,
    )
    out = _INLINE_RE_BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = _INLINE_RE_ITAL.sub(lambda m: f"<em>{m.group(1)}</em>", out)
    # Strip backslash from MD escapes last so the literal char survives the
    # earlier regex passes (e.g. \| stays a pipe in the cell text).
    out = _MD_ESCAPE_RE.sub(r"\1", out)
    return out


# Splits a markdown table row on un-escaped pipes only ("\|" → literal pipe).
_PIPE_PLACEHOLDER = "\x00ESC_PIPE\x00"


def _render_table(header_line: str, body_lines: list[str]) -> str:
    def cells(line: str) -> list[str]:
        line = line.strip()
        # Protect escaped pipes so they don't split the row.
        line = line.replace(r"\|", _PIPE_PLACEHOLDER)
        if line.startswith("|"): line = line[1:]
        if line.endswith("|"):   line = line[:-1]
        parts = [c.strip().replace(_PIPE_PLACEHOLDER, r"\|") for c in line.split("|")]
        return parts

    header_cells = cells(header_line)
    rows_html = []
    for body in body_lines:
        rows_html.append(
            "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells(body)) + "</tr>"
        )
    return (
        '<div class="table-scroll"><table class="md-table"><thead><tr>'
        + "".join(f"<th>{_inline(c)}</th>" for c in header_cells)
        + "</tr></thead><tbody>"
        + "".join(rows_html)
        + "</tbody></table></div>"
    )


def _markdown_to_html(md: str) -> str:
    """Render a focused subset of CommonMark used in our docs."""
    md = _HTML_COMMENT_RE.sub("", md)
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    in_code = False
    code_buf: list[str] = []

    while i < len(lines):
        line = lines[i]

        # Code fences
        if _CODE_FENCE_RE.match(line):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
                code_buf = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # Block math: $$...$$
        if line.lstrip().startswith("$$"):
            buf = [line]
            j = i + 1
            if line.strip() == "$$":
                while j < len(lines) and lines[j].strip() != "$$":
                    buf.append(lines[j])
                    j += 1
                if j < len(lines):
                    buf.append(lines[j])
                    j += 1
                inner = "\n".join(buf[1:-1])
            else:
                m = _MATH_BLOCK_RE.match(line)
                inner = m.group(1) if m else line.strip().strip("$")
                j = i + 1
            out.append(
                '<div class="math-block" data-tex="'
                + html.escape(inner.strip(), quote=True)
                + '"></div>'
            )
            i = j
            continue

        # Tables
        if "|" in line and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            header = line
            j = i + 2
            body: list[str] = []
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                body.append(lines[j])
                j += 1
            out.append(_render_table(header, body))
            i = j
            continue

        # Blockquote
        m = _BLOCKQUOTE_RE.match(line)
        if m:
            quote_lines = [m.group(1)]
            j = i + 1
            while j < len(lines):
                m2 = _BLOCKQUOTE_RE.match(lines[j])
                if m2:
                    quote_lines.append(m2.group(1))
                    j += 1
                else:
                    break
            out.append("<blockquote>" + _inline(" ".join(quote_lines)) + "</blockquote>")
            i = j
            continue

        # Headings
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        # Lists (flat)
        if _LIST_RE.match(line):
            ordered = bool(re.match(r"^\s*\d+\.", line))
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while i < len(lines) and _LIST_RE.match(lines[i]):
                items.append(_LIST_RE.match(lines[i]).group(3))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + f"</{tag}>")
            continue

        # Horizontal rule
        if re.match(r"^\s*-{3,}\s*$", line) or re.match(r"^\s*\*{3,}\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # Blank line
        if line.strip() == "":
            i += 1
            continue

        # Paragraph
        para = [line]
        j = i + 1
        while j < len(lines) and lines[j].strip() != "" \
                and not _HEADING_RE.match(lines[j]) \
                and not _LIST_RE.match(lines[j]) \
                and not _BLOCKQUOTE_RE.match(lines[j]) \
                and not _CODE_FENCE_RE.match(lines[j]) \
                and not lines[j].lstrip().startswith("$$") \
                and not ("|" in lines[j] and j + 1 < len(lines) and _TABLE_SEP_RE.match(lines[j + 1])):
            para.append(lines[j])
            j += 1
        out.append("<p>" + _inline(" ".join(para)) + "</p>")
        i = j

    if in_code and code_buf:
        out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")

    return "\n".join(out)


# ── Summary card (theoretical vs live) ───────────────────────────

def _fmt_pct(p: float | None) -> str:
    return f"{p*100:.1f}%" if p is not None else "—"


def _fmt_num(x: float | None, prefix: str = "", decimals: int = 2) -> str:
    if x is None:
        return "—"
    return f"{prefix}{x:,.{decimals}f}"


def _fmt_rs(x: float | None) -> str:
    if x is None:
        return "—"
    sign = "+" if x >= 0 else "−"
    return f"Rs.{sign}{abs(x):,.0f}"


def _summary_card(stats: LiveStats) -> str:
    """Theoretical-vs-live snapshot rendered before the doc body."""

    # Theoretical numbers — mirror §3 of docs/TRADE_STATISTICS.md.
    theoretical = [
        ("Win rate",            "55%"),
        ("Profit Factor",       "≥ 1.50"),
        ("Expectancy / trade",  "+0.10 R (≈ +Rs.25)"),
        ("P(profitable day)",   "≈ 60%"),
        ("Sharpe (annualised)", "1.5 – 2.5"),
        ("Max drawdown",        "< 10% of capital"),
    ]

    def colour_class(value: float | None, *, target: float, higher_is_better: bool = True) -> str:
        if value is None:
            return "muted"
        if higher_is_better:
            return "ok" if value >= target else "warn"
        return "ok" if value <= target else "warn"

    wr_cls   = colour_class(stats.win_rate,      target=0.50)
    pf_cls   = colour_class(stats.profit_factor, target=1.30)
    exp_cls  = colour_class(stats.expectancy,    target=0.0)
    day_cls  = colour_class(stats.day_win_rate,  target=0.50)
    sh_cls   = colour_class(stats.sharpe_daily,  target=1.0)

    live_rows = [
        ("Win rate",            f'<span class="v {wr_cls}">{_fmt_pct(stats.win_rate)}</span>',
            f"{stats.win_count} W / {stats.loss_count} L of {stats.trade_count}"),
        ("Profit Factor",       f'<span class="v {pf_cls}">{_fmt_num(stats.profit_factor)}</span>',
            f"GP {_fmt_num(stats.gross_profit, 'Rs.', 0)} / GL {_fmt_num(stats.gross_loss, 'Rs.', 0)}"),
        ("Expectancy / trade",  f'<span class="v {exp_cls}">{_fmt_rs(stats.expectancy)}</span>',
            f"Net {_fmt_rs(stats.net_pnl)} over {stats.trade_count} trades"),
        ("P(profitable day)",   f'<span class="v {day_cls}">{_fmt_pct(stats.day_win_rate)}</span>',
            f"{stats.profitable_days} of {stats.total_days} days"),
        ("Sharpe (annualised)", f'<span class="v {sh_cls}">{_fmt_num(stats.sharpe_daily)}</span>',
            f"Sortino {_fmt_num(stats.sortino_daily)}"),
        ("Max drawdown",        f'<span class="v warn">{_fmt_rs(-abs(stats.max_drawdown)) if stats.max_drawdown else _fmt_rs(0)}</span>',
            "peak-to-trough on cumulative net"),
    ]

    live_html = "".join(
        f'<tr><td class="m">{html.escape(label)}</td>'
        f'<td class="v-cell">{value}</td>'
        f'<td class="hint">{html.escape(hint)}</td></tr>'
        for label, value, hint in live_rows
    )

    theory_html = "".join(
        f'<tr><td class="m">{html.escape(k)}</td>'
        f'<td class="v-cell"><span class="v">{html.escape(v)}</span></td></tr>'
        for k, v in theoretical
    )

    if stats.trade_count == 0:
        live_block = (
            '<div class="empty-live">No closed intraday trades found in '
            f'<code>{html.escape(stats.window_from)} to {html.escape(stats.window_to)}</code>. '
            'Live numbers populate as trades close.</div>'
        )
    else:
        live_block = f'<table class="summary-tbl"><tbody>{live_html}</tbody></table>'

    return f"""
<section class="summary-card">
  <h2 class="summary-title">Quick snapshot — theoretical vs live</h2>
  <p class="summary-sub">
    What the strategy <strong>should</strong> deliver (from §3 of the doc below)
    versus what the live ledger shows for the current FY
    <code>{html.escape(stats.window_from)} to {html.escape(stats.window_to)}</code>
    (verified + provisional).
  </p>
  <div class="summary-grid">
    <div class="summary-col">
      <div class="col-title">Theoretical (target)</div>
      <table class="summary-tbl"><tbody>{theory_html}</tbody></table>
    </div>
    <div class="summary-col">
      <div class="col-title">Live (current FY)</div>
      {live_block}
    </div>
  </div>
  <div class="summary-disclaimer">
    Live numbers are <strong>reference only</strong> — strategy mix has changed
    materially during early development. Treat them as sanity-check, not as a
    clean backtest. Full caveat in "Live trade analysis" below.
  </div>
</section>
"""


# ── Page assembly ────────────────────────────────────────────────

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__ — AI Portfolio Manager</title>
<link rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<style>
  :root {
    --bg: #f7f8fa; --fg: #1c1f23; --muted: #6a7280;
    --card: #ffffff; --line: #e5e7eb; --accent: #1c1f23;
    --ok: #1b8e3a; --warn: #c62828; --soft: #f0f1f3;
  }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         background: var(--bg); color: var(--fg); margin: 0; padding: 24px;
         line-height: 1.55; }
  .wrap { max-width: 1080px; margin: 0 auto; }

    __TOPNAV_CSS__
  nav.topnav .docs-pick label { font-size: 11px; color: var(--muted);
                                text-transform: uppercase; letter-spacing: 0.05em;
                                margin-right: 8px; }
  nav.topnav select { font: inherit; padding: 6px 10px; border: 1px solid var(--line);
                      border-radius: 5px; background: white; cursor: pointer; }

  h1.page-title { font-size: 24px; margin: 4px 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 18px; }

  .summary-card { background: linear-gradient(180deg, #ffffff, #fbfcfe);
                  border: 1px solid var(--line); border-radius: 10px;
                  padding: 22px 26px; margin-bottom: 22px;
                  box-shadow: 0 1px 2px rgba(0,0,0,0.03); }
  .summary-title { font-size: 17px; margin: 0 0 4px; }
  .summary-sub { font-size: 13px; color: var(--muted); margin: 0 0 16px; }
  .summary-grid { display: grid; grid-template-columns: 1fr 1.4fr; gap: 22px; }
  @media (max-width: 720px) { .summary-grid { grid-template-columns: 1fr; } }
  .summary-col .col-title { font-size: 11px; color: var(--muted);
                            text-transform: uppercase; letter-spacing: 0.08em;
                            font-weight: 600; margin-bottom: 8px; }
  table.summary-tbl { width: 100%; border-collapse: collapse;
                      font-size: 13.5px; font-variant-numeric: tabular-nums; }
  table.summary-tbl td { padding: 7px 6px; border-bottom: 1px dashed var(--line);
                         vertical-align: top; }
  table.summary-tbl tr:last-child td { border-bottom: none; }
  table.summary-tbl td.m { color: var(--muted); white-space: nowrap; }
  table.summary-tbl td.v-cell { font-weight: 600; text-align: right; white-space: nowrap; }
  table.summary-tbl td.hint { color: var(--muted); font-size: 12px;
                              padding-left: 12px; text-align: right; }
  span.v { font-weight: 600; }
  span.v.ok    { color: var(--ok); }
  span.v.warn  { color: var(--warn); }
  span.v.muted { color: var(--muted); font-weight: 500; }
  .empty-live { padding: 18px; background: var(--soft); border-radius: 6px;
                color: var(--muted); font-size: 13px; }
  .summary-disclaimer { margin-top: 14px; padding: 8px 12px;
                        background: #fff8e1; border-left: 3px solid #f0c75a;
                        font-size: 12.5px; color: #5b4a18; border-radius: 0 4px 4px 0; }

  .doc-section { background: var(--card); border: 1px solid var(--line);
                 border-radius: 10px; padding: 22px 30px; margin-bottom: 24px;
                 box-shadow: 0 1px 2px rgba(0,0,0,0.03); }
  .doc-section > h1:first-child,
  .doc-section > h2:first-child { margin-top: 0; }
  .doc-section h1 { font-size: 22px; border-bottom: 2px solid var(--line);
                    padding-bottom: 6px; margin-top: 28px; }
  .doc-section h2 { font-size: 18px; margin-top: 24px; color: #2c3138; }
  .doc-section h3 { font-size: 15px; margin-top: 18px; color: #2c3138; }
  .doc-section h4 { font-size: 13px; margin-top: 14px; color: var(--muted);
                    text-transform: uppercase; letter-spacing: 0.04em; }
  .doc-section p { margin: 8px 0; }
  .doc-section ul, .doc-section ol { margin: 8px 0; padding-left: 22px; }
  .doc-section li { margin: 3px 0; }
  .doc-section blockquote { border-left: 3px solid #c7d2fe; background: #eef2ff;
                            margin: 10px 0; padding: 8px 14px; color: #1c2942;
                            border-radius: 0 4px 4px 0; }
  .doc-section code { background: var(--soft); padding: 1px 6px; border-radius: 3px;
                      font-size: 12.5px; font-family: ui-monospace, Menlo, Consolas, monospace; }
  .doc-section pre { background: #1c1f23; color: #e5e7eb; padding: 12px 16px;
                     border-radius: 6px; overflow-x: auto; font-size: 12.5px; }
  .doc-section pre code { background: transparent; color: inherit; padding: 0; }
  .doc-section .math-block { background: #f4f6fa; color: #1c1f23;
                              border-left: 3px solid #c7d2fe;
                              padding: 12px 16px; border-radius: 0 6px 6px 0;
                              margin: 12px 0; overflow-x: auto;
                              text-align: center; font-size: 15px; }
  .doc-section .math-inline { font-size: 14px; padding: 0 1px; }
  .doc-section .katex { font-size: 1em; }
  .doc-section .katex-display { margin: 0; }
  .doc-section a { color: #1f4ed8; }
  .doc-section .table-scroll { overflow-x: auto; margin: 12px 0; }
  .doc-section table.md-table { width: 100%; border-collapse: collapse;
                                font-size: 13.5px;
                                font-variant-numeric: tabular-nums; }
  .doc-section table.md-table th { text-align: left; padding: 8px 10px;
                                   border-bottom: 2px solid var(--line);
                                   background: #f7f8fa; font-weight: 600;
                                   font-size: 12.5px; color: #2c3138; }
  .doc-section table.md-table td { padding: 7px 10px;
                                   border-bottom: 1px solid var(--line);
                                   vertical-align: top; }
  .doc-section table.md-table tr:hover td { background: #fafbfc; }
  .doc-section hr { border: 0; border-top: 1px solid var(--line); margin: 18px 0; }
  .source-banner { font-size: 12px; color: var(--muted);
                   margin-bottom: 12px; padding: 6px 10px;
                   background: var(--soft); border-radius: 4px;
                   display: inline-block; }
  footer { color: var(--muted); font-size: 12px; margin-top: 32px;
           text-align: center; }
</style>
</head>
<body>
<div class="wrap">
    __TOPNAV__

  <h1 class="page-title">__TITLE__</h1>
  <div class="sub">
    Rendered live from <code>docs/__FILENAME__</code>. Edit the markdown to update this page.
  </div>

  __SUMMARY__

  <section class="doc-section">
    <div class="source-banner">Source: <code>docs/__FILENAME__</code></div>
    __BODY__
  </section>

  <footer>
    Generated <span id="now"></span>.
  </footer>
</div>
<script>
  document.getElementById("now").textContent =
    new Date().toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });

  // Render KaTeX into every element that has a data-tex attribute.
  function renderAllMath() {
    if (typeof katex === "undefined") { setTimeout(renderAllMath, 30); return; }
    document.querySelectorAll("[data-tex]").forEach(function (el) {
      try {
        katex.render(el.dataset.tex, el, {
          displayMode: el.classList.contains("math-block"),
          throwOnError: false,
          output: "html",
        });
      } catch (e) {
        el.textContent = el.dataset.tex;
      }
    });
  }
  renderAllMath();
</script>
</body>
</html>
"""


def _options_html(active_slug: str) -> str:
    parts = []
    for slug, (label, _filename, _has_summary) in PAGES.items():
        sel = " selected" if slug == active_slug else ""
        href = f"/theory/{slug}"
        parts.append(f'<option value="{href}"{sel}>{html.escape(label)}</option>')
    return "".join(parts)


def render_theory_page(slug: str = DEFAULT_PAGE) -> str:
    """Render a single theory page identified by slug."""
    if slug not in PAGES:
        slug = DEFAULT_PAGE
    label, filename, has_summary = PAGES[slug]

    path = _DOCS_DIR / filename
    if path.exists():
        try:
            md = path.read_text(encoding="utf-8")
            body = _markdown_to_html(md)
        except Exception as exc:  # noqa: BLE001
            body = (
                f'<p class="source-banner">Failed to read <code>docs/{html.escape(filename)}</code>: '
                f'{html.escape(str(exc))}</p>'
            )
    else:
        body = (
            f'<p class="source-banner">Source file <code>docs/{html.escape(filename)}</code> '
            'not found.</p>'
        )

    summary_html = ""
    if has_summary:
        try:
            stats = compute_live_stats()
            summary_html = _summary_card(stats)
        except Exception as exc:  # noqa: BLE001
            summary_html = (
                '<section class="summary-card">'
                f'<p class="summary-sub">Live summary unavailable: {html.escape(str(exc))}</p>'
                '</section>'
            )

    docs_picker = (
        '<div class="docs-pick">'
        '<label for="docs-select">Docs</label>'
        '<select id="docs-select" onchange="window.location.href=this.value">'
        + _options_html(slug)
        + '</select></div>'
    )

    return (_TEMPLATE
            .replace("__TITLE__", html.escape(label))
            .replace("__FILENAME__", html.escape(filename))
            .replace("__TOPNAV_CSS__", topnav_css())
            .replace("__TOPNAV__", render_topnav(f"/theory/{slug}", after_links=docs_picker))
            .replace("__SUMMARY__", summary_html)
            .replace("__BODY__", body))


__all__ = ["render_theory_page", "PAGES", "DEFAULT_PAGE"]
