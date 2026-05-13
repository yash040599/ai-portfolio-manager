# ================================================================
# modes/dashboard/swing_page.py
# ================================================================
# Dashboard `/swing` page (Dashboard D31, SWING_ROADMAP S16).
#
# Renders:
#   - Realised P&L summary
#   - Broker-entry instruction card
#   - Priority-sorted entry recommendation table
#   - Open swing book with live prices
#   - Action controls (Done / Skip / Exit)
#   - AI mode control
#   - Auto-scan trigger
# ================================================================

from __future__ import annotations

import html
import json
import os
from typing import Any

from config import Config, now_ist
from modes.swing.persistence import (
    init_db, open_positions, pending_actions, realised_pnl_summary,
    latest_run_for_date, latest_run, actions_for_run,
    candidate_by_symbol, candidates_for_run,
)
from modes.swing.types import SwingAction, SwingPosition
from modes.dashboard.live_quotes import get_live_quotes
from modes.dashboard.swing_actions import latest_swing_status


# ── Shared nav + style (matches portfolio_page.py) ──────────────

def _auth_pill() -> str:
    token_path = os.path.join("data", "access_token.json")
    valid = False
    if os.path.exists(token_path):
        try:
            with open(token_path, encoding="utf-8") as f:
                import json as _j
                saved = _j.load(f)
            valid = saved.get("date") == str(now_ist().date())
        except Exception:
            pass
    if valid:
        return ('<a class="auth ok" href="/login" title="Token valid for today">'
                'Auth: <strong>OK</strong></a>')
    return ('<a class="auth bad" href="/login" '
            'title="Re-login required">'
            'Auth: <strong>Re-login</strong></a>')


def _topnav(here: str) -> str:
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
            parts.append('<span class="sep">&middot;</span>')
        if href == here:
            parts.append(f'<span class="here">{html.escape(label)}</span>')
        else:
            parts.append(f'<a href="{href}">{html.escape(label)}</a>')
    return ('<nav class="topnav">'
            + "".join(parts)
            + '<span class="spacer"></span>'
            + _auth_pill()
            + '</nav>')


# ── Page renderer ───────────────────────────────────────────────

