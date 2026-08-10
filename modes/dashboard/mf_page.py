"""Mutual-fund dashboard page (`/mf`).

Mirrors the US / Swing pages in structure: server-rendered shell with
KPI cards and charts, then the heavy tables stream in from
`/api/mf/sections` after first paint so opening the page never blocks
on Zerodha.

Two books live here side by side:
  * Coin holdings — broker truth, refreshed from `kite.mf_holdings()`.
  * Externally-held funds — hand-entered rows in `data/mf.db` for
    funds bought elsewhere, priced off the same Coin NAV.

The page is deliberate about one thing: a mutual fund has no live
price. Every value on it is marked to an end-of-day NAV, and the
`NAV as of` stamp is shown next to the money so it never reads as a
real-time number.
"""

from __future__ import annotations

import html
import json

from modes.dashboard.nav import render_topnav, topnav_css
from modes.mf.book import build_book
from modes.mf.catalog import catalog_as_of, ensure_catalog, search_catalog
from modes.mf.types import SRC_COIN, SRC_EXTERNAL


# ── Formatting ────────────────────────────────────────────────

def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _grouped(n: float) -> str:
    """Indian digit grouping: 12,34,567."""
    s = str(abs(int(n)))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    parts.append(tail)
    return ",".join(parts)


def _inr(value, *, signed: bool = False) -> str:
    v = _num(value)
    sign = "-" if v < 0 else ("+" if signed else "")
    return f"&#8377;{sign}{_grouped(round(abs(v)))}"


def _inr_compact(value, *, signed: bool = False) -> str:
    v = _num(value)
    sign = "-" if v < 0 else ("+" if signed else "")
    a = abs(v)
    if a >= 1e7:
        return f"&#8377;{sign}{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"&#8377;{sign}{a / 1e5:.2f} L"
    return f"&#8377;{sign}{_grouped(round(a))}"


def _units(value) -> str:
    v = _num(value)
    return f"{v:,.3f}".rstrip("0").rstrip(".") if v else "0"


def _cls(value) -> str:
    return "pos" if _num(value) >= 0 else "neg"


