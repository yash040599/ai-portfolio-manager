"""US stock dashboard page.

Mirrors the Indian Swing dashboard (`modes/dashboard/swing_page.py`)
in structure and design: same card layout, same kvtable detail view,
same data-live-symbol live-price poller, plus a USD<->INR currency
toggle in the topnav so all dollar values can be flipped to rupees.
Stock holdings persist via the shared Swing schema, partitioned by
the `exchange` column (NASDAQ / NYSE / AMEX / ARCA).
"""

from __future__ import annotations

import html
import json
from typing import Any

from config import Config, now_ist
from modes.dashboard import us_config
from modes.dashboard.us_analysis import (
    analyse_us_symbol, analyse_us_universe, latest_us_scan,
    cached_us_live_quotes, get_us_live_quotes,
    get_us_stock_name, cached_usd_inr_rate, get_usd_inr_rate,
    build_us_health_checks,
)
from modes.dashboard.nav import render_topnav, topnav_css
from modes.swing.persistence import (
    init_db, open_positions, get_watchlist, realised_pnl_summary,
)
from modes.swing.types import SwingPosition


US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA", "US"}


# ── Page renderers ─────────────────────────────────────────────

def render_us_page() -> str:
    """Render the /us dashboard page."""
    init_db()
    default_ticket = float(us_config.US_TICKET_AMOUNT)

    positions = _us_positions()
    positions.sort(key=lambda p: (p.exchange, p.symbol, p.position_id))
    watchlist = _us_watchlist()
    latest_scan = latest_us_scan() or {}
    fx = cached_usd_inr_rate()
    pnl = realised_pnl_summary(exchange=None)  # filter to US below
    pnl = _filter_pnl_for_us(pnl)
    live_positions = cached_us_live_quotes([p.symbol for p in positions]) if positions else {}

    # Symbols that can opt into live polling after the first paint.
    live_syms = sorted({p.symbol for p in positions}
                       | {w.symbol for w in watchlist}
                       | {c.get("symbol", "")
                          for c in (latest_scan.get("candidates") or [])
                          if c.get("symbol")})

    body: list[str] = []
    body.append(_topnav("/us", fx))
    body.append('<div class="wrap">')
    body.append('<h1 class="page-title">US Trading</h1>')
    body.append(_render_freshness(latest_scan, bool(live_syms), fx))

    # P&L summary card (mirrors swing's "Realised Swing P&L").
    body.append(_render_pnl_card(pnl, positions, live_positions))

    # Daily Scan card.
    body.append(_render_scan_card(default_ticket, latest_scan))

    # What-changed-since-last-scan card (mirrors the Indian Swing
    # diff card so every analyse-run click surfaces exactly what
    # entered, dropped, or moved in rank. Diff data is loaded
    # client-side from `/api/us/changes_since` (see _js below) so a
    # fresh in-page scan re-fetches it without a full reload.
    # Origin: 2026-05-19 user asked for the same "what changed"
    # signal on /us that they already get on /swing.
    body.append('<div class="card">')
    body.append('<h2>What changed since last scan</h2>')
    body.append(
        '<p class="muted" style="margin-bottom:10px">'
        'Compares the latest US scan against the immediately '
        'previous scan. New entries, drops, and rank moves of 3+ '
        'positions are highlighted.</p>'
    )
    body.append('<div id="us-changes-since-host">'
                '<span class="muted">Loading\u2026</span></div>')
    body.append('</div>')

    # Single-stock analyse card.
    body.append(_render_single_stock_card(default_ticket))

    # Compare up to 4 stocks (matches the Indian Swing compare flow).
    body.append(_render_compare_card(default_ticket))

    body.append(_render_us_sections_loader())

    body.append('</div>')  # /.wrap
    body.append(_js(fx))
    return _wrap("US", body, fx)


def render_us_sections_json() -> str:
    init_db()
    positions = _us_positions()
    positions.sort(key=lambda p: (p.exchange, p.symbol, p.position_id))
    watchlist = _us_watchlist()
    latest_scan = latest_us_scan() or {}

    # 2026-05-19: open book + watchlist symbols get a LIVE fetch on
    # initial render so the first paint shows real prices instead
    # of falling back to entry_price (which was the user-visible
    # "live price = entry price" complaint). This matches the
    # detail page, which already does a live call on render.
    # Recommendation list stays cached-only because it can be ~100
    # symbols and we don't want to block sections render on a big
    # yfinance batch.
    money_syms = sorted({p.symbol for p in positions}
                        | {w.symbol for w in watchlist})
    reco_syms = sorted({c.get("symbol", "")
                        for c in (latest_scan.get("candidates") or [])
                        if c.get("symbol")} - set(money_syms))
    live: dict = {}
    if money_syms:
        try:
            live.update(get_us_live_quotes(money_syms))
        except Exception:
            live.update(cached_us_live_quotes(money_syms))
    if reco_syms:
        live.update(cached_us_live_quotes(reco_syms))

    names = _scan_names(latest_scan)
    rows = latest_scan.get("candidates") or []
    open_symbols = {p.symbol.strip().upper() for p in positions}
    html_frag = "".join([
        _render_broker_instructions(rows),
        _render_recommendations(latest_scan, live, open_symbols),
        _render_watchlist(watchlist, live, names, open_symbols),
        _render_positions(positions, live, names),
    ])
    return json.dumps({"ok": True, "html": html_frag})


def _scan_names(latest_scan: dict) -> dict[str, str]:
    names: dict[str, str] = {}
    for candidate in latest_scan.get("candidates") or []:
        symbol = str(candidate.get("symbol") or "").strip().upper()
        stock_name = str(candidate.get("stock_name") or "").strip()
        if symbol and stock_name:
            names[symbol] = stock_name
    return names


def render_us_detail(symbol: str) -> str:
    """Render a per-stock US detail page in the same kvtable style
    as `render_swing_detail`."""
    init_db()
    sym = symbol.strip().upper()
    default_ticket = float(us_config.US_TICKET_AMOUNT)
    fx = get_usd_inr_rate()

    body: list[str] = []
    body.append(_topnav("/us", fx))
    body.append('<div class="wrap">')
    body.append(f'<h1 class="page-title">{html.escape(sym)} — US Detail</h1>')
    body.append('<div class="sub"><a href="/us">&larr; Back to US Dashboard</a></div>')

    try:
        row = analyse_us_symbol(sym, default_ticket)
    except Exception as exc:
        body.append('<div class="card"><p class="muted">No US analysis '
                    f'available for {html.escape(sym)}: '
                    f'{html.escape(str(exc))}</p></div>')
        body.append('</div>')
        body.append(_js(fx))
        return _wrap(f"US — {sym}", body, fx)

    live = get_us_live_quotes([sym])
    lq = live.get(sym, {})
    lprice = float(lq.get("price") or row.get("current_price") or 0.0)
    chg = float(lq.get("change_pct") or 0.0)

    payload = html.escape(json.dumps(_action_payload(row),
                                     separators=(",", ":")))
    pnl_cls = "pos" if chg >= 0 else "neg"
    buy_label = "I Bought More" if any(
        p.symbol.strip().upper() == sym for p in _us_positions()
    ) else "I Bought It"

    # ── Summary card ───────────────────────────────────────────
    body.append('<div class="card">')
    body.append('<h2>Recommendation Summary</h2>')
    body.append(
        '<div style="display:flex;justify-content:flex-end;margin:-4px 0 12px">'
        f'<select class="add-dropdown" data-row="{payload}" '
        f'onchange="addUsCandidate(this)">'
        '<option value="">Add+</option>'
        '<option value="watch">Watch</option>'
        f'<option value="buy">{html.escape(buy_label)}</option>'
        '</select></div>'
    )

    setup_explain = {
        "BREAKOUT": "Stock is breaking above its recent price ceiling with strong trading activity — a sign buyers are stepping in.",
        "PULLBACK_UPTREND": "Stock has been going up overall but dipped temporarily to a good buy level — like a sale on a stock that's been rising.",
        "TREND_CONTINUATION": "Stock has been steadily rising across all timeframes — trend is strong and continuing upward.",
        "SUPPORT_REVERSAL": "Stock bounced off a major support level where it historically finds buyers — early sign of a potential recovery.",
        "52W_DIP": "Stock has fallen significantly from its 52-week high. Buy the dip and sell on the recovery — works best on quality names that tend to bounce back.",
    }
    setup_text = setup_explain.get(row.get("setup_type", ""),
                                    "Technical setup detected.")
    stock_name = row.get("stock_name") or get_us_stock_name(sym)
    name_html = (f'<div class="muted" style="font-size:13px;margin-top:-4px;'
                 f'margin-bottom:10px">{html.escape(stock_name)}</div>'
                 if stock_name else '')
    body.append(name_html)
    body.append(f'<div style="font-size:14px;line-height:1.6;margin-bottom:14px">'
                f'<strong>Setup: {html.escape((row.get("setup_type") or "NONE").replace("_", " ").title())}</strong>'
                f'<br>{html.escape(setup_text)}</div>')

    ind = row.get("indicators") or {}
    # Detail page lives in its own 15 s polling tier so a user
    # actively studying one symbol gets the freshest possible
    # quote without contending with the dashboard's 30/60 s
    # buckets.
    body.append(f'<table class="kvtable" data-live-symbol="{html.escape(sym)}" '
                 f'data-live-tier="detail">')
    _kv = lambda k, v: f'<tr><td>{k}</td><td>{v}</td></tr>'
    body.append(_kv("Exchange", html.escape(row.get("exchange", "NASDAQ"))))
    body.append(_kv("Data Source",
                     html.escape(f"{row.get('data_source','yfinance')} · data through {row.get('as_of','')}")))
    body.append(_kv("Current Price",
                     f'<span data-live-field="price_with_change">'
                     f'<span class="{pnl_cls}">{_money_span(lprice)} '
                     f'({chg:+.2f}% today)</span></span>'))
    body.append(_kv("Suggested Entry", _money_span(row.get("entry_price"))))
    body.append(_kv("Stop Loss", _money_span(row.get("stop_price"))))
    body.append(_kv("Target", _money_span(row.get("target_price"))))
    body.append(_kv("Risk vs Reward",
                     f'{float(row.get("rr_ratio") or 0):.2f}x'))
    body.append(_kv("Suggested Quantity",
                     f'{_fmt_qty(row.get("suggested_qty"))} shares'))
    body.append(_kv("Position Size",
                     _money_span(row.get("position_value"))))
    body.append(_kv("Composite Score",
                     f'{float(row.get("score") or 0):.2f}'))
    h52 = float(ind.get("high_52w") or 0.0)
    dip = float(ind.get("dip_from_52w_high_pct") or 0.0)
    if h52 > 0:
        body.append(_kv("% Below 52w High",
                         f'{dip:.2f}% (high {_money_span(h52)})'))
    rank = int(row.get("priority_rank") or 0)
    if rank:
        body.append(_kv("Latest Scan Rank", f'#{rank}'))
    body.append('</table></div>')

    # ── Why this stock ─────────────────────────────────────────
    body.append('<div class="card">')
    body.append('<h2>Why This Stock?</h2>')
    reasons = row.get("reasons") or []
    if reasons:
        body.append('<ol style="font-size:13px;line-height:1.8">')
        for r in reasons:
            body.append(f'<li>{html.escape(str(r))}</li>')
        body.append('</ol>')
    else:
        body.append('<p class="muted">No quant reasons returned — '
                    'setup did not pass any of the technical or '
                    '52w-dip filters.</p>')
    warnings = row.get("warnings") or []
    if warnings:
        body.append('<p class="muted" style="margin-top:6px">'
                    '<strong>Warnings:</strong></p>'
                    '<ul style="font-size:13px;line-height:1.7">')
        for w in warnings:
            body.append(f'<li>{html.escape(str(w))}</li>')
        body.append('</ul>')
    body.append('</div>')

    # ── Stock Health Check ────────────────────────────────────
    body.append('<div class="card">')
    body.append('<h2>Stock Health Check</h2>')
    body.append('<p class="muted" style="margin-bottom:12px">'
                'Checks we run before recommending a US stock. '
                'More green checks = stronger recommendation.</p>')
    body.append('<table class="holdings" style="max-width:820px">')
    body.append('<tr><th>What We Checked</th><th>Result</th>'
                '<th style="width:40px"></th></tr>')
    for name, explanation, value, passed in build_us_health_checks(row, lprice):
        icon = ('<span class="pos" style="font-size:16px">&#10003;</span>'
                if passed else
                '<span class="neg" style="font-size:16px">&#10007;</span>')
        body.append(f'<tr>'
                    f'<td><strong>{html.escape(name)}</strong>'
                    f'<br><span class="muted" style="font-size:11px">'
                    f'{html.escape(explanation)}</span></td>'
                    f'<td>{html.escape(str(value))}</td>'
                    f'<td>{icon}</td></tr>')
    body.append('</table></div>')

    # ── AI Analysis ────────────────────────────────────────────
    body.append('<div class="card">')
    body.append('<h2>AI Analysis</h2>')
    body.append(
        '<div style="display:flex;gap:8px;align-items:center;'
        'margin-bottom:10px;flex-wrap:wrap">'
        f'<button class="action" id="ai-analyse-btn" '
        f'onclick="aiAnalyseUsSingle(\'{html.escape(sym)}\')" '
        f'style="padding:6px 12px;font-size:13px">'
        'Analyse with AI</button>'
        '<span class="muted" style="font-size:12px">'
        'One Claude call for this stock only.</span>'
        '</div>'
    )
    body.append('<div id="ai-overlay-host">')
    body.append('<p class="muted">Click <em>Analyse with AI</em> above '
                'to add a qualitative thesis.</p>')
    body.append('</div></div>')

    body.append('</div>')  # /.wrap
    body.append(_js(fx))
    return _wrap(f"US — {sym}", body, fx)