def render_swing_page() -> str:
    """Render the full /swing HTML page."""
    init_db()

    today = now_ist().date().isoformat()
    # Use the most recent run for display — may be for yesterday if
    # the scan ran before market close.
    latest_run_row = latest_run()
    positions = open_positions()
    pnl = realised_pnl_summary()

    # Fetch Zerodha available funds for the capital default
    default_capital = 100_000.0
    try:
        import json as _j
        token_path = os.path.join("data", "access_token.json")
        if os.path.exists(token_path):
            with open(token_path, encoding="utf-8") as f:
                saved = _j.load(f)
            if saved.get("date") == str(now_ist().date()):
                from core.zerodha_client import ZerodhaClient
                from core.logger import Logger as _Log
                _z = ZerodhaClient(Config, _Log("SwingPageFunds"))
                _z.login(interactive=False)
                default_capital = _z.get_available_funds()
    except Exception:
        pass  # fallback to 100k

    # Get pending entry actions (priority sorted) + candidates for reasons
    entry_actions: list[SwingAction] = []
    run_actions: list[SwingAction] = []
    candidates_by_symbol: dict[str, Any] = {}
    if latest_run_row:
        run_actions = actions_for_run(int(latest_run_row["run_id"]))
        entry_actions = [a for a in run_actions
                         if a.action_type == "ENTRY" and a.status == "PENDING"]
        entry_actions.sort(key=lambda a: a.priority_rank or 999)
        # Load candidates to get setup_type + reasons
        for c in candidates_for_run(int(latest_run_row["run_id"])):
            candidates_by_symbol[c.symbol] = c

    # Live quotes
    all_symbols = list({a.symbol for a in entry_actions} |
                       {p.symbol for p in positions})
    live = get_live_quotes(all_symbols) if all_symbols else {}

    # Job status
    job = latest_swing_status()

    body = []
    body.append(_topnav("/swing"))
    body.append('<div class="wrap">')
    body.append('<h1 class="page-title">Swing Trading</h1>')

    # ── Data freshness line ─────────────────────────────────────
    freshness_parts = []
    if latest_run_row:
        run_when = latest_run_row.get('finished_at', '')[:19].replace('T', ' ')
        run_date = latest_run_row.get('run_for_date', '')
        run_mode = latest_run_row.get('mode', 'NOAI')
        freshness_parts.append(
            f'Last analysis: {run_mode} run completed {run_when} IST '
            f'&middot; data through {run_date}')
    else:
        freshness_parts.append('No swing analysis run yet.')

    if all_symbols:
        freshness_parts.append(
            'Live prices refresh every 5 seconds (Zerodha quote polling)')

    body.append('<div class="sub">' + '<br>'.join(freshness_parts) + '</div>')

    # ── P&L summary ────────────────────────────────────────────
    body.append('<div class="card">')
    body.append('<h2>Realised Swing P&amp;L</h2>')
    body.append('<table class="kvtable">')
    _kv = lambda k, v: f'<tr><td>{k}</td><td>{v}</td></tr>'
    pnl_cls = "pos" if pnl["net_pnl"] >= 0 else "neg"
    body.append(_kv("Gross P&amp;L",
                     f'<span class="{pnl_cls}">Rs.{pnl["gross_pnl"]:+,.2f}</span>'))
    body.append(_kv("Charges", f'Rs.{pnl["charges"]:,.2f}'))
    body.append(_kv("Net P&amp;L",
                     f'<span class="{pnl_cls}">Rs.{pnl["net_pnl"]:+,.2f}</span>'))
    body.append(_kv("Closed trades", str(pnl["count"])))
    body.append('</table></div>')

    # ── Scan controls ──────────────────────────────────────────
    body.append('<div class="card">')
    body.append('<h2>Daily Scan</h2>')

    # Job banner (for loading/status feedback)
    body.append('<div id="swing-job-banner"></div>')

    is_market_open = _is_market_open()
    if is_market_open:
        body.append('<div class="banner info">Market is still open. '
                    "Running now will use data through yesterday's close. "
                    'For the freshest analysis, run after 3:30 PM IST.</div>')

    if job and job.status == "RUNNING":
        body.append('<div class="banner info">'
                    '<span class="spinner"></span> Swing scan running&hellip;'
                    '</div>')

    # Capital input
    body.append('<div style="margin-bottom:12px">')
    body.append('<label style="font-size:13px;font-weight:500">'
                'Swing Capital (Rs.): </label>')
    body.append(f'<input type="number" id="swing-capital" '
                f'value="{int(default_capital)}" '
                f'min="10000" step="1000" '
                f'style="width:160px;padding:6px 10px;font:inherit;'
                f'border:1px solid #cfd9eb;border-radius:5px;'
                f'font-variant-numeric:tabular-nums" />')
    body.append(f'<span class="muted" style="margin-left:8px;font-size:12px">'
                f'Default: Zerodha available funds (Rs.{default_capital:,.0f})</span>')
    body.append('</div>')

    body.append('<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">')
    body.append('<button class="action" onclick="runSwingScan()">'
                'Run Scan</button>')
    body.append('<label class="ai-toggle" '
                'title="Toggle to add Claude qualitative overlay">' 
                '<input type="checkbox" id="swing-ai-toggle">'
                '<span class="lbl">Use Claude AI overlay</span>'
                '<span class="hint">(NoAI is default; AI adds thesis + risks + news)</span>'
                '</label>')

    if latest_run_row:
        mode_badge = latest_run_row.get("mode", "NOAI")
        finished_at = latest_run_row.get("finished_at", "")[:16]
        run_date = latest_run_row.get("run_for_date", "")
        body.append(f'<span class="muted">Last run: {mode_badge} '
                    f'(data: {run_date}, ran {finished_at})</span>')
    body.append('</div>')

    # Auto-run note
    body.append('<div class="muted" style="font-size:12px;margin-top:8px">')
    if _is_market_open():
        body.append('Auto-scan will run at 3:30 PM IST today (after market close) '
                    'if this page is open. You can also run manually anytime '
                    '\u2014 pre-close scans use yesterday\'s completed data.')
    else:
        body.append('Auto-scan runs once per day after 3:30 PM IST when this '
                    'page is open. Next auto-scan: tomorrow after market close.')
    body.append('</div>')

    # Embed flag so JS can prompt before rerun
    has_run_today = "true" if latest_run_row else "false"
    last_mode = latest_run_row.get("mode", "") if latest_run_row else ""
    body.append(f'<script>window._swingHasRunToday={has_run_today};'
                f'window._swingLastMode="{last_mode}";</script>')
    body.append('</div>')

    # ── Broker instructions ────────────────────────────────────
    if entry_actions:
        body.append('<div class="card">')
        body.append('<h2>How to Enter in Zerodha</h2>')
        body.append('<ol style="font-size:13px;line-height:1.8">')
        body.append('<li>Open Zerodha Kite &rarr; search the symbol on NSE</li>')
        body.append('<li>Click <strong>BUY</strong> with product '
                    '<strong>CNC</strong> (delivery). Do NOT use MIS/F&amp;O.</li>')
        body.append('<li>Use the suggested quantity and limit price from the table below</li>')
        body.append('<li>Set a stop-loss GTT at the suggested stop price</li>')
        body.append('<li>After your order fills, come back here and click '
                    '<strong>Done</strong> &rarr; enter actual qty + price</li>')
        body.append('</ol>')
        body.append('<div class="muted" style="font-size:12px;margin-top:6px">'
                    'Tip: If you want to enter at next market open, place an '
                    'AMO (After Market Order) as BUY CNC with the limit price. '
                    'Zerodha will execute it at market open if the price is reached.'
                    '</div>')
        body.append('</div>')

    # ── Entry recommendations ──────────────────────────────────
    body.append('<div class="card">')
    body.append(f'<h2>Entry Recommendations ({len(entry_actions)})</h2>')

    if entry_actions:
        body.append('<table class="holdings">')
        body.append('<tr>'
                    '<th>#</th><th>Symbol</th><th>Setup</th>'
                    '<th class="right">Live Price</th>'
                    '<th class="right">Entry</th><th class="right">Stop</th>'
                    '<th class="right">Target</th>'
                    '<th class="right">Qty</th><th class="right">R:R</th>'
                    '<th>Reason</th>'
                    '<th>Actions</th>'
                    '</tr>')
        for a in entry_actions:
            lq = live.get(a.symbol, {})
            lprice = lq.get("price", a.live_price) or a.live_price
            chg = lq.get("change_pct", 0)
            chg_cls = "pos" if chg >= 0 else "neg"

            cand = candidates_by_symbol.get(a.symbol)
            setup = cand.setup_type if cand else "ENTRY"
            short_reason = ""
            if cand and cand.reasons:
                short_reason = cand.reasons[0]
                if len(cand.reasons) > 1:
                    short_reason += f" (+{len(cand.reasons)-1} more)"

            body.append(
                f'<tr>'
                f'<td>{a.priority_rank}</td>'
                f'<td><a href="/swing/{html.escape(a.symbol)}" '
                f'style="color:var(--fg);font-weight:600">'
                f'{html.escape(a.symbol)}</a></td>'
                f'<td><span style="font-size:11px">{html.escape(setup)}</span></td>'
                f'<td class="right"><span class="{chg_cls}">Rs.{lprice:,.2f}</span>'
                f' <span class="muted">({chg:+.1f}%)</span></td>'
                f'<td class="right">Rs.{a.suggested_price:,.2f}</td>'
                f'<td class="right">Rs.{a.suggested_stop:,.2f}</td>'
                f'<td class="right">Rs.{a.suggested_target:,.2f}</td>'
                f'<td class="right">{a.suggested_qty}</td>'
                f'<td class="right">{_rr(a):.1f}</td>'
                f'<td style="font-size:11px;max-width:200px">'
                f'{html.escape(short_reason)}</td>'
                f'<td>'
                f'<button class="action" onclick="confirmAction({a.action_id})" '
                f'style="padding:4px 8px;font-size:12px">Done</button> '
                f'<button class="action alt" onclick="skipAction({a.action_id})" '
                f'style="padding:4px 8px;font-size:12px">Skip</button>'
                f'</td>'
                f'</tr>'
            )
        body.append('</table>')
    else:
        if latest_run_row:
            body.append('<div class="muted">No new entry recommendations from the latest scan.</div>')
        else:
            body.append('<div class="muted">No scan run yet. '
                        'Click "Run Scan" to start.</div>')
    body.append('</div>')

    # ── Open swing book ────────────────────────────────────────
    body.append('<div class="card">')
    body.append(f'<h2>Open Swing Book ({len(positions)})</h2>')

    if positions:
        body.append('<table class="holdings">')
        body.append('<tr>'
                    '<th>Symbol</th><th class="right">Qty</th>'
                    '<th class="right">Entry</th>'
                    '<th class="right">Live Price</th>'
                    '<th class="right">P&amp;L</th>'
                    '<th class="right">Stop</th><th class="right">Target</th>'
                    '<th class="right">R</th><th>Action</th>'
                    '<th>Controls</th>'
                    '</tr>')
        for p in positions:
            lq = live.get(p.symbol, {})
            lprice = lq.get("price", 0) or p.entry_price
            upnl = (lprice - p.entry_price) * p.managed_qty
            risk_per = p.entry_price - p.stop_price
            r_mult = ((lprice - p.entry_price) / risk_per
                      if risk_per > 0 else 0)
            pnl_cls = "pos" if upnl >= 0 else "neg"

            body.append(
                f'<tr>'
                f'<td><strong>{html.escape(p.symbol)}</strong></td>'
                f'<td class="right">{p.managed_qty}</td>'
                f'<td class="right">Rs.{p.entry_price:,.2f}</td>'
                f'<td class="right">Rs.{lprice:,.2f}</td>'
                f'<td class="right"><span class="{pnl_cls}">'
                f'Rs.{upnl:+,.2f}</span></td>'
                f'<td class="right">Rs.{p.stop_price:,.2f}</td>'
                f'<td class="right">Rs.{p.target_price:,.2f}</td>'
                f'<td class="right">{r_mult:+.1f}R</td>'
                f'<td>{html.escape(p.daily_action)}</td>'
                f'<td>'
                f'<button class="action alt" '
                f'onclick="exitPosition({p.position_id})" '
                f'style="padding:4px 8px;font-size:12px">Mark Exit Done</button>'
                f'</td>'
                f'</tr>'
            )
        body.append('</table>')
    else:
        body.append('<div class="muted">No open swing positions. '
                    'Confirm an entry recommendation above to start tracking.</div>')
    body.append('</div>')

    body.append('</div>')  # .wrap

    # ── JavaScript ─────────────────────────────────────────────
    body.append(_js())

    return _wrap("Swing", body)


