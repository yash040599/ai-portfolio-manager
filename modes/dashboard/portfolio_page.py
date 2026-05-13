"""Portfolio dashboard pages — D25 (summary), D26 (per-stock
drill-down), D28 (Zerodha login), D29 (latest-vs-prior history).

Every page renders the shared four-link nav (Portfolio · Trading ·
Tax · Theory) plus a small auth-status pill (D28) so a user with an
expired Zerodha token can re-login from the dashboard without
dropping back to the CLI.

All pages are read-only: they query
`data/portfolio_analyses.db` and the in-memory job dict from
`modes/dashboard/portfolio_actions.py`. Live data is never fetched
inline (slow renders, surprise broker calls). The "Analyse now"
buttons POST to `/api/analyse_run` which spawns a background worker
via `portfolio_actions.submit_run()`; the same page polls
`/api/run_status` and re-renders when the worker completes.
"""

from __future__ import annotations

import datetime
import html
import json
import os
import sqlite3

from config import Config
from modes.analyze.persistence import (
    history_for_symbol,
    latest_for_symbol,
    latest_run,
    latest_snapshot,
)
from modes.analyze.types import (
    Field,
    GapAnalysis,
    PortfolioMetrics,
    PortfolioSnapshot,
    StockAnalysis,
)
from modes.dashboard.portfolio_actions import estimate_ai_cost, latest_status


# ── Shared chrome ───────────────────────────────────────────────

_TOKEN_PATH = os.path.join("data", "access_token.json")


def _auth_pill() -> str:
    """Returns a small pill showing whether today's Zerodha token is
    valid. Click → /login."""
    today = datetime.date.today().isoformat()
    valid = False
    try:
        if os.path.exists(_TOKEN_PATH):
            with open(_TOKEN_PATH, encoding="utf-8") as f:
                saved = json.load(f)
            valid = saved.get("date") == today
    except Exception:
        valid = False
    if valid:
        return ('<a class="auth ok" href="/login" title="Token valid for today">'
                'Auth: <strong>OK</strong></a>')
    return ('<a class="auth bad" href="/login" '
            'title="Re-login required (token expired or missing)">'
            'Auth: <strong>Re-login</strong></a>')


def _topnav(here: str) -> str:
    """Render the four-link nav with `here` highlighted as <span>."""
    items = [
        ("Portfolio", "/portfolio"),
        ("Swing", "/swing"),
        ("Trading (Live P&L)", "/trading"),
        ("Tax", "/tax"),
        ("Theory", "/theory/statistics"),
    ]
    parts = []
    for i, (label, href) in enumerate(items):
        if i:
            parts.append('<span class="sep">·</span>')
        if href.startswith(here) or label.startswith(here):
            parts.append(f'<span class="here">{html.escape(label)}</span>')
        else:
            parts.append(f'<a href="{href}">{html.escape(label)}</a>')
    return ('<nav class="topnav">'
            + "".join(parts)
            + '<span class="spacer"></span>'
            + _auth_pill()
            + '</nav>')


_STYLE = r"""
:root { --bg: #fafbfc; --fg: #1c1f23; --muted: #6a7280;
        --card: #ffffff; --line: #e5e7eb;
        --accent: #1c1f23; --pos: #1b8e3a; --neg: #c62828;
        --warn-bg: #fff4e0; --warn-fg: #b06a00; --warn-line: #f0d28a;
        --risk-bg: #fdecec; --risk-fg: #c62828; --risk-line: #f4c0c0; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
       background: var(--bg); color: var(--fg); margin: 0; padding: 24px; }
.wrap { max-width: 1180px; margin: 0 auto; }
h1.page-title { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em;
     color: var(--muted); margin: 28px 0 8px; font-weight: 600; }
.sub { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
.muted { color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line);
        border-radius: 8px; padding: 18px 22px; margin-bottom: 16px; }
.card a { color: var(--accent); }
nav.topnav { display: flex; gap: 14px; align-items: center;
             padding: 10px 16px; background: var(--card);
             border: 1px solid var(--line); border-radius: 8px;
             margin-bottom: 18px; font-size: 14px; }
nav.topnav a { color: var(--fg); text-decoration: none; font-weight: 500; }
nav.topnav a:hover { text-decoration: underline; }
nav.topnav .here { color: var(--muted); cursor: default; }
nav.topnav .sep { color: var(--muted); }
nav.topnav .spacer { flex: 1; }
nav.topnav .auth { font-size: 12px; padding: 4px 10px; border-radius: 999px;
                   text-decoration: none; }
nav.topnav .auth.ok  { background: #e6f4ea; color: var(--pos); }
nav.topnav .auth.bad { background: var(--risk-bg); color: var(--risk-fg);
                       border: 1px solid var(--risk-line); }
table.holdings { width: 100%; border-collapse: collapse; font-size: 13px;
                 font-variant-numeric: tabular-nums; }
table.holdings th { text-align: left; padding: 6px 10px;
                    border-bottom: 2px solid var(--line);
                    color: var(--muted); font-weight: 600; font-size: 11px;
                    text-transform: uppercase; letter-spacing: 0.04em; }
table.holdings td { padding: 6px 10px; border-bottom: 1px solid var(--line); }
table.holdings tr:hover td { background: #f7f8fa; }
table.holdings .right { text-align: right; }
table.holdings .pos { color: var(--pos); font-weight: 600; }
table.holdings .neg { color: var(--neg); font-weight: 600; }
.kvtable { width: 100%; border-collapse: collapse; font-size: 14px;
           font-variant-numeric: tabular-nums; }
.kvtable td { padding: 5px 0; border-bottom: 1px dashed var(--line); }
.kvtable td:last-child { text-align: right; font-weight: 500; }
.kvtable td:last-child .src { color: var(--muted); font-size: 11px;
                              font-weight: 400; }
.flag { padding: 10px 14px; border-radius: 6px; margin: 8px 0; font-size: 13px;
        border-left: 3px solid var(--line); }
.flag.WARN { background: var(--warn-bg); border-left-color: var(--warn-fg);
             color: var(--warn-fg); }
.flag.RISK { background: var(--risk-bg); border-left-color: var(--risk-fg);
             color: var(--risk-fg); }
.flag.INFO { background: #f0f3f7; border-left-color: var(--muted);
             color: var(--fg); }
.flag .head { font-weight: 600; }
.flag .sugg { font-size: 12px; color: var(--muted); margin-top: 4px; }
button.action { font: inherit; padding: 8px 14px; border: 1px solid #1c1f23;
                border-radius: 5px; background: #1c1f23; color: white;
                cursor: pointer; margin-right: 8px; }
button.action.alt { background: white; color: #1c1f23; }
button.action[disabled] { opacity: 0.55; cursor: not-allowed; }
.banner { padding: 10px 14px; border-radius: 6px; font-size: 13px;
          margin-bottom: 12px; }
.banner.info { background: #eef4ff; border: 1px solid #cfd9eb; }
.banner.warn { background: var(--warn-bg); border: 1px solid var(--warn-line);
               color: var(--warn-fg); }
.sectorbar { display: inline-block; height: 8px; background: #1c1f23;
             vertical-align: middle; margin-left: 6px; border-radius: 2px; }
.history-strip { display: flex; gap: 10px; flex-wrap: wrap; }
.history-strip .tile { flex: 1 1 140px; padding: 10px 12px; background: var(--card);
                       border: 1px solid var(--line); border-radius: 6px;
                       min-width: 130px; }
.history-strip .tile .when { font-size: 11px; color: var(--muted);
                              text-transform: uppercase; letter-spacing: 0.04em; }
.history-strip .tile .act { font-weight: 600; font-size: 14px; margin-top: 4px; }
footer { color: var(--muted); font-size: 12px; margin-top: 32px; text-align: center; }
code { background: #f0f1f3; padding: 1px 6px; border-radius: 3px; font-size: 12px; }

/* AI toggle */
.ai-toggle { display: inline-flex; align-items: center; gap: 8px;
             padding: 6px 12px; background: var(--card);
             border: 1px solid var(--line); border-radius: 999px;
             font-size: 13px; cursor: pointer; user-select: none;
             margin-right: 12px; }
.ai-toggle input { margin: 0; cursor: pointer; }
.ai-toggle .lbl { font-weight: 500; }
.ai-toggle .hint { color: var(--muted); font-size: 11px; }

/* Loading spinner (used during background analyse runs) */
.spinner { display: inline-block; width: 14px; height: 14px;
           border: 2px solid #cfd9eb; border-top-color: var(--accent);
           border-radius: 50%; animation: spin 0.8s linear infinite;
           vertical-align: middle; margin-right: 6px; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Chart canvases */
.chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px;
              margin-bottom: 16px; }
@media (max-width: 760px) { .chart-grid { grid-template-columns: 1fr; } }
.chart-grid .chart-card { padding: 14px 18px; background: var(--card);
                          border: 1px solid var(--line); border-radius: 8px; }
.chart-grid .chart-card h3 { margin: 0 0 8px; font-size: 13px;
                              text-transform: uppercase; letter-spacing: 0.06em;
                              color: var(--muted); font-weight: 600; }
.chart-card canvas { max-height: 280px; }

/* Back link on drill-down */
.back-link { display: inline-block; padding: 6px 0; margin-bottom: 8px;
             color: var(--accent); text-decoration: none; font-size: 14px; }
.back-link:hover { text-decoration: underline; }

/* Suggested-additions cards */
.sugg-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
             gap: 10px; }
.sugg-grid .card-mini { padding: 10px 12px; background: var(--card);
                        border: 1px solid var(--line); border-radius: 6px; }
.sugg-grid .card-mini a { font-weight: 600; color: var(--accent);
                          text-decoration: none; font-size: 14px; }
.sugg-grid .card-mini a:hover { text-decoration: underline; }
.sugg-grid .card-mini .meta { color: var(--muted); font-size: 12px;
                               margin-top: 4px; }
.sugg-grid .card-mini .why { font-size: 12px; margin-top: 6px;
                              color: var(--fg); }
"""