# ── Section renderers ──────────────────────────────────────────

def _render_freshness(latest_scan: dict, has_live: bool,
                       fx: dict) -> str:
    parts: list[str] = []
    if latest_scan:
        finished = (latest_scan.get("finished_at", "")
                    [:19].replace("T", " "))
        mode = latest_scan.get("mode", "NOAI")
        as_of = latest_scan.get("started_at", "")[:10]
        count = int(latest_scan.get("candidate_count")
                     or len(latest_scan.get("candidates") or []))
        parts.append(
            f'Last analysis: {html.escape(mode)} run completed '
            f'{html.escape(finished)} IST &middot; data through '
            f'{html.escape(as_of)} &middot; '
            f'{count} candidates'
        )
    else:
        parts.append('No US analysis run yet.')
    if has_live:
        parts.append(
            '<button id="us-live-toggle" class="action alt" '
            'style="padding:3px 8px;font-size:12px" type="button">'
            'Load live prices</button> '
            '<span id="us-live-state">Live prices paused</span>'
        )
    rate = float(fx.get("rate") or 0.0)
    if rate > 0:
        fx_ts = (fx.get("as_of", "")[:16].replace("T", " "))
        parts.append(f'USD/INR {rate:,.2f} '
                     f'<span class="muted">(as of {html.escape(fx_ts)})</span>')
    return '<div class="sub">' + '<br>'.join(parts) + '</div>'


def _render_pnl_card(
    pnl: dict,
    positions: list[SwingPosition],
    live_by_symbol: dict[str, dict],
) -> str:
    invested = sum(p.managed_qty * p.entry_price for p in positions)
    current_value = 0.0
    for p in positions:
        lq = live_by_symbol.get(p.symbol, {})
        lprice = float(lq.get("price") or p.entry_price)
        current_value += p.managed_qty * lprice
    unrealised = current_value - invested
    upnl_cls = "pos" if unrealised >= 0 else "neg"
    pnl_cls = "pos" if pnl.get("net_pnl", 0) >= 0 else "neg"
    out: list[str] = []
    out.append('<div class="card">')
    out.append('<h2>Realised US P&amp;L</h2>')
    out.append('<table class="kvtable">')
    _kv = lambda k, v: f'<tr><td>{k}</td><td>{v}</td></tr>'
    out.append(_kv("Gross P&amp;L",
                    f'<span class="{pnl_cls}">{_money_span(pnl.get("gross_pnl", 0), signed=True)}</span>'))
    out.append(_kv("Charges", _money_span(pnl.get("charges", 0))))
    out.append(_kv("Net P&amp;L",
                    f'<span class="{pnl_cls}">{_money_span(pnl.get("net_pnl", 0), signed=True)}</span>'))
    out.append(_kv("Closed Trades", str(int(pnl.get("count", 0)))))
    out.append(_kv("Open Positions", str(len(positions))))
    out.append(_kv("Book Amount", _money_span(invested)))
    out.append(_kv("Current Amount", _money_span(current_value)))
    out.append(_kv("Unrealised P&amp;L",
                   f'<span class="{upnl_cls}">{_money_span(unrealised, signed=True)}</span>'))
    out.append('</table></div>')
    return "".join(out)


def _render_scan_card(default_ticket: float,
                       latest_scan: dict) -> str:
    out: list[str] = []
    out.append('<div class="card">')
    out.append('<h2>Daily Scan</h2>')
    out.append('<div id="us-scan-banner"></div>')
    if latest_scan:
        finished = (latest_scan.get("finished_at", "")
                    [:19].replace("T", " "))
        mode = latest_scan.get("mode", "NOAI")
        count = int(latest_scan.get("candidate_count")
                     or len(latest_scan.get("candidates") or []))
        last_universe = latest_scan.get("universe", "")
        out.append(f'<div class="muted" style="margin-bottom:12px">Last run: '
                   f'{html.escape(mode)} on {html.escape(last_universe)} '
                   f'&middot; ran {html.escape(finished)} '
                   f'&middot; {count} candidates</div>')
    else:
        out.append('<div class="muted" style="margin-bottom:12px">'
                   'No US scan run yet.</div>')
    out.append('<div style="margin-bottom:12px">')
    out.append('<label style="font-size:13px;font-weight:500">'
               'Universe: </label>')
    out.append('<select id="us-scan-universe" '
               'style="min-width:100px;padding:6px 10px;font:inherit;'
               'border:1px solid #cfd9eb;border-radius:5px;margin-right:10px" '
               'title="US50 is the top 50 by market cap and runs faster than US100">'
               '<option value="US100">US100</option>'
               '<option value="US50">US50 (faster)</option>'
               '</select>')
    out.append('<label style="font-size:13px;font-weight:500">'
               'Amount per stock to invest ($): </label>')
    out.append(f'<input type="number" id="us-scan-ticket" '
               f'value="{int(default_ticket)}" '
               f'min="1" step="50" '
               f'style="width:150px;padding:6px 10px;font:inherit;'
               f'border:1px solid #cfd9eb;border-radius:5px;'
               f'font-variant-numeric:tabular-nums" />')
    out.append('<span style="margin-left:8px;font-size:12px;color:#5d6b82">'
               'Per-stock ticket for sizing entries.</span>')
    out.append('</div>')
    out.append('<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">')
    out.append('<button class="action" onclick="runUsScan()">Run Scan</button>')
    out.append('<label class="ai-toggle" '
               'title="Toggle to add Claude qualitative overlay">'
               '<input type="checkbox" id="us-ai-toggle">'
               '<span class="lbl">Use Claude AI overlay</span>'
               '<span class="hint">(NoAI is default)</span>'
               '</label>')
    out.append('</div>')
    out.append('<div class="muted" style="font-size:12px;margin-top:8px">'
               'Scans are manual-only from this dashboard. US data uses cached daily candles first, then refreshes only when you run a scan.'
               '</div>')
    out.append('</div>')
    return "".join(out)


def _render_single_stock_card(default_ticket: float) -> str:
    return f"""
<div class="card">
    <h2>Analyse a Single Stock</h2>
    <p class="muted" style="margin-top:0;font-size:13px">
        Type any US ticker, including names outside the scan universe.
  </p>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
    <input type="text" id="us-analyse-symbol" placeholder="e.g. AAPL"
                     style="width:180px;padding:8px 10px;font:inherit;
                  border:1px solid #cfd9eb;border-radius:5px;
                  text-transform:uppercase"
           onkeydown="if(event.key==='Enter'){{analyseUsStock();}}" />
    <button class="action" onclick="analyseUsStock()">Analyse</button>
        <label class="ai-toggle" title="Run Claude overlay for this one stock">
      <input type="checkbox" id="us-single-ai-toggle">
            <span class="lbl">Use AI</span>
            <span class="hint">(one-stock overlay)</span>
    </label>
  </div>
  <div id="us-analysis-result"></div>
</div>
"""


def _render_compare_card(default_ticket: float) -> str:
        # 2026-05-19 rewrite: now structurally identical to the
        # Indian Swing compare card (`swing_page._render_compare_card`)
        # so the two products have the same UX. Sector dropdown
        # loads from `/api/us/sectors` on DOMContentLoaded; picking
        # a sector auto-fills the input AND auto-renders the
        # compare result. The result table is rendered by the
        # shared `_renderCompareResult` JS which highlights winning
        # cells in green and shows a "X of N metrics" tally per
        # symbol.
        return """
<div class="card">
    <h2>Compare Stocks (up to 4)</h2>
    <p class="muted" style="margin-bottom:10px">
        Side-by-side comparison of up to 4 US swing candidates.
        Type a comma-separated list of tickers OR pick a sector to
        auto-populate the top 4. Each metric row highlights the
        winning value so you can see WHY one stock outranks another
        (example: <em>NVDA vs AMD &mdash; RS vs SPY +18% vs -2%,
        composite score 7.1 vs 4.4, etc.</em>).
    </p>
    <div style="display:flex;gap:8px;align-items:center;
                flex-wrap:wrap;margin-bottom:8px">
        <input type="text" id="us-compare-symbols"
               placeholder="e.g. AAPL, MSFT, NVDA, GOOGL"
               style="flex:1;min-width:280px;padding:6px 10px;font:inherit;
                      border:1px solid #cfd9eb;border-radius:5px;
                      text-transform:uppercase"
               onkeydown="if(event.key==='Enter'){compareUsNow();}" />
        <select id="us-compare-sector"
                style="padding:6px 10px;font:inherit;
                       border:1px solid #cfd9eb;border-radius:5px">
            <option value="">&mdash; or pick a sector &mdash;</option>
        </select>
        <button class="action" onclick="compareUsNow()">Compare</button>
        <button class="action alt" onclick="compareUsClear()"
                style="padding:5px 10px;font-size:12px">Clear</button>
    </div>
    <p class="muted" style="font-size:11px;margin:0 0 10px 0">
        Sector dropdown loads top-4 from the curated US sector map
        (e.g. SEMICONDUCTORS gives NVDA, AVGO, AMD, QCOM). You can
        edit the input afterwards before clicking Compare.
    </p>
    <div id="us-compare-result-host"></div>
</div>
"""