# ── Detail page for /swing/<symbol> ────────────────────────────

def render_swing_detail(symbol: str) -> str:
    """Render the per-stock swing detail page."""
    init_db()
    sym = symbol.strip().upper()
    cand = candidate_by_symbol(sym)

    body = []
    body.append(_topnav("/swing"))
    body.append('<div class="wrap">')
    body.append(f'<h1 class="page-title">{html.escape(sym)} — Swing Detail</h1>')
    body.append('<div class="sub"><a href="/swing">&larr; Back to Swing Dashboard</a></div>')

    if not cand:
        body.append('<div class="card"><p class="muted">No swing analysis '
                    f'found for {html.escape(sym)}.</p></div>')
        body.append('</div>')
        return _wrap(f"Swing — {sym}", body)

    # Live quote
    lq = get_live_quotes([sym])
    lprice = lq.get(sym, {}).get("price", cand.close_price) or cand.close_price
    chg = lq.get(sym, {}).get("change_pct", 0)

    # ── Summary card ────────────────────────────────────────────
    body.append('<div class="card">')
    body.append('<h2>Recommendation Summary</h2>')

    # Plain-English setup explanation
    setup_explain = {
        "BREAKOUT": "This stock is breaking above its recent price ceiling with strong trading activity — a sign that buyers are stepping in.",
        "PULLBACK_UPTREND": "This stock has been going up overall, but dipped temporarily to a good buy level — like a sale on a stock that's been rising.",
        "TREND_CONTINUATION": "This stock has been steadily rising across all timeframes — the trend is strong and continuing upward.",
        "SUPPORT_REVERSAL": "This stock bounced off a major support level where it historically finds buyers — early sign of a potential recovery.",
    }
    setup_text = setup_explain.get(cand.setup_type, "Technical setup detected.")
    body.append(f'<div style="font-size:14px;line-height:1.6;margin-bottom:14px">'
                f'<strong>Setup: {html.escape(cand.setup_type.replace("_", " ").title())}</strong>'
                f'<br>{setup_text}</div>')

    body.append('<table class="kvtable">')
    _kv = lambda k, v: f'<tr><td>{k}</td><td>{v}</td></tr>'
    body.append(_kv("Sector", cand.sector))
    pnl_cls = "pos" if chg >= 0 else "neg"
    body.append(_kv("Current Price",
                     f'<span class="{pnl_cls}">Rs.{lprice:,.2f} ({chg:+.1f}% today)</span>'))
    body.append(_kv("Suggested Buy Price", f'Rs.{cand.entry_price:,.2f}'))
    body.append(_kv("Stop Loss (exit if it falls to)",
                     f'Rs.{cand.stop_price:,.2f}'))
    body.append(_kv("Target (expected profit zone)",
                     f'Rs.{cand.target_price:,.2f}'))
    body.append(_kv("Risk vs Reward",
                     f'{cand.rr_ratio:.1f}x '
                     f'<span class="muted">(you risk Rs.{cand.risk_rupees:,.0f} '
                     f'to potentially make Rs.{cand.reward_rupees:,.0f})</span>'))
    body.append(_kv("How many to buy", f'{cand.suggested_qty} shares'))
    body.append(_kv("Confidence Score",
                     f'{cand.score:.1f} / 10 (rank #{cand.priority_rank} today)'))
    body.append('</table></div>')

    # ── Signal Reasons (why we recommend entry) ─────────────────
    body.append('<div class="card">')
    body.append('<h2>Why This Stock?</h2>')
    if cand.reasons:
        body.append('<ol style="font-size:13px;line-height:1.8">')
        for reason in cand.reasons:
            body.append(f'<li>{html.escape(reason)}</li>')
        body.append('</ol>')
    else:
        body.append('<p class="muted">No detailed reasons stored for this '
                    'candidate. Run a fresh scan to populate reasons.</p>')
    body.append('</div>')

    # ── Health Check (plain English) ────────────────────────────
    body.append('<div class="card">')
    body.append('<h2>Stock Health Check</h2>')
    body.append('<p class="muted" style="margin-bottom:12px">'
                'These are the checks we run on every stock before recommending it. '
                'More green checks = stronger recommendation.</p>')
    body.append('<table class="holdings" style="max-width:800px">')
    body.append('<tr><th>What We Checked</th><th>Result</th>'
                '<th style="width:40px"></th></tr>')

    checks = _build_checks(cand, lprice)
    for check_name, explanation, value, passed in checks:
        icon = ('<span class="pos" style="font-size:16px">&#10003;</span>'
                if passed else
                '<span class="neg" style="font-size:16px">&#10007;</span>')
        body.append(f'<tr>'
                    f'<td><strong>{html.escape(check_name)}</strong>'
                    f'<br><span class="muted" style="font-size:11px">'
                    f'{html.escape(explanation)}</span></td>'
                    f'<td>{html.escape(str(value))}</td>'
                    f'<td>{icon}</td></tr>')

    body.append('</table></div>')

    # ── AI Overlay (if available) ───────────────────────────────
    if cand.ai_overlay_json:
        body.append('<div class="card">')
        body.append('<h2>AI Analysis</h2>')
        try:
            import json as _j
            ai = _j.loads(cand.ai_overlay_json)
            raw = ai.get("raw_response", "")
            if raw:
                body.append(f'<div style="font-size:13px;line-height:1.7;'
                            f'white-space:pre-wrap">{html.escape(raw)}</div>')
            err = ai.get("error", "")
            if err:
                body.append(f'<div class="banner warn">AI error: '
                            f'{html.escape(err)}</div>')
        except Exception:
            body.append('<p class="muted">Could not parse AI overlay.</p>')
        body.append('</div>')
    else:
        body.append('<div class="card">')
        body.append('<h2>AI Analysis</h2>')
        body.append('<p class="muted">No AI analysis for this stock. '
                    'Run an AI swing scan from the dashboard to add '
                    'qualitative thesis, risks, and news context.</p>')
        body.append('</div>')

    # ── Rejected reason (if not accepted) ───────────────────────
    if cand.rejected_reason:
        body.append('<div class="card">')
        body.append('<h2>Rejection Reason</h2>')
        body.append(f'<p>{html.escape(cand.rejected_reason)}</p>')
        body.append('</div>')

    body.append('</div>')  # .wrap
    return _wrap(f"Swing — {sym}", body)