# ── /portfolio (D25 — summary page) ─────────────────────────────

def render_portfolio_page() -> str:
    """Renders the D25 summary page from the latest persisted snapshot.
    When no run exists yet, shows a friendly empty state with a button
    to launch the first NoAI run."""
    snap = latest_snapshot()
    body = []

    if snap is None:
        body.append(_empty_state())
        return _wrap("Portfolio", "Portfolio", "".join(body),
                     holdings_count=0)

    body.append(_render_header(snap))
    body.append(_render_actions(snap))
    body.append(_render_charts(snap))
    body.append(_render_metrics(snap.metrics))
    body.append(_render_gaps(snap.gaps))
    body.append(_render_holdings_table(snap))
    body.append(_render_suggested(snap))
    return _wrap("Portfolio", "Portfolio", "".join(body),
                 holdings_count=len(snap.holdings))


def _empty_state() -> str:
    per_call = float(getattr(Config, "CLAUDE_COST_PER_CALL", 3.0))
    return f"""
<div class="card">
  <h2>No analysis on file yet</h2>
  <p>Click below to run your first portfolio analysis. NoAI is the
  default and free (no Claude calls). Toggle <em>Use Claude AI overlay</em>
  to add qualitative thesis / risks / news context (~Rs.{per_call:.0f} per
  stock).</p>
  <p>
    {_ai_toggle_html()}
    <button class="action" onclick="runAnalysis('all')">Analyse all</button>
  </p>
  <div id="job-banner"></div>
  <p class="muted">Reads holdings live from your Zerodha demat account.
  Make sure your access token is valid (Auth pill above).</p>
</div>
{_runs_polling_script()}
"""


def _render_header(snap: PortfolioSnapshot) -> str:
    m = snap.metrics
    invested = _v(m.total_invested)
    current  = _v(m.total_current_value)
    pnl      = _v(m.total_pnl)
    pnl_pct  = _v(m.total_pnl_pct)
    cls = "pos" if pnl >= 0 else "neg"
    sign = "+" if pnl >= 0 else ""
    most_stale = snap.most_stale_at()
    when = snap.timestamp.strftime("%Y-%m-%d %H:%M IST")
    age = _staleness_label(most_stale)
    badge = ('<span class="banner info" style="display:inline-block; margin:0 0 0 8px;">'
             f'{html.escape(snap.mode)} run</span>')
    return f"""
<h1 class="page-title">Portfolio Analyser {badge}</h1>
<div class="sub">
  Run completed {html.escape(when)} · most-stale field: {html.escape(age)}
  · holdings: {len(snap.holdings)}
  <br><span class="muted" style="font-size:11px">Live prices refresh every 5 seconds (Zerodha quote polling)</span>
</div>
<div class="card">
  <table class="kvtable">
    <tr><td>Invested value</td><td>{_inr_html(invested)}</td></tr>
    <tr><td>Current value</td><td>{_inr_html(current)}</td></tr>
    <tr><td>P&amp;L</td><td class="{cls}">{sign}{_inr_html(pnl)} ({pnl_pct:+.2f}%)</td></tr>
  </table>
</div>
"""


def _render_actions(snap: PortfolioSnapshot) -> str:
    return f"""
<div class="card">
  <h2>Refresh analysis</h2>
  <p class="muted">A new run reads live prices + cached candles +
  reference seeds, then refreshes this page. Same-day re-runs
  overwrite — your DB stays one-row-per-day clean.</p>
  <p>
    {_ai_toggle_html()}
    <button class="action" onclick="runAnalysis('all')">Analyse all</button>
  </p>
  <div id="job-banner"></div>
</div>
{_runs_polling_script()}
"""