def _render_us_sections_loader() -> str:
        return """
<div id="us-sections-host">
    <div class="card"><p class="muted"><span class="spinner"></span>
        Loading US recommendations, watchlist, and open book...</p></div>
</div>
<script>
window.addEventListener('DOMContentLoaded', function() {
    var host = document.getElementById('us-sections-host');
    if (!host) return;
    fetch('/api/us/sections')
        .then(function(r) { return r.json(); })
        .then(function(j) {
            if (!j || !j.ok) {
                host.innerHTML = '<div class="banner warn">Could not load US sections.</div>';
                return;
            }
            host.innerHTML = String(j.html || '');
            _applyCurrencyToAll();
            // Sections just got swapped in — cascade fresh quotes
            // (open first, then watch, then reco) so the user sees
            // their open book light up before the reco list.
            if (_usLiveEnabled()) setTimeout(_usCascadePoll, 50);
        })
        .catch(function() {
            host.innerHTML = '<div class="banner warn">Could not load US sections.</div>';
        });
});
</script>
"""


def _render_broker_instructions(rows: list[dict]) -> str:
        if not rows:
                return ""
        return """
<div class="card">
    <h2>How to Enter in Broker</h2>
    <ol style="font-size:13px;line-height:1.8">
        <li>Open your US broker and search the symbol on NASDAQ / NYSE / AMEX / ARCA.</li>
        <li>Use a delivery/cash buy, not margin unless you explicitly intend leverage.</li>
        <li>Use the suggested quantity and limit price from the table below.</li>
        <li>Place or note the suggested stop and target for position management.</li>
        <li>After your order fills, come back here and choose Add+ -> I Bought It.</li>
    </ol>
    <div class="muted" style="font-size:12px;margin-top:6px">
        Tip: fractional shares are supported; the confirmation popup accepts decimals.
    </div>
</div>
"""


def _render_recommendations(latest_scan: dict, live: dict,
                            open_symbols: set[str] | None = None) -> str:
    rows = latest_scan.get("candidates") or []
    out: list[str] = []
    out.append('<div class="card">')
    out.append('<details open><summary class="collapse-header">'
               f'<h2 style="display:inline">Entry Recommendations ({len(rows)})</h2>'
               '<span class="collapse-hint">click to expand</span></summary>')
    out.append('<p class="muted" style="margin-bottom:10px">'
               'Combined view of trend setups (breakouts / pullbacks / '
               'continuations / support reversals) and 52w-dip buys. '
               'Use the page-level live-price toggle to refresh quotes.</p>')
    if not rows:
        out.append('<div class="muted">No US recommendations yet. '
                   'Click <em>Run Scan</em> above to analyse the universe.</div>')
        out.append('</details></div>')
        return "".join(out)
    out.append('<table class="holdings">')
    out.append('<tr>'
               '<th>#</th><th>Symbol</th><th>Name</th><th>Setup</th>'
               '<th class="right">% Below 52w</th>'
               '<th class="right">Live Price</th>'
               '<th class="right">Entry</th><th class="right">Stop</th>'
               '<th class="right">Target</th>'
               '<th class="right">Qty</th><th class="right">R:R</th>'
               '<th>Reason</th><th>Actions</th>'
               '</tr>')
    for r in rows:
        sym = r.get("symbol", "")
        ind = r.get("indicators") or {}
        dip = float(ind.get("dip_from_52w_high_pct") or 0.0)
        setup = (r.get("setup_type") or "NONE").replace("_", " ").title()
        ai_mark = ' <span class="muted" style="font-size:10px">AI</span>' if r.get("ai_overlay") else ''
        reason = (r.get("reasons") or [""])[0]
        if len(r.get("reasons") or []) > 1:
            reason += f" (+{len(r['reasons']) - 1} more)"
        payload = html.escape(json.dumps(_action_payload(r),
                                         separators=(",", ":")))
        lq = live.get(sym, {})
        lprice = float(lq.get("price") or r.get("current_price") or 0.0)
        chg = float(lq.get("change_pct") or 0.0)
        chg_cls = "pos" if chg >= 0 else "neg"
        stock_name = r.get("stock_name") or ""
        buy_label = "I Bought More" if sym.strip().upper() in (open_symbols or set()) else "I Bought It"
        if dip >= 18:
            dip_cell = f'<span class="neg" style="font-weight:600">{dip:.1f}%</span>'
        elif dip >= 10:
            dip_cell = f'<span>{dip:.1f}%</span>'
        else:
            dip_cell = f'<span class="muted">{dip:.1f}%</span>'
        out.append(
            # `data-live-tier="reco"` puts recommendation rows in
            # the slow 60 s polling bucket — these are not
            # money-at-risk so they don't need 15 s refreshes.
            f'<tr data-live-symbol="{html.escape(sym)}" '
            f'data-live-tier="reco">'
            f'<td>{int(r.get("priority_rank") or 0)}</td>'
            f'<td><a href="/us/{html.escape(sym)}" class="ticker">'
            f'{html.escape(sym)}</a></td>'
            f'<td><span class="small">{html.escape(stock_name)}</span></td>'
            f'<td><span style="font-size:11px">{html.escape(setup)}</span>{ai_mark}</td>'
            f'<td class="right">{dip_cell}</td>'
            f'<td class="right" data-live-field="price_with_change">'
            f'<span class="{chg_cls}">{_money_span(lprice)}</span> '
            f'<span class="muted">({chg:+.1f}%)</span></td>'
            f'<td class="right">{_money_span(r.get("entry_price"))}</td>'
            f'<td class="right">{_money_span(r.get("stop_price"))}</td>'
            f'<td class="right">{_money_span(r.get("target_price"))}</td>'
            f'<td class="right">{_fmt_qty(r.get("suggested_qty"))}</td>'
            f'<td class="right">{float(r.get("rr_ratio") or 0):.2f}</td>'
            f'<td style="font-size:11px;max-width:220px">'
            f'{html.escape(reason)}</td>'
            f'<td><select class="add-dropdown" data-row="{payload}" '
            f'onchange="addUsCandidate(this)" '
            f'style="padding:4px 6px;font-size:12px">'
            f'<option value="">Add+</option>'
            f'<option value="watch">Watch</option>'
            f'<option value="buy">{html.escape(buy_label)}</option>'
            f'</select></td>'
            f'</tr>'
        )
    out.append('</table>')
    out.append('</details></div>')
    return "".join(out)


def _render_watchlist(watchlist, live: dict,
                      names: dict[str, str] | None = None,
                      open_symbols: set[str] | None = None) -> str:
    out: list[str] = []
    out.append('<div class="card">')
    out.append('<details open><summary class="collapse-header">'
               f'<h2 style="display:inline">Watchlist ({len(watchlist)})</h2>'
               '<span class="collapse-hint">click to expand</span></summary>')
    out.append('<p class="muted" style="margin-bottom:10px">'
               'US stocks you are watching but have not bought yet. '
               'Live price + virtual P&amp;L update when live prices are enabled.</p>')
    if not watchlist:
        out.append('<div class="muted">No stocks in US watchlist. '
                   'Use Add+ on a recommendation to watch it.</div>')
        out.append('</details></div>')
        return "".join(out)
    out.append('<table class="holdings">')
    out.append('<tr>'
               '<th>Symbol</th><th>Name</th><th>Setup</th>'
               '<th class="right">Watchlist Price</th>'
               '<th class="right">Live Price</th>'
               '<th class="right">Virtual P&amp;L</th>'
               '<th>Added</th><th>Actions</th>'
               '</tr>')
    for w in watchlist:
        sym = w.symbol
        lq = live.get(sym, {})
        lprice = float(lq.get("price") or 0.0)
        if lprice > 0 and w.added_price > 0:
            vpnl = lprice - w.added_price
            vpct = ((lprice / w.added_price) - 1) * 100
        else:
            vpnl = 0.0
            vpct = 0.0
        pcls = "pos" if vpnl >= 0 else "neg"
        stock_name = (names or {}).get(sym.strip().upper(), "")
        added_short = w.added_at[:10] if w.added_at else ""
        buy_label = "I Bought More" if sym.strip().upper() in (open_symbols or set()) else "I Bought It"
        out.append(
            # `data-watch-price` is what the live-price poller
            # reads to recompute virtual P&L on every tick.
            # `data-live-tier="watch"` puts watchlist rows in the
            # 30 s polling bucket (slower than open book, faster
            # than recommendations).
            f'<tr data-live-symbol="{html.escape(sym)}" '
            f'data-live-tier="watch" '
            f'data-watch-price="{w.added_price}">'
            f'<td><a href="/us/{html.escape(sym)}" class="ticker">'
            f'{html.escape(sym)}</a></td>'
            f'<td><span class="small">{html.escape(stock_name)}</span></td>'
            f'<td><span style="font-size:11px">'
            f'{html.escape((w.setup_type or "").replace("_", " ").title())}'
            f'</span></td>'
            f'<td class="right">{_money_span(w.added_price)}</td>'
            f'<td class="right" data-live-field="price">{_money_span(lprice)}</td>'
            f'<td class="right" data-live-field="vpnl">'
            f'<span class="{pcls}">{_money_span(vpnl, signed=True)} '
            f'({vpct:+.1f}%)</span></td>'
            f'<td class="muted" style="font-size:11px">{html.escape(added_short)}</td>'
            f'<td>'
            f'<button class="action" '
            f'onclick="promoteUsWatchlist({w.watchlist_id})" '
            f'style="padding:4px 8px;font-size:12px">{html.escape(buy_label)}</button> '
            f'<button class="action alt" '
            f'onclick="removeUsWatchlist({w.watchlist_id})" '
            f'style="padding:4px 8px;font-size:12px">Remove</button>'
            f'</td></tr>'
        )
    out.append('</table>')
    out.append('</details></div>')
    return "".join(out)