def _safe_json(data) -> str:
    """JSON for an inline <script> — no value can close the tag early."""
    return (json.dumps(data, default=str)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026"))


# ── Page ──────────────────────────────────────────────────────

def render_mf_page() -> str:
    """Render `/mf` from cache only. Live data arrives via sections."""
    book = build_book(live=False)
    payload = book.to_dict()

    body: list[str] = []
    body.append(render_topnav("/mf"))
    body.append('<div class="wrap">')
    body.append('<h1 class="page-title">Mutual Funds</h1>')
    body.append(
        '<p class="sub">Coin holdings plus funds you hold at other '
        'brokers, valued on the same published NAV. Mutual funds have '
        'no intraday price &mdash; every number here is marked to the '
        'last end-of-day NAV.</p>'
    )
    body.append(_render_freshness(book))
    body.append(_render_kpis(book))
    body.append(_render_charts())
    body.append(_render_sections_loader())
    body.append('</div>')
    body.append(f'<script id="mf-bootstrap" type="application/json">'
                f'{_safe_json(payload)}</script>')
    body.append(_SCRIPT)
    return _wrap("Mutual Funds", body)


def render_mf_sections_json(*, live: bool = False) -> str:
    """GET /api/mf/sections — the heavy tables plus refreshed KPIs."""
    if live:
        # Keeps the scheme picker and external-fund NAVs current.
        try:
            ensure_catalog()
        except Exception:  # noqa: BLE001 — a stale catalogue still renders
            pass

    book = build_book(live=live)

    if live and book.schemes:
        # One call per scheme, but rate-limited to weekly per scheme so a
        # routine refresh does not re-download five years of NAVs.
        try:
            from modes.mf.catalog import refresh_nav_history
            refresh_nav_history([s.scheme_code for s in book.schemes])
        except Exception:  # noqa: BLE001 — analytics degrade, book still renders
            pass
    coin = [h for h in book.holdings if h.source == SRC_COIN]
    external = [h for h in book.holdings if h.source == SRC_EXTERNAL]

    fragment = "".join([
        _render_split_notice(book),
        _render_insights(book),
        _render_combined_table(book),
        _render_coin_table(coin, book),
        _render_external_table(external),
        _render_sips(book),
        _render_orders(book),
    ])
    return json.dumps({
        "ok": True,
        "html": fragment,
        "summary": book.to_dict(),
    }, default=str)


def render_mf_search_json(query: str) -> str:
    """GET /api/mf/search?q= — scheme picker for the add-fund form."""
    try:
        ensure_catalog()
    except Exception:  # noqa: BLE001
        pass
    return json.dumps({"ok": True, "results": search_catalog(query, limit=25)},
                      default=str)


def render_mf_nav_history_json(scheme_code: str, *, days: int = 365) -> str:
    """GET /api/mf/nav_history?scheme= — daily NAV series for a chart."""
    from modes.mf.catalog import nav_history
    return json.dumps(nav_history(scheme_code, days=days), default=str)


# ── Fragments ─────────────────────────────────────────────────

def _synced_label(synced_at: str) -> str:
    if not synced_at:
        return "Never synced"
    return "Coin synced " + synced_at[:16].replace("T", " ")


def _render_freshness(book) -> str:
    chips: list[str] = []
    if book.nav_as_of:
        chips.append(
            f'<span class="chip" title="Funds are valued on the NAV the AMC '
            f'published for this date. There is no intraday NAV.">'
            f'NAV as of <strong>{html.escape(book.nav_as_of)}</strong></span>')
    else:
        chips.append('<span class="chip warn">No NAV resolved yet</span>')

    cat = catalog_as_of()
    if cat:
        chips.append(f'<span class="chip">Scheme catalogue '
                     f'{html.escape(cat[:10])}</span>')
    else:
        chips.append('<span class="chip warn" title="Refresh to download the '
                     'Coin scheme list">Catalogue not downloaded</span>')

    if book.unpriced_count:
        chips.append(
            f'<span class="chip warn" title="These funds are shown at cost '
            f'because no NAV could be resolved for the scheme code.">'
            f'{book.unpriced_count} unpriced</span>')

    chips.append(
        f'<span class="chip" id="mf-live-chip" title="The book is stored '
        f'locally after each Coin fetch, so this page opens on the last '
        f'known numbers without calling the broker.">'
        f'{html.escape(_synced_label(book.synced_at))}</span>')

    return (
        '<div class="freshness">'
        + "".join(chips)
        + '<span class="spacer"></span>'
        + '<button class="action" type="button" id="mf-refresh" '
          'onclick="refreshMf(true)">Refresh from Coin</button>'
        + '</div>'
        + '<div class="banner error" id="mf-error" style="display:none"></div>'
    )


def _render_kpis(book) -> str:
    alloc = book.allocation or {}
    return f"""
<section class="kpis">
  <article class="kpi accent">
    <div class="k-label">Current value</div>
    <div class="k-value" id="mf-kpi-current"
         title="{_inr(book.current_value)}">{_inr_compact(book.current_value)}</div>
    <div class="k-foot"><span id="mf-kpi-schemes">{alloc.get('scheme_count', 0)}</span>
      schemes</div>
  </article>
  <article class="kpi">
    <div class="k-label">Invested</div>
    <div class="k-value" id="mf-kpi-invested"
         title="{_inr(book.invested_value)}">{_inr_compact(book.invested_value)}</div>
    <div class="k-foot">at cost</div>
  </article>
  <article class="kpi">
    <div class="k-label">Unrealised P&amp;L</div>
    <div class="k-value {_cls(book.pnl)}" id="mf-kpi-pnl"
         title="{_inr(book.pnl, signed=True)}">{_inr_compact(book.pnl, signed=True)}</div>
    <div class="k-foot"><span class="{_cls(book.pnl_pct)}"
      id="mf-kpi-pnl-pct">{book.pnl_pct:+.2f}%</span> on cost</div>
  </article>
  <article class="kpi">
    <div class="k-label">Monthly SIP</div>
    <div class="k-value" id="mf-kpi-sip">{_inr_compact(book.monthly_sip_outflow)}</div>
    <div class="k-foot"><span id="mf-kpi-sip-count">{len(book.active_sips)}</span>
      active &middot; <span id="mf-kpi-sip-paused">{len(book.paused_sips)}</span> paused</div>
  </article>
</section>
"""


def _render_charts() -> str:
    return """
<section class="split">
  <div class="card">
    <h3>Asset class</h3>
    <div class="donut-wrap"><canvas id="mf-chart-asset" height="200"></canvas></div>
    <ul class="legend" id="mf-asset-legend"></ul>
  </div>
  <div class="card">
    <h3>Fund house</h3>
    <div class="donut-wrap"><canvas id="mf-chart-amc" height="200"></canvas></div>
    <ul class="legend" id="mf-amc-legend"></ul>
  </div>
</section>
<section class="split">
  <div class="card">
    <h3>Direct vs Regular
      <span class="k-info" title="Regular plans pay a distributor trail every
year. The same scheme in a direct plan keeps that fee invested.">?</span></h3>
    <ul class="sectors" id="mf-plan-list"></ul>
  </div>
  <div class="card">
    <h3>Where it is held</h3>
    <ul class="sectors" id="mf-broker-list"></ul>
  </div>
</section>
<section class="card">
  <h3>NAV history <span class="muted small" id="mf-nav-chart-label">
    &mdash; pick a fund below</span></h3>
  <div class="chart-wrap"><canvas id="mf-chart-nav" height="220"></canvas></div>
</section>
"""


def _render_sections_loader() -> str:
    return (
        '<div id="mf-sections">'
        '<div class="card muted">Loading holdings&hellip;</div>'
        '</div>'
    )


def _render_split_notice(book) -> str:
    split = [s for s in book.schemes if s.is_split]
    if not split:
        return ""
    items = "".join(
        f'<li><strong>{html.escape(s.fund)}</strong> &mdash; '
        + " + ".join(
            f'{html.escape(leg.broker)} {_units(leg.units)}u @ '
            f'{_num(leg.avg_nav):,.4f}' for leg in s.legs)
        + f' &rarr; blended {_num(s.avg_nav):,.4f}</li>'
        for s in split
    )
    return (
        '<div class="banner info"><strong>Same scheme, two brokers.</strong> '
        'These funds are held in more than one place; the combined table '
        'below merges them into one position with a unit-weighted average '
        f'NAV.<ul class="tight">{items}</ul></div>'
    )


def _scheme_row(s) -> str:
    split_flag = (
        f'<span class="chip mini" title="Held at: '
        f'{html.escape(", ".join(s.brokers))}">{len(s.brokers)} brokers</span>'
        if s.is_split else "")
    return (
        f'<tr>'
        f'<td><button class="linklike" type="button" '
        f'onclick="loadNavChart(\'{html.escape(s.scheme_code)}\', '
        f'\'{html.escape(s.fund)}\')" '
        f'title="Show NAV history">{html.escape(s.fund)}</button>'
        f'{split_flag}<br>'
        f'<span class="small">{html.escape(s.amc or "")}'
        f'{" &middot; " + html.escape(s.plan) if s.plan else ""}</span></td>'
        f'<td class="right">{_units(s.units)}</td>'
        f'<td class="right">{_num(s.avg_nav):,.4f}</td>'
        f'<td class="right">{_num(s.nav):,.4f}<br>'
        f'<span class="small">{html.escape(s.nav_date or "&mdash;")}</span></td>'
        f'<td class="right">{_inr(s.invested_value)}</td>'
        f'<td class="right">{_inr(s.current_value)}</td>'
        f'<td class="right {_cls(s.pnl)}">{_inr(s.pnl, signed=True)}<br>'
        f'<span class="small {_cls(s.pnl_pct)}">{_num(s.pnl_pct):+.2f}%</span></td>'
        f'</tr>'
    )


def _render_combined_table(book) -> str:
    out = ['<h2>Combined book</h2>', '<div class="card">']
    if not book.schemes:
        out.append('<div class="muted">No mutual funds yet. Refresh from Coin, '
                   'or add a fund you hold at another broker below.</div>'
                   '</div>')
        return "".join(out)
    out.append('<p class="muted small">One row per scheme, merged across every '
               'broker. Click a fund name to chart its NAV.</p>')
    out.append('<div class="table-scroll"><table class="holdings">')
    out.append('<tr><th>Fund</th><th class="right">Units</th>'
               '<th class="right">Avg NAV</th><th class="right">NAV</th>'
               '<th class="right">Invested</th><th class="right">Value</th>'
               '<th class="right">P&amp;L</th></tr>')
    out.extend(_scheme_row(s) for s in book.schemes)
    out.append(
        f'<tr class="total-row"><td>Total</td><td></td><td></td><td></td>'
        f'<td class="right">{_inr(book.invested_value)}</td>'
        f'<td class="right">{_inr(book.current_value)}</td>'
        f'<td class="right {_cls(book.pnl)}">{_inr(book.pnl, signed=True)}</td></tr>')
    out.append('</table></div></div>')
    return "".join(out)


def _render_coin_table(coin, book) -> str:
    out = ['<h2>Zerodha Coin</h2>', '<div class="card">']
    if book.coin_error:
        out.append(f'<div class="banner warn">Coin is unreachable '
                   f'({html.escape(book.coin_error)}). Showing the last cached '
                   f'NAVs.</div>')
    if not coin:
        out.append('<div class="muted">No Coin holdings loaded. Click '
                   '<em>Refresh from Coin</em> above &mdash; a valid Zerodha '
                   'login is required.</div></div>')
        return "".join(out)
    out.append('<div class="table-scroll"><table class="holdings">')
    out.append('<tr><th>Fund</th><th>Folio</th><th class="right">Units</th>'
               '<th class="right">Avg NAV</th><th class="right">NAV</th>'
               '<th class="right">Value</th><th class="right">P&amp;L</th></tr>')
    for h in coin:
        out.append(
            f'<tr><td>{html.escape(h.fund)}<br>'
            f'<span class="small">{html.escape(h.scheme_code)}</span></td>'
            f'<td><span class="small">{html.escape(h.folio or "&mdash;")}</span></td>'
            f'<td class="right">{_units(h.units)}</td>'
            f'<td class="right">{_num(h.avg_nav):,.4f}</td>'
            f'<td class="right">{_num(h.nav):,.4f}</td>'
            f'<td class="right">{_inr(h.current_value)}</td>'
            f'<td class="right {_cls(h.pnl)}">{_inr(h.pnl, signed=True)}'
            f'<span class="small {_cls(h.pnl_pct)}"> '
            f'{_num(h.pnl_pct):+.2f}%</span></td></tr>')
    out.append('</table></div></div>')
    return "".join(out)


def _render_external_table(external) -> str:
    out = ['<h2>Held at other brokers</h2>', '<div class="card">']
    out.append(
        '<p class="muted small">Funds bought outside Coin. Pick the scheme '
        'from the Coin catalogue so the NAV resolves automatically, then '
        'enter your units and average NAV. The same scheme can be added '
        'here even if you also hold it on Coin &mdash; the combined table '
        'brokers so the review can tell which funds still receive money.</p>')

    out.append(_render_add_form())

    if not external:
        out.append('<div class="muted">Nothing tracked outside Coin yet.</div>')
    else:
        out.append('<div class="table-scroll"><table class="holdings">')
        out.append('<tr><th>Fund</th><th>Broker</th><th>Folio</th>'
                   '<th class="right">Units</th><th class="right">Avg NAV</th>'
                   '<th class="right">NAV</th><th class="right">Value</th>'
                   '<th class="right">P&amp;L</th><th class="right">SIP</th>'
                   '<th>Controls</th></tr>')
        for h in external:
            out.append(
                f'<tr><td>{html.escape(h.fund)}<br>'
                f'<span class="small">{html.escape(h.scheme_code)}</span></td>'
                f'<td>{html.escape(h.broker)}</td>'
                f'<td><span class="small">{html.escape(h.folio or "&mdash;")}</span></td>'
                f'<td class="right">{_units(h.units)}</td>'
                f'<td class="right">{_num(h.avg_nav):,.4f}</td>'
                f'<td class="right">{_num(h.nav):,.4f}</td>'
                f'<td class="right">{_inr(h.current_value)}</td>'
                f'<td class="right {_cls(h.pnl)}">{_inr(h.pnl, signed=True)}'
                f'<span class="small {_cls(h.pnl_pct)}"> '
                f'{_num(h.pnl_pct):+.2f}%</span></td>'
                f'<td class="right">'
                f'{_inr(h.sip_amount) if h.sip_amount > 0 else "&mdash;"}</td>'
                f'<td><button class="action alt mini" type="button" '
                f'onclick="editExternal({h.holding_id}, {_num(h.units)}, '
                f'{_num(h.avg_nav)})">Edit</button> '
                f'<button class="action alt mini" type="button" '
                f'onclick="removeExternal({h.holding_id})">Remove</button>'
                f'</td></tr>')
        out.append('</table></div>')
    out.append('</div>')
    return "".join(out)


def _render_add_form() -> str:
    return """
<div class="add-form">
  <div class="form-row">
    <label>Scheme
      <input type="text" id="mf-add-search" placeholder="Search Coin catalogue, e.g. parag parikh flexi"
             autocomplete="off" oninput="searchSchemes()">
    </label>
    <label>Broker
      <input type="text" id="mf-add-broker" placeholder="Groww / MF Central / ICICI Direct">
    </label>
  </div>
  <div id="mf-search-results" class="search-results"></div>
  <div class="form-row">
    <label>Units
      <input type="number" id="mf-add-units" step="0.001" min="0" placeholder="120.456">
    </label>
    <label>Average NAV
      <input type="number" id="mf-add-nav" step="0.0001" min="0" placeholder="88.2340">
    </label>
    <label>Folio <span class="muted small">(optional)</span>
      <input type="text" id="mf-add-folio" placeholder="12345678/90">
    </label>
    <label>Monthly SIP <span class="muted small">(0 if none)</span>
      <input type="number" id="mf-add-sip" step="100" min="0" value="0">
    </label>
    <button class="action" type="button" id="mf-add-btn"
            onclick="addExternal()" disabled>Add fund</button>
  </div>
  <div class="muted small" id="mf-add-picked">No scheme selected yet.</div>
</div>
"""


def _render_sips(book) -> str:
    out = ['<h2>SIPs</h2>', '<div class="card">']
    if not book.sips:
        out.append('<div class="muted">No SIPs found. Refresh from Coin to '
                   'load them &mdash; SIP state only exists on the broker.'
                   '</div></div>')
        return "".join(out)

    out.append(
        f'<p class="muted small">{len(book.active_sips)} active, '
        f'{len(book.paused_sips)} paused. Active instalments normalise to '
        f'<strong>{_inr(book.monthly_sip_outflow)}</strong> a month.</p>')
    out.append('<div class="table-scroll"><table class="holdings">')
    out.append('<tr><th>Status</th><th>Fund</th><th class="right">Instalment</th>'
               '<th>Frequency</th><th class="right">Day</th><th>Next</th>'
               '<th class="right">Done</th><th class="right">Pending</th></tr>')
    for s in book.sips:
        state = s.status.lower()
        pill = ("pos" if s.is_active else
                "warn" if s.is_paused else "muted")
        pending = (s.pending_instalments if s.pending_instalments >= 0
                   else "&#8734;")
        out.append(
            f'<tr><td><span class="chip mini {pill}" '
            f'data-sip-state="{html.escape(state)}">'
            f'{html.escape(s.status or "UNKNOWN")}</span></td>'
            f'<td>{html.escape(s.fund)}</td>'
            f'<td class="right">{_inr(s.instalment_amount)}</td>'
            f'<td>{html.escape(s.frequency or "&mdash;")}</td>'
            f'<td class="right">{s.instalment_day or "&mdash;"}</td>'
            f'<td>{html.escape((s.next_instalment or "&mdash;")[:10])}</td>'
            f'<td class="right">{s.completed_instalments}</td>'
            f'<td class="right">{pending}</td></tr>')
    out.append('</table></div></div>')
    return "".join(out)


def _render_orders(book) -> str:
    out = ['<h2>Recent Coin orders</h2>', '<div class="card">']
    if not book.orders:
        out.append('<div class="muted">No orders loaded. Refresh from Coin to '
                   'fetch the order book.</div></div>')
        return "".join(out)
    out.append('<div class="table-scroll"><table class="holdings">')
    out.append('<tr><th>Date</th><th>Fund</th><th>Type</th>'
               '<th class="right">Amount</th><th class="right">Units</th>'
               '<th class="right">NAV</th><th>Status</th></tr>')
    for o in book.orders[:40]:
        side = str(o.get("transaction_type") or "")
        out.append(
            f'<tr><td>{html.escape(str(o.get("placed_at") or "")[:10])}</td>'
            f'<td>{html.escape(str(o.get("fund") or ""))}</td>'
            f'<td><span class="chip mini {"pos" if side == "BUY" else "warn"}">'
            f'{html.escape(side)}</span></td>'
            f'<td class="right">{_inr(o.get("amount"))}</td>'
            f'<td class="right">{_units(o.get("units"))}</td>'
            f'<td class="right">{_num(o.get("avg_nav")):,.4f}</td>'
            f'<td><span class="small">{html.escape(str(o.get("status") or ""))}'
            f'</span></td></tr>')
    out.append('</table></div></div>')
    return "".join(out)


def _render_insights(book) -> str:
    """Structural review: overlap, accumulation split, tax consolidation.

    Deliberately has no buy/sell verdict — for a long-held book the
    useful questions are how many distinct bets you own and what it
    costs to tidy them, not which fund did well recently.
    """
    from modes.mf.insights import build_insights

    if not book.schemes:
        return ""
    ins = build_insights(book)
    acc = ins["accumulation"]
    tax = ins["tax"]

    out = ['<h2>Review</h2>']

    findings = ins.get("findings") or []
    if findings:
        out.append('<div class="card">')
        out.append('<p class="muted small">Structural observations only. '
                   'Nothing here is a buy or sell call &mdash; ranking funds '
                   'on recent return mostly measures when you bought.</p>')
        for f in findings:
            sev = f["severity"].lower()
            out.append(
                f'<div class="finding {sev}">'
                f'<div class="f-head"><span class="chip mini {sev}">'
                f'{html.escape(f["severity"])}</span>'
                f'<strong>{html.escape(f["title"])}</strong></div>'
                f'<div class="f-detail">{html.escape(f["detail"])}</div>'
                f'</div>')
        out.append('</div>')

    # Accumulation vs dormant.
    out.append('<div class="card">')
    out.append('<h3>Where new money goes</h3>')
    out.append(
        f'<p class="muted small">'
        f'{_inr(acc["monthly_inflow"])}/month into '
        f'{acc["funded_count"]} of {len(book.schemes)} schemes &mdash; '
        f'{acc["inflow_rate_pct"]:.0f}% of the book a year. The other '
        f'{acc["dormant_count"]} schemes ({_inr(acc["dormant_value"])}, '
        f'{acc["dormant_pct"]:.0f}% of corpus) receive nothing, so anything '
        f'redundant there will not be diluted by future SIPs.</p>')
    if acc["funded"]:
        out.append('<div class="table-scroll"><table class="holdings">')
        out.append('<tr><th>Fund</th><th>Exposure</th>'
                   '<th class="right">Monthly</th><th class="right">Share</th>'
                   '<th class="right">Held</th></tr>')
        for r in acc["funded"]:
            out.append(
                f'<tr><td>{html.escape(r["fund"])}</td>'
                f'<td><span class="small">{html.escape(r["exposure"])}</span></td>'
                f'<td class="right">{_inr(r["sip_amount"])}</td>'
                f'<td class="right">{r["sip_share_pct"]:.1f}%</td>'
                f'<td class="right">{_inr(r["value"])}</td></tr>')
        out.append('</table></div>')

    rows = acc.get("corpus_vs_inflow") or []
    if rows:
        out.append('<h3 style="margin-top:18px">Corpus vs new money</h3>')
        out.append('<div class="table-scroll"><table class="holdings">')
        out.append('<tr><th>Asset class</th><th class="right">Corpus</th>'
                   '<th class="right">New money</th><th class="right">Gap</th></tr>')
        for r in rows:
            gap = r["gap"]
            out.append(
                f'<tr><td>{html.escape(r["label"])}</td>'
                f'<td class="right">{r["corpus_pct"]:.1f}%</td>'
                f'<td class="right">{r["inflow_pct"]:.1f}%</td>'
                f'<td class="right {_cls(gap)}">{gap:+.1f}</td></tr>')
        out.append('</table></div>')
    out.append('</div>')

    # Exposure map.
    out.append('<div class="card">')
    out.append('<h3>Exposure map</h3>')
    out.append('<p class="muted small">One row per distinct bet. More than '
               'one fund in a row means the same exposure is held twice.</p>')
    out.append('<div class="table-scroll"><table class="holdings">')
    out.append('<tr><th>Exposure</th><th class="right">Funds</th>'
               '<th class="right">Weight</th><th class="right">Dormant</th>'
               '<th class="right">Monthly SIP</th><th>Holdings</th></tr>')
    for c in ins["clusters"]:
        names = ", ".join(f["fund"] for f in c["funds"])
        flag = ' <span class="chip mini warn">overlap</span>' if c["is_redundant"] else ""
        out.append(
            f'<tr><td>{html.escape(c["label"])}{flag}</td>'
            f'<td class="right">{c["fund_count"]}</td>'
            f'<td class="right">{c["weight_pct"]:.1f}%</td>'
            f'<td class="right">{c["dormant_count"]}</td>'
            f'<td class="right">{_inr(c["sip_amount"])}</td>'
            f'<td><span class="small">{html.escape(names[:120])}</span></td></tr>')
    out.append('</table></div>')

    pairs = ins.get("correlated_pairs") or []
    if pairs:
        out.append('<h3 style="margin-top:18px">Funds that move together</h3>')
        out.append('<p class="muted small">Correlation of daily NAV moves. '
                   'Above 0.90 the two funds are effectively one bet, whatever '
                   'their categories say. This is the honest stand-in for true '
                   'holdings overlap, which needs AMFI portfolio disclosures '
                   'we do not have.</p>')
        out.append('<div class="table-scroll"><table class="holdings">')
        out.append('<tr><th class="right">r</th><th>Fund A</th><th>Fund B</th>'
                   '<th class="right">Combined</th></tr>')
        for p in pairs[:12]:
            out.append(
                f'<tr><td class="right"><strong>{p["correlation"]:.2f}</strong></td>'
                f'<td>{html.escape(p["a_fund"])}</td>'
                f'<td>{html.escape(p["b_fund"])}</td>'
                f'<td class="right">{_inr(p["combined_value"])}</td></tr>')
        out.append('</table></div>')
    out.append('</div>')

    # Consolidation + tax.
    out.append('<div class="card">')
    out.append('<h3>Consolidation &amp; tax</h3>')
    out.append(
        f'<p class="muted small">LTCG on equity-oriented funds is exempt up to '
        f'{_inr(tax["ltcg_exemption"])} a financial year &mdash; the cheapest '
        f'way to tidy a long-held book. Unrealised gain across the book is '
        f'{_inr(tax["unrealised_gain"])}. {html.escape(tax["equity_oriented_note"])}</p>')
    out.append(
        '<div class="banner warn"><strong>Verify before acting.</strong> '
        'Per-lot purchase dates are not available from the broker, so these '
        'gains cannot be confirmed as long-term here. Check the holding '
        'period of each lot in your CAS statement first.</div>')
    options = ins.get("consolidation") or []
    if not options:
        out.append('<div class="muted">No dormant duplicates to merge.</div>')
    else:
        out.append('<div class="table-scroll"><table class="holdings">')
        out.append('<tr><th>Exposure</th><th>Keep</th><th>Could merge</th>'
                   '<th class="right">Frees</th><th class="right">Gain</th>'
                   '<th>Within exemption</th></tr>')
        for o in options:
            merge = "<br>".join(html.escape(m["fund"]) for m in o["merge"])
            fits = ('<span class="chip mini pos">yes</span>'
                    if o["fits_exemption"] else
                    '<span class="chip mini warn">check</span>')
            out.append(
                f'<tr><td>{html.escape(o["cluster"])}</td>'
                f'<td><span class="small">{html.escape(o["keep"])}</span></td>'
                f'<td><span class="small">{merge}</span></td>'
                f'<td class="right">{_inr(o["freed_value"])}</td>'
                f'<td class="right">{_inr(o["gain_realised"])}</td>'
                f'<td>{fits}</td></tr>')
        out.append('</table></div>')
    out.append('</div>')

    # Return & risk.
    risk = ins.get("risk") or {}
    cov = ins.get("nav_history_coverage") or {}
    out.append('<div class="card">')
    out.append('<h3>Return &amp; risk</h3>')
    if not risk:
        out.append('<div class="muted">No NAV history stored yet. '
                   'Refresh from Coin to download it.</div>')
    else:
        out.append(
            f'<p class="muted small">Annualised from published NAVs, so this '
            f'measures the fund rather than your entry timing &mdash; which is '
            f'why it replaces a "best/worst performer" list. Compare funds '
            f'inside the same exposure row above. '
            f'Coverage {cov.get("have", 0)}/{cov.get("total", 0)}.</p>')
        out.append('<div class="table-scroll"><table class="holdings">')
        out.append('<tr><th>Fund</th><th class="right">1Y</th>'
                   '<th class="right">3Y</th><th class="right">5Y</th>'
                   '<th class="right">Volatility</th>'
                   '<th class="right">Max drawdown</th></tr>')
        for s in book.schemes:
            p = risk.get(s.scheme_code)
            if not p:
                continue
            out.append(
                f'<tr><td>{html.escape(s.fund)}</td>'
                f'<td class="right {_ret_cls(p["cagr_1y"])}">{_pct(p["cagr_1y"])}</td>'
                f'<td class="right {_ret_cls(p["cagr_3y"])}">{_pct(p["cagr_3y"])}</td>'
                f'<td class="right {_ret_cls(p["cagr_5y"])}">{_pct(p["cagr_5y"])}</td>'
                f'<td class="right">{_mag(p["volatility"])}</td>'
                f'<td class="right">{_mag(p["max_drawdown"])}</td></tr>')
        out.append('</table></div>')
    out.append('</div>')

    return "".join(out)


def _pct(value) -> str:
    return "&mdash;" if value is None else f"{float(value):+.1f}%"


def _mag(value) -> str:
    return "&mdash;" if value is None else f"{abs(float(value)):.1f}%"


def _ret_cls(value) -> str:
    """No colour for a missing return — an absent number is not a gain."""
    return "" if value is None else _cls(value)


# ── Chrome ────────────────────────────────────────────────────

def _wrap(title: str, body_parts: list[str]) -> str:
    from modes.dashboard.theme import head_common

    parts = list(body_parts)
    topnav = ""
    if parts and parts[0].lstrip().startswith("<nav"):
        topnav = parts.pop(0)
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">"
        + head_common(f"{title} — AI Portfolio Manager",
                      chartjs=True, extra_style=_STYLE)
        + "<body>"
        + topnav
        + "".join(parts)
        + "</body></html>"
    )