def _render_charts(snap: PortfolioSnapshot) -> str:
    """Render two doughnut charts (sector + market-cap tier) +
    the top-10 holdings P&L bar. Chart.js is loaded once in the
    shared shell (`_wrap`)."""
    m = snap.metrics
    # ── Sector pie data ──
    sectors = [(sw.sector, sw.weight_pct) for sw in (m.sector_weights or [])
               if sw.weight_pct > 0]
    sector_labels = [s[0] for s in sectors]
    sector_data   = [s[1] for s in sectors]

    # ── Market-cap tier doughnut data ──
    cap_data: list[tuple[str, float]] = []
    if m.cap_tier_weights and isinstance(m.cap_tier_weights.value, dict):
        for tier in ("LARGE", "MID", "SMALL", "ETF", "UNKNOWN"):
            v = m.cap_tier_weights.value.get(tier)
            if v is not None and v > 0:
                cap_data.append((tier, v))
    cap_labels = [c[0] for c in cap_data]
    cap_values = [c[1] for c in cap_data]

    # ── Top-10 P&L bar (sorted by absolute P&L) ──
    pnl_rows = sorted(
        ((s.symbol, _v(s.pnl)) for s in snap.holdings),
        key=lambda t: abs(t[1]), reverse=True,
    )[:10]
    pnl_labels = [r[0] for r in pnl_rows]
    pnl_values = [round(r[1], 0) for r in pnl_rows]
    pnl_colors = ["#1b8e3a" if v >= 0 else "#c62828" for v in pnl_values]

    # JSON-encode for the inline scripts.
    sec_json = json.dumps({"labels": sector_labels, "data": sector_data})
    cap_json = json.dumps({"labels": cap_labels, "data": cap_values})
    pnl_json = json.dumps({"labels": pnl_labels, "data": pnl_values,
                           "colors": pnl_colors})

    return f"""
<h2>At a glance</h2>
<div class="chart-grid">
  <div class="chart-card">
    <h3>Sector mix</h3>
    <canvas id="chart-sector"></canvas>
  </div>
  <div class="chart-card">
    <h3>Market-cap tier (AMFI)</h3>
    <canvas id="chart-cap-tier"></canvas>
  </div>
</div>
<div class="chart-grid" style="grid-template-columns: 1fr;">
  <div class="chart-card">
    <h3>Top movers (absolute P&amp;L)</h3>
    <canvas id="chart-pnl"></canvas>
  </div>
</div>
<script>
(function () {{
  if (typeof Chart === 'undefined') return;
  Chart.defaults.font.family = '-apple-system, "Segoe UI", Roboto, sans-serif';
  Chart.defaults.font.size = 12;

  var palette = ['#3457d5','#10b981','#f59e0b','#ef4444','#8b5cf6',
                 '#ec4899','#14b8a6','#f97316','#6366f1','#84cc16',
                 '#06b6d4','#a3a3a3'];

  var sec = {sec_json};
  if (sec.labels.length) {{
    new Chart(document.getElementById('chart-sector'), {{
      type: 'doughnut',
      data: {{
        labels: sec.labels,
        datasets: [{{
          data: sec.data,
          backgroundColor: sec.labels.map(function (_, i) {{ return palette[i % palette.length]; }}),
          borderWidth: 1, borderColor: '#fff',
        }}],
      }},
      options: {{
        plugins: {{
          legend: {{ position: 'right', labels: {{ boxWidth: 12 }} }},
          tooltip: {{ callbacks: {{ label: function (ctx) {{
            return ctx.label + ': ' + ctx.parsed.toFixed(1) + '%';
          }} }} }},
        }},
        cutout: '55%',
      }},
    }});
  }}

  var cap = {cap_json};
  if (cap.labels.length) {{
    var capColors = {{
      LARGE: '#1b8e3a', MID: '#3457d5', SMALL: '#f59e0b',
      ETF: '#8b5cf6', UNKNOWN: '#c62828',
    }};
    new Chart(document.getElementById('chart-cap-tier'), {{
      type: 'doughnut',
      data: {{
        labels: cap.labels,
        datasets: [{{
          data: cap.data,
          backgroundColor: cap.labels.map(function (l) {{ return capColors[l] || '#a3a3a3'; }}),
          borderWidth: 1, borderColor: '#fff',
        }}],
      }},
      options: {{
        plugins: {{
          legend: {{ position: 'right', labels: {{ boxWidth: 12 }} }},
          tooltip: {{ callbacks: {{ label: function (ctx) {{
            return ctx.label + ': ' + ctx.parsed.toFixed(1) + '%';
          }} }} }},
        }},
        cutout: '55%',
      }},
    }});
  }}

  var pnl = {pnl_json};
  if (pnl.labels.length) {{
    new Chart(document.getElementById('chart-pnl'), {{
      type: 'bar',
      data: {{
        labels: pnl.labels,
        datasets: [{{
          label: 'P&L (Rs.)', data: pnl.data,
          backgroundColor: pnl.colors, borderWidth: 0,
        }}],
      }},
      options: {{
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          y: {{ ticks: {{ callback: function (v) {{
            return 'Rs.' + (Math.abs(v) >= 1000 ? (v/1000).toFixed(0) + 'k' : v);
          }} }} }},
        }},
      }},
    }});
  }}
}})();
</script>
"""