def _render_positions(positions: list[SwingPosition],
                       live: dict,
                       names: dict[str, str] | None = None) -> str:
    out: list[str] = []
    out.append('<h2>Open US Book</h2>')
    out.append('<div class="card">')
    if not positions:
        out.append('<div class="muted">No open US positions. '
                   'Confirm an entry recommendation above to start tracking.</div>')
        out.append('</div>')
        return "".join(out)
    out.append('<table class="holdings">')
    out.append('<tr>'
               '<th>Symbol</th><th>Name</th>'
               '<th class="right">Qty</th>'
               '<th class="right">Entry</th>'
               '<th class="right">Live Price</th>'
               '<th class="right">P&amp;L</th>'
               '<th class="right">Stop</th>'
               '<th class="right">Target</th>'
               '<th class="right">R</th>'
               '<th>Action</th>'
               '<th>Controls</th>'
               '</tr>')
    for p in positions:
        lq = live.get(p.symbol, {})
        lprice = float(lq.get("price") or p.entry_price)
        upnl = (lprice - p.entry_price) * p.managed_qty
        risk_per = p.entry_price - p.stop_price
        r_mult = ((lprice - p.entry_price) / risk_per
                  if risk_per > 0 else 0)
        pnl_cls = "pos" if upnl >= 0 else "neg"
        stock_name = (names or {}).get(p.symbol.strip().upper(), "")
        out.append(
            # `data-live-tier="open"` puts this row in the
            # fastest polling bucket (15 s) so live P&L on real
            # money positions refreshes ahead of watchlist and
            # recommendations. See `_usPollTier` in _js below.
            f'<tr data-live-symbol="{html.escape(p.symbol)}" '
            f'data-live-tier="open" '
            f'data-entry-price="{p.entry_price}" '
            f'data-managed-qty="{p.managed_qty}">'
            f'<td><a href="/us/{html.escape(p.symbol)}" class="ticker">'
            f'{html.escape(p.symbol)}</a><br>'
            f'<span class="small muted">{html.escape(p.exchange)}</span></td>'
            f'<td><span class="small">{html.escape(stock_name)}</span></td>'
            f'<td class="right">{_fmt_qty(p.managed_qty)}</td>'
            f'<td class="right">{_money_span(p.entry_price)}</td>'
            f'<td class="right" data-live-field="price">{_money_span(lprice)}</td>'
            f'<td class="right" data-live-field="pnl">'
            f'<span class="{pnl_cls}">{_money_span(upnl, signed=True)}</span></td>'
            f'<td class="right">{_money_span(p.stop_price)}</td>'
            f'<td class="right">{_money_span(p.target_price)}</td>'
            f'<td class="right" data-live-field="r_mult">{r_mult:+.2f}R</td>'
            f'<td><span class="small">{html.escape(p.daily_action or "HOLD")}</span></td>'
            f'<td>'
            f'<button class="action alt" '
            f'onclick="editUsPosition({p.position_id}, {p.managed_qty}, '
            f'{p.entry_price:.4f}, {p.stop_price:.4f}, '
            f'{p.target_price:.4f})" '
            f'style="padding:4px 8px;font-size:12px">Edit</button> '
            f'<button class="action alt" '
            f'onclick="exitUsPosition({p.position_id}, {p.managed_qty})" '
            f'style="padding:4px 8px;font-size:12px">Mark Exit Done</button>'
            f'</td>'
            f'</tr>'
        )
    out.append('</table>')
    out.append('</div>')
    return "".join(out)


# ── Helpers ───────────────────────────────────────────────────

def _us_positions() -> list[SwingPosition]:
    return [p for p in open_positions()
            if (p.exchange or "").upper() in US_EXCHANGES]


def _us_watchlist():
    return [w for w in get_watchlist()
            if (w.exchange or "").upper() in US_EXCHANGES]


def _filter_pnl_for_us(pnl: dict) -> dict:
    """`realised_pnl_summary` currently sums across all exchanges
    when called with `exchange=None`.  Use a per-exchange call and
    aggregate to keep US numbers honest."""
    out = {"gross_pnl": 0.0, "charges": 0.0, "net_pnl": 0.0, "count": 0}
    for ex in US_EXCHANGES:
        try:
            row = realised_pnl_summary(exchange=ex)
        except Exception:
            continue
        out["gross_pnl"] += float(row.get("gross_pnl", 0) or 0)
        out["charges"] += float(row.get("charges", 0) or 0)
        out["net_pnl"] += float(row.get("net_pnl", 0) or 0)
        out["count"] += int(row.get("count", 0) or 0)
    return out


def _money_span(value, signed: bool = False) -> str:
    """Render a USD value as a <span class="money" data-usd="..."> so
    the client-side currency toggle can re-render in INR on demand."""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    text = (f"${'+' if v >= 0 else '-'}{abs(v):,.2f}" if signed
            else f"${v:,.2f}")
    return (f'<span class="money" data-usd="{v}" '
            f'data-signed="{1 if signed else 0}">{text}</span>')


def _fmt_qty(value) -> str:
    """Format a US share quantity that may be fractional.

    Whole numbers render without a decimal tail; fractional values
    show up to four decimals (yfinance dividends / fractional brokers).
    """
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v == int(v):
        return f'{int(v):,}'
    return f'{v:,.4f}'.rstrip('0').rstrip('.')


def _action_payload(row: dict) -> dict:
    return {
        "symbol": row.get("symbol", ""),
        "exchange": row.get("exchange", "NASDAQ"),
        "price": row.get("entry_price") or row.get("current_price") or 0,
        "stop": row.get("stop_price") or 0,
        "target": row.get("target_price") or 0,
        "qty": row.get("suggested_qty") or 0,
        "setup_type": row.get("setup_type") or "",
        "stock_name": row.get("stock_name") or "",
    }


# ── Topnav with FX badge + currency toggle ────────────────────

def _topnav(here: str, fx: dict) -> str:
    rate = float(fx.get("rate") or 0.0)
    fx_html = ''
    if rate > 0:
        fx_ts = fx.get("as_of", "")[:16].replace("T", " ")
        fx_html = (
            f'<span class="fx-badge" title="USD-INR rate from yfinance, '
            f'updated {html.escape(fx_ts)}">'
            f'USD/INR <strong>{rate:,.2f}</strong></span>'
        )
    toggle_html = (
        '<button id="us-currency-toggle" class="cur-toggle" type="button" '
        'onclick="toggleUsCurrency()" '
        'title="Switch all dollar values between USD and INR">'
        '<span id="cur-usd-pill" class="cur-pill active">USD</span>'
        '<span id="cur-inr-pill" class="cur-pill">INR</span>'
        '</button>'
    )
    return render_topnav(here, after_links=fx_html + toggle_html)


# ── Page chrome ───────────────────────────────────────────────

def _wrap(title: str, body_parts: list[str], fx: dict) -> str:
    rate = float(fx.get("rate") or 0.0)
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} — AI Portfolio Manager</title>
<style>{_STYLE}</style>
<script>window._usdInrRate = {rate};</script>
</head><body>
{''.join(body_parts)}
</body></html>"""


_STYLE = """
:root { --bg: #fafbfc; --fg: #1c1f23; --muted: #6a7280;
    --card: #ffffff; --line: #e5e7eb;
    --accent: #1c1f23; --pos: #1b8e3a; --neg: #c62828;
    --warn-bg: #fff4e0; --warn-fg: #b06a00; --warn-line: #f0d28a;
    --soft: #f0f1f3; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
       background: var(--bg); color: var(--fg); margin: 0; padding: 24px; }
.wrap { max-width: 1180px; margin: 0 auto; }
h1.page-title { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em;
     color: var(--muted); margin: 28px 0 8px; font-weight: 600; }
.sub { margin: 0 0 14px; color: var(--muted); font-size: 13px; line-height: 1.6; }
.sub a, .ticker { color: var(--accent); font-weight: 700; text-decoration: none; }
.sub a:hover, .ticker:hover { text-decoration: underline; }
.muted, .empty { color: var(--muted); }
.small { font-size: 12px; color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line);
    border-radius: 8px; padding: 18px 22px; margin-bottom: 16px; }
.card a { color: var(--accent); }
table.holdings { width: 100%; border-collapse: collapse; font-size: 13px;
         font-variant-numeric: tabular-nums; }
table.holdings th { text-align: left; padding: 6px 10px;
            border-bottom: 2px solid var(--line);
            color: var(--muted); font-weight: 600; font-size: 11px;
            text-transform: uppercase; letter-spacing: 0.04em; }
