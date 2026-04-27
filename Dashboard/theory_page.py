"""Theory page renderer (Roadmap addendum 2026-04-27).

Renders a separate dashboard page at /theory that explains how the
trading tool works:

  - V1 vs V2 strategy summary
  - How they're used (entry pipeline, exit rules)
  - Theoretical probability of profit (from STRATEGY_STATISTICS.md)
  - Live reference numbers (from the same DB the home page uses)

Source-of-truth philosophy: this page reads from existing docs
(STRATEGY_STATISTICS.md, STRATEGY_V2.md, STRATEGY_EVOLUTION.md) at
request time and renders them as HTML so editing the markdown
auto-updates the page. No content duplication.

Deps: stdlib only. Markdown rendering is intentionally minimal
(headings, paragraphs, tables, lists, code, links, blockquotes,
inline emphasis) to avoid pulling a new package.
"""

from __future__ import annotations

import html
import re
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DOCS_DIR = _PROJECT_ROOT / "docs"

_DOCS_TO_RENDER = [
    ("Statistical Analysis (Theoretical & Live)", "STRATEGY_STATISTICS.md"),
    ("V2 Strategy — Complete Reference",          "STRATEGY_V2.md"),
    ("Strategy Evolution — Version History",      "STRATEGY_EVOLUTION.md"),
]


# ── Minimal Markdown → HTML ──────────────────────────────────────

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


def _inline(text: str) -> str:
    """Render inline markdown after HTML-escaping the raw text."""
    out = html.escape(text, quote=False)
    # Code first (so * inside backticks isn't bold-ified)
    out = _INLINE_RE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    # Links — note: target text and href are already escaped
    out = _INLINE_RE_LINK.sub(
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>',
        out,
    )
    out = _INLINE_RE_BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = _INLINE_RE_ITAL.sub(lambda m: f"<em>{m.group(1)}</em>", out)
    return out