def _render_metrics(m: PortfolioMetrics) -> str:
    if m.sector_weights:
        bars = []
        for sw in m.sector_weights:
            w = max(2, int(round(sw.weight_pct * 4)))
            bars.append(
                f'<tr><td>{html.escape(sw.sector)}</td>'
                f'<td>{sw.weight_pct:.1f}% '
                f'<span class="sectorbar" style="width:{w}px"></span>'
                f' <span class="muted">({sw.holdings_count})</span></td></tr>'
            )
        sec_html = '<table class="kvtable">' + "".join(bars) + "</table>"
    else:
        sec_html = '<p class="muted">No sector data.</p>'

    def _kv(label: str, field, fmt=lambda v: f"{v:.2f}", note=True) -> str:
        if field is None or field.value is None:
            return (f'<tr><td>{html.escape(label)}</td>'
                    f'<td class="muted">n/a</td></tr>')
        try:
            v_str = fmt(field.value)
        except Exception:
            v_str = html.escape(str(field.value))
        n = ""
        if note and getattr(field, "note", ""):
            n = f' <span class="src">· {html.escape(field.note)}</span>'
        return (f'<tr><td>{html.escape(label)}</td>'
                f'<td>{v_str}{n}</td></tr>')

    core_html = (
        '<table class="kvtable">'
        + _kv("HHI concentration", m.hhi_concentration, lambda v: f"{v:.0f}")
        + _kv("Top-5 concentration", m.top_5_concentration_pct,
              lambda v: f"{v:.1f}%")
        + _kv(
            f"Single-name max ({_v_str(m.single_name_max_symbol, 'n/a')})",
            m.single_name_max_pct, lambda v: f"{v:.2f}%",
        )
        + _kv("Weighted P/E (TTM)", m.weighted_pe, lambda v: f"{v:.2f}")
        + _kv("Weighted div yield (TTM)", m.weighted_dividend_yield,
              lambda v: f"{v:.2f}%")
        + _kv("Annual dividend estimate", m.annual_dividend_estimate,
              lambda v: f"Rs.{v:,.0f}")
        + _kv("Beta vs NIFTY", m.portfolio_beta_vs_nifty,
              lambda v: f"{v:.2f}")
        + "</table>"
    )

    risk_rows = (
        _kv("Volatility (annualised)", m.volatility_30d_pct,
            lambda v: f"{v:.2f}%")
        + _kv("Sharpe ratio", m.sharpe_ratio, lambda v: f"{v:.2f}")
        + _kv("Max drawdown", m.max_drawdown_pct, lambda v: f"{v:.2f}%")
        + _kv("CAGR (compound annual)", m.xirr_pct, lambda v: f"{v:+.2f}%")
    )
    risk_html = (
        '<table class="kvtable">' + risk_rows + "</table>"
        if any(getattr(m, f, None) and getattr(m, f).value is not None
               for f in ("volatility_30d_pct", "sharpe_ratio",
                         "max_drawdown_pct", "xirr_pct"))
        else '<p class="muted">Risk metrics need ≥ 60 days of cached '
             'daily candles AND ≥ 2 prior analyse runs (≥ 30 days '
             'apart) for CAGR. Re-run after the cache + DB warm up.</p>'
    )

    cash_html = ""
    if m.cash_balance and m.cash_balance.value is not None:
        cash_html = (
            '<h2>Cash position</h2><div class="card"><table class="kvtable">'
            + _kv("Cash balance", m.cash_balance,
                  lambda v: f"Rs.{v:,.0f}")
            + _kv("Cash drag (% of total account)", m.cash_drag_pct,
                  lambda v: f"{v:.2f}%")
            + "</table></div>"
        )

    group_html = ""
    if m.group_concentration and isinstance(m.group_concentration.value, dict) \
            and m.group_concentration.value:
        groups = sorted(m.group_concentration.value.items(),
                        key=lambda kv: -kv[1])
        group_rows = "".join(
            f'<tr><td>{html.escape(g)}</td><td>{p:.2f}%</td></tr>'
            for g, p in groups
        )
        group_html = (
            '<h2>Promoter-group concentration</h2><div class="card">'
            f'<table class="kvtable">{group_rows}</table></div>'
        )

    # Market-cap tier breakdown (P9).
    cap_tier_html = ""
    if m.cap_tier_weights and isinstance(m.cap_tier_weights.value, dict) \
            and m.cap_tier_weights.value:
        tier_order = ("LARGE", "MID", "SMALL", "ETF", "UNKNOWN")
        rows = []
        for tier in tier_order:
            pct = m.cap_tier_weights.value.get(tier)
            if pct is None:
                continue
            w = max(2, int(round(pct * 4)))
            tag = ""
            if tier == "UNKNOWN":
                tag = (' <span class="src" style="color:var(--warn-fg)">⚠ '
                       'refresh data/market_cap_tier.json</span>')
            rows.append(
                f'<tr><td>{tier}</td>'
                f'<td>{pct:.1f}% '
                f'<span class="sectorbar" style="width:{w}px"></span>{tag}</td></tr>'
            )
        cap_tier_html = (
            '<h2>Market-cap tier (AMFI)</h2><div class="card">'
            f'<table class="kvtable">{"".join(rows)}</table></div>'
        )

    return f"""
<h2>Sector weights</h2>
<div class="card">{sec_html}</div>
<h2>Concentration &amp; valuation</h2>
<div class="card">{core_html}</div>
<h2>Risk &amp; return</h2>
<div class="card">{risk_html}</div>
{cap_tier_html}
{group_html}
{cash_html}
"""


def _render_gaps(g: GapAnalysis) -> str:
    if not g.flags:
        return """
<h2>What's missing</h2>
<div class="card">
  <p>No structural gaps detected against benchmark. Diversification
  looks healthy.</p>
</div>
"""
    cards = []
    for f in g.flags:
        sugg = ""
        if f.suggested_symbols:
            sugg = ('<div class="sugg">Suggested additions: '
                    + " · ".join(html.escape(s) for s in f.suggested_symbols)
                    + "</div>")
        cards.append(
            f'<div class="flag {html.escape(f.severity)}">'
            f'<div class="head">[{html.escape(f.severity)}] '
            f'{html.escape(f.headline)}</div>'
            f'<div>{html.escape(f.detail)}</div>'
            f'{sugg}</div>'
        )
    return f"""
<h2>What's missing</h2>
<div class="card">
  <p class="muted">Benchmark: {html.escape(g.benchmark_label)}</p>
  {"".join(cards)}
</div>
"""


def _render_holdings_table(snap: PortfolioSnapshot) -> str:
    rows = []
    for s in snap.holdings:
        weight = _v(s.weight_in_portfolio_pct)
        pnl = _v(s.pnl)
        pnl_pct = _v(s.pnl_pct)
        cls = "pos" if pnl >= 0 else "neg"
        sign = "+" if pnl >= 0 else ""
        href = f"/portfolio/{html.escape(s.symbol)}"
        action = s.effective_action()
        cap = (s.market_cap_tier.value
               if s.market_cap_tier and s.market_cap_tier.value
               else "—") or "—"
        rows.append(
            f'<tr>'
            f'<td><a href="{href}">{html.escape(s.symbol)}</a></td>'
            f'<td class="muted">{html.escape(_v_str(s.sector, ""))}</td>'
            f'<td class="muted">{html.escape(str(cap))}</td>'
            f'<td class="right">{int(_v(s.qty))}</td>'
            f'<td class="right">Rs.{_v(s.avg_buy_price):,.2f}</td>'
            f'<td class="right">Rs.{_v(s.current_price):,.2f}</td>'
            f'<td class="right">Rs.{_v(s.current_value):,.0f}</td>'
            f'<td class="right">{weight:.1f}%</td>'
            f'<td class="right {cls}">{sign}Rs.{abs(pnl):,.0f} '
            f'({pnl_pct:+.2f}%)</td>'
            f'<td>{html.escape(action)}</td>'
            "</tr>"
        )
    return f"""
<h2>Holdings ({len(snap.holdings)})</h2>
<div class="card" style="padding: 8px 12px;">
  <table class="holdings">
    <thead><tr>
      <th>Symbol</th><th>Sector</th><th>Cap</th><th class="right">Qty</th>
      <th class="right">Avg</th><th class="right">LTP</th>
      <th class="right">Value</th><th class="right">Weight</th>
      <th class="right">P&amp;L</th><th>Rule action</th>
    </tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
</div>
"""


# ── Suggested-additions panel (clickable into drill-down) ───────

def _sector_for_symbol(symbol: str) -> str:
    """Resolve sector from the trade-mode SECTOR_MAP (lazy import to
    avoid pulling the trade-mode tree into dashboard cold paths)."""
    try:
        from modes.trade.stock_scanner import SECTOR_MAP
        return SECTOR_MAP.get(symbol, "OTHER")
    except Exception:
        return "OTHER"


def _render_suggested(snap: PortfolioSnapshot) -> str:
    """Below the holdings table: a clickable grid of suggested
    additions pulled from gap flags. Each card links to the same
    `/portfolio/<symbol>` drill-down which handles the unheld
    (wishlist) case automatically."""
    seen: set[str] = set()
    items: list[tuple[str, str, str]] = []   # (symbol, sector, reason)
    for f in snap.gaps.flags:
        for sym in f.suggested_symbols:
            if sym in seen:
                continue
            seen.add(sym)
            sector = _sector_for_symbol(sym)
            items.append((sym, sector, f.headline))
    if not items:
        return ""

    cards = []
    for sym, sec, reason in items:
        cards.append(
            '<div class="card-mini">'
            f'<a href="/portfolio/{html.escape(sym)}">{html.escape(sym)}</a>'
            f' <span class="meta">· {html.escape(sec)}</span>'
            f'<div class="why">{html.escape(reason)}</div>'
            '</div>'
        )
    return f"""
<h2>Suggested additions</h2>
<div class="card">
  <p class="muted">Pulled from the "what's missing" engine. Each card
  links to the drill-down page where you can see deterministic
  metrics for that name and run a single-stock <em>Analyse this
  stock</em> pass (NoAI free, AI optional). You don't need to own
  the stock to drill down.</p>
  <div class="sugg-grid">
    {"".join(cards)}
  </div>
</div>
"""