table.holdings td { padding: 6px 10px; border-bottom: 1px solid var(--line); }
table.holdings tr:hover td { background: #f7f8fa; }
table.holdings .right, .right { text-align: right; }
.pos { color: var(--pos); font-weight: 600; }
.neg { color: var(--neg); font-weight: 600; }
.kvtable { width: 100%; border-collapse: collapse; font-size: 14px;
       font-variant-numeric: tabular-nums; }
.kvtable td { padding: 5px 0; border-bottom: 1px dashed var(--line); }
.kvtable td:last-child { text-align: right; font-weight: 500; }
button.action, .action { font: inherit; padding: 8px 14px; border: 1px solid #1c1f23;
        border-radius: 5px; background: #1c1f23; color: white;
        cursor: pointer; margin-right: 8px; font-weight: 500; }
button.action.alt, .action.alt { background: white; color: #1c1f23; }
button.action[disabled], .action[disabled] { opacity: 0.55; cursor: not-allowed; }
input, select { padding: 7px 9px; border: 1px solid #cfd9eb; border-radius: 5px;
        background: white; color: var(--fg); font: inherit; }
input#us-symbol, input#us-analyse-symbol, input#us-compare-symbols { text-transform: uppercase; }
label { color: var(--fg); }
.form-row { display: flex; align-items: end; gap: 10px; flex-wrap: wrap; }
.add-dropdown { padding: 4px 6px; font-size: 12px; font-weight: 600;
        border: 1px solid var(--accent); border-radius: 5px;
        background: var(--card); color: var(--accent); cursor: pointer; }
.banner { padding: 10px 14px; border-radius: 6px; font-size: 13px;
      margin-bottom: 12px; }
.banner.info { background: #eef4ff; border: 1px solid #cfd9eb; }
.banner.warn { background: var(--warn-bg); border: 1px solid var(--warn-line);
           color: var(--warn-fg); }
.banner.error { background: #fdecec; border: 1px solid #f4c0c0; color: var(--neg); }
.spinner { display: inline-block; width: 14px; height: 14px;
       border: 2px solid #cfd9eb; border-top-color: var(--accent);
       border-radius: 50%; animation: spin 0.8s linear infinite;
       vertical-align: middle; margin-right: 6px; }
@keyframes spin { to { transform: rotate(360deg); } }
footer { color: var(--muted); font-size: 12px; margin-top: 32px; text-align: center; }
summary.collapse-header { cursor: pointer; list-style: none; display: flex;
              align-items: center; gap: 8px; }
summary.collapse-header::-webkit-details-marker { display: none; }
summary.collapse-header::before { content: '\25BE'; font-size: 14px; color: var(--muted);
                  transition: transform 0.2s; }
details:not([open]) > summary.collapse-header::before { transform: rotate(-90deg); }
.collapse-hint { font-size: 11px; color: var(--muted); font-weight: 400; display: none; }
details:not([open]) > summary .collapse-hint { display: inline; }
.ai-toggle { display: inline-flex; align-items: center; gap: 8px;
         padding: 6px 12px; background: var(--card);
         border: 1px solid var(--line); border-radius: 999px;
         font-size: 13px; cursor: pointer; user-select: none; }
.ai-toggle input { margin: 0; cursor: pointer; }
.ai-toggle .lbl { font-weight: 500; }
.ai-toggle .hint { color: var(--muted); font-size: 11px; }
.fx-badge { background: #e6f4ea; color: var(--pos); border: 1px solid #cce8d4;
        border-radius: 999px; padding: 3px 9px; font-size: 12px;
        font-weight: 700; margin-left: 8px; white-space: nowrap; }
.cur-toggle { display: inline-flex; align-items: center; gap: 0;
          background: white; border: 1px solid var(--line);
          border-radius: 999px; padding: 0; margin-left: 8px;
          cursor: pointer; font: inherit; overflow: hidden; }
.cur-pill { padding: 3px 10px; font-size: 12px; font-weight: 700;
        color: var(--muted); }
.cur-pill.active { background: var(--accent); color: white; }
.money { font-variant-numeric: tabular-nums; }
""" + topnav_css()


# ── Page JS (live polling + currency toggle + scan controls) ──

def _js(fx: dict) -> str:
    rate = float(fx.get("rate") or 0.0)
    return r"""<script>
window._usdInrRate = """ + f"{rate}" + r""";

function _esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function(c) {
        return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
}
function _num(raw, label) {
    var n = Number(String(raw == null ? '' : raw).replace(/,/g, '').trim());
    if (!isFinite(n) || n <= 0) {
        alert(label + ' must be a positive number');
        return null;
    }
    return n;
}
function _qty(raw, label) {
    // Accepts fractional shares (US brokers allow decimals).
    var n = Number(String(raw == null ? '' : raw).replace(/,/g, '').trim());
    if (!isFinite(n) || n <= 0) {
        alert(label + ' must be a positive number (decimals OK)');
        return null;
    }
    return n;
}
function _optionalNum(raw, label) {
    if (raw === null || raw === undefined) return 0;
    var s = String(raw).trim();
    if (!s) return 0;
    return _num(s, label);
}
function _usTicketAmount() {
    var ticketEl = document.getElementById('us-scan-ticket') ||
                   document.getElementById('us-ticket') ||
                   document.getElementById('us-compare-ticket');
    return _num(ticketEl ? ticketEl.value : '500', 'Amount per stock');
}
function _defaultField(v) {
    var n = Number(v || 0);
    if (!isFinite(n) || n <= 0) return '';
    return String(n);
}
function _jsonFetch(postUrl, payload, failureLabel) {
    fetch(postUrl, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload || {})
    })
        .then(function(r) {
            return r.json()
                .catch(function() { return {}; })
                .then(function(j) { return {ok: r.ok, body: j}; });
        })
        .then(function(res) {
            if (!res.ok || !res.body.ok) {
                alert(failureLabel + ': ' + (res.body.error || 'unknown error'));
                return;
            }
            location.reload();
        })
        .catch(function(e) { alert('Network error: ' + e); });
}
function confirmUsPurchase(postUrl, failureLabel, extraBody, defaults) {
    defaults = defaults || {};
    var symbol = String(defaults.symbol || (extraBody && extraBody.symbol) || '').toUpperCase();
    var exchange = String(defaults.exchange || (extraBody && extraBody.exchange) || 'NASDAQ').toUpperCase();
    var overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;' +
        'background:rgba(0,0,0,0.4);z-index:1000;display:flex;' +
        'align-items:center;justify-content:center;padding:16px';
    overlay.innerHTML =
        '<div style="background:white;border-radius:10px;padding:24px 28px;' +
        'min-width:320px;max-width:430px;width:100%;box-shadow:0 8px 32px rgba(0,0,0,0.2)">' +
        '<h3 style="margin:0 0 6px;font-size:16px">Confirm Purchase</h3>' +
        '<div class="muted" style="font-size:12px;margin-bottom:16px">' +
        _esc(symbol ? (symbol + ' ' + exchange) : 'US position') + '</div>' +
        '<label style="font-size:13px;font-weight:500;display:block;margin-bottom:4px">' +
        'Quantity (shares)</label>' +
        '<input id="us-buy-qty" type="number" min="0.0001" step="0.0001" value="' +
        _esc(_defaultField(defaults.qty)) + '" ' +
        'style="width:100%;padding:8px 10px;font:inherit;border:1px solid #cfd9eb;' +
        'border-radius:5px;margin-bottom:12px;font-size:15px" autofocus />' +
        '<label style="font-size:13px;font-weight:500;display:block;margin-bottom:4px">' +
        'Price per share ($)</label>' +
        '<input id="us-buy-price" type="number" min="0.01" step="0.01" value="' +
        _esc(_defaultField(defaults.price)) + '" ' +
        'style="width:100%;padding:8px 10px;font:inherit;border:1px solid #cfd9eb;' +
        'border-radius:5px;margin-bottom:12px;font-size:15px" />' +
        '<label style="font-size:13px;font-weight:500;display:block;margin-bottom:4px">' +
        'Stop-loss price ($) <span style="color:var(--muted);font-weight:400">optional</span></label>' +
        '<input id="us-buy-stop" type="number" min="0" step="0.01" value="' +
        _esc(_defaultField(defaults.stop)) + '" ' +
        'style="width:100%;padding:8px 10px;font:inherit;border:1px solid #cfd9eb;' +
        'border-radius:5px;margin-bottom:12px;font-size:15px" />' +
        '<label style="font-size:13px;font-weight:500;display:block;margin-bottom:4px">' +
        'Target price ($) <span style="color:var(--muted);font-weight:400">optional</span></label>' +
        '<input id="us-buy-target" type="number" min="0" step="0.01" value="' +
        _esc(_defaultField(defaults.target)) + '" ' +
        'style="width:100%;padding:8px 10px;font:inherit;border:1px solid #cfd9eb;' +
        'border-radius:5px;margin-bottom:16px;font-size:15px" />' +
        '<div style="display:flex;gap:8px;justify-content:flex-end">' +
        '<button id="us-buy-cancel" class="action alt" style="padding:8px 16px">Cancel</button>' +
        '<button id="us-buy-submit" class="action" style="padding:8px 16px">Confirm</button>' +
        '</div></div>';
    document.body.appendChild(overlay);
    setTimeout(function() { document.getElementById('us-buy-qty').focus(); }, 50);
    overlay.addEventListener('click', function(e) {
        if (e.target === overlay) document.body.removeChild(overlay);
    });
    document.getElementById('us-buy-cancel').onclick = function() {
        document.body.removeChild(overlay);
    };
    document.getElementById('us-buy-submit').onclick = function() {
        var qty = _qty(document.getElementById('us-buy-qty').value, 'quantity');
        if (qty === null) return;
        var price = _num(document.getElementById('us-buy-price').value, 'price');
        if (price === null) return;
        var stop = _optionalNum(document.getElementById('us-buy-stop').value, 'stop');
        if (stop === null) return;
        var target = _optionalNum(document.getElementById('us-buy-target').value, 'target');
        if (target === null) return;
        var payload = Object.assign({}, extraBody || {});
        if (symbol && !payload.symbol) payload.symbol = symbol;
        if (exchange && !payload.exchange) payload.exchange = exchange;
        payload.qty = qty;
        payload.price = price;
        payload.stop = stop;
        payload.target = target;
        document.body.removeChild(overlay);
        _jsonFetch(postUrl, payload, failureLabel);
    };
    overlay.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') document.getElementById('us-buy-submit').click();
        if (e.key === 'Escape') document.body.removeChild(overlay);
    });
}

/* ── Currency toggle ───────────────────────────────────────── */
function _currentCurrency() {
    try { return localStorage.getItem('us-currency') || 'USD'; }
    catch (e) { return 'USD'; }
}
function _fmtMoneyUsd(v, signed) {
    var abs = Math.abs(v);
    var s = '$' + abs.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    if (signed) return (v >= 0 ? '+' : '-') + s;
    return (v < 0 ? '-' : '') + s;
}
function _fmtMoneyInr(v, signed) {
    var rate = Number(window._usdInrRate || 0);
    if (rate <= 0) return _fmtMoneyUsd(v, signed);
    var inr = v * rate;
    var abs = Math.abs(inr);
    var s = '\u20B9' + abs.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    if (signed) return (inr >= 0 ? '+' : '-') + s;
    return (inr < 0 ? '-' : '') + s;
}
function _renderMoney(span) {
    var v = Number(span.getAttribute('data-usd') || 0);
    var signed = span.getAttribute('data-signed') === '1';
    span.textContent = (_currentCurrency() === 'INR')
        ? _fmtMoneyInr(v, signed)
        : _fmtMoneyUsd(v, signed);
}
function _applyCurrencyToAll() {
    var spans = document.querySelectorAll('span.money');
    for (var i = 0; i < spans.length; i++) _renderMoney(spans[i]);
    var cur = _currentCurrency();
    var usd = document.getElementById('cur-usd-pill');
    var inr = document.getElementById('cur-inr-pill');
    if (usd) usd.classList.toggle('active', cur === 'USD');
    if (inr) inr.classList.toggle('active', cur === 'INR');
}
function toggleUsCurrency() {
    var next = _currentCurrency() === 'USD' ? 'INR' : 'USD';
    try { localStorage.setItem('us-currency', next); } catch (e) {}
    _applyCurrencyToAll();
}

/* ── Live price poller (tiered) ────────────────────────────
 *
 * 2026-05-19: split the previous single 15 s poller into three
 * independent tiers so real-money positions refresh first,
 * watchlist next, and recommendations (no money at risk) get a
 * slow 60 s cadence.
 *
 *   open   — open book rows         → every 15 s
 *   watch  — watchlist rows         → every 30 s
 *   reco   — recommendation rows    → every 60 s
 *   detail — /us/<symbol> kvtable   → every 15 s
 *
 * Each tier fetches only its own symbols so a slow / rate-limited
 * yfinance batch on one tier never blocks another. On toggle-on
 * we cascade open → watch → reco with a 250 ms gap so the user
 * visibly sees their open book come alive first.
 */
var _US_TIER_INTERVALS = {
    open: 15000,
    watch: 30000,
    reco: 60000,
    detail: 15000
};
var _US_TIER_TIMERS = {};

function _usCollectTierSymbols(tier) {
    var nodes = document.querySelectorAll(
        '[data-live-symbol][data-live-tier="' + tier + '"]');
    var symbols = [];
    for (var i = 0; i < nodes.length; i++) {
        var s = nodes[i].getAttribute('data-live-symbol');
        if (s && symbols.indexOf(s) === -1) symbols.push(s);
    }
    return {nodes: nodes, symbols: symbols};
}