def _build_checks(cand, live_price: float) -> list[tuple[str, str, str, bool]]:
    """Build the stock health checklist for the detail page.
    Returns [(plain_name, explanation, value_str, passed_bool), ...]
    Every check is written so anyone can understand it."""
    c = cand
    checks: list[tuple[str, str, str, bool]] = []

    # Long-term trend
    above_sma200 = c.close_price > c.sma_200 if c.sma_200 > 0 else False
    checks.append((
        "Long-term trend (200-day)",
        "Is the stock above its 200-day average price? "
        "If yes, the long-term direction is up.",
        f"Price Rs.{c.close_price:,.2f} vs avg Rs.{c.sma_200:,.2f}",
        above_sma200,
    ))

    # Medium-term trend
    above_sma50 = c.close_price > c.sma_50 if c.sma_50 > 0 else False
    checks.append((
        "Medium-term trend (50-day)",
        "Is the stock above its 50-day average price? "
        "If yes, the medium-term direction is up.",
        f"Price Rs.{c.close_price:,.2f} vs avg Rs.{c.sma_50:,.2f}",
        above_sma50,
    ))

    # Short-term trend
    above_ema20 = c.close_price > c.ema_20 if c.ema_20 > 0 else False
    checks.append((
        "Short-term trend (20-day)",
        "Is the stock above its recent 20-day trend line? "
        "If yes, short-term momentum is positive.",
        f"Price Rs.{c.close_price:,.2f} vs trend Rs.{c.ema_20:,.2f}",
        above_ema20,
    ))

    # All trends aligned
    sma_stacked = (c.ema_20 > c.sma_50 > c.sma_200) if (c.sma_50 > 0 and c.sma_200 > 0) else False
    checks.append((
        "All trends aligned",
        "Are the short, medium, and long-term trends all pointing up? "
        "This is the strongest signal that the stock is in a clear uptrend.",
        "Yes — all aligned" if sma_stacked else "No — trends are mixed",
        sma_stacked,
    ))

    # Buying/selling pressure
    rsi_ok = 30 <= c.rsi_daily <= 70
    if c.rsi_daily > 70:
        rsi_desc = f"{c.rsi_daily:.0f} — may be overbought (too expensive right now)"
    elif c.rsi_daily < 30:
        rsi_desc = f"{c.rsi_daily:.0f} — oversold (might be a bargain, but risky)"
    else:
        rsi_desc = f"{c.rsi_daily:.0f} — healthy zone"
    checks.append((
        "Buying/selling pressure",
        "RSI measures if a stock is overbought (>70) or oversold (<30). "
        "The sweet spot for buying is between 30-70.",
        rsi_desc,
        rsi_ok,
    ))

    # Trading activity
    vol_ok = c.volume_ratio >= 1.0
    checks.append((
        "Trading activity",
        "Is more money flowing into this stock than usual? "
        "Higher volume means more traders agree with the move.",
        f"{c.volume_ratio:.1f}x normal volume",
        vol_ok,
    ))

    # Weekly direction
    checks.append((
        "Weekly trend direction",
        "Looking at the broader weekly picture, is the stock going up? "
        "This filters out short-term noise.",
        "Upward" if c.weekly_trend_up else "Downward or flat",
        c.weekly_trend_up,
    ))

    # Beating the market
    rs_ok = c.relative_strength > 0
    checks.append((
        "Beating the market?",
        "Is this stock performing better than NIFTY 50 over the last 60 days? "
        "Stocks outperforming the market tend to keep outperforming.",
        f"{c.relative_strength:+.1f}% vs NIFTY",
        rs_ok,
    ))

    # Risk-reward
    rr_ok = c.rr_ratio >= 2.0
    checks.append((
        "Risk vs reward ratio",
        "For every Rs.1 you risk, how much could you gain? "
        "We look for at least 2x reward for the risk taken.",
        f"{c.rr_ratio:.1f}x (risk Rs.{c.risk_rupees:,.0f}, potential Rs.{c.reward_rupees:,.0f})",
        rr_ok,
    ))

    # Stop-loss distance
    if c.atr_14 > 0:
        stop_atr = (c.entry_price - c.stop_price) / c.atr_14
        checks.append((
            "Stop-loss safety margin",
            "Is the stop far enough from normal daily price swings? "
            "Too tight = you get stopped out by noise. Too wide = too much risk.",
            f"{stop_atr:.1f}x daily swing range",
            1.0 <= stop_atr <= 3.0,
        ))

    # 52-week position
    if c.high_52w > 0:
        pct_from_high = ((c.close_price / c.high_52w) - 1) * 100
        if pct_from_high > -5:
            pos_desc = f"{pct_from_high:+.1f}% from the year's high — near the top"
        elif pct_from_high > -20:
            pos_desc = f"{pct_from_high:+.1f}% from the year's high — reasonable range"
        else:
            pos_desc = f"{pct_from_high:+.1f}% from the year's high — significantly below"
        checks.append((
            "Where in its yearly range?",
            "How far is the stock from its highest price this year? "
            "We avoid stocks that have dropped too far (>20%) unless it's a reversal setup.",
            pos_desc,
            pct_from_high > -20,
        ))

    # Extension check
    if c.ema_20 > 0:
        ext = ((c.close_price / c.ema_20) - 1) * 100
        checks.append((
            "Not too far from trend",
            "Is the stock extended too far above its trend? "
            "Chasing a stock that's already run up means buying at a worse price.",
            f"{ext:+.1f}% from trend line",
            abs(ext) < 8,
        ))

    # Setup-specific checks
    if c.setup_type == "BREAKOUT":
        checks.append((
            "Breaking recent price ceiling",
            "Has the stock just broken above its highest price in the last 20 days? "
            "This signals that buyers are pushing it to new highs.",
            f"Price Rs.{c.close_price:,.2f} vs 20-day high Rs.{c.high_20d:,.2f}",
            c.close_price > c.high_20d * 0.998,
        ))
        checks.append((
            "Strong volume on breakout",
            "A breakout with high volume (1.5x+ normal) means the move is backed by real demand, "
            "not just a few trades.",
            f"{c.volume_ratio:.1f}x normal",
            c.volume_ratio >= 1.5,
        ))

    if c.setup_type == "PULLBACK_UPTREND":
        dist_ema20 = abs(c.close_price / c.ema_20 - 1) * 100 if c.ema_20 > 0 else 99
        checks.append((
            "Pulled back to a good buy level",
            "The stock dipped close to its 20-day trend line — "
            "like buying on a temporary sale in an uptrend.",
            f"{dist_ema20:.1f}% from the trend line",
            dist_ema20 <= 5.0,
        ))
        checks.append((
            "Not oversold or overbought",
            "The buying pressure is in the sweet spot (40-60) — "
            "not too hot, not too cold, just right for a pullback buy.",
            f"Pressure reading: {c.rsi_daily:.0f}",
            40 <= c.rsi_daily <= 60,
        ))

    if c.setup_type == "SUPPORT_REVERSAL":
        dist_200 = abs(c.close_price / c.sma_200 - 1) * 100 if c.sma_200 > 0 else 99
        checks.append((
            "Near strong support level",
            "The stock is near its 200-day average — a level where "
            "buyers historically step in and push it back up.",
            f"{dist_200:.1f}% from support",
            dist_200 <= 5.0,
        ))

    return checks