# ── /portfolio/<symbol> (D26 + D29 drill-down) ──────────────────

def render_stock_drilldown(symbol: str) -> str:
    """Per-stock drill-down. Pulls latest StockAnalysis from DB and a
    short history strip for D29. Provides per-stock 'Analyse this
    stock' buttons that re-run enrichment for ONLY this symbol
    (held → refresh row; not-held → wishlist run that pulls full
    NoAI enrichment + optional AI overlay so the user can evaluate
    a candidate before buying).

    The page also embeds a 1-year price chart with the user's
    average-cost line so the value of holding (or buying) can be
    seen at a glance.
    """
    sym = (symbol or "").strip().upper()
    if not sym or not sym.replace("-", "").replace("&", "").isalnum():
        return _wrap(
            "Invalid symbol", "Portfolio",
            f'<div class="card"><p>Invalid symbol: <code>'
            f'{html.escape(sym)}</code></p></div>',
        )
    s = latest_for_symbol(sym)
    history = history_for_symbol(sym, limit=5)
    is_held = bool(s and s.qty and (s.qty.value or 0) > 0)
    body = [
        '<a class="back-link" href="/portfolio">\u2190 Back to portfolio</a>',
        _render_drilldown_header(sym, s, is_held=is_held),
        _render_drilldown_actions(sym),
    ]
    if s is not None:
        if is_held:
            body.append(_render_drilldown_position(s))
        else:
            body.append(_render_drilldown_wishlist_card(sym))
        body.append(_render_drilldown_market(s))
        body.append(_render_drilldown_chart(sym, s))
        body.append(_render_drilldown_rule(s))
        body.append(_render_drilldown_ai(s))
        body.append(_render_drilldown_history(sym, history))
    else:
        body.append(_render_drilldown_chart(sym, None))
    return _wrap(f"{sym} · Drill-down", "Portfolio", "".join(body),
                 holdings_count=1)


def _render_drilldown_header(sym: str, s: StockAnalysis | None, *,
                             is_held: bool) -> str:
    if s is None:
        sec = _sector_for_symbol(sym)
        return f"""
<h1 class="page-title">{html.escape(sym)}</h1>
<div class="sub">{html.escape(sec)} · no analysis on file yet</div>
<div class="card">
  <p>This symbol isn't in your latest snapshot. Click <em>Analyse
  this stock</em> below to fetch a deterministic snapshot (live
  price, 52-week range, beta vs NIFTY, sector, market-cap tier,
  P/E, dividend yield, RSI / SMA-50 / SMA-200) — and optionally
  add the Claude AI overlay (long-term thesis, risks, peer
  comparison, recent news).</p>
</div>
"""
    age = _staleness_label(s.most_stale_at())
    weight = _v(s.weight_in_portfolio_pct)
    badge = ('<span class="banner info" style="display:inline-block; margin:0 0 0 8px;">'
             f'{"HELD" if is_held else "WISHLIST"}</span>')
    return f"""
<h1 class="page-title">{html.escape(s.symbol)} ({html.escape(s.exchange)}) {badge}</h1>
<div class="sub">Most stale field: {html.escape(age)} ·
  weight in portfolio: {weight:.1f}%</div>
"""


def _render_drilldown_wishlist_card(sym: str) -> str:
    return f"""
<h2>Position</h2>
<div class="card">
  <p>You don't hold {html.escape(sym)} (qty = 0). The drill-down
  shows deterministic market data + (optionally) AI thesis so you
  can decide whether to add it. Use <em>Analyse this stock</em>
  above to refresh the data.</p>
</div>
"""


def _render_drilldown_position(s: StockAnalysis) -> str:
    pnl = _v(s.pnl)
    cls = "pos" if pnl >= 0 else "neg"
    return f"""
<h2>Position</h2>
<div class="card"><table class="kvtable">
  <tr><td>Quantity</td><td>{int(_v(s.qty))}</td></tr>
  <tr><td>Average buy price</td><td>Rs.{_v(s.avg_buy_price):,.2f}</td></tr>
  <tr><td>Current price</td><td>Rs.{_v(s.current_price):,.2f}
      <span class="src">· {html.escape(_src_tag(s.current_price))}</span></td></tr>
  <tr><td>Invested value</td><td>Rs.{_v(s.invested_value):,.0f}</td></tr>
  <tr><td>Current value</td><td>Rs.{_v(s.current_value):,.0f}</td></tr>
  <tr><td>P&amp;L</td><td class="{cls}">Rs.{pnl:+,.0f} ({_v(s.pnl_pct):+.2f}%)</td></tr>
</table></div>
"""


def _render_drilldown_market(s: StockAnalysis) -> str:
    cap_tier = (s.market_cap_tier.value
                if s.market_cap_tier and s.market_cap_tier.value
                else "n/a") or "n/a"
    cap_src  = (_src_tag(s.market_cap_tier)
                if s.market_cap_tier else "missing")
    return f"""
<h2>Market context</h2>
<div class="card"><table class="kvtable">
  <tr><td>Sector</td><td>{html.escape(_v_str(s.sector, ""))}
      <span class="src">· {html.escape(_src_tag(s.sector))}</span></td></tr>
  <tr><td>Market-cap tier (AMFI)</td>
      <td>{html.escape(str(cap_tier))}
      <span class="src">· {html.escape(cap_src)}</span></td></tr>
  <tr><td>52-week range</td>
      <td>Rs.{_v(s.low_52w):,.2f} – Rs.{_v(s.high_52w):,.2f}
      ({_v(s.price_vs_high_52w_pct):+.2f}% from high)
      <span class="src">· {html.escape(_src_tag(s.high_52w))}</span></td></tr>
  <tr><td>Beta vs NIFTY</td><td>{_v(s.beta_vs_nifty):.2f}
      <span class="src">· {html.escape(_src_tag(s.beta_vs_nifty))}</span></td></tr>
  <tr><td>Dividend yield (TTM)</td><td>{_v(s.dividend_yield_ttm):.2f}%
      <span class="src">· {html.escape(_src_tag(s.dividend_yield_ttm))}</span></td></tr>
  <tr><td>P/E (TTM)</td><td>{_v(s.weighted_pe):.1f}
      <span class="src">· {html.escape(_src_tag(s.weighted_pe))}</span></td></tr>
  <tr><td>RSI (daily, 14)</td><td>{_v(s.rsi_daily):.1f}</td></tr>
  <tr><td>SMA-50</td><td>Rs.{_v(s.sma_50):,.2f}</td></tr>
  <tr><td>SMA-200</td><td>Rs.{_v(s.sma_200):,.2f}</td></tr>
  <tr><td>Above SMA-200</td><td>
      {"yes" if _v_bool(s.above_sma_200) else "no"}</td></tr>
</table></div>
"""