function _usApplyQuotesToNodes(nodes, quotes) {
    for (var i = 0; i < nodes.length; i++) {
        var row = nodes[i];
        var sym = row.getAttribute('data-live-symbol');
        var q = quotes[sym];
        if (!q || !q.price) continue;
        var price = Number(q.price || 0);
        var change = Number(q.change_pct || 0);
        _updateLiveCell(row, 'price', price, false);
        _updateLiveCell(row, 'price_with_change', price, false, change);
        var entry = parseFloat(row.getAttribute('data-entry-price') || '0');
        var qty = parseFloat(row.getAttribute('data-managed-qty') || '0');
        if (entry > 0 && qty > 0) {
            var pnl = (price - entry) * qty;
            _updateLiveCell(row, 'pnl', pnl, true);
            var rm = (price - entry) / Math.max(entry * 0.001, 1e-6);
            var stopAttr = parseFloat(row.getAttribute('data-stop-price') || '0');
            if (stopAttr > 0 && entry > stopAttr) {
                rm = (price - entry) / (entry - stopAttr);
            }
            var rmCell = row.querySelector('[data-live-field="r_mult"]');
            if (rmCell) rmCell.textContent =
                (rm >= 0 ? '+' : '') + rm.toFixed(2) + 'R';
        }
        var watchAdded = parseFloat(row.getAttribute('data-watch-price') || '0');
        if (watchAdded > 0) {
            var v = price - watchAdded;
            var vp = (price / watchAdded - 1) * 100;
            _updateWatchVpnl(row, v, vp);
        }
    }
}

function _usPollTier(tier) {
    if (!_usLiveEnabled() || document.hidden) return;
    var bundle = _usCollectTierSymbols(tier);
    if (!bundle.symbols.length) return;
    fetch('/api/us/live_prices?symbols=' +
          encodeURIComponent(bundle.symbols.join(',')))
        .then(function(r) { return r.json(); })
        .then(function(d) {
            _usApplyQuotesToNodes(bundle.nodes, (d && d.quotes) || {});
        })
        .catch(function() {});
}

/* Bottom-up cascade: open book first, watchlist second,
 * recommendations last. Used on toggle-on and on tab-visible. */
function _usCascadePoll() {
    if (!_usLiveEnabled() || document.hidden) return;
    _usPollTier('open');
    _usPollTier('detail');
    setTimeout(function() { _usPollTier('watch'); }, 250);
    setTimeout(function() { _usPollTier('reco'); }, 500);
}

function _usStartTierTimers() {
    _usStopTierTimers();
    var tiers = ['open', 'watch', 'reco', 'detail'];
    for (var i = 0; i < tiers.length; i++) {
        var tier = tiers[i];
        (function(t) {
            _US_TIER_TIMERS[t] = setInterval(function() {
                _usPollTier(t);
            }, _US_TIER_INTERVALS[t]);
        })(tier);
    }
}

function _usStopTierTimers() {
    for (var k in _US_TIER_TIMERS) {
        if (_US_TIER_TIMERS[k]) clearInterval(_US_TIER_TIMERS[k]);
        _US_TIER_TIMERS[k] = null;
    }
}

function _usLiveEnabled() {
    try { return localStorage.getItem('us-live-prices') === '1'; }
    catch (e) { return false; }
}

function _setUsLiveEnabled(enabled) {
    try { localStorage.setItem('us-live-prices', enabled ? '1' : '0'); }
    catch (e) {}
    _syncUsLiveToggle();
    if (enabled) {
        _usCascadePoll();
        _usStartTierTimers();
    } else {
        _usStopTierTimers();
    }
}

function _syncUsLiveToggle() {
    var btn = document.getElementById('us-live-toggle');
    var state = document.getElementById('us-live-state');
    var enabled = _usLiveEnabled();
    if (btn) btn.textContent = enabled ? 'Pause live prices' : 'Load live prices';
    if (state) state.textContent = enabled
        ? 'Open book 15 s · Watchlist 30 s · Recommendations 60 s'
        : 'Live prices paused';
}
function _updateLiveCell(row, field, price, signed, changePct) {
    var cell = row.querySelector('[data-live-field="' + field + '"]');
    if (!cell) return;
    if (field === 'price_with_change') {
        var cls = (changePct != null && changePct >= 0) ? 'pos' : 'neg';
        cell.innerHTML =
            '<span class="' + cls + '">' +
            '<span class="money" data-usd="' + price + '" data-signed="0">' +
            _fmtMoneyUsd(price, false) + '</span></span> ' +
            '<span class="muted">(' +
            (changePct >= 0 ? '+' : '') + Number(changePct || 0).toFixed(1) + '%)</span>';
    } else if (signed) {
        // P&L cells: wrap in pos/neg so green/red colour stays in
        // sync with the live value (2026-05-19 fix — used to drop
        // the colour class on every poll, causing the open-book
        // P&L colour to flip between black and pos/neg every 15 s).
        var pnlCls = price >= 0 ? 'pos' : 'neg';
        cell.innerHTML =
            '<span class="' + pnlCls + '">' +
            '<span class="money" data-usd="' + price + '" data-signed="1">' +
            _fmtMoneyUsd(price, true) + '</span></span>';
    } else {
        cell.innerHTML =
            '<span class="money" data-usd="' + price + '" data-signed="' +
            (signed ? '1' : '0') + '">' + _fmtMoneyUsd(price, signed) + '</span>';
    }
    var spans = cell.querySelectorAll('span.money');
    for (var i = 0; i < spans.length; i++) _renderMoney(spans[i]);
}
function _updateWatchVpnl(row, vpnl, vpct) {
    var cell = row.querySelector('[data-live-field="vpnl"]');
    if (!cell) return;
    var cls = vpnl >= 0 ? 'pos' : 'neg';
    cell.innerHTML =
        '<span class="' + cls + '">' +
        '<span class="money" data-usd="' + vpnl + '" data-signed="1">' +
        _fmtMoneyUsd(vpnl, true) + '</span> (' +
        (vpct >= 0 ? '+' : '') + vpct.toFixed(1) + '%)</span>';
    var spans = cell.querySelectorAll('span.money');
    for (var i = 0; i < spans.length; i++) _renderMoney(spans[i]);
}

/* ── FX poller (every 5 min) ──────────────────────────────── */
function _usPollFx() {
    if (document.hidden) return;
    fetch('/api/fx/usdinr')
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d && d.rate > 0) {
                window._usdInrRate = Number(d.rate);
                _applyCurrencyToAll();
            }
        })
        .catch(function() {});
}

window.addEventListener('DOMContentLoaded', function() {
    _applyCurrencyToAll();
    _syncUsLiveToggle();
    var btn = document.getElementById('us-live-toggle');
    if (btn) btn.addEventListener('click', function() {
        _setUsLiveEnabled(!_usLiveEnabled());
    });
    if (_usLiveEnabled()) {
        // Bottom-up cascade: open book lights up first, then
        // watchlist (250 ms later), then recommendations (500 ms).
        _usCascadePoll();
        _usStartTierTimers();
    }
    setTimeout(_usPollFx, 1000);
    setInterval(_usPollFx, 5 * 60 * 1000);
    if (window._loadUsChangesSince) window._loadUsChangesSince();
});

document.addEventListener('visibilitychange', function() {
    if (document.hidden) return;
    // Tab came back into focus: re-cascade so the user sees fresh
    // prices in their open book first, watchlist next, recos last.
    if (_usLiveEnabled()) _usCascadePoll();
    _usPollFx();
});

/* ── What changed since last US scan (mirrors swing diff card) ──
 * Loads /api/us/changes_since once on page-load and re-fetches
 * whenever an in-page scan completes (runUsScan triggers a full
 * location.reload so this fires again on the next render). The
 * renderer mirrors the swing _renderChangesSince HTML so the two
 * cards look identical.
 */
window._loadUsChangesSince = function () {
    var host = document.getElementById('us-changes-since-host');
    if (!host) return;
    fetch('/api/us/changes_since')
        .then(function (r) { return r.json(); })
        .then(function (j) { _renderUsChangesSince(host, j || {}); })
        .catch(function () {
            host.innerHTML = '<span class="muted">Unable to load ' +
                             'change diff.</span>';
        });
};

function _renderUsChangesSince(host, d) {
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
        });
    }
    if (!d || !d.current_run_id) {
        host.innerHTML = '<span class="muted">No US scan on file yet — ' +
                         'click <em>Run Scan</em> above to start tracking ' +
                         'changes.</span>';
        return;
    }
    if (!d.prior_run_id) {
        host.innerHTML = '<span class="muted">First US scan on file — ' +
                         'the next scan will produce a diff here.</span>';
        return;
    }
    var html = '';
    var priorLabel = d.prior_run_age_label || ('scan from ' +
                     (d.prior_run_date || '?'));
    var curStamp = d.current_run_finished_at || d.current_run_date || '';
    html += '<div style="font-size:13px;margin-bottom:10px">';
    html += '<strong>Comparing latest scan</strong> ' +
            '<span class="muted">(' + esc(d.current_run_date) +
            (curStamp && curStamp !== d.current_run_date
                ? ' · ' + esc(curStamp.slice(11, 16))
                : '') + ')</span>';
    html += ' <strong>vs ' + esc(priorLabel) + '</strong>';
    html += '</div>';

    var nIn  = (d.new_entries || []).length;
    var nOut = (d.dropped || []).length;
    var nMov = (d.rank_movers || []).length;
    if (nIn === 0 && nOut === 0 && nMov === 0) {
        html += '<div class="muted">No changes from the previous scan.</div>';
        host.innerHTML = html;
        return;
    }

    html += '<div style="margin-bottom:12px;font-size:13px">';
    if (nIn) html += '<span style="background:#e6f4ea;color:#1b5e20;' +
                    'padding:3px 8px;border-radius:4px;margin-right:6px;' +
                    'font-weight:600">+' + nIn + ' new</span>';
    if (nOut) html += '<span style="background:#fde8e8;color:#7a1f1f;' +
                     'padding:3px 8px;border-radius:4px;margin-right:6px;' +
                     'font-weight:600">−' + nOut + ' dropped</span>';
    if (nMov) html += '<span style="background:#fff4cc;color:#7a5500;' +
                     'padding:3px 8px;border-radius:4px;margin-right:6px;' +
                     'font-weight:600">⇅ ' + nMov + ' rank mover' +
                     (nMov === 1 ? '' : 's') + '</span>';
    html += '</div>';

    function _link(sym) {
        return '<a href="/us/' + encodeURIComponent(sym) +
               '" style="font-weight:600;color:var(--fg)">' +
               esc(sym) + '</a>';
    }

    if (nIn) {
        html += '<div style="margin-bottom:10px"><strong>New entries</strong>' +
                ' <span class="muted">— in the latest scan but not in ' +
                esc(priorLabel) + ':</span><br>';
        html += '<div style="margin-top:6px;font-size:13px;line-height:1.8">';
        d.new_entries.forEach(function (e) {
            html += '• ' + _link(e.symbol) +
                    ' <span class="muted">(rank #' + e.rank +
                    ', score ' + (Number(e.score) || 0).toFixed(1) +
                    ', ' + esc(e.setup_type || '') + ')</span><br>';
        });
        html += '</div></div>';
    }

    if (nOut) {
        html += '<div style="margin-bottom:10px"><strong>Dropped</strong>' +
                ' <span class="muted">— were in ' + esc(priorLabel) +
                ' but not in the latest scan:</span><br>';
        html += '<div style="margin-top:6px;font-size:13px;line-height:1.8">';
        d.dropped.forEach(function (e) {
            html += '• ' + _link(e.symbol) +
                    ' <span class="muted">(was rank #' + e.prior_rank +
                    ', score ' + (Number(e.prior_score) || 0).toFixed(1) +
                    ', ' + esc(e.prior_setup_type || '') +
                    ') — not present in latest</span><br>';
        });
        html += '</div></div>';
    }

    if (nMov) {
        html += '<div style="margin-bottom:6px"><strong>Rank movers</strong>' +
                ' <span class="muted">— in both scans, |Δrank| ≥ 3:' +
                '</span><br>';
        html += '<div style="margin-top:6px;font-size:13px;line-height:1.8">';
        d.rank_movers.forEach(function (e) {
            var dir = e.delta > 0 ? '↑' : '↓';
            var col = e.delta > 0 ? '#1b5e20' : '#7a1f1f';
            html += '• ' + _link(e.symbol) +
                    ' <span style="color:' + col + ';font-weight:600">' +
                    dir + Math.abs(e.delta) + '</span> ' +
                    '<span class="muted">(#' + e.prior_rank +
                    ' → #' + e.new_rank;
            if (e.score_delta && Math.abs(e.score_delta) >= 0.1) {
                html += ', Δscore ' +
                        (e.score_delta > 0 ? '+' : '') +
                        Number(e.score_delta).toFixed(1);
            }
            html += ')</span><br>';
        });
        html += '</div></div>';
    }

    host.innerHTML = html;
}