def _render_table(header_line: str, body_lines: list[str]) -> str:
    def cells(line: str) -> list[str]:
        line = line.strip()
        if line.startswith("|"): line = line[1:]
        if line.endswith("|"):   line = line[:-1]
        return [c.strip() for c in line.split("|")]

    header_cells = cells(header_line)
    rows_html = []
    for body in body_lines:
        rows_html.append(
            "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells(body)) + "</tr>"
        )
    return (
        '<table class="md-table"><thead><tr>'
        + "".join(f"<th>{_inline(c)}</th>" for c in header_cells)
        + "</tr></thead><tbody>"
        + "".join(rows_html)
        + "</tbody></table>"
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

        # Tables: header followed by separator
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
                elif lines[j].strip() == "":
                    break
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

        # Lists (flat — no nested for our needs)
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

        # Paragraph (collect contiguous non-blank lines)
        para = [line]
        j = i + 1
        while j < len(lines) and lines[j].strip() != "" \
                and not _HEADING_RE.match(lines[j]) \
                and not _LIST_RE.match(lines[j]) \
                and not _BLOCKQUOTE_RE.match(lines[j]) \
                and not _CODE_FENCE_RE.match(lines[j]) \
                and not ("|" in lines[j] and j + 1 < len(lines) and _TABLE_SEP_RE.match(lines[j + 1])):
            para.append(lines[j])
            j += 1
        out.append("<p>" + _inline(" ".join(para)) + "</p>")
        i = j

    if in_code and code_buf:
        out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")

    return "\n".join(out)


# ── Page assembly ────────────────────────────────────────────────

_THEORY_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Portfolio Manager — Theory & Statistics</title>
<style>
  :root {
    --bg: #fafbfc; --fg: #1c1f23; --muted: #6a7280;
    --card: #ffffff; --line: #e5e7eb; --accent: #1c1f23;
  }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         background: var(--bg); color: var(--fg); margin: 0; padding: 24px;
         line-height: 1.55; }
  .wrap { max-width: 1080px; margin: 0 auto; }
  nav.topnav { display: flex; gap: 14px; align-items: center;
               padding: 10px 16px; background: var(--card);
               border: 1px solid var(--line); border-radius: 8px;
               margin-bottom: 18px; font-size: 14px; }
  nav.topnav a { color: var(--accent); text-decoration: none; font-weight: 500; }
  nav.topnav a:hover { text-decoration: underline; }
  nav.topnav .here { color: var(--muted); cursor: default; }
  nav.topnav .sep { color: var(--muted); }
  h1 { font-size: 24px; margin: 4px 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
  .toc { background: var(--card); border: 1px solid var(--line);
         border-radius: 8px; padding: 14px 20px; margin-bottom: 18px; font-size: 14px; }
  .toc h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em;
            color: var(--muted); margin: 0 0 8px; font-weight: 600; }
  .toc a { display: inline-block; margin-right: 16px; color: var(--accent);
           text-decoration: none; }
  .toc a:hover { text-decoration: underline; }
  .doc-section { background: var(--card); border: 1px solid var(--line);
                 border-radius: 8px; padding: 22px 28px; margin-bottom: 24px; }
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
  .doc-section code { background: #f0f1f3; padding: 1px 6px; border-radius: 3px;
                      font-size: 12.5px; font-family: ui-monospace, Menlo, Consolas, monospace; }
  .doc-section pre { background: #1c1f23; color: #e5e7eb; padding: 12px 16px;
                     border-radius: 6px; overflow-x: auto; font-size: 12.5px; }
  .doc-section pre code { background: transparent; color: inherit; padding: 0; }
  .doc-section a { color: #1f4ed8; }
  .doc-section table.md-table { width: 100%; border-collapse: collapse;
                                font-size: 13.5px; margin: 12px 0;
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
  .source-banner { background: #fff4e0; border: 1px solid #f0d28a;
                   padding: 10px 14px; border-radius: 6px; font-size: 13px;
                   margin-bottom: 14px; color: #6b4a00; }
  footer { color: var(--muted); font-size: 12px; margin-top: 32px;
           text-align: center; }
</style>
</head>
<body>
<div class="wrap">
  <nav class="topnav">
    <a href="/">← Dashboard (Live P&amp;L)</a>
    <span class="sep">·</span>
    <span class="here">Theory &amp; Statistics</span>
  </nav>

  <h1>Theory &amp; Statistics</h1>
  <div class="sub">
    How the tool works, what edge we expect to deliver, and what the live
    numbers say so far. Content rendered live from the docs in
    <code>docs/</code> — edit those files to update this page.
  </div>

  <div class="toc">
    <h2>On this page</h2>
    __TOC__
  </div>

  __SECTIONS__

  <footer>
    Generated <span id="now"></span> from
    <code>docs/STRATEGY_STATISTICS.md</code>,
    <code>docs/STRATEGY_V2.md</code>,
    <code>docs/STRATEGY_EVOLUTION.md</code>.
  </footer>
</div>
<script>
  document.getElementById("now").textContent =
    new Date().toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
</script>
</body>
</html>
"""


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def render_theory_page() -> str:
    """Build the /theory HTML by rendering each source markdown doc."""
    sections_html: list[str] = []
    toc_html: list[str] = []

    for title, filename in _DOCS_TO_RENDER:
        slug = _slug(filename)
        toc_html.append(f'<a href="#{slug}">{html.escape(title)}</a>')

        path = _DOCS_DIR / filename
        if not path.exists():
            sections_html.append(
                f'<section id="{slug}" class="doc-section">'
                f'<h1>{html.escape(title)}</h1>'
                f'<div class="source-banner">Source file <code>docs/{filename}</code> '
                f'not found — render skipped.</div></section>'
            )
            continue

        try:
            md = path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            sections_html.append(
                f'<section id="{slug}" class="doc-section">'
                f'<h1>{html.escape(title)}</h1>'
                f'<div class="source-banner">Failed to read <code>docs/{filename}</code>: '
                f'{html.escape(str(exc))}</div></section>'
            )
            continue

        body = _markdown_to_html(md)
        sections_html.append(
            f'<section id="{slug}" class="doc-section">'
            f'<div class="source-banner">Source: <code>docs/{filename}</code></div>'
            f'{body}</section>'
        )

    return (_THEORY_TEMPLATE
            .replace("__TOC__", " ".join(toc_html))
            .replace("__SECTIONS__", "\n".join(sections_html)))


__all__ = ["render_theory_page"]