_STYLE = """
* { box-sizing: border-box; }
body { font-family: var(--font); background: var(--bg); color: var(--fg);
       margin: 0; padding: 24px; }
.wrap { max-width: 1180px; margin: 0 auto; }
h1.page-title { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em;
     color: var(--muted); margin: 28px 0 8px; font-weight: 600; }
h3 { font-size: 14px; margin: 0 0 12px; }
.sub { margin: 0 0 14px; color: var(--muted); font-size: 13px; line-height: 1.6; }
.muted { color: var(--muted); }
.small { font-size: 12px; color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line);
        border-radius: 12px; padding: 18px 22px; margin-bottom: 16px; }
.freshness { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
             margin-bottom: 14px; }
.freshness .spacer { flex: 1; }
.chip { background: var(--soft); border: 1px solid var(--line);
        border-radius: 999px; padding: 3px 10px; font-size: 12px;
        color: var(--muted); white-space: nowrap; }
.chip strong { color: var(--fg); }
.chip.mini { font-size: 11px; padding: 2px 8px; font-weight: 700; }
.chip.warn { background: var(--warn-bg); border-color: var(--warn-line);
             color: var(--warn-fg); }
.chip.pos { background: var(--pos-bg); border-color: var(--pos-line);
            color: var(--pos); }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px;
        margin-bottom: 18px; }
.kpi { background: var(--card); border: 1px solid var(--line);
       border-radius: 12px; padding: 16px 18px; }
.kpi.accent { border-color: var(--accent-line); background: var(--accent-soft); }
.k-label { font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
           color: var(--muted); font-weight: 600; }
.k-value { font-size: 24px; font-weight: 700; margin: 6px 0 2px;
           font-variant-numeric: tabular-nums; }
.k-foot { font-size: 12px; color: var(--muted); }
.k-info { cursor: help; color: var(--muted); font-size: 11px; }
.split { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.donut-wrap { position: relative; height: 200px; }
.chart-wrap { position: relative; height: 220px; }
.legend { list-style: none; margin: 12px 0 0; padding: 0; font-size: 12px; }
.legend li { display: flex; align-items: center; gap: 8px; padding: 3px 0; }
.legend i { width: 10px; height: 10px; border-radius: 3px; flex: none; }
.legend b { margin-left: auto; font-variant-numeric: tabular-nums; }
.sectors { list-style: none; margin: 0; padding: 0; font-size: 13px; }
.sectors li { display: flex; align-items: center; gap: 10px; padding: 5px 0; }
.sectors .s-name { min-width: 120px; }
.sectors .s-bar { flex: 1; height: 8px; background: var(--soft);
                  border-radius: 999px; overflow: hidden; }
.sectors .s-bar i { display: block; height: 100%; background: var(--accent); }
.sectors .s-val { font-variant-numeric: tabular-nums; min-width: 52px;
                  text-align: right; }
.table-scroll { overflow-x: auto; }
table.holdings { width: 100%; border-collapse: collapse; font-size: 13px;
                 font-variant-numeric: tabular-nums; }
table.holdings th { text-align: left; padding: 6px 10px;
                    border-bottom: 2px solid var(--line); color: var(--muted);
                    font-weight: 600; font-size: 11px; text-transform: uppercase;
                    letter-spacing: .04em; }
table.holdings td { padding: 7px 10px; border-bottom: 1px solid var(--line);
                    vertical-align: top; }
table.holdings .right, .right { text-align: right; }
table.holdings tr.total-row td { font-weight: 700; border-top: 2px solid var(--line);
                                 border-bottom: none; }
.pos { color: var(--pos); font-weight: 600; }
.neg { color: var(--neg); font-weight: 600; }
button.action, .action { font: inherit; padding: 8px 14px; border: 1px solid var(--accent);
        border-radius: 6px; background: var(--accent); color: #fff;
        cursor: pointer; font-weight: 500; }
button.action.alt, .action.alt { background: var(--card); color: var(--fg);
        border-color: var(--line); }
button.action.mini { padding: 4px 8px; font-size: 12px; }
button.action[disabled] { opacity: .55; cursor: not-allowed; }
button.linklike { font: inherit; background: none; border: none; padding: 0;
        color: var(--accent); cursor: pointer; text-align: left;
        font-weight: 600; }
button.linklike:hover { text-decoration: underline; }
.add-form { border: 1px dashed var(--line); border-radius: 10px;
            padding: 14px 16px; margin-bottom: 16px; }
.form-row { display: flex; gap: 12px; align-items: end; flex-wrap: wrap;
            margin-bottom: 10px; }
.form-row label { display: flex; flex-direction: column; gap: 4px;
                  font-size: 12px; color: var(--muted); }
input { padding: 7px 9px; border: 1px solid var(--line); border-radius: 6px;
        background: var(--card); color: var(--fg); font: inherit; min-width: 160px; }
#mf-add-search { min-width: 340px; }
.search-results { max-height: 220px; overflow-y: auto; margin-bottom: 10px; }
.search-results button { display: block; width: 100%; text-align: left;
        font: inherit; background: var(--card); border: 1px solid var(--line);
        border-radius: 6px; padding: 7px 10px; margin-bottom: 4px;
        cursor: pointer; color: var(--fg); font-size: 13px; }
.search-results button:hover { background: var(--soft); }
.banner { padding: 10px 14px; border-radius: 8px; font-size: 13px;
          margin-bottom: 12px; }
.banner.info { background: var(--accent-soft); border: 1px solid var(--accent-line); }
.banner.warn { background: var(--warn-bg); border: 1px solid var(--warn-line);
               color: var(--warn-fg); }
.banner.error { background: var(--neg-bg, #fdecec); border: 1px solid var(--neg);
                color: var(--neg); }
.finding { border-left: 3px solid var(--line); padding: 8px 0 8px 12px;
           margin: 10px 0; }
.finding.review { border-left-color: var(--warn-fg, #b06d1a); }
.finding.note { border-left-color: var(--accent); }
.finding.good { border-left-color: var(--pos); }
.finding .f-head { display: flex; align-items: baseline; gap: 8px;
                   flex-wrap: wrap; font-size: 13.5px; }
.finding .f-detail { color: var(--muted); font-size: 12.5px; margin-top: 4px;
                     line-height: 1.55; }
.chip.mini.review { background: var(--warn-bg); color: var(--warn-fg);
                    border-color: var(--warn-line); }
.chip.mini.note { background: var(--accent-soft); color: var(--accent);
                  border-color: var(--accent-line); }
.chip.mini.good { background: var(--pos-bg); color: var(--pos);
                  border-color: var(--pos-line); }
ul.tight { margin: 8px 0 0; padding-left: 18px; }
ul.tight li { margin: 3px 0; }
@media (max-width: 900px) {
  .kpis { grid-template-columns: repeat(2, 1fr); }
  .split { grid-template-columns: 1fr; }
}
""" + topnav_css()