/* ── Scan controls ─────────────────────────────────────────── */
function _scanBanner(msg, kind) {
    var host = document.getElementById('us-scan-banner');
    if (!host) return;
    var spin = (kind === 'info') ? '<span class="spinner"></span>' : '';
    host.innerHTML = '<div class="banner ' + (kind || 'info') + '">' + spin + _esc(msg) + '</div>';
}
function runUsScan() {
    var ticketEl = document.getElementById('us-scan-ticket');
    var ticket = _num(ticketEl ? ticketEl.value : '0', 'Amount per stock');
    if (ticket === null) return;
    var universeEl = document.getElementById('us-scan-universe');
    var universe = universeEl ? universeEl.value : 'US100';
    var useAi = document.getElementById('us-ai-toggle') && document.getElementById('us-ai-toggle').checked;
    if (useAi && !confirm('Run ' + universe + ' scan with Claude AI overlay for top candidates?')) return;
    _scanBanner('Running ' + (useAi ? 'AI' : 'NoAI') + ' ' + universe + ' scan. This can take 1-2 minutes...', 'info');
    fetch('/api/us/run?mode=' + (useAi ? 'AI' : 'NOAI') +
          '&ticket=' + encodeURIComponent(ticket) +
          '&universe=' + encodeURIComponent(universe))
        .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
        .then(function(res) {
            if (!res.ok || !res.body.ok) {
                _scanBanner(res.body.error || 'US scan failed', 'error');
                return;
            }
            _scanBanner('Scan complete \u2014 refreshing\u2026', 'info');
            setTimeout(function() { location.reload(); }, 800);
        })
        .catch(function(e) { _scanBanner('Network error: ' + e, 'error'); });
}

/* ── Add+ + manual position ───────────────────────────────── */
function addUsCandidate(selectEl) {
    var mode = selectEl.value;
    if (!mode) return;
    var row = JSON.parse(selectEl.getAttribute('data-row') || '{}');
    selectEl.value = '';
    if (mode === 'watch') {
        fetch('/api/us/watchlist/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(row)
        })
            .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
            .then(function(res) {
                if (!res.ok || !res.body.ok) alert('Watchlist add failed: ' + (res.body.error || 'unknown error'));
                else location.reload();
            })
            .catch(function(e) { alert('Network error: ' + e); });
        return;
    }
    buyUsCandidate(row);
}
function buyUsCandidate(row) {
    confirmUsPurchase('/api/us/positions/add', 'Add failed', {
        symbol: row.symbol,
        exchange: row.exchange || 'NASDAQ'
    }, row || {});
}
function addUsPosition() {
    var symbolEl = document.getElementById('us-analyse-symbol');
    var symbol = String(symbolEl ? symbolEl.value : '').trim().toUpperCase();
    if (!symbol) {
        alert('Type a ticker in Analyse a Single Stock first, then use Add+.');
        return;
    }
    confirmUsPurchase('/api/us/positions/add', 'Add failed', {
        symbol: symbol,
        exchange: 'NASDAQ'
    }, {symbol: symbol, exchange: 'NASDAQ'});
}
function editUsPosition(posId, qty, price, stop, target) {
    var newQty = _qty(prompt('Total shares (decimals OK):', String(qty)), 'Qty');
    if (newQty === null) return;
    var newPrice = _optionalNum(prompt('Average cost ($):', String(price)), 'Price');
    if (newPrice === null || newPrice <= 0) return;
    var newStop = _optionalNum(prompt('Stop ($):', String(stop)), 'Stop');
    var newTarget = _optionalNum(prompt('Target ($):', String(target)), 'Target');
    fetch('/api/us/positions/' + posId + '/edit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({qty: newQty, price: newPrice, stop: newStop, target: newTarget})
    })
        .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
        .then(function(res) {
            if (!res.ok || !res.body.ok) alert('Edit failed: ' + (res.body.error || 'unknown error'));
            else location.reload();
        })
        .catch(function(e) { alert('Network error: ' + e); });
}
function exitUsPosition(posId, currentQty) {
    var qty = _qty(prompt('Exit shares (decimals OK):', String(currentQty || '')), 'Exit shares');
    if (qty === null) return;
    var price = _num(prompt('Exit price ($):'), 'Exit price');
    if (price === null) return;
    fetch('/api/us/positions/' + posId + '/exit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({qty: qty, price: price})
    })
        .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
        .then(function(res) {
            if (!res.ok || !res.body.ok) alert('Exit failed: ' + (res.body.error || 'unknown error'));
            else location.reload();
        })
        .catch(function(e) { alert('Network error: ' + e); });
}
function promoteUsWatchlist(watchlistId) {
    confirmUsPurchase('/api/us/watchlist/' + watchlistId + '/promote',
        'Promote failed', {}, {});
}
function removeUsWatchlist(watchlistId) {
    if (!confirm('Remove from US watchlist?')) return;
    fetch('/api/us/watchlist/' + watchlistId + '/remove', {method: 'POST'})
        .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
        .then(function(res) {
            if (!res.ok || !res.body.ok) alert('Remove failed: ' + (res.body.error || 'unknown error'));
            else location.reload();
        })
        .catch(function(e) { alert('Network error: ' + e); });
}

/* ── Single-stock analyse ─────────────────────────────────── */
function _analysisHost() { return document.getElementById('us-analysis-result'); }
function _badgeClass(action) {
    if (action === 'BUY_CANDIDATE') return 'pos';
    if (action === 'WATCH') return 'muted';
    return 'muted';
}
function _actionLabel(action) {
    return {BUY_CANDIDATE:'Buy Candidate', WATCH:'Watch', WAIT:'Wait', NO_SETUP:'No Setup'}[action] || action || 'Unknown';
}
function analyseUsStock(symbol) {
    var symbolEl = document.getElementById('us-analyse-symbol');
    var host = _analysisHost();
    var ticker = String(symbol || (symbolEl ? symbolEl.value : '') || '').trim().toUpperCase();
    if (!ticker) { alert('Symbol is required'); return; }
    if (symbolEl) symbolEl.value = ticker;
    var ticket = _usTicketAmount();
    if (ticket === null) return;
    if (host) host.innerHTML = '<p class="muted"><span class="spinner"></span>Analysing ' + _esc(ticker) + '\u2026</p>';
    var useAi = document.getElementById('us-single-ai-toggle') && document.getElementById('us-single-ai-toggle').checked;
    fetch('/api/us/analyse?symbol=' + encodeURIComponent(ticker) + '&ticket=' + encodeURIComponent(ticket) + '&ai=' + (useAi ? '1' : '0'))
        .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
        .then(function(res) {
            if (!res.ok || !res.body.ok) {
                if (host) host.innerHTML = '<div class="banner error">' + _esc(res.body.error || 'Analysis failed') + '</div>';
                return;
            }
            _renderUsAnalysis(res.body);
        })
        .catch(function(e) {
            if (host) host.innerHTML = '<div class="banner error">Network error: ' + _esc(e) + '</div>';
        });
}
function _moneyHtml(v) {
    var n = Number(v || 0);
    return '<span class="money" data-usd="' + n + '" data-signed="0">' +
           _fmtMoneyUsd(n, false) + '</span>';
}
function _usActionPayload(row) {
    return {
        symbol: row.symbol || '',
        exchange: row.exchange || 'NASDAQ',
        price: row.entry_price || row.current_price || 0,
        stop: row.stop_price || 0,
        target: row.target_price || 0,
        qty: row.suggested_qty || 0,
        setup_type: row.setup_type || '',
        stock_name: row.stock_name || ''
    };
}
function _renderUsAnalysis(row) {
    var host = _analysisHost();
    if (!host) return;
    var ind = row.indicators || {};
    var reasons = (row.reasons || []).map(function(r) { return '<li>' + _esc(r) + '</li>'; }).join('');
    var warnings = (row.warnings || []).map(function(r) { return '<li>' + _esc(r) + '</li>'; }).join('');
    var ai = row.ai_overlay || null;
    var border = row.action === 'BUY_CANDIDATE' ? '#1b8e3a' : '#6a7280';
    var aiHtml = '';
    if (ai && ai.raw_response) aiHtml = '<p><strong>AI Overlay</strong></p><div class="banner">' + _esc(ai.raw_response).replace(/\n/g, '<br>') + '</div>';
    else if (ai && ai.error) aiHtml = '<div class="banner error">AI overlay failed: ' + _esc(ai.error) + '</div>';
    var payload = _esc(JSON.stringify(_usActionPayload(row)));
    var controls =
        '<div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">' +
        '<select class="add-dropdown" data-row="' + payload + '" ' +
        'onchange="addUsCandidate(this)" ' +
        'style="padding:4px 6px;font-size:12px;font-weight:600;' +
        'border:1px solid var(--accent);border-radius:5px;' +
        'background:var(--card);cursor:pointer">' +
        '<option value="">Add+</option>' +
        '<option value="watch">Watch</option>' +
        '<option value="buy">I Bought It</option>' +
        '</select>' +
        '<a href="/us/' + encodeURIComponent(row.symbol || '') + '" ' +
        'style="padding:5px 10px;font-size:12px;' +
        'border:1px solid #cfd9eb;border-radius:5px;' +
        'text-decoration:none;display:inline-block">' +
        'Open detail page</a>' +
        '</div>';
    host.innerHTML =
        '<div style="border-left:4px solid ' + border + ';padding:10px 12px;' +
        'background:#fafbfc;border-radius:4px;margin-top:6px">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">' +
        '<strong style="font-size:15px">' + _esc(row.symbol) + '</strong>' +
        '<span style="font-size:12px;color:' + border + ';font-weight:600">' +
        _actionLabel(row.action) + '</span></div>' +
        (_esc(row.stock_name || '') ? '<div class="muted small" style="margin-bottom:6px">' + _esc(row.stock_name || '') + '</div>' : '') +
        '<div class="muted small">' + _esc(row.data_source) + ' \u00b7 data through ' + _esc(row.as_of) + ' \u00b7 benchmark ' + _esc(row.benchmark || 'SPY') + '</div>' +
        '<table class="kvtable" style="margin-top:10px">' +
        '<tr><td>Action</td><td><strong>' + _actionLabel(row.action) + '</strong></td></tr>' +
        '<tr><td>Setup</td><td>' + _esc(row.setup_type || 'NONE') + '</td></tr>' +
        '<tr><td>Price</td><td>' + _moneyHtml(row.current_price) + '</td></tr>' +
        '<tr><td>Entry</td><td>' + _moneyHtml(row.entry_price) + '</td></tr>' +
        '<tr><td>Stop</td><td>' + _moneyHtml(row.stop_price) + '</td></tr>' +
        '<tr><td>Target</td><td>' + _moneyHtml(row.target_price) + '</td></tr>' +
        '<tr><td>R:R</td><td>' + Number(row.rr_ratio || 0).toFixed(2) + 'x</td></tr>' +
        '<tr><td>Qty</td><td>' + (row.suggested_qty || 0) + ' shares (' + _moneyHtml(row.position_value) + ')</td></tr>' +
        '<tr><td>Score</td><td>' + Number(row.score || 0).toFixed(2) + '</td></tr>' +
        '<tr><td>RSI / ATR</td><td>' + Number(ind.rsi || 0).toFixed(1) + ' / ' + _moneyHtml(ind.atr_14) + '</td></tr>' +
        '<tr><td>SMA-50 / SMA-200</td><td>' + _moneyHtml(ind.sma_50) + ' / ' + _moneyHtml(ind.sma_200) + '</td></tr>' +
        '<tr><td>52W High Gap</td><td>' + Number(ind.dip_from_52w_high_pct || 0).toFixed(2) + '%</td></tr>' +
        '<tr><td>RS vs SPY</td><td>' + Number(ind.relative_strength || 0).toFixed(2) + '%</td></tr>' +
        '</table>' +
        controls +
        (reasons ? '<p style="margin-top:10px"><strong>Reasons</strong></p><ol>' + reasons + '</ol>' : '') +
        (warnings ? '<p><strong>Warnings</strong></p><ul>' + warnings + '</ul>' : '') +
        aiHtml +
        '</div>';
    _applyCurrencyToAll();
}