def _render_drilldown_rule(s: StockAnalysis) -> str:
    return f"""
<h2>Rule-based recommendation</h2>
<div class="card"><table class="kvtable">
  <tr><td>Action</td><td><strong>{html.escape(_v_str(s.rule_action, "HOLD"))}</strong></td></tr>
  <tr><td>Conviction</td><td>{html.escape(_v_str(s.rule_conviction, "Medium"))}</td></tr>
  <tr><td>Horizon</td><td>{html.escape(_v_str(s.rule_horizon, ""))}</td></tr>
  <tr><td>Target price</td><td>{html.escape(_v_str(s.rule_target_price, ""))}</td></tr>
</table>
<p style="margin-top: 10px;">{html.escape(_v_str(s.rule_reasoning, ""))}</p>
</div>
"""


def _render_drilldown_ai(s: StockAnalysis) -> str:
    if not (s.ai_thesis_long_term and s.ai_thesis_long_term.value):
        return """
<h2>AI overlay</h2>
<div class="card">
  <p class="muted">Not populated for this run. Re-run with the
  <em>Analyse all (AI)</em> button below to get long-term thesis,
  qualitative risks, peer comparison, and recent-news context.</p>
</div>
"""
    risks_html = ""
    if s.ai_qualitative_risks and s.ai_qualitative_risks.value:
        items = "".join(f"<li>{html.escape(r)}</li>"
                        for r in (s.ai_qualitative_risks.value or []))
        risks_html = f"<h3>Risks</h3><ul>{items}</ul>"
    sec = lambda title, fld: (
        f"<h3>{html.escape(title)}</h3>"
        f"<p>{html.escape(str(fld.value))}</p>"
        if fld and fld.value else ""
    )
    action_html = ""
    if s.ai_action and s.ai_action.value:
        det = s.ai_action_detail.value if s.ai_action_detail and s.ai_action_detail.value else ""
        action_html = (
            f"<h3>AI action</h3>"
            f"<p><strong>{html.escape(str(s.ai_action.value))}</strong>"
            + (f" — {html.escape(det)}" if det else "")
            + "</p>"
        )
    thesis_text = str(s.ai_thesis_long_term.value)
    return f"""
<h2>AI overlay</h2>
<div class="card">
  <h3>Thesis (long-term)</h3>
  <pre style="white-space: pre-wrap; margin: 6px 0; font: inherit;">{html.escape(thesis_text)}</pre>
  {risks_html}
  {sec("Peer comparison", s.ai_peer_comparison)}
  {sec("Recent news (30 days)", s.ai_news_context)}
  {sec("Change vs prior analysis", s.ai_change_vs_prior)}
  {action_html}
</div>
"""


def _render_drilldown_history(sym: str, history: list[StockAnalysis]) -> str:
    if not history:
        return ""
    tiles = []
    for h in history:
        when = h.most_stale_at().strftime("%Y-%m-%d %H:%M")
        action = h.effective_action()
        pnl_pct = (h.pnl_pct.value or 0) if h.pnl_pct else 0
        cls = "pos" if pnl_pct >= 0 else "neg"
        tiles.append(
            f'<div class="tile">'
            f'<div class="when">{html.escape(when)}</div>'
            f'<div class="act">{html.escape(action)}</div>'
            f'<div class="muted">P&L {pnl_pct:+.2f}%</div>'
            f'</div>'
        )
    drift = _action_drift_message(history)
    return f"""
<h2>History</h2>
<div class="card">
  {drift}
  <div class="history-strip">{"".join(tiles)}</div>
</div>
"""


def _action_drift_message(history: list[StockAnalysis]) -> str:
    if len(history) < 2:
        return ""
    latest = history[0].effective_action()
    prior  = history[1].effective_action()
    if latest == prior:
        return (f'<p class="muted">Action unchanged since previous run '
                f'({html.escape(prior)}).</p>')
    return (f'<div class="banner warn">Action drift: '
            f'<strong>{html.escape(prior)}</strong> → '
            f'<strong>{html.escape(latest)}</strong> on the latest run.</div>')


def _render_drilldown_actions(sym: str) -> str:
    """Per-stock actions card: AI toggle + 'Analyse this stock' (single)
    + 'Re-analyse all' (full portfolio)."""
    return f"""
<div class="card">
  <h2>Refresh</h2>
  <p class="muted">Single-stock refresh re-fetches live price + 1y
  candles + indicators for {html.escape(sym)} only and merges into
  the latest snapshot. Portfolio metrics + gaps stay as-is. Re-running
  the full portfolio also recomputes those.</p>
  <p>
    {_ai_toggle_html()}
  </p>
  <p>
    <button class="action"
      onclick="runAnalysis('symbol:{html.escape(sym)}')">Analyse this stock</button>
    <button class="action alt"
      onclick="runAnalysis('all')">Re-analyse all</button>
  </p>
  <div id="job-banner"></div>
</div>
{_runs_polling_script()}
"""


def _render_drilldown_chart(sym: str, s: StockAnalysis | None) -> str:
    """Embed a 1-year price chart for `sym`. Data is fetched async via
    `/api/stock_chart?symbol=X`. Renders a horizontal line at the
    user's average buy price (when held) so cost vs current is
    visible at a glance."""
    avg_attr = ""
    if s and s.avg_buy_price and s.avg_buy_price.value:
        avg_attr = f' data-avg-price="{float(s.avg_buy_price.value):.2f}"'
    return f"""
<h2>Price history (1 year)</h2>
<div class="card chart-card" id="chart-card-stock"
     data-symbol="{html.escape(sym)}"{avg_attr}>
  <p id="chart-stock-msg" class="muted"><span class="spinner"></span>
    Loading {html.escape(sym)} candles from local cache\u2026</p>
  <canvas id="chart-stock" style="display:none;"></canvas>
</div>
<script>
(function () {{
  if (typeof Chart === 'undefined') return;
  var card = document.getElementById('chart-card-stock');
  var sym = card.getAttribute('data-symbol');
  var avg = parseFloat(card.getAttribute('data-avg-price') || '0');
  fetch('/api/stock_chart?symbol=' + encodeURIComponent(sym))
    .then(function (r) {{ return r.json(); }})
    .then(function (j) {{
      var msg = document.getElementById('chart-stock-msg');
      if (!j.dates || j.dates.length === 0) {{
        msg.innerHTML = 'No daily candles cached for ' + sym
          + '. Run intraday trade-mode at least once with this symbol '
          + 'in the universe to populate <code>data/candle_cache.db</code>, '
          + 'or wait for the next scheduled cache refresh.';
        return;
      }}
      msg.style.display = 'none';
      var canvas = document.getElementById('chart-stock');
      canvas.style.display = 'block';
      var datasets = [{{
        label: sym + ' close',
        data: j.closes,
        borderColor: '#3457d5',
        backgroundColor: 'rgba(52, 87, 213, 0.08)',
        borderWidth: 1.6,
        pointRadius: 0,
        tension: 0.15,
        fill: true,
      }}];
      if (avg > 0) {{
        datasets.push({{
          label: 'Your avg cost (Rs.' + avg.toFixed(2) + ')',
          data: j.dates.map(function () {{ return avg; }}),
          borderColor: '#c62828',
          borderWidth: 1.2,
          borderDash: [6, 4],
          pointRadius: 0,
          fill: false,
        }});
      }}
      new Chart(canvas, {{
        type: 'line',
        data: {{ labels: j.dates, datasets: datasets }},
        options: {{
          plugins: {{
            legend: {{ position: 'top', labels: {{ boxWidth: 14 }} }},
            tooltip: {{ mode: 'index', intersect: false }},
          }},
          interaction: {{ mode: 'index', intersect: false }},
          scales: {{
            x: {{ ticks: {{ maxTicksLimit: 10, autoSkip: true }} }},
            y: {{ ticks: {{ callback: function (v) {{
              return 'Rs.' + v.toFixed(0);
            }} }} }},
          }},
        }},
      }});
    }})
    .catch(function (e) {{
      var m = document.getElementById('chart-stock-msg');
      if (m) m.innerHTML = 'Chart load error: ' + e;
    }});
}})();
</script>
"""