_SCRIPT = r"""
<script>
(function () {
  'use strict';

  var assetChart = null, amcChart = null, navChart = null;
  var picked = null;
  var searchTimer = null;
  var inFlight = false;

  function boot() {
    var el = document.getElementById('mf-bootstrap');
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  function group(n) {
    var s = String(Math.abs(Math.round(n)));
    if (s.length <= 3) return s;
    var tail = s.slice(-3), head = s.slice(0, -3), parts = [];
    while (head.length > 2) { parts.unshift(head.slice(-2)); head = head.slice(0, -2); }
    if (head) parts.unshift(head);
    parts.push(tail);
    return parts.join(',');
  }
  function inr(v, signed) {
    v = Number(v) || 0;
    return '\u20B9' + (v < 0 ? '-' : (signed ? '+' : '')) + group(v);
  }
  function inrC(v, signed) {
    v = Number(v) || 0;
    var sign = v < 0 ? '-' : (signed ? '+' : ''), a = Math.abs(v);
    if (a >= 1e7) return '\u20B9' + sign + (a / 1e7).toFixed(2) + ' Cr';
    if (a >= 1e5) return '\u20B9' + sign + (a / 1e5).toFixed(2) + ' L';
    return '\u20B9' + sign + group(a);
  }
  function cls(v) { return (Number(v) || 0) >= 0 ? 'pos' : 'neg'; }
  function syncedLabel(stamp) {
    if (!stamp) return 'Never synced';
    return 'Coin synced ' + String(stamp).slice(0, 16).replace('T', ' ');
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function set(id, text, klass, title) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    if (klass !== undefined && klass !== null) {
      el.classList.remove('pos', 'neg');
      if (klass) el.classList.add(klass);
    }
    if (title) el.setAttribute('title', title);
  }
  function showError(msg) {
    var box = document.getElementById('mf-error');
    if (!box) return;
    if (!msg) { box.style.display = 'none'; return; }
    box.textContent = msg;
    box.style.display = 'block';
  }
  function cssVar(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
  }

  var PALETTE = ['#2f5fe0', '#f0913a', '#41a05e', '#b8558f', '#8a7be0',
                 '#d9534f', '#3aa7bd', '#c9a227'];

  // ── KPIs + charts ────────────────────────────────────────
  function applySummary(d) {
    if (!d) return;
    var a = d.allocation || {};
    set('mf-kpi-current', inrC(d.current_value), null, inr(d.current_value));
    set('mf-kpi-invested', inrC(d.invested_value), null, inr(d.invested_value));
    set('mf-kpi-pnl', inrC(d.pnl, true), cls(d.pnl), inr(d.pnl, true));
    set('mf-kpi-pnl-pct', (Number(d.pnl_pct) || 0).toFixed(2) + '%', cls(d.pnl_pct));
    set('mf-kpi-schemes', String(a.scheme_count || 0));
    set('mf-kpi-sip', inrC(d.monthly_sip_outflow));
    set('mf-kpi-sip-count', String(d.active_sip_count || 0));
    set('mf-kpi-sip-paused', String(d.paused_sip_count || 0));

    drawDonut('mf-chart-asset', 'mf-asset-legend', a.by_asset_class,
              function (c) { assetChart = c; }, assetChart);
    drawDonut('mf-chart-amc', 'mf-amc-legend', a.by_amc,
              function (c) { amcChart = c; }, amcChart);
    drawBars('mf-plan-list', a.by_plan);
    drawBars('mf-broker-list', a.by_broker);
  }

  function drawDonut(canvasId, legendId, rows, keep, existing) {
    var canvas = document.getElementById(canvasId);
    var legend = document.getElementById(legendId);
    if (!canvas || typeof Chart === 'undefined') return;
    rows = (rows || []).filter(function (r) { return Number(r.value) > 0; });
    if (!rows.length) {
      if (existing) { existing.destroy(); keep(null); }
      if (legend) legend.innerHTML = '<li class="muted">Nothing to chart yet.</li>';
      return;
    }
    var labels = rows.map(function (r) { return r.label; });
    var data = rows.map(function (r) { return Number(r.value) || 0; });
    if (legend) {
      legend.innerHTML = rows.map(function (r, i) {
        return '<li><i style="background:' + PALETTE[i % PALETTE.length] + '"></i>' +
          esc(r.label) + '<b>' + inrC(r.value) + ' \u00B7 ' +
          (Number(r.weight_pct) || 0).toFixed(1) + '%</b></li>';
      }).join('');
    }
    if (existing) {
      existing.data.labels = labels;
      existing.data.datasets[0].data = data;
      existing.update();
      return;
    }
    keep(new Chart(canvas.getContext('2d'), {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [{
          data: data,
          backgroundColor: labels.map(function (_, i) { return PALETTE[i % PALETTE.length]; }),
          borderColor: cssVar('--card') || '#fff',
          borderWidth: 3,
          hoverOffset: 6
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '62%',
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: function (ctx) {
            return ' ' + ctx.label + ': ' + inr(ctx.parsed); } } }
        }
      }
    }));
  }

  function drawBars(listId, rows) {
    var el = document.getElementById(listId);
    if (!el) return;
    rows = rows || [];
    if (!rows.length) {
      el.innerHTML = '<li class="muted">Nothing here yet.</li>';
      return;
    }
    el.innerHTML = rows.map(function (r) {
      var pct = Math.min(100, Number(r.weight_pct) || 0);
      return '<li><span class="s-name" title="' + esc(r.label) + '">' +
        esc(r.label) + '</span>' +
        '<span class="s-bar"><i style="width:' + pct.toFixed(1) + '%"></i></span>' +
        '<span class="s-val">' + pct.toFixed(1) + '%</span></li>';
    }).join('');
  }

  // ── NAV history ──────────────────────────────────────────
  function loadNavChart(scheme, fundName) {
    var label = document.getElementById('mf-nav-chart-label');
    if (label) label.textContent = '\u2014 ' + fundName;
    fetch('/api/mf/nav_history?scheme=' + encodeURIComponent(scheme))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var canvas = document.getElementById('mf-chart-nav');
        if (!canvas || typeof Chart === 'undefined') return;
        if (!d.ok || !d.points || !d.points.length) {
          if (label) {
            label.textContent = '\u2014 ' + fundName +
              ' (no NAV history available)';
          }
          if (navChart) { navChart.destroy(); navChart = null; }
          return;
        }
        var labels = d.points.map(function (p) { return p.date; });
        var data = d.points.map(function (p) { return p.nav; });
        if (navChart) {
          navChart.data.labels = labels;
          navChart.data.datasets[0].data = data;
          navChart.data.datasets[0].label = fundName;
          navChart.update();
          return;
        }
        navChart = new Chart(canvas.getContext('2d'), {
          type: 'line',
          data: {
            labels: labels,
            datasets: [{
              label: fundName, data: data, borderColor: PALETTE[0],
              backgroundColor: 'rgba(47,95,224,.12)', fill: true,
              pointRadius: 0, borderWidth: 2, tension: .15
            }]
          },
          options: {
            responsive: true, maintainAspectRatio: false,
            scales: { x: { ticks: { maxTicksLimit: 8 } } },
            plugins: { legend: { display: false } }
          }
        });
      })
      .catch(function (e) { showError('NAV history failed: ' + e.message); });
  }
  window.loadNavChart = loadNavChart;

  // ── Scheme picker ────────────────────────────────────────
  function searchSchemes() {
    var input = document.getElementById('mf-add-search');
    var host = document.getElementById('mf-search-results');
    if (!input || !host) return;
    var q = input.value.trim();
    if (searchTimer) clearTimeout(searchTimer);
    if (q.length < 3) { host.innerHTML = ''; return; }
    searchTimer = setTimeout(function () {
      fetch('/api/mf/search?q=' + encodeURIComponent(q))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var rows = (d && d.results) || [];
          if (!rows.length) {
            host.innerHTML = '<div class="muted small">No scheme matched. ' +
              'Refresh from Coin if the catalogue has not downloaded yet.</div>';
            return;
          }
          host.innerHTML = rows.map(function (r) {
            return '<button type="button" data-code="' + esc(r.scheme_code) +
              '" data-name="' + esc(r.name) + '">' + esc(r.name) +
              '<span class="small"> \u00B7 NAV ' +
              (Number(r.nav) || 0).toFixed(4) + ' \u00B7 ' +
              esc(r.scheme_code) + '</span></button>';
          }).join('');
          Array.prototype.forEach.call(host.querySelectorAll('button'),
            function (btn) {
              btn.addEventListener('click', function () {
                pick(btn.getAttribute('data-code'), btn.getAttribute('data-name'));
              });
            });
        })
        .catch(function (e) { showError('Scheme search failed: ' + e.message); });
    }, 250);
  }
  window.searchSchemes = searchSchemes;

  function pick(code, name) {
    picked = { code: code, name: name };
    var note = document.getElementById('mf-add-picked');
    if (note) note.textContent = 'Selected: ' + name + ' (' + code + ')';
    var host = document.getElementById('mf-search-results');
    if (host) host.innerHTML = '';
    var input = document.getElementById('mf-add-search');
    if (input) input.value = name;
    var btn = document.getElementById('mf-add-btn');
    if (btn) btn.disabled = false;
  }

  function post(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {})
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
        return d;
      });
    });
  }

  function addExternal() {
    if (!picked) { showError('Pick a scheme from the search results first.'); return; }
    var units = Number((document.getElementById('mf-add-units') || {}).value);
    var nav = Number((document.getElementById('mf-add-nav') || {}).value);
    var broker = ((document.getElementById('mf-add-broker') || {}).value || '').trim();
    var folio = ((document.getElementById('mf-add-folio') || {}).value || '').trim();
    var sip = Number((document.getElementById('mf-add-sip') || {}).value) || 0;
    if (!(units > 0) || !(nav > 0)) {
      showError('Units and average NAV must both be positive numbers.');
      return;
    }
    if (!broker) { showError('Enter the broker holding this fund.'); return; }
    showError('');
    post('/api/mf/external/add', {
      scheme_code: picked.code, fund: picked.name, units: units,
      avg_nav: nav, broker: broker, folio: folio, sip_amount: sip
    }).then(function () {
      ['mf-add-units', 'mf-add-nav', 'mf-add-folio', 'mf-add-search']
        .forEach(function (id) {
          var el = document.getElementById(id);
          if (el) el.value = '';
        });
      var sipEl = document.getElementById('mf-add-sip');
      if (sipEl) sipEl.value = '0';
      picked = null;
      var note = document.getElementById('mf-add-picked');
      if (note) note.textContent = 'No scheme selected yet.';
      var btn = document.getElementById('mf-add-btn');
      if (btn) btn.disabled = true;
      refreshMf(false);
    }).catch(function (e) { showError('Could not add fund: ' + e.message); });
  }
  window.addExternal = addExternal;

  function editExternal(id, units, nav) {
    var newUnits = window.prompt('Units held:', units);
    if (newUnits === null) return;
    var newNav = window.prompt('Average NAV:', nav);
    if (newNav === null) return;
    if (!(Number(newUnits) > 0) || !(Number(newNav) > 0)) {
      showError('Units and average NAV must both be positive numbers.');
      return;
    }
    post('/api/mf/external/' + id + '/edit',
         { units: Number(newUnits), avg_nav: Number(newNav) })
      .then(function () { refreshMf(false); })
      .catch(function (e) { showError('Could not update: ' + e.message); });
  }
  window.editExternal = editExternal;

  function removeExternal(id) {
    if (!window.confirm('Stop tracking this externally-held fund?')) return;
    post('/api/mf/external/' + id + '/remove', {})
      .then(function () { refreshMf(false); })
      .catch(function (e) { showError('Could not remove: ' + e.message); });
  }
  window.removeExternal = removeExternal;

  // ── Sections ─────────────────────────────────────────────
  function refreshMf(live) {
    if (inFlight) return;
    inFlight = true;
    var btn = document.getElementById('mf-refresh');
    if (btn && live) { btn.disabled = true; btn.textContent = 'Refreshing\u2026'; }
    var chip = document.getElementById('mf-live-chip');
    if (chip && live) chip.textContent = 'Fetching from Coin\u2026';

    fetch('/api/mf/sections' + (live ? '?live=1' : ''))
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        showError('');
        var host = document.getElementById('mf-sections');
        if (host) host.innerHTML = d.html || '';
        applySummary(d.summary);
        if (chip) chip.textContent = syncedLabel((d.summary || {}).synced_at);
      })
      .catch(function (e) {
        showError('Could not load the mutual-fund book: ' + e.message);
      })
      .then(function () {
        inFlight = false;
        if (btn) { btn.disabled = false; btn.textContent = 'Refresh from Coin'; }
      });
  }
  window.refreshMf = refreshMf;

  document.addEventListener('DOMContentLoaded', function () {
    applySummary(boot());
    refreshMf(false);
    window.addEventListener('dashthemechange', function () {
      [assetChart, amcChart].forEach(function (c) {
        if (c) {
          c.data.datasets[0].borderColor = cssVar('--card') || '#fff';
          c.update();
        }
      });
    });
  });
})();
</script>
"""


__all__ = [
    "render_mf_page",
    "render_mf_sections_json",
    "render_mf_search_json",
    "render_mf_nav_history_json",
]