# ── Data API for AJAX ──────────────────────────────────────────

def render_swing_data_json() -> str:
    """JSON payload for /api/swing/data."""
    init_db()
    today = now_ist().date().isoformat()
    latest_run_row = latest_run()
    positions = open_positions()
    pnl = realised_pnl_summary()

    entry_actions: list[dict] = []
    if latest_run_row:
        run_actions = actions_for_run(int(latest_run_row["run_id"]))
        for a in run_actions:
            if a.action_type == "ENTRY" and a.status == "PENDING":
                entry_actions.append(a.to_dict())
        entry_actions.sort(key=lambda d: d.get("priority_rank", 999))

    return json.dumps({
        "date": today,
        "latest_run": latest_run_row,
        "pnl": pnl,
        "entry_actions": entry_actions,
        "positions": [p.to_dict() for p in positions],
        "job": _job_dict(),
    }, default=str)


def render_swing_status_json() -> str:
    """JSON for /api/swing/run_status."""
    return json.dumps(_job_dict(), default=str)


# ── Helpers ─────────────────────────────────────────────────────

def _job_dict() -> dict:
    job = latest_swing_status()
    if not job:
        return {"status": "NONE"}
    return {
        "job_id": job.job_id,
        "mode": job.mode,
        "status": job.status,
        "error": job.error,
        "db_run_id": job.db_run_id,
    }