# ── /login (D28 — Zerodha auth on the dashboard) ────────────────

def render_login_page() -> str:
    today = datetime.date.today().isoformat()
    valid_until_today = False
    cached_at = None
    try:
        if os.path.exists(_TOKEN_PATH):
            with open(_TOKEN_PATH, encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("date") == today:
                valid_until_today = True
                cached_at = saved.get("date")
    except Exception:
        pass

    api_key = getattr(Config, "ZERODHA_API_KEY", "") or ""
    login_url = (
        f"https://kite.trade/connect/login?api_key={api_key}&v=3"
        if api_key else ""
    )

    if valid_until_today:
        status_html = f"""
<div class="banner info">
  Today's Zerodha access token is valid (cached for {html.escape(cached_at)}).
  It will expire at midnight; come back tomorrow morning to re-login.
</div>
"""
    else:
        status_html = """
<div class="banner warn">
  No valid Zerodha access token for today. Re-login below to enable
  the live-data buttons (Analyse now, holdings refresh).
</div>
"""

    # Check if assisted login is available (KITE_USER_ID + KITE_PASSWORD set)
    has_creds = bool(
        (getattr(Config, "KITE_USER_ID", "") or "")
        and (getattr(Config, "KITE_PASSWORD", "") or "")
    )

    assisted_html = ""
    if has_creds:
        user_id = getattr(Config, "KITE_USER_ID", "")
        assisted_html = f"""
<h2>Quick login (saved credentials)</h2>
<div class="card">
  <p>Your Kite user ID <strong>{html.escape(user_id)}</strong> and password are
  saved in <code>.env</code>. Just enter your 6-digit authenticator code below.</p>
  <form method="post" action="/api/login_assisted" style="margin-top: 10px;">
    <div style="display:flex;gap:8px;align-items:center">
      <input type="text" name="otp" required
        placeholder="6-digit code"
        pattern="[0-9]{{6}}" maxlength="6" inputmode="numeric"
        autocomplete="one-time-code"
        style="width: 140px; padding: 8px 10px; font: inherit; font-size: 18px;
               letter-spacing: 4px; text-align: center;
               border: 1px solid #cfd9eb; border-radius: 5px;" />
      <button class="action" type="submit">Login</button>
    </div>
  </form>
  <p class="muted" style="margin-top: 8px; font-size: 12px;">
    Open your authenticator app (Apple Passwords / Authy / Google Auth)
    and enter the current 6-digit code for Zerodha Kite.
  </p>
</div>
"""

    body = f"""
<h1 class="page-title">Zerodha Login</h1>
<div class="sub">
  Kite access tokens expire at midnight every day; the bot re-uses
  today's token until then.
</div>
{status_html}
{assisted_html}
<h2>Manual login (paste redirect URL)</h2>
<div class="card">
  <ol>
    <li>Click the link to open Zerodha's login page in a new tab.
        Log in with your Kite credentials + 2FA.</li>
    <li>You'll be redirected to a URL of the form
        <code>http://localhost:8080/?status=success&request_token=...&action=login</code>.</li>
    <li>Copy the FULL redirect URL from your address bar and paste it
        below, then click Submit.</li>
  </ol>
  {('<p><a href="' + html.escape(login_url) + '" target="_blank" rel="noopener">'
    'Open Zerodha login →</a></p>') if login_url else
   '<p class="warn">ZERODHA_API_KEY missing in <code>.env</code>; '
   'cannot generate the login URL.</p>'}
  <form method="post" action="/api/login_submit" style="margin-top: 10px;">
    <input type="text" name="redirect_url" required
      placeholder="Paste full redirect URL here…"
      style="width: 100%; padding: 8px 10px; font: inherit;
             border: 1px solid #cfd9eb; border-radius: 5px;" />
    <p style="margin-top: 10px;">
      <button class="action" type="submit">Submit token</button>
    </p>
  </form>
  <p class="muted" style="margin-top: 14px;">
    For zero-touch / ASSISTED login (env-driven password + TOTP),
    see <a href="/theory/trade-strategy">README §5.4</a>. Those modes
    are configured in <code>.env</code> and run from the CLI; the
    dashboard exposes only the manual paste-back flow because it's
    the safest from a single-page browser context.
  </p>
</div>
"""
    return _wrap("Login", "Login", body)


# ── /api/stock_chart response builder ───────────────────────────

_CANDLE_CACHE_PATH = os.path.join("data", "candle_cache.db")


def render_stock_chart_json(symbol: str, *,
                            lookback_days: int = 365) -> str:
    """JSON for the per-stock price chart on the drill-down page.

    Reads daily closes from `data/candle_cache.db` for the last
    `lookback_days` calendar days. Returns:
      {dates: [...], closes: [...], lookback_days: int, symbol: str}
    Empty arrays when no cache exists for the symbol — the page
    shows a friendly "no candles" message in that case.
    """
    sym = (symbol or "").strip().upper()
    if not sym or not sym.replace("-", "").replace("&", "").isalnum():
        return json.dumps({"dates": [], "closes": [], "symbol": sym,
                           "error": "invalid symbol"})
    if not os.path.exists(_CANDLE_CACHE_PATH):
        return json.dumps({"dates": [], "closes": [], "symbol": sym,
                           "error": "no candle cache"})
    rows: list[tuple[str, float]] = []
    try:
        conn = sqlite3.connect(_CANDLE_CACHE_PATH)
        try:
            res = conn.execute(
                """SELECT candle_date, close FROM candle_cache
                   WHERE symbol = ? AND interval = 'day'
                   ORDER BY candle_date DESC LIMIT ?""",
                (sym, int(lookback_days)),
            ).fetchall()
            # Newest-first → flip to chronological for charting.
            rows = [(r[0], float(r[1])) for r in res if r[1] is not None]
            rows.reverse()
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        return json.dumps({"dates": [], "closes": [], "symbol": sym,
                           "error": str(e)[:200]})
    # Strip time component if present (cache stores
    # "YYYY-MM-DD HH:MM:SS").
    dates = [r[0].split(" ")[0] for r in rows]
    closes = [round(r[1], 2) for r in rows]
    return json.dumps({
        "symbol": sym,
        "dates": dates,
        "closes": closes,
        "lookback_days": int(lookback_days),
    })


# ── /api/run_status response builder ────────────────────────────

def render_status_json() -> str:
    """JSON snippet for the polling page. Returned by `/api/run_status`."""
    job = latest_status()
    if job is None:
        return json.dumps({"status": "IDLE"})
    out = {
        "job_id":       job.job_id,
        "mode":         job.mode,
        "scope":        job.scope,
        "status":       job.status,
        "started_at":   job.started_at.isoformat() if job.started_at else None,
        "finished_at":  (job.finished_at.isoformat()
                         if job.finished_at else None),
        "error":        job.error,
        "db_run_id":    job.db_run_id,
    }
    return json.dumps(out)


# ── Polling JS (shared across pages with action buttons) ────────

def _runs_polling_script() -> str:
    return r"""
<script>
function _setBanner(msg, kind) {
  var host = document.getElementById('job-banner');
  if (!host) return;
  var spin = (kind === 'info') ? '<span class="spinner"></span>' : '';
  host.innerHTML = '<div class="banner ' + kind + '">' + spin + msg + '</div>';
}

function _disableButtons(disabled) {
  document.querySelectorAll('button.action').forEach(function (b) {
    b.disabled = disabled;
  });
}

function _aiToggleOn() {
  var t = document.getElementById('ai-toggle-input');
  return !!(t && t.checked);
}

// Run an analyse job. `scope` is 'all' or 'symbol:HDFCBANK'.
// Mode is taken from the AI toggle (defaults to NoAI).
function runAnalysis(scope) {
  var mode = _aiToggleOn() ? 'AI' : 'NOAI';
  if (mode === 'AI') {
    var hold = parseInt(document.body.getAttribute('data-holdings') || '0', 10);
    var per = parseFloat(document.body.getAttribute('data-ai-per-call') || '3');
    var cost = (hold > 0 ? hold * per : per);
    var msg = (scope === 'all'
      ? 'Estimated Claude cost ~Rs.' + cost.toFixed(0) + ' for ' + hold + ' holdings. Proceed?'
      : 'AI overlay for one stock will use ~Rs.' + per.toFixed(0) + '. Proceed?');
    if (!confirm(msg)) { return; }
  }
  // Show spinner IMMEDIATELY so the user sees feedback on click.
  var label = (scope === 'all' ? 'Analysing your full portfolio' : 'Analysing ' + scope.replace('symbol:', ''));
  _setBanner(label + ' (' + mode + ')\u2026 this can take 30-90 seconds for 30+ holdings.', 'info');
  _disableButtons(true);
  var url = '/api/analyse_run?mode=' + mode + '&scope=' + encodeURIComponent(scope);
  fetch(url, {method: 'POST'})
    .then(function (r) { return r.json(); })
    .then(function (j) {
      _setBanner(label + ' \u2014 job #' + j.job_id + ' started\u2026', 'info');
      _poll();
    })
    .catch(function (e) {
      _setBanner('Error: ' + e, 'warn');
      _disableButtons(false);
    });
}

function _poll() {
  setTimeout(function () {
    fetch('/api/run_status').then(function (r) { return r.json(); }).then(function (j) {
      if (j.status === 'IDLE') return;
      if (j.status === 'RUNNING') {
        var scopeLabel = j.scope === 'all' ? 'full portfolio'
                                            : (j.scope || '').replace('symbol:', '');
        _setBanner('Job #' + j.job_id + ' running\u2026 (' + j.mode
                   + ' / ' + scopeLabel + ')', 'info');
        _poll();
      } else if (j.status === 'DONE') {
        _setBanner('Job #' + j.job_id + ' done \u2014 refreshing page\u2026', 'info');
        setTimeout(function () { window.location.reload(); }, 1200);
      } else {
        _setBanner('Job #' + j.job_id + ' FAILED: '
                   + (j.error || 'unknown error'), 'warn');
        _disableButtons(false);
      }
    });
  }, 1500);
}

window.addEventListener('DOMContentLoaded', function () {
  // If a job is already in flight when the page loads, surface it.
  fetch('/api/run_status').then(function (r) { return r.json(); }).then(function (j) {
    if (j && j.status === 'RUNNING') {
      var scopeLabel = j.scope === 'all' ? 'full portfolio'
                                          : (j.scope || '').replace('symbol:', '');
      _setBanner('Job #' + j.job_id + ' already running\u2026 ('
                 + j.mode + ' / ' + scopeLabel + ')', 'info');
      _disableButtons(true);
      _poll();
    }
  });
});
</script>
"""


def _ai_toggle_html() -> str:
    """Shared AI/NoAI toggle. Read by `runAnalysis()` JS."""
    return ('<label class="ai-toggle" title="Toggle to add Claude qualitative overlay (~Rs.5/stock)">'
            '<input type="checkbox" id="ai-toggle-input">'
            '<span class="lbl">Use Claude AI overlay</span>'
            '<span class="hint">(NoAI is the default; AI adds thesis + risks + news)</span>'
            '</label>')


# ── Shared shell ────────────────────────────────────────────────

def _wrap(title: str, here: str, body: str,
          *, holdings_count: int = 0) -> str:
    per_call = float(getattr(Config, "CLAUDE_COST_PER_CALL", 3.0))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Portfolio Manager — {html.escape(title)}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>{_STYLE}</style>
</head>
<body data-holdings="{holdings_count}" data-ai-per-call="{per_call:.2f}">
<div class="wrap">
  {_topnav(here)}
  {body}
  <footer>
    AI Portfolio Manager · read-only dashboard · data persisted to
    <code>data/portfolio_analyses.db</code>
  </footer>
</div>
</body>
</html>"""


# ── Field helpers ──────────────────────────────────────────────

def _v(field: Field | None, default: float = 0.0) -> float:
    if field is None or field.value is None:
        return default
    try:
        return float(field.value)
    except (TypeError, ValueError):
        return default


def _v_str(field: Field | None, default: str) -> str:
    if field is None or field.value is None:
        return default
    return str(field.value)


def _v_bool(field: Field | None) -> bool:
    if field is None or field.value is None:
        return False
    return bool(field.value)


def _src_tag(field: Field | None) -> str:
    if field is None or field.value is None:
        return "missing"
    return f"{field.source} · {field.staleness_label}"


def _staleness_label(ts: datetime.datetime | None) -> str:
    if ts is None:
        return "unknown"
    delta = datetime.datetime.now() - ts
    mins = max(0, int(delta.total_seconds() // 60))
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    hrs, mins = divmod(mins, 60)
    if hrs < 24:
        return f"{hrs}h {mins}m ago"
    days = hrs // 24
    return f"{days}d ago" if days < 30 else ts.strftime("%Y-%m-%d")


def _inr_html(amount: float) -> str:
    sign = "-" if amount < 0 else ""
    n = abs(int(round(amount)))
    s = str(n)
    if len(s) <= 3:
        return f"Rs.{sign}{s}"
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return f"Rs.{sign}{','.join(parts)},{tail}"
