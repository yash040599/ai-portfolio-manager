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
    candidate_by_symbol, dip_candidate_by_symbol, candidates_for_run,
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
    # Even when the token file looks fresh, a recent auth-shaped
    # external error (recorded via `core.error_sink`) means the
    # broker rejected the token — flip the pill so the user has a
    # clear next step. Origin: 2026-05-14 user reported "Funds
    # fetch failed (Incorrect api_key or access_token); using
    # default Rs.1,00,000" while pill still showed Auth: OK.
    if valid:
        try:
            from core.error_sink import has_auth_invalid
            if has_auth_invalid():
                valid = False
        except Exception:
            pass
    if valid:
        return ('<a class="auth ok" href="/login" title="Token valid for today">'
                'Auth: <strong>OK</strong></a>')
    return ('<a class="auth bad" href="/login" '
            'title="Re-login required (token rejected by Zerodha)">'
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

    # Fetch Zerodha available funds for the capital default.
    # Origin: 2026-05-14 user reported "swing capital is shown as 1L,
    # why is it not fetching live data from zerodha?".
    # Root cause was the bare `except Exception: pass` swallowing the
    # real reason silently — the user sees "1L" with no idea why.
    # New path captures the failure into `capital_source_note` so it
    # surfaces in the muted hint below the input box, AND avoids the
    # `login(interactive=False)` browser-fallback trap (the Zerodha
    # client's `interactive=False` only suppresses input(), it still
    # falls through to `_login_browser()` when the saved token is
    # invalid — a real bug for a server-side render path).
    default_capital = 100_000.0
    capital_source_note = ""
    try:
        token_path = os.path.join("data", "access_token.json")
        if not os.path.exists(token_path):
            capital_source_note = (
                "Zerodha not logged in — using default Rs.1,00,000. "
                "Open the Login page (Auth pill above) to fetch live funds."
            )
        else:
            import json as _j
            with open(token_path, encoding="utf-8") as f:
                saved = _j.load(f)
            if saved.get("date") != str(now_ist().date()):
                capital_source_note = (
                    "Zerodha token expired — using default Rs.1,00,000. "
                    "Re-login (Auth pill above) to fetch live funds."
                )
            else:
                # Token is fresh — fetch margins.
                from core.zerodha_client import ZerodhaClient
                from core.logger import Logger as _Log
                _z = ZerodhaClient(Config, _Log("SwingPageFunds"))
                # We have a valid same-day token, so this should NOT
                # hit the browser fallback; pass the saved token
                # directly via the kite client to be safe.
                try:
                    from kiteconnect import KiteConnect as _KC
                    _z._kite = _KC(api_key=Config.ZERODHA_API_KEY)
                    _z._kite.set_access_token(saved["token"])
                except Exception as login_exc:
                    capital_source_note = (
                        f"Zerodha client init failed ({login_exc}); "
                        f"using default Rs.1,00,000."
                    )
                    # Surface the failure as a top-right toast too.
                    from core.error_sink import record_external_error
                    record_external_error("zerodha", login_exc)
                else:
                    try:
                        default_capital = _z.get_available_funds()
                        capital_source_note = (
                            f"Live from Zerodha (Rs.{default_capital:,.0f} "
                            "available margin)"
                        )
                    except Exception as funds_exc:
                        capital_source_note = (
                            f"Funds fetch failed ({funds_exc}); "
                            f"using default Rs.1,00,000."
                        )
                        # Auth-shaped errors here ("Incorrect `api_key`
                        # or `access_token`") will additionally invalidate
                        # the saved token file inside `record_external_error`,
                        # so the auth pill flips to "Re-login" on the
                        # next render and the user knows exactly what
                        # to do.
                        from core.error_sink import record_external_error
                        record_external_error("zerodha", funds_exc)
    except Exception as outer_exc:
        capital_source_note = (
            f"Capital lookup failed ({outer_exc}); using default Rs.1,00,000."
        )
        from core.error_sink import record_external_error
        record_external_error("zerodha", outer_exc)

    # Get pending entry actions (priority sorted) + candidates for reasons
    entry_actions: list[SwingAction] = []
    run_actions: list[SwingAction] = []
    candidates_by_symbol: dict[str, Any] = {}
    if latest_run_row:
        run_actions = actions_for_run(int(latest_run_row["run_id"]))
        entry_actions = [a for a in run_actions
                         if a.action_type == "ENTRY" and a.status == "PENDING"]
        entry_actions.sort(key=lambda a: a.priority_rank or 999)
        # Load candidates to populate per-symbol context (setup_type +
        # reasons + ath_price + dip_from_ath_pct).
        # Resolution rule for duplicate symbols (when both scanners
        # produced a row): prefer ACCEPTED > REJECTED, then prefer the
        # ATH-dip record for ATH context, otherwise keep the first.
        # This guards the "% Below 52w High" column from flipping based on
        # DB insertion order; the technical and dip-buy scanners now
        # share the same lookback window so the values agree, but
        # this resolution still gives a deterministic winner.
        for c in candidates_for_run(int(latest_run_row["run_id"])):
            existing = candidates_by_symbol.get(c.symbol)
            if existing is None:
                candidates_by_symbol[c.symbol] = c
                continue
            # Prefer ACCEPTED over anything else.
            if existing.status != "ACCEPTED" and c.status == "ACCEPTED":
                candidates_by_symbol[c.symbol] = c
                continue
            # If both have the same status, prefer the technical setup
            # (more diagnostic detail in `reasons`) but copy ath_price
            # / dip_from_ath_pct from the dip-buy record. Always copy
            # (S42 hardening, 2026-05-14): the prior `if not getattr(c,
            # "ath_price", 0)` guard meant the technical row's
            # ath_price (computed from a different lookback) silently
            # won, which surfaced as a wrong "% Below 52w High" cell
            # for any symbol with both row types in the same run.
            # The dip-buy scanner's reference is the canonical one
            # because it was the value used to qualify the dip rule.
            # Both legacy 'ATH_DIP' and current '52W_DIP' are treated
            # as dip-buy rows here.
            if existing.status == c.status:
                _DIP_TYPES = {"ATH_DIP", "52W_DIP"}
                if c.setup_type not in _DIP_TYPES and existing.setup_type in _DIP_TYPES:
                    # Keep dip-buy context, swap to technical row.
                    c.ath_price = existing.ath_price
                    c.dip_from_ath_pct = existing.dip_from_ath_pct
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
    # The source-note carries either "Live from Zerodha (Rs.X)"
    # (success) or a precise reason for the 1L fallback so the user
    # never has to guess why the field defaulted.
    note_color = ("#1b8e3a"
                  if capital_source_note.startswith("Live from")
                  else "#b06a00")
    body.append(
        f'<span style="margin-left:8px;font-size:12px;color:{note_color}">'
        f'{html.escape(capital_source_note)}</span>'
    )
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

    # ── AI cost-of-run preview ─────────────────────────────────
    # Origin (2026-05-14): user ran AI mode and it consumed credits
    # for several minutes before being Ctrl+C'd. The dashboard now
    # shows the worst-case cost and the per-run cap up-front so
    # there's no surprise.
    per_call = float(getattr(Config, "CLAUDE_COST_PER_CALL", 3.0))
    ai_cap = int(getattr(Config, "SWING_AI_MAX_CANDIDATES", 15))
    capped_cost = ai_cap * per_call
    # Reasonable worst-case for a NIFTY 100 scan that triggers many
    # ATH-dips after a market correction — ~50 accepted candidates.
    worst_case_n = 50
    worst_case_cost = worst_case_n * per_call
    body.append(
        '<div class="muted" style="font-size:12px;margin-top:8px;'
        'border-left:3px solid #e0a800;padding-left:8px">'
        f'<strong>AI cost preview</strong> (Rs.{per_call:.0f}/stock at the '
        f'<code>{getattr(Config, "CLAUDE_PLAN", "pro").upper()}</code> plan):<br>'
        f'&bull; <strong>Single stock</strong> &mdash; click "Analyse this stock" '
        f'on a swing detail page: <strong>~Rs.{per_call:.0f}</strong> per call.<br>'
        f'&bull; <strong>Full scan with AI overlay</strong>: capped at '
        f'<strong>Rs.{capped_cost:.0f}</strong> '
        f'({ai_cap} top-priority candidates) via '
        f'<code>SWING_AI_MAX_CANDIDATES</code>. Without this cap a wide '
        f'NIFTY 100 scan could process up to ~{worst_case_n} candidates and '
        f'cost ~Rs.{worst_case_cost:.0f}.<br>'
        f'&bull; A confirm dialog appears before the AI scan starts; if '
        f'you Ctrl+C mid-scan the pre-AI snapshot is still saved so the '
        f'report and dashboard table never end up empty.'
        '</div>'
    )

    # Embed cap + per-call into the page so the JS confirm dialog
    # can echo the same numbers without a second round-trip.
    body.append(
        f'<script>window._swingAiPerCall={per_call:.2f};'
        f'window._swingAiCap={ai_cap};</script>'
    )

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

    # ── Single-stock search box (S38) ───────────────────────────
    # Origin: 2026-05-14 user request — "we should have a text field
    # which takes the ticker name of the indian stock and then
    # analyse just that and give details about it below". Lets the
    # user evaluate any NSE name on demand without re-running the
    # full universe scan; result card supports the same Done / Skip
    # controls and (optionally) the per-stock AI overlay.
    per_call_one = float(getattr(Config, "CLAUDE_COST_PER_CALL", 3.0))
    body.append('<div class="card">')
    body.append('<h2>Analyse a Single Stock</h2>')
    body.append(
        '<p class="muted" style="margin-bottom:10px">'
        'Type any NSE symbol (e.g. SBIN, RELIANCE, TCS) and click '
        '<em>Analyse</em>. The full per-stock pipeline runs on '
        f'just that name, then surfaces the result below with the '
        f'same Done / Skip controls. Tick the AI box to add Claude '
        f'colour (~Rs.{per_call_one:.0f} per call).'
        '</p>'
    )
    body.append(
        '<div style="display:flex;gap:8px;align-items:center;'
        'flex-wrap:wrap;margin-bottom:10px">'
        '<input type="text" id="single-symbol" '
        'placeholder="e.g. SBIN" '
        'style="width:180px;padding:6px 10px;font:inherit;'
        'border:1px solid #cfd9eb;border-radius:5px;text-transform:uppercase" '
        'onkeydown="if(event.key===\'Enter\'){analyseOne();}" />'
        '<button class="action" onclick="analyseOne()">Analyse</button>'
        '<label class="ai-toggle" '
        'title="Add Claude qualitative overlay (~Rs.' + f'{per_call_one:.0f}'
        ') for this single stock">'
        '<input type="checkbox" id="single-ai-toggle">'
        '<span class="lbl">Use Claude AI overlay</span>'
        '</label>'
        '</div>'
    )
    body.append('<div id="single-result-host"></div>')
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

    # ── Unified entry recommendations (technical + dip-buy) ─────
    # Single table — earlier UI split these into two cards but the
    # user asked (2026-05-14) to fold the dip-buy context into the
    # main row as another column ("% Below 52w High") instead of a
    # separate card. The manager's `priority_rank` is already unified
    # across both scanners (technical first, then dip-buy), so we
    # just sort.
    sorted_entries = sorted(
        entry_actions,
        key=lambda a: a.priority_rank if a.priority_rank else 999,
    )

    body.append('<div class="card">')
    body.append(f'<h2>Entry Recommendations ({len(sorted_entries)})</h2>')

    # Sweet-spot calibration banner — sourced from the standalone
    # NIFTY 50 dip-buy backtest in the `market-research` repo.
    dip_pct = float(getattr(Config, "SWING_DIP_PCT", 18.0))
    dip_target = float(getattr(Config, "SWING_DIP_TARGET_PCT", 12.0))
    dip_amount = float(getattr(Config, "SWING_DIP_BUY_AMOUNT", 10000.0))
    dip_lookback = int(getattr(Config, "SWING_DIP_LOOKBACK_DAYS", 252))
    body.append(
        '<p class="muted" style="margin-bottom:10px">'
        'Combined view: technical setups (breakouts / pullbacks / '
        'trend continuations / support reversals) <strong>and</strong> '
        f'dip-buys (currently {dip_pct:.0f}%+ below the rolling '
        f'{dip_lookback}-day high ≈ 52 weeks, target +{dip_target:.0f}%, '
        f'Rs.{dip_amount:,.0f} ticket — calibrated from the 10y 121-combo '
        f'backtest sweet-spot at X=18-20% / Y=10-13%). The "% Below 52w '
        f'High" column is computed for every candidate so the strongest '
        f'dips surface even when the row comes from a technical setup. '
        f'Setups also receive a bonus / penalty based on 52w-high '
        f'proximity (continuation setups benefit, mean-reversion '
        f'setups are penalised when too close).</p>'
    )

    if sorted_entries:
        body.append(_render_action_table(
            sorted_entries, live, candidates_by_symbol,
            show_setup_as="Setup",
        ))
    else:
        if latest_run_row:
            body.append('<div class="muted">'
                        'No entry recommendations from the latest scan. '
                        'Try widening the universe (CLI: --nifty 200) or '
                        f'lowering Config.SWING_DIP_PCT (currently '
                        f'{dip_pct:.0f}%).'
                        '</div>')
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

            # Mark price-bearing cells for the JS poller (data-live-*).
            # The poller reads `data-live-symbol` to build the request,
            # then writes the new `price` / `pnl` back into matching
            # cells. Entry / stop / target / qty are static so they
            # have no markers — they never change between scans.
            entry_price_str = f"{p.entry_price}"
            qty_str = f"{p.managed_qty}"
            body.append(
                f'<tr data-live-symbol="{html.escape(p.symbol)}" '
                f'data-entry-price="{entry_price_str}" '
                f'data-managed-qty="{qty_str}">'
                f'<td><strong>{html.escape(p.symbol)}</strong></td>'
                f'<td class="right">{p.managed_qty}</td>'
                f'<td class="right">Rs.{p.entry_price:,.2f}</td>'
                f'<td class="right" data-live-field="price">'
                f'Rs.{lprice:,.2f}</td>'
                f'<td class="right" data-live-field="pnl">'
                f'<span class="{pnl_cls}">Rs.{upnl:+,.2f}</span></td>'
                f'<td class="right">Rs.{p.stop_price:,.2f}</td>'
                f'<td class="right">Rs.{p.target_price:,.2f}</td>'
                f'<td class="right" data-live-field="r_mult">{r_mult:+.1f}R</td>'
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


def _render_action_table(actions: list, live: dict,
                         candidates_by_symbol: dict,
                         show_setup_as: str = "Setup") -> str:
    """Render the entry recommendations table.

    Single unified table used by both the technical and ATH-dip
    pipelines. Adds a "% Below 52w High" column populated from the
    candidate's `dip_from_ath_pct` (despite the legacy field name,
    the value held since 2026-05-14 is the dip from the rolling
    52-week high, not the all-time high).
    """
    parts: list[str] = []
    parts.append('<table class="holdings">')
    parts.append('<tr>'
                 '<th>#</th><th>Symbol</th><th>' + html.escape(show_setup_as) + '</th>'
                 '<th class="right">% Below 52w High</th>'
                 '<th class="right">Live Price</th>'
                 '<th class="right">Entry</th><th class="right">Stop</th>'
                 '<th class="right">Target</th>'
                 '<th class="right">Qty</th><th class="right">R:R</th>'
                 '<th>Reason</th>'
                 '<th>Actions</th>'
                 '</tr>')
    for a in actions:
        lq = live.get(a.symbol, {})
        lprice = lq.get("price", a.live_price) or a.live_price
        chg = lq.get("change_pct", 0)
        chg_cls = "pos" if chg >= 0 else "neg"

        cand = candidates_by_symbol.get(a.symbol)
        setup = cand.setup_type.replace("_", " ").title() if cand else "Entry"
        short_reason = ""
        if cand and cand.reasons:
            short_reason = cand.reasons[0]
            if len(cand.reasons) > 1:
                short_reason += f" (+{len(cand.reasons)-1} more)"

        # 52w dip context — populated for every candidate (technical
        # scanner sets it from its own candle history, ATH scanner
        # from its longer lookback). Bigger dip = more saturated
        # red text so the eye lands on real crash-class names.
        dip_pct = float(getattr(cand, "dip_from_ath_pct", 0.0)) if cand else 0.0
        ath_price = float(getattr(cand, "ath_price", 0.0)) if cand else 0.0
        if ath_price <= 0:
            dip_cell = '<span class="muted">n/a</span>'
        elif dip_pct >= 18:
            dip_cell = (f'<span class="neg" title="ATH Rs.{ath_price:,.2f}" '
                        f'style="font-weight:600">{dip_pct:.1f}%</span>')
        elif dip_pct >= 10:
            dip_cell = f'<span title="ATH Rs.{ath_price:,.2f}">{dip_pct:.1f}%</span>'
        else:
            dip_cell = (f'<span class="muted" title="ATH Rs.{ath_price:,.2f}">'
                        f'{dip_pct:.1f}%</span>')

        parts.append(
            f'<tr data-live-symbol="{html.escape(a.symbol)}">'
            f'<td>{a.priority_rank}</td>'
            f'<td><a href="/swing/{html.escape(a.symbol)}" '
            f'style="color:var(--fg);font-weight:600">'
            f'{html.escape(a.symbol)}</a></td>'
            f'<td><span style="font-size:11px">{html.escape(setup)}</span></td>'
            f'<td class="right">{dip_cell}</td>'
            f'<td class="right" data-live-field="price_with_change">'
            f'<span class="{chg_cls}">Rs.{lprice:,.2f}</span>'
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
    parts.append('</table>')
    return "\n".join(parts)


# ── Detail page for /swing/<symbol> ────────────────────────────

def render_swing_detail(symbol: str) -> str:
    """Render the per-stock swing detail page."""
    init_db()
    sym = symbol.strip().upper()
    cand = candidate_by_symbol(sym)          # prefers technical candidate
    dip_cand = dip_candidate_by_symbol(sym)   # dip-buy candidate if exists

    body = []
    body.append(_topnav("/swing"))
    body.append('<div class="wrap">')
    body.append(f'<h1 class="page-title">{html.escape(sym)} — Swing Detail</h1>')
    body.append('<div class="sub"><a href="/swing">&larr; Back to Swing Dashboard</a></div>')

    if not cand and not dip_cand:
        body.append('<div class="card"><p class="muted">No swing analysis '
                    f'found for {html.escape(sym)}.</p></div>')
        body.append('</div>')
        return _wrap(f"Swing — {sym}", body)

    # Use the best available candidate for the main display
    # (technical has richer data; fall back to ATH if no technical)
    if not cand:
        cand = dip_cand

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
        "52W_DIP": "This stock has fallen significantly from its 52-week high. The strategy is to buy the dip and sell when it recovers by a fixed percentage — works best with quality stocks that tend to bounce back.",
        "ATH_DIP": "Legacy dip-buy entry (now superseded by 52W_DIP). The strategy is to buy the dip and sell when it recovers by a fixed percentage — works best with quality stocks that tend to bounce back.",
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
    # 52-week high context — useful regardless of setup type so the
    # detail page mirrors the unified table column. Despite the
    # legacy "ath_*" field name the value held since S22 is the
    # rolling 52-week-high reference.
    _ath_p = float(getattr(cand, "ath_price", 0.0) or 0.0)
    _dip_p = float(getattr(cand, "dip_from_ath_pct", 0.0) or 0.0)
    if _ath_p > 0:
        if _dip_p >= 18:
            dip_html = (f'<strong>{_dip_p:.1f}% below</strong> 52w high '
                        f'(Rs.{_ath_p:,.2f}) — qualifies as a 52w-dip buy')
        elif _dip_p >= 10:
            dip_html = (f'{_dip_p:.1f}% below 52w high '
                        f'(Rs.{_ath_p:,.2f})')
        elif _dip_p >= 0:
            dip_html = (f'<span class="muted">{_dip_p:.1f}% below '
                        f'52w high (Rs.{_ath_p:,.2f})</span>')
        else:
            dip_html = (f'<span class="muted">at fresh 52w high '
                        f'(Rs.{_ath_p:,.2f})</span>')
        body.append(_kv("% Below 52w High", dip_html))
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

    # ── Dip-Buy Info (52-week-high reference; legacy ATH name) ─
    if dip_cand and dip_cand.status == "ACCEPTED":
        # Distinguish legacy ATH_DIP rows (pre-2026-05-14) from
        # current 52W_DIP rows in the heading so historical positions
        # being re-reviewed are accurately labelled.
        ref_label = ("All-Time High" if dip_cand.setup_type == "ATH_DIP"
                     else "52-Week High")
        body.append('<div class="card">')
        body.append(f'<h2>Dip-Buy Signal ({html.escape(ref_label)} reference)</h2>')
        body.append('<p style="font-size:13px;line-height:1.7">')
        for r in (dip_cand.reasons or []):
            body.append(f'{html.escape(r)}<br>')
        if not dip_cand.reasons:
            body.append(f'Stock is currently below its {ref_label.lower()}. '
                        f'Score: {dip_cand.score:.1f}% dip.')
        body.append('</p>')
        body.append('<table class="kvtable" style="max-width:400px">')
        _kv2 = lambda k, v: f'<tr><td>{k}</td><td>{v}</td></tr>'
        body.append(_kv2("Dip-Buy Entry", f'Rs.{dip_cand.entry_price:,.2f}'))
        body.append(_kv2("Dip-Buy Stop", f'Rs.{dip_cand.stop_price:,.2f}'))
        body.append(_kv2("Dip-Buy Target", f'Rs.{dip_cand.target_price:,.2f}'))
        body.append(_kv2("Dip-Buy Qty", str(dip_cand.suggested_qty)))
        body.append('</table></div>')

    # ── AI Overlay (if available) ───────────────────────────────
    # Per-stock AI analyse button (S37) — costs one Claude call
    # (~Rs.{CLAUDE_COST_PER_CALL}). Sits ABOVE the AI section so the
    # user can populate or refresh just this one symbol without
    # paying for the full top-K cap.
    per_call = float(getattr(Config, "CLAUDE_COST_PER_CALL", 3.0))
    body.append('<div class="card">')
    body.append('<h2>AI Analysis</h2>')
    body.append(
        '<div style="display:flex;gap:8px;align-items:center;'
        'margin-bottom:10px;flex-wrap:wrap">'
        f'<button class="action" id="ai-analyse-btn" '
        f'onclick="aiAnalyseSingle(\'{html.escape(sym)}\')" '
        f'style="padding:6px 12px;font-size:13px">'
        f'Analyse with AI (~Rs.{per_call:.0f})</button>'
        f'<span class="muted" style="font-size:12px">'
        f'One Claude call for this stock only. Replaces the existing '
        f'AI analysis below if any.</span>'
        '</div>'
    )
    body.append('<div id="ai-overlay-host">')
    if cand.ai_overlay_json:
        # Render the saved AI text + a freshness badge so the user
        # knows whether they're looking at fresh analysis or a
        # carry-forward from an earlier scan / detail-page click.
        # The badge ts comes from the parent run's finished_at via
        # the same persistence helper the manager uses for carry-
        # forward, so the values agree across surfaces.
        ai_ts = ""
        try:
            from modes.swing.persistence import latest_ai_overlay_for_symbol
            cached = latest_ai_overlay_for_symbol(sym, max_age_days=365)
            if cached:
                ai_ts = cached[1]
        except Exception:
            ai_ts = ""
        if ai_ts:
            # Pretty age string ("Analysed 3 days ago" / "today" / "1 day ago")
            try:
                import datetime as _dt
                _t = _dt.datetime.fromisoformat(ai_ts.split(".")[0])
                _age = (_dt.datetime.utcnow() - _t).days
                age_str = ("today" if _age <= 0
                           else "yesterday" if _age == 1
                           else f"{_age} days ago")
            except Exception:
                age_str = ai_ts[:10]
            body.append(
                f'<div class="muted" style="font-size:11px;margin-bottom:6px">'
                f'Analysed <strong>{html.escape(age_str)}</strong> '
                f'<span style="opacity:0.7">({html.escape(ai_ts[:16])} UTC)</span>. '
                f'Click <em>Analyse with AI</em> above to refresh.'
                f'</div>'
            )
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
    else:
        body.append('<p class="muted">No AI analysis for this stock yet. '
                    'Click <em>Analyse with AI</em> above to add '
                    'qualitative thesis, risks, and news context — '
                    'or run a full AI swing scan from /swing.</p>')
    body.append('</div>')   # /ai-overlay-host
    body.append('</div>')   # /card

    # JS for the per-stock AI button. Idempotent (guarded by
    # `_aiAnalyseInstalled`) so multiple swing detail pages in one
    # session don't double-bind the handler.
    body.append('''<script>
(function () {
    if (window._aiAnalyseInstalled) return;
    window._aiAnalyseInstalled = true;
    window.aiAnalyseSingle = function (sym) {
        var btn = document.getElementById('ai-analyse-btn');
        var host = document.getElementById('ai-overlay-host');
        if (!btn || !host) return;
        var perCall = ''' + f'{per_call:.0f}' + ''';
        if (!confirm('Spend ~Rs.' + perCall + ' on a Claude call for ' +
                     sym + '?\\nThis runs the swing AI overlay on ' +
                     'this one stock and replaces any existing AI ' +
                     'analysis below.')) {
            return;
        }
        btn.disabled = true;
        var origText = btn.textContent;
        btn.textContent = 'Analysing...';
        host.innerHTML =
            '<p class="muted"><span class="spinner"></span> ' +
            'Calling Claude (typically 5-15 s)...</p>';
        fetch('/api/swing/ai_analyse/' + encodeURIComponent(sym),
              {method: 'POST'})
            .then(function (r) {
                return r.json().then(function (j) {
                    return {ok: r.ok, body: j};
                });
            })
            .then(function (res) {
                btn.disabled = false;
                btn.textContent = origText;
                if (!res.ok || !res.body.ok) {
                    var msg = (res.body && res.body.error) ||
                              'unknown error';
                    host.innerHTML =
                        '<div class="banner warn">AI analyse failed: '
                        + msg + '</div>';
                    return;
                }
                var raw = (res.body.overlay && res.body.overlay.raw_response)
                          || '';
                if (raw) {
                    var pre = document.createElement('div');
                    pre.style.cssText =
                        'font-size:13px;line-height:1.7;white-space:pre-wrap';
                    pre.textContent = raw;
                    host.innerHTML = '';
                    host.appendChild(pre);
                } else {
                    host.innerHTML =
                        '<p class="muted">AI returned an empty response.</p>';
                }
            })
            .catch(function (e) {
                btn.disabled = false;
                btn.textContent = origText;
                host.innerHTML =
                    '<div class="banner warn">Network error: ' + e + '</div>';
            });
    };
})();
</script>''')

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
    from modes.dashboard.error_toast import error_toast_html, error_toast_script
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — AI Portfolio Manager</title>
<style>{_STYLE}</style>
</head><body>
{error_toast_html()}
{"".join(body_parts)}
{error_toast_script()}
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

    // AI cost confirm — origin 2026-05-14 user feedback ("ran AI
    // mode and it ran no stop until I stopped it"). Echo the
    // server-side cap + per-call so the dialog matches the same
    // numbers shown above the Run Scan button.
    if (mode === 'AI') {
        var perCall = window._swingAiPerCall || 3.0;
        var cap = window._swingAiCap || 15;
        var maxCost = (perCall * cap).toFixed(0);
        if (!confirm('Claude AI overlay will be added on top of the NoAI scan.\\n\\n' +
                     'Cost cap: ~Rs.' + maxCost + ' for up to ' + cap +
                     ' top-priority candidates (Rs.' + perCall.toFixed(0) +
                     '/stock).\\n\\nProceed?')) {
            return;
        }
    }

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

function _parsePosNum(raw, label) {
    // Defensive parse: returns the number when raw is a positive
    // integer/float string, or null when it's empty / negative /
    // non-numeric. The server-side endpoints reject the same cases,
    // but failing early in the browser saves a round-trip and gives
    // an instantly-readable error to the user.
    if (raw === null || raw === undefined) return null;
    var s = String(raw).trim();
    if (!s) return null;
    var n = Number(s);
    if (!isFinite(n) || isNaN(n) || n <= 0) {
        alert('Please enter a positive number for ' + label + ' (got "' + raw + '").');
        return null;
    }
    return n;
}

function confirmAction(actionId) {
    var qtyRaw = prompt('Executed quantity:');
    var qty = _parsePosNum(qtyRaw, 'quantity');
    if (qty === null) return;
    var priceRaw = prompt('Executed price (Rs.):');
    var price = _parsePosNum(priceRaw, 'price');
    if (price === null) return;
    var stopRaw = prompt('Stop-loss price (Rs.) — leave blank to use the suggested stop:', '');
    var stop = 0;
    if (stopRaw && stopRaw.trim()) {
        var parsedStop = _parsePosNum(stopRaw, 'stop');
        if (parsedStop === null) return;
        stop = parsedStop;
    }
    fetch('/api/swing/actions/' + actionId + '/confirm', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({qty: Math.floor(qty), price: price, stop: stop})
    })
        .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
        .then(function(res) {
            if (!res.ok || !res.body.ok) {
                alert('Confirm failed: ' + (res.body.error || 'unknown error'));
                return;
            }
            location.reload();
        })
        .catch(function(e) { alert('Network error: ' + e); });
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
    var qtyRaw = prompt('Exit quantity:');
    var qty = _parsePosNum(qtyRaw, 'quantity');
    if (qty === null) return;
    var priceRaw = prompt('Exit price (Rs.):');
    var price = _parsePosNum(priceRaw, 'price');
    if (price === null) return;
    fetch('/api/swing/positions/' + posId + '/exit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({qty: Math.floor(qty), price: price})
    })
        .then(function(r) { return r.json().then(function(j) { return {ok: r.ok, body: j}; }); })
        .then(function(res) {
            if (!res.ok || !res.body.ok) {
                alert('Exit failed: ' + (res.body.error || 'unknown error'));
                return;
            }
            location.reload();
        })
        .catch(function(e) { alert('Network error: ' + e); });
}

// ── Live-price poller (2026-05-14 fix) ─────────────────────────
//
// Origin: user reported the dashboard claimed prices refreshed
// every 5 seconds but they were actually frozen at page-render
// time. This poller does the work the copy was promising:
//
//   1. Walk every `[data-live-symbol]` element on the page,
//      collect the unique set of symbols actually visible.
//   2. POST /api/live_prices?symbols=A,B,C — backed by the
//      existing rate-limited get_live_quotes() helper, so the
//      Zerodha broker is never hit faster than once per 5s
//      regardless of how many polls fire.
//   3. For each `[data-live-symbol] [data-live-field]` cell,
//      rewrite ONLY the live values (price / pnl / r-mult /
//      price_with_change). Avg / qty / entry / stop / target
//      have no markers and therefore never get touched.
//
// Quiet on errors: a failed poll leaves the previous DOM untouched
// so a network blip doesn't blank out the table.
function _swingPollLivePrices() {
    var nodes = document.querySelectorAll('[data-live-symbol]');
    var symbols = [];
    var seen = {};
    nodes.forEach(function (n) {
        var s = n.getAttribute('data-live-symbol');
        if (s && !seen[s]) { seen[s] = true; symbols.push(s); }
    });
    if (!symbols.length) return;
    fetch('/api/live_prices?symbols=' + encodeURIComponent(symbols.join(',')))
        .then(function (r) { return r.json(); })
        .then(function (j) {
            var quotes = (j && j.quotes) || {};
            nodes.forEach(function (row) {
                var sym = row.getAttribute('data-live-symbol');
                var q = quotes[sym] || {};
                var price = Number(q.price);
                if (!isFinite(price) || price <= 0) return;
                var change = Number(q.change_pct) || 0;
                var chgCls = change >= 0 ? 'pos' : 'neg';
                // Update each marked cell within this row.
                row.querySelectorAll('[data-live-field]').forEach(function (cell) {
                    var field = cell.getAttribute('data-live-field');
                    if (field === 'price') {
                        cell.textContent = 'Rs.' + price.toLocaleString(
                            'en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                    } else if (field === 'price_with_change') {
                        cell.innerHTML = '<span class="' + chgCls + '">Rs.'
                            + price.toLocaleString('en-IN',
                                { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                            + '</span> <span class="muted">('
                            + (change >= 0 ? '+' : '') + change.toFixed(1) + '%)</span>';
                    } else if (field === 'pnl') {
                        var entry = Number(row.getAttribute('data-entry-price'));
                        var qty = Number(row.getAttribute('data-managed-qty'));
                        if (isFinite(entry) && isFinite(qty) && entry > 0 && qty > 0) {
                            var upnl = (price - entry) * qty;
                            var pnlCls = upnl >= 0 ? 'pos' : 'neg';
                            cell.innerHTML = '<span class="' + pnlCls + '">Rs.'
                                + (upnl >= 0 ? '+' : '')
                                + upnl.toLocaleString('en-IN',
                                    { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                                + '</span>';
                        }
                    } else if (field === 'r_mult') {
                        var entry2 = Number(row.getAttribute('data-entry-price'));
                        // We don't have stop in a data-attr; this cell
                        // is informational on swing positions where
                        // stop comes from the backend on next reload.
                        // Leave as-is to avoid showing wrong R-multiple.
                    }
                });
            });
        })
        .catch(function () { /* silent — keep stale values */ });
}

// First poll a moment after load (let the page paint), then every 5s.
window.addEventListener('DOMContentLoaded', function () {
    setTimeout(_swingPollLivePrices, 800);
    setInterval(_swingPollLivePrices, 5000);
});

// ── Single-stock analyse (S38 search box) ──────────────────────
//
// Reads the symbol + AI checkbox + capital input, POSTs to
// /api/swing/analyse_one, renders the result card below the
// search box. Result card carries Done / Skip buttons that re-use
// the existing /api/swing/actions/<id>/{confirm,skip} endpoints
// (so the user's input flow is identical to a recommendation
// from the full scan — same prompts, same persistence).
function _renderSingleResult(host, data) {
    var c = data.candidate || {};
    var status = c.status || 'UNKNOWN';
    var actionId = data.action_id;
    var ai = data.ai_overlay || null;
    var rejected = (status !== 'ACCEPTED');
    var border = rejected ? '#c62828' : '#1b8e3a';

    var html = '';
    html += '<div style="border-left:4px solid ' + border + ';' +
            'padding:10px 12px;background:#fafbfc;border-radius:4px;' +
            'margin-top:6px">';
    html += '<div style="display:flex;justify-content:space-between;' +
            'align-items:center;margin-bottom:6px">';
    html += '<strong style="font-size:15px">' + (c.symbol || '?') +
            '</strong>';
    html += '<span style="font-size:12px;color:' + border +
            ';font-weight:600">' + status + '</span>';
    html += '</div>';

    if (rejected) {
        html += '<p style="margin:4px 0;font-size:13px">' +
                (c.rejected_reason || 'Rejected for unknown reason') +
                '</p>';
    } else {
        html += '<table class="kvtable" style="margin-top:4px">';
        var rr = (c.rr_ratio || 0).toFixed(2);
        var dip = (c.dip_from_ath_pct || 0).toFixed(1);
        var fmt = function (n, d) {
            return 'Rs.' + Number(n || 0).toLocaleString('en-IN',
                { minimumFractionDigits: d, maximumFractionDigits: d });
        };
        html += '<tr><td>Setup</td><td>' + (c.setup_type || '—') +
                ' (score ' + (c.score || 0).toFixed(2) + ')</td></tr>';
        html += '<tr><td>Sector</td><td>' + (c.sector || '—') + '</td></tr>';
        html += '<tr><td>Current</td><td>' + fmt(c.close_price, 2) + '</td></tr>';
        html += '<tr><td>Suggested entry</td><td>' + fmt(c.entry_price, 2) +
                '</td></tr>';
        html += '<tr><td>Stop</td><td>' + fmt(c.stop_price, 2) + '</td></tr>';
        html += '<tr><td>Target</td><td>' + fmt(c.target_price, 2) + '</td></tr>';
        html += '<tr><td>Suggested qty</td><td>' + (c.suggested_qty || 0) +
                '</td></tr>';
        html += '<tr><td>R:R</td><td>' + rr + 'x</td></tr>';
        html += '<tr><td>% Below 52w high</td><td>' + dip + '% (Rs.' +
                Number(c.ath_price || 0).toLocaleString('en-IN',
                    { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ')</td></tr>';
        html += '<tr><td>RSI</td><td>' + (c.rsi_daily || 0).toFixed(1) + '</td></tr>';
        html += '<tr><td>RS vs NIFTY</td><td>' + (c.relative_strength >= 0 ? '+' : '') +
                (c.relative_strength || 0).toFixed(1) + '%</td></tr>';
        html += '<tr><td>Volume vs avg</td><td>' + (c.volume_ratio || 0).toFixed(1) +
                'x</td></tr>';
        html += '</table>';

        var reasons = c.reasons || [];
        if (reasons.length) {
            html += '<p style="margin:8px 0 4px;font-size:12px;font-weight:600">' +
                    'Why this score:</p>';
            html += '<ul style="margin:0 0 8px 20px;font-size:12px;line-height:1.6">';
            reasons.forEach(function (r) {
                html += '<li>' + r + '</li>';
            });
            html += '</ul>';
        }

        if (actionId) {
            html += '<div style="margin-top:8px;display:flex;gap:8px">';
            html += '<button class="action" onclick="confirmAction(' +
                    actionId + ')" style="padding:5px 10px;font-size:12px">' +
                    'Done — add to swing book</button>';
            html += '<button class="action alt" onclick="skipAction(' +
                    actionId + ')" style="padding:5px 10px;font-size:12px">' +
                    'Skip</button>';
            html += '<a href="/swing/' + encodeURIComponent(c.symbol) +
                    '" style="padding:5px 10px;font-size:12px;' +
                    'border:1px solid #cfd9eb;border-radius:5px;text-decoration:none">' +
                    'Open detail page</a>';
            html += '</div>';
        }
    }

    if (ai) {
        html += '<div style="margin-top:12px;padding-top:8px;' +
                'border-top:1px solid #e5e7eb">';
        html += '<strong style="font-size:13px">AI Analysis</strong>';
        if (ai.error) {
            html += '<div class="banner warn" style="margin-top:6px">AI error: ' +
                    ai.error + '</div>';
        } else if (ai.raw_response) {
            var pre = document.createElement('pre');
            // Render via DOM textContent to avoid HTML injection.
            html += '<div id="single-ai-pre" style="font-size:12.5px;' +
                    'line-height:1.7;white-space:pre-wrap;margin-top:6px"></div>';
        }
        html += '</div>';
    }

    html += '</div>';
    host.innerHTML = html;

    // Inject AI text via textContent so newlines render and HTML
    // can't be injected from the Claude response.
    if (ai && ai.raw_response) {
        var pre = host.querySelector('#single-ai-pre');
        if (pre) pre.textContent = ai.raw_response;
    }
}

function analyseOne() {
    var symEl = document.getElementById('single-symbol');
    var aiEl = document.getElementById('single-ai-toggle');
    var capEl = document.getElementById('swing-capital');
    var host = document.getElementById('single-result-host');
    if (!symEl || !host) return;
    var sym = (symEl.value || '').trim().toUpperCase();
    if (!sym) {
        host.innerHTML = '<div class="banner warn">' +
            'Type a ticker (e.g. SBIN) first.</div>';
        return;
    }
    var ai = aiEl && aiEl.checked ? '1' : '0';
    var capital = capEl ? parseFloat((capEl.value || '0').replace(/,/g, '')) : 0;
    if (ai === '1') {
        var perCall = window._swingAiPerCall || 3;
        if (!confirm('Spend ~Rs.' + perCall.toFixed(0) +
                     ' on a Claude AI overlay for ' + sym + '?')) {
            return;
        }
    }
    host.innerHTML = '<p class="muted"><span class="spinner"></span> ' +
        'Fetching candles + computing indicators for ' + sym +
        (ai === '1' ? ' (with AI overlay)' : '') + '...</p>';
    fetch('/api/swing/analyse_one?symbol=' + encodeURIComponent(sym) +
          '&ai=' + ai + '&capital=' + (capital || 0),
          {method: 'POST'})
        .then(function (r) { return r.json().then(function (j) {
            return {ok: r.ok, body: j};
        }); })
        .then(function (res) {
            if (!res.ok || !res.body.ok) {
                host.innerHTML = '<div class="banner warn">' +
                    'Analyse failed: ' + (res.body.error || 'unknown') + '</div>';
                return;
            }
            _renderSingleResult(host, res.body);
        })
        .catch(function (e) {
            host.innerHTML = '<div class="banner warn">Network error: ' +
                e + '</div>';
        });
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