def _rr(a: SwingAction) -> float:
    risk = a.suggested_price - a.suggested_stop
    reward = a.suggested_target - a.suggested_price
    return (reward / risk) if risk > 0 else 0.0


def _is_market_open() -> bool:
    n = now_ist()
    # Weekends
    if n.weekday() >= 5:
        return False
    market_open = n.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = n.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= n < market_close


def _wrap(title: str, body_parts: list[str]) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — AI Portfolio Manager</title>
<style>{_STYLE}</style>
</head><body>
{"".join(body_parts)}
</body></html>"""


def _js() -> str:
    return """<script>
function _swingBanner(msg, kind) {
    var host = document.getElementById('swing-job-banner');
    if (!host) return;
    var spin = (kind === 'info') ? '<span class="spinner"></span> ' : '';
    host.innerHTML = '<div class="banner ' + kind + '">' + spin + msg + '</div>';
}

function _swingDisableButtons(disabled) {
    document.querySelectorAll('button.action').forEach(function(b) {
        b.disabled = disabled;
    });
}

function runSwingScan() {
    var aiToggle = document.getElementById('swing-ai-toggle');
    var mode = (aiToggle && aiToggle.checked) ? 'AI' : 'NOAI';

    // If a run already exists today, ask before rerunning
    if (window._swingHasRunToday) {
        var lastMode = window._swingLastMode || 'NoAI';
        if (mode === 'AI' && lastMode === 'NOAI') {
            if (!confirm('Today\\'s scan was NoAI. Run again with AI overlay?\\n' +
                         'This will add qualitative analysis on top of the existing scan.')) {
                return;
            }
        } else {
            if (!confirm('A ' + lastMode + ' swing scan already ran today.\\n' +
                         'Rerun the analysis? (e.g. after code improvements)')) {
                return;
            }
        }
    }

    // Read capital from input
    var capitalEl = document.getElementById('swing-capital');
    var capital = capitalEl ? parseFloat(capitalEl.value.replace(/,/g, '')) : 0;

    // Show loading immediately
    _swingBanner('Starting swing scan (' + mode + ')\\u2026 this can take 2-5 minutes for NIFTY 100.', 'info');
    _swingDisableButtons(true);

    fetch('/api/swing/run?mode=' + mode + '&capital=' + capital, {method: 'POST'})
        .then(r => r.json())
        .then(d => {
            if (d.status === 'RUNNING') {
                _swingBanner('Swing scan running (job #' + d.job_id + ', ' + d.mode + ')\\u2026', 'info');
                _pollSwingStatus();
            } else {
                _swingBanner('Scan submitted.', 'info');
                _pollSwingStatus();
            }
        })
        .catch(e => {
            _swingBanner('Error: ' + e, 'warn');
            _swingDisableButtons(false);
        });
}