/* ── Compare up to 4 US stocks ────────────────────────────── */
/* ── Compare up to 4 US stocks (mirrors swing /api/swing/compare)
 *
 * 2026-05-19 rewrite. The previous version had a hard-coded
 * "group" dropdown, fired N parallel /api/us/analyse calls
 * client-side, and rendered its own table without winner
 * highlighting. The user wanted EXACTLY the same UX as /swing
 * compare, so we now:
 *
 *   1. Load sector keys from /api/us/sectors on DOMContentLoaded.
 *   2. When the sector dropdown changes, /api/us/compare?sector=X
 *      returns the top-4 list AND a fully-built winner-tagged
 *      result matrix in one call (auto-fills the input + renders).
 *   3. The "Compare" button calls /api/us/compare?symbols=A,B,C,D
 *      for free-text inputs.
 *   4. _renderCompareResult() is the same renderer that /swing
 *      uses — winning cells highlighted in green, headline tally
 *      "<symbol> wins most metrics", etc.
 */
window.addEventListener('DOMContentLoaded', function () {
    var sel = document.getElementById('us-compare-sector');
    if (!sel) return;
    fetch('/api/us/sectors')
        .then(function (r) { return r.json(); })
        .then(function (j) {
            var sectors = (j && j.sectors) || [];
            sectors.forEach(function (s) {
                var opt = document.createElement('option');
                opt.value = s;
                // Pretty-print: MEGACAP_TECH -> Megacap Tech
                opt.textContent = s.toLowerCase().replace(/_/g, ' ')
                    .replace(/\b\w/g, function(c) { return c.toUpperCase(); });
                sel.appendChild(opt);
            });
        })
        .catch(function () { /* silent — dropdown stays minimal */ });
    sel.addEventListener('change', function () {
        var sector = sel.value;
        if (!sector) return;
        var host = document.getElementById('us-compare-result-host');
        if (host) host.innerHTML =
            '<p class="muted"><span class="spinner"></span> ' +
            'Fetching ' + _esc(sector) + ' top-4 ' +
            '(this can take 5-15 seconds for 4 names)...</p>';
        fetch('/api/us/compare?sector=' + encodeURIComponent(sector))
            .then(function (r) { return r.json(); })
            .then(function (j) {
                if (j && j.symbols) {
                    var inp = document.getElementById('us-compare-symbols');
                    if (inp) inp.value = j.symbols.join(', ');
                    _renderUsCompareResult(host, j);
                }
            })
            .catch(function () { /* silent */ });
    });
});

function compareUsNow() {
    var inp = document.getElementById('us-compare-symbols');
    var sel = document.getElementById('us-compare-sector');
    var host = document.getElementById('us-compare-result-host');
    if (!host) return;
    var syms = (inp && inp.value || '').trim();
    var sector = (sel && sel.value || '').trim();
    if (!syms && !sector) {
        host.innerHTML = '<div class="banner warn">' +
            'Type tickers OR pick a sector first.</div>';
        return;
    }
    var url = syms
        ? '/api/us/compare?symbols=' + encodeURIComponent(syms)
        : '/api/us/compare?sector=' + encodeURIComponent(sector);
    host.innerHTML = '<p class="muted"><span class="spinner"></span> ' +
        'Fetching candles + computing comparison ' +
        '(this can take 5-15 seconds for 4 names)...</p>';
    fetch(url)
        .then(function (r) { return r.json().then(function (j) {
            return {ok: r.ok, body: j}; }); })
        .then(function (res) {
            if (!res.ok || !res.body.ok) {
                host.innerHTML = '<div class="banner warn">' +
                    'Compare failed: ' +
                    (res.body && res.body.error || 'unknown') + '</div>';
                return;
            }
            _renderUsCompareResult(host, res.body);
        })
        .catch(function (e) {
            host.innerHTML = '<div class="banner warn">Network error: ' +
                e + '</div>';
        });
}

function compareUsClear() {
    var inp = document.getElementById('us-compare-symbols');
    var sel = document.getElementById('us-compare-sector');
    var host = document.getElementById('us-compare-result-host');
    if (inp) inp.value = '';
    if (sel) sel.value = '';
    if (host) host.innerHTML = '';
}

/* Renderer is functionally identical to the swing
 * `_renderCompareResult` in `swing_page.py`. Kept inline here
 * because the two pages don't share a JS bundle. */
function _renderUsCompareResult(host, data) {
    if (!host) return;
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
        });
    }
    var syms = data.symbols || [];
    if (!syms.length) {
        host.innerHTML = '<div class="banner warn">No data.</div>';
        return;
    }
    var winnerCounts = data.win_counts || [];
    var headOverall = data.winner_overall;
    var html = '';
    if (headOverall) {
        html += '<div style="margin:6px 0 10px 0;font-size:13px">';
        html += '<strong>' + esc(headOverall) + '</strong> wins ' +
                'most metrics. Tally: ';
        var bits = [];
        for (var i = 0; i < syms.length; i++) {
            bits.push('<span style="font-weight:' +
                      (syms[i] === headOverall ? '600' : '400') + '">' +
                      esc(syms[i]) + ' ' + winnerCounts[i] + '</span>');
        }
        html += bits.join(' &middot; ');
        html += '</div>';
    }
    if (data.sector) {
        html += '<div class="muted" style="font-size:11px;margin-bottom:6px">' +
                'Sector: <strong>' + esc(data.sector) + '</strong> &middot; ' +
                'top ' + syms.length + ' from the curated US sector map.</div>';
    }
    html += '<div style="overflow-x:auto"><table class="holdings" ' +
            'style="font-size:12.5px"><thead><tr>';
    html += '<th style="text-align:left;min-width:180px">Metric</th>';
    syms.forEach(function (s) {
        html += '<th style="text-align:center;min-width:120px">' +
                '<a href="/us/' + encodeURIComponent(s) + '" ' +
                'style="color:var(--fg);font-weight:600">' +
                esc(s) + '</a></th>';
    });
    html += '</tr></thead><tbody>';
    (data.rows || []).forEach(function (row) {
        html += '<tr>';
        var lbl = esc(row.label);
        if (row.explain) {
            lbl = '<span title="' + esc(row.explain) + '" ' +
                  'style="border-bottom:1px dotted #cfd9eb;cursor:help">' +
                  lbl + '</span>';
        }
        html += '<td style="text-align:left">' + lbl + '</td>';
        (row.values || []).forEach(function (v, i) {
            var wins = row.winners_idx;
            var winning = false;
            if (Array.isArray(wins) && wins.length) {
                winning = wins.indexOf(i) !== -1;
            } else {
                winning = (row.winner_idx === i);
            }
            var bg = winning ? 'background:#e6f4ea;font-weight:600' : '';
            html += '<td style="text-align:center;' + bg + '">' +
                    esc(v) + '</td>';
        });
        html += '</tr>';
    });
    html += '</tbody></table></div>';
    if (data.notes && data.notes.length) {
        html += '<div class="muted" style="font-size:11px;margin-top:8px">' +
                data.notes.map(esc).join('<br>') + '</div>';
    }
    host.innerHTML = html;
}

/* ── AI per-stock (detail page) ───────────────────────────── */
function aiAnalyseUsSingle(symbol) {
    var btn = document.getElementById('ai-analyse-btn');
    var host = document.getElementById('ai-overlay-host');
    if (!btn || !host) return;
    if (!confirm('Run Claude AI overlay for ' + symbol + '?')) return;
    btn.disabled = true;
    var orig = btn.textContent;
    btn.textContent = 'Analysing\u2026';
    host.innerHTML = '<p class="muted"><span class="spinner"></span>Calling Claude\u2026</p>';
    fetch('/api/us/analyse?symbol=' + encodeURIComponent(symbol) + '&ai=1')
        .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
        .then(function(res) {
            btn.disabled = false;
            btn.textContent = orig;
            if (!res.ok || !res.body.ok) {
                host.innerHTML = '<div class="banner error">AI analyse failed: ' + _esc((res.body && res.body.error) || 'unknown error') + '</div>';
                return;
            }
            var ai = res.body.ai_overlay || {};
            if (ai.raw_response) {
                host.innerHTML = '<div class="banner">' + _esc(ai.raw_response).replace(/\n/g, '<br>') + '</div>';
            } else if (ai.error) {
                host.innerHTML = '<div class="banner error">' + _esc(ai.error) + '</div>';
            } else {
                host.innerHTML = '<p class="muted">AI returned an empty response.</p>';
            }
        })
        .catch(function(e) {
            btn.disabled = false;
            btn.textContent = orig;
            host.innerHTML = '<div class="banner error">Network error: ' + _esc(e) + '</div>';
        });
}
</script>"""


__all__ = ["render_us_page", "render_us_detail", "render_us_sections_json"]
