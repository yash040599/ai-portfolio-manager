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
    else:
        body.append(_render_header(snap))
        body.append(_render_actions(snap))
        body.append(_render_metrics(snap.metrics))
        body.append(_render_gaps(snap.gaps))
        body.append(_render_holdings_table(snap))

    return _wrap("Portfolio", "Portfolio", "".join(body))


def _empty_state() -> str:
    return f"""
<div class="card">
  <h2>No analysis on file yet</h2>
  <p>Click below to run your first NoAI portfolio analysis. NoAI is
  free (no Claude calls). Add the AI overlay any time after the first
  run for qualitative thesis / risks / news context.</p>
  <p>
    <button class="action"
      onclick="runAnalysis('NOAI', 'all')">Analyse all (NoAI)</button>
    <button class="action alt"
      onclick="runAnalysis('AI', 'all')">Analyse all (AI)</button>
  </p>
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
    cost = estimate_ai_cost(len(snap.holdings))
    return f"""
<div class="card">
  <h2>Run a new analysis</h2>
  <p class="muted">A new run reads live prices + cached candles +
  reference seeds, persists to <code>data/portfolio_analyses.db</code>,
  and refreshes this page.</p>
  <p>
    <button class="action"
      onclick="runAnalysis('NOAI', 'all')">Analyse all (NoAI)</button>
    <button class="action alt"
      onclick="runAnalysisAi({len(snap.holdings)}, {cost:.0f})">Analyse all (AI)</button>
  </p>
  <div id="job-banner"></div>
</div>
{_runs_polling_script()}
"""


def _render_metrics(m: PortfolioMetrics) -> str:
    rows = []
    if m.sector_weights:
        bars = []
        for sw in m.sector_weights:
            w = max(2, int(round(sw.weight_pct * 4)))
            bars.append(
                f'<tr><td>{html.escape(sw.sector)}</td>'
                f'<td>{sw.weight_pct:.1f}% '
                f'<span class="sectorbar" style="width:{w}px"></span></td></tr>'
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


# ── /portfolio/<symbol> (D26 + D29 drill-down) ──────────────────

def render_stock_drilldown(symbol: str) -> str:
    """Per-stock drill-down. Pulls latest StockAnalysis from DB and a
    short history strip for D29. Provides per-stock 'Analyse now'
    buttons (re-runs the FULL portfolio — running just one stock
    in the new pipeline isn't supported and would give wrong
    metrics anyway since metrics need the full set)."""
    sym = (symbol or "").strip().upper()
    if not sym or not sym.replace("-", "").replace("&", "").isalnum():
        return _wrap(
            "Invalid symbol", "Portfolio",
            f'<div class="card"><p>Invalid symbol: <code>'
            f'{html.escape(sym)}</code></p></div>',
        )
    s = latest_for_symbol(sym)
    history = history_for_symbol(sym, limit=5)
    body = [_render_drilldown_header(sym, s)]
    if s is not None:
        body.append(_render_drilldown_position(s))
        body.append(_render_drilldown_market(s))
        body.append(_render_drilldown_rule(s))
        body.append(_render_drilldown_ai(s))
        body.append(_render_drilldown_history(sym, history))
    body.append(_render_drilldown_actions())
    return _wrap(f"{sym} · Drill-down", "Portfolio", "".join(body))


def _render_drilldown_header(sym: str, s: StockAnalysis | None) -> str:
    if s is None:
        return f"""
<h1 class="page-title">{html.escape(sym)}</h1>
<div class="sub">No analysis on file for this symbol yet.</div>
<div class="card">
  <p>Run an analyse pass first (button below). The pipeline writes
  every holding from your Zerodha demat to the DB; if this symbol
  isn't in your demat, it won't appear here.</p>
</div>
"""
    age = _staleness_label(s.most_stale_at())
    weight = _v(s.weight_in_portfolio_pct)
    return f"""
<h1 class="page-title">{html.escape(s.symbol)} ({html.escape(s.exchange)})</h1>
<div class="sub">Most stale field: {html.escape(age)} ·
  weight in portfolio: {weight:.1f}%</div>
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


def _render_drilldown_actions() -> str:
    # Drill-down can only re-run the FULL portfolio — single-stock
    # mode would give wrong portfolio metrics + gaps, so don't
    # pretend it works.
    return f"""
<h2>Re-analyse</h2>
<div class="card">
  <p class="muted">Re-running refreshes the whole portfolio (live
  prices, indicators, rule engine). Single-stock-only runs aren't
  supported — they'd give wrong portfolio metrics.</p>
  <p>
    <button class="action"
      onclick="runAnalysis('NOAI', 'all')">Re-analyse all (NoAI)</button>
    <button class="action alt"
      onclick="runAnalysisAi(0, 0)">Re-analyse all (AI)</button>
  </p>
  <div id="job-banner"></div>
</div>
{_runs_polling_script()}
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

    body = f"""
<h1 class="page-title">Zerodha Login</h1>
<div class="sub">
  Kite access tokens expire at midnight every day; the bot re-uses
  today's token until then.
</div>
{status_html}

<h2>Manual login (works on any device)</h2>
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
  const host = document.getElementById('job-banner');
  if (!host) return;
  host.innerHTML = '<div class="banner ' + kind + '">' + msg + '</div>';
}
function runAnalysis(mode, scope) {
  _setBanner('Submitting ' + mode + ' run…', 'info');
  fetch('/api/analyse_run?mode=' + mode + '&scope=' + scope, {method: 'POST'})
    .then(r => r.json())
    .then(j => {
      _setBanner('Job #' + j.job_id + ' ' + j.status + '…', 'info');
      _poll();
    })
    .catch(e => _setBanner('Error: ' + e, 'warn'));
}
function runAnalysisAi(holdings_count, est_cost) {
  const msg = est_cost
    ? 'Estimated Claude cost ~Rs.' + est_cost + ' for '
      + holdings_count + ' holdings. Proceed?'
    : 'Re-run with Claude AI overlay (will use credits). Proceed?';
  if (!confirm(msg)) return;
  runAnalysis('AI', 'all');
}
function _poll() {
  setTimeout(function() {
    fetch('/api/run_status').then(r => r.json()).then(j => {
      if (j.status === 'IDLE') return;
      if (j.status === 'RUNNING') {
        _setBanner('Job #' + j.job_id + ' running… (' + j.mode + ')', 'info');
        _poll();
      } else if (j.status === 'DONE') {
        _setBanner('Job #' + j.job_id + ' DONE — refreshing page…', 'info');
        setTimeout(function() { window.location.reload(); }, 1500);
      } else {
        _setBanner('Job #' + j.job_id + ' FAILED: '
                   + (j.error || 'unknown error'), 'warn');
      }
    });
  }, 2000);
}
window.addEventListener('DOMContentLoaded', function() {
  fetch('/api/run_status').then(r => r.json()).then(j => {
    if (j && j.status === 'RUNNING') {
      _setBanner('Job #' + j.job_id + ' already running… (' + j.mode + ')',
                 'info');
      _poll();
    }
  });
});
</script>
"""


# ── Shared shell ────────────────────────────────────────────────

def _wrap(title: str, here: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AI Portfolio Manager — {html.escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
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