function _pollSwingStatus() {
    setTimeout(function() {
        fetch('/api/swing/run_status')
            .then(r => r.json())
            .then(d => {
                if (d.status === 'RUNNING') {
                    _swingBanner('Swing scan running (job #' + d.job_id + ', ' + d.mode + ')\\u2026', 'info');
                    _pollSwingStatus();
                } else if (d.status === 'DONE') {
                    if (d.error) {
                        _swingBanner('Scan completed with note: ' + d.error, 'warn');
                        setTimeout(function() { location.reload(); }, 2000);
                    } else {
                        _swingBanner('Scan complete \\u2014 refreshing page\\u2026', 'info');
                        setTimeout(function() { location.reload(); }, 1200);
                    }
                } else if (d.status === 'FAILED') {
                    _swingBanner('Scan FAILED: ' + (d.error || 'unknown error'), 'warn');
                    _swingDisableButtons(false);
                } else {
                    _swingDisableButtons(false);
                }
            })
            .catch(function() { _pollSwingStatus(); });
    }, 2000);
}

// On page load: if a job is already running, show the banner + poll
window.addEventListener('DOMContentLoaded', function() {
    fetch('/api/swing/run_status')
        .then(r => r.json())
        .then(d => {
            if (d && d.status === 'RUNNING') {
                _swingBanner('Swing scan already running (job #' + d.job_id + ')\\u2026', 'info');
                _swingDisableButtons(true);
                _pollSwingStatus();
            }
        });
});

function confirmAction(actionId) {
    const qty = prompt('Executed quantity:');
    if (!qty) return;
    const price = prompt('Executed price (Rs.):');
    if (!price) return;
    fetch('/api/swing/actions/' + actionId + '/confirm', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({qty: parseInt(qty), price: parseFloat(price)})
    }).then(() => location.reload());
}

function skipAction(actionId) {
    const reason = prompt('Reason for skipping (optional):') || '';
    fetch('/api/swing/actions/' + actionId + '/skip', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({reason: reason})
    }).then(() => location.reload());
}

function exitPosition(posId) {
    const qty = prompt('Exit quantity:');
    if (!qty) return;
    const price = prompt('Exit price (Rs.):');
    if (!price) return;
    fetch('/api/swing/positions/' + posId + '/exit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({qty: parseInt(qty), price: parseFloat(price)})
    }).then(() => location.reload());
}
</script>"""


_STYLE = r"""
:root { --bg: #fafbfc; --fg: #1c1f23; --muted: #6a7280;
        --card: #ffffff; --line: #e5e7eb;
        --accent: #1c1f23; --pos: #1b8e3a; --neg: #c62828;
        --warn-bg: #fff4e0; --warn-fg: #b06a00; --warn-line: #f0d28a; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
       background: var(--bg); color: var(--fg); margin: 0; padding: 24px; }
.wrap { max-width: 1180px; margin: 0 auto; }
h1.page-title { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em;
     color: var(--muted); margin: 28px 0 8px; font-weight: 600; }
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
nav.topnav .auth.bad { background: #fdecec; color: var(--neg);
                       border: 1px solid #f4c0c0; }
table.holdings { width: 100%; border-collapse: collapse; font-size: 13px;
                 font-variant-numeric: tabular-nums; }
table.holdings th { text-align: left; padding: 6px 10px;
                    border-bottom: 2px solid var(--line);
                    color: var(--muted); font-weight: 600; font-size: 11px;
                    text-transform: uppercase; letter-spacing: 0.04em; }
table.holdings td { padding: 6px 10px; border-bottom: 1px solid var(--line); }
table.holdings tr:hover td { background: #f7f8fa; }
table.holdings .right { text-align: right; }
.pos { color: var(--pos); font-weight: 600; }
.neg { color: var(--neg); font-weight: 600; }
.kvtable { width: 100%; border-collapse: collapse; font-size: 14px;
           font-variant-numeric: tabular-nums; }
.kvtable td { padding: 5px 0; border-bottom: 1px dashed var(--line); }
.kvtable td:last-child { text-align: right; font-weight: 500; }
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
.spinner { display: inline-block; width: 14px; height: 14px;
           border: 2px solid #cfd9eb; border-top-color: var(--accent);
           border-radius: 50%; animation: spin 0.8s linear infinite;
           vertical-align: middle; margin-right: 6px; }
@keyframes spin { to { transform: rotate(360deg); } }
footer { color: var(--muted); font-size: 12px; margin-top: 32px; text-align: center; }
.ai-toggle { display: inline-flex; align-items: center; gap: 8px;
             padding: 6px 12px; background: var(--card);
             border: 1px solid var(--line); border-radius: 999px;
             font-size: 13px; cursor: pointer; user-select: none; }
.ai-toggle input { margin: 0; cursor: pointer; }
.ai-toggle .lbl { font-weight: 500; }
.ai-toggle .hint { color: var(--muted); font-size: 11px; }
"""
