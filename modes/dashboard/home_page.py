"""Dashboard home — the one screen that answers "where do I stand?".

Layout (2026-11 redesign, in Rupevo's Material 3 style):

    page head       IST date, title, one-line lede and a status row: NSE / US
                    market, USD/INR, Zerodha connection, refresh, auto-refresh
    connect card    only while Zerodha is not connected: inline login (TOTP /
                    paste), with a Rupevo pointer for visitors without an account
    kpi tiles       net worth, unrealised P&L, India book, mutual funds, US book
    holdings        one card, tabbed: India, mutual funds, US, swing tracker
    allocation      book-mix doughnut + Indian sector weights
    realised        swing India, US, intraday, total
    ask AI          collapsed prompt builder
    footer          the net-worth rule and a pointer to Rupevo

Page navigation (and Rupevo) lives in the shared app shell from `nav.py`,
so HQ no longer repeats it as tiles. Book boundaries are enforced in
`modes/dashboard/home_summary.py`:
net worth = Zerodha holdings + US book.  The India swing open book is
a *tracking* ledger over shares that already sit inside the Zerodha
holdings, so it is shown separately and never summed into net worth.

Cost discipline
---------------
The first render is snapshot-only: SQLite reads plus already-cached
quotes, no broker or yfinance round-trips (this page was originally
kept empty for exactly that reason).  After first paint the browser
calls `/api/home/summary?live=1` once; auto-refresh is opt-in and
remembered in `localStorage`.
"""

from __future__ import annotations

import html
import json

from modes.dashboard.home_summary import build_summary
from modes.dashboard.icons import icon
from modes.dashboard.nav import render_topnav
from modes.dashboard.rupevo import rupevo_footer_link, rupevo_login_hint
from modes.dashboard.theme import (
    theme_boot_script,
    theme_css,
    theme_overrides_css,
)


# ── Formatting ───────────────────────────────────────────────────

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


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _inr(value, *, signed: bool = False) -> str:
    v = _num(value)
    sign = "-" if v < 0 else ("+" if signed else "")
    return f"₹{sign}{_grouped(round(abs(v)))}"


def _inr_compact(value, *, signed: bool = False) -> str:
    v = _num(value)
    sign = "-" if v < 0 else ("+" if signed else "")
    a = abs(v)
    if a >= 1e7:
        return f"₹{sign}{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"₹{sign}{a / 1e5:.2f} L"
    return f"₹{sign}{_grouped(round(a))}"


def _usd(value, *, signed: bool = False) -> str:
    v = _num(value)
    sign = "-" if v < 0 else ("+" if signed else "")
    return f"${sign}{abs(v):,.2f}"


def _pct(value) -> str:
    return f"{_num(value):+.2f}%"


def _cls(value) -> str:
    return "pos" if _num(value) >= 0 else "neg"


# ── Fragments ────────────────────────────────────────────────────

def _market_chip(is_open: bool, label: str) -> str:
    dot = '<span class="dot live"></span>' if is_open else '<span class="dot"></span>'
    return (f'<span class="t-chip{" pos" if is_open else ""}" data-privacy-public>'
            f'{dot}{html.escape(label)}</span>')


def _page_head(data: dict) -> str:
    mk = data["market"]
    fx = data["fx"]
    rate = _num(fx.get("rate"))
    fx_chip = (
        f'<span class="t-chip" data-privacy-public title="Source: {html.escape(str(fx.get("source", "")))}">'
        f'USD/INR&nbsp;<strong>{rate:,.2f}</strong></span>'
        if rate > 0 else
        '<span class="t-chip warn">USD/INR unavailable</span>'
    )
    # Connected: a compact chip here. Not connected: a warning chip that
    # points at the login card rendered under the head.
    zerodha = (
        _connect_card(data) if data["auth"]["valid"] else
        '<a class="t-chip warn" href="#connect-card"><span class="dot"></span>'
        'Zerodha not connected</a>'
    )
    return f"""
<header class="page-head home-head">
  <div class="eyebrow">{html.escape(mk['ist_date'])} &middot; {html.escape(mk['ist'])} IST</div>
  <h1>Overview</h1>
  <p class="lede">Every book in one place: Zerodha holdings, mutual funds, the US
     book and the India swing tracker.</p>
  <div class="status-row">
    {_market_chip(mk['india_open'], mk['india_label'])}
    {_market_chip(mk['us_open'], mk['us_label'])}
    {fx_chip}
    {zerodha}
    <span class="spacer"></span>
    <span class="stamp" id="home-stamp">Snapshot &middot; not yet refreshed</span>
    <button id="home-refresh" class="text-btn" type="button" onclick="refreshHome(true)"
            title="Fetch live prices for every book">{icon("refresh", size=18)}<span
            class="btn-label">Refresh live prices</span></button>
    <label class="switch" title="Re-fetch live prices every 60 seconds">
      <input type="checkbox" id="home-auto"> Auto refresh
    </label>
  </div>
</header>
"""


def _info(tip: str) -> str:
    return (f'<span class="k-info" role="img" title="{html.escape(tip)}" '
            f'aria-label="{html.escape(tip)}">{icon("info", size=16)}</span>')


def _kpis(data: dict) -> str:
    t = data["totals"]
    ind, us = data["india"], data["us"]
    mf = data.get("mf") or {}
    net_tip = ("Zerodha holdings + mutual funds + US book, converted at the live "
               "USD/INR rate. The India swing tracker is excluded on purpose — "
               "those shares already sit inside the Zerodha holdings.")
    mf_tip = ("Coin units plus funds you track at other brokers. Valued on the "
              "last published NAV"
              + (f", as of {mf.get('nav_as_of')}" if mf.get("nav_as_of") else "")
              + " — mutual funds have no intraday price.")
    # Until yfinance answers, US positions are held at cost, so a
    # "+$0.00" P&L would be fiction. Show a dash instead.
    if us.get("live"):
        us_pnl = (f'<span class="{_cls(us["pnl_usd"])}" id="kpi-us-pnl">'
                  f'{_usd(us["pnl_usd"], signed=True)} ({_pct(us["pnl_pct"])})</span>')
        unreal_attr = ""
    else:
        us_pnl = ('<span class="muted" id="kpi-us-pnl" '
                  'title="Waiting for live US prices">&mdash;</span>')
        unreal_attr = ' title="US positions are held at cost until live prices load"'
    return f"""
<section class="kpis" aria-label="Totals">
  <article class="kpi accent">
    <div class="k-label">Net worth {_info(net_tip)}</div>
    <div class="k-value" id="kpi-networth" title="{_inr(t['net_worth_inr'])}">{_inr_compact(t['net_worth_inr'])}</div>
    <div class="k-foot"><span id="kpi-networth-split">India {t['india_share_pct']:.0f}%
      &middot; MF {t.get('mf_share_pct', 0):.0f}%
      &middot; US {t['us_share_pct']:.0f}%</span></div>
  </article>

  <article class="kpi"{unreal_attr}>
    <div class="k-label">Unrealised P&amp;L</div>
    <div class="k-value {_cls(t['unrealised_inr'])}" id="kpi-unrealised"
         title="{_inr(t['unrealised_inr'], signed=True)}">{_inr_compact(t['unrealised_inr'], signed=True)}</div>
    <div class="k-foot"><span class="{_cls(t['unrealised_pct'])}"
      id="kpi-unrealised-pct">{_pct(t['unrealised_pct'])}</span>
      on {_inr_compact(t['invested_inr'])} invested</div>
  </article>

  <article class="kpi">
    <div class="k-label">India &middot; Zerodha</div>
    <div class="k-value" id="kpi-india" title="{_inr(ind['current'])}">{_inr_compact(ind['current'])}</div>
    <div class="k-foot"><span class="{_cls(ind['pnl'])}" id="kpi-india-pnl">{_inr_compact(ind['pnl'], signed=True)}
      ({_pct(ind['pnl_pct'])})</span> &middot; <span id="kpi-india-count">{ind['holdings']}</span> holdings</div>
  </article>

  <article class="kpi">
    <div class="k-label">Mutual funds {_info(mf_tip)}</div>
    <div class="k-value" id="kpi-mf" title="{_inr(mf.get('current', 0))}">{_inr_compact(mf.get('current', 0))}</div>
    <div class="k-foot"><span class="{_cls(mf.get('pnl', 0))}" id="kpi-mf-pnl">{_inr_compact(mf.get('pnl', 0), signed=True)}
      ({_pct(mf.get('pnl_pct', 0))})</span> &middot; <span id="kpi-mf-count">{mf.get('schemes', 0)}</span> schemes</div>
  </article>

  <article class="kpi">
    <div class="k-label">US book</div>
    <div class="k-value" id="kpi-us">{_usd(us['current_usd'])}</div>
    <div class="k-foot">{us_pnl} &middot; <span id="kpi-us-count">{us['positions']}</span> positions</div>
  </article>
</section>
"""


def _connect_card(data: dict) -> str:
    """Zerodha status: a chip for the page head when connected, otherwise the
    full login card. Both carry `id="connect-card"`; the page script reloads
    when a live refresh finds the token expired under an `ok` chip."""
    auth = data["auth"]
    if auth["valid"]:
        return (
            '<span class="t-chip pos connect ok" id="connect-card" '
            f'title="Token valid for {html.escape(auth["token_date"])}; '
            'Kite expires it at midnight IST.">'
            '<span class="dot"></span>Zerodha connected'
            '<a class="chip-link" href="/login">Re-login</a></span>'
        )

    reason = {
        "missing": "No access token saved yet.",
        "expired": "Yesterday's token has expired.",
        "rejected": "Zerodha rejected the saved token (session killed, or the API key changed).",
        "unreadable": "The saved token file could not be read.",
    }.get(auth["reason"], "Login required.")

    quick = ""
    if auth["has_saved_creds"]:
        quick = f"""
    <form class="login-form" method="post" action="/api/login_assisted">
      <input type="hidden" name="next" value="/">
      <label>Authenticator code for <strong data-private>{html.escape(auth['user_id'])}</strong></label>
      <div class="t-row">
        <input type="text" name="otp" required placeholder="000000" pattern="[0-9]{{6}}"
               maxlength="6" inputmode="numeric" autocomplete="one-time-code"
               class="otp" aria-label="Six digit authenticator code">
        <button class="action" type="submit">Connect</button>
      </div>
      <p class="hint">User ID and password come from <code>.env</code>; only the
         rotating 6-digit code is typed here.</p>
    </form>"""

    login_link = (
        f'<a href="{html.escape(auth["login_url"])}" target="_blank" rel="noopener">'
        f'Open Zerodha login &#8599;</a>'
        if auth["login_url"] else
        '<span class="neg">ZERODHA_API_KEY missing in <code>.env</code></span>'
    )

    return f"""
<section class="card connect bad" id="connect-card">
  <div class="card-head">
    <h2>Connect Zerodha</h2>
    <span class="t-chip warn"><span class="dot"></span>Not connected</span>
  </div>
  <p class="connect-why">{html.escape(reason)} Live Indian prices, holdings refresh
     and analyse runs stay disabled until you log in.</p>
  <div class="connect-grid">
    {quick}
    <form class="login-form" method="post" action="/api/login_submit">
      <input type="hidden" name="next" value="/">
      <label>Paste the redirect URL</label>
      <div class="t-row">
        <input type="text" name="redirect_url" required
               placeholder="http://localhost:8080/?request_token=…"
               aria-label="Zerodha redirect URL">
        <button class="action alt" type="submit">Submit</button>
      </div>
      <p class="hint">{login_link} → log in → copy the full address-bar URL back here.</p>
    </form>
  </div>
  {rupevo_login_hint()}
</section>
"""


def _allocation(data: dict) -> str:
    sectors = (data["india"].get("sectors") or [])[:6]
    rows = [
        f'<li><span class="s-name" title="{html.escape(s["sector"] or "OTHER")}">'
        f'{html.escape(s["sector"] or "OTHER")}</span>'
        f'<span class="s-bar"><i style="width:{min(100.0, _num(s["weight_pct"])):.1f}%"></i></span>'
        f'<span class="s-val num">{_num(s["weight_pct"]):.1f}%</span></li>'
        for s in sectors
    ]
    if not rows:
        rows.append('<li class="muted">Run a portfolio analysis to see sector weights.</li>')

    return f"""
<section class="card alloc">
  <div class="card-head"><h2>Allocation</h2><span class="hint">where the money sits</span></div>
  <div class="donut-wrap"><canvas id="chart-geo" height="170" data-privacy-public></canvas></div>
  <ul class="legend" id="geo-legend"></ul>
  <h3 class="sub-head">Indian sectors</h3>
  <ul class="sectors">{''.join(rows)}</ul>
</section>
"""


def _rows_html(rows, *, currency: str, href: str) -> str:
    if not rows:
        return '<tr><td colspan="4" class="muted pad">Nothing here yet.</td></tr>'
    out = []
    for r in rows:
        money = _usd(r["value"]) if currency == "usd" else _inr(r["value"])
        if r.get("priced", True):
            pnl = (_usd(r["pnl"], signed=True) if currency == "usd"
                   else _inr(r["pnl"], signed=True))
            pnl_cell = (f'<td class="num right {_cls(r["pnl"])}">{pnl}'
                        f'<span class="sub-pct">{_pct(r["pnl_pct"])}</span></td>')
        else:
            # No live quote — the value column is just entry price, so a
            # "+0.00" P&L would be a made-up number.
            pnl_cell = ('<td class="num right muted" '
                        'title="No live price yet — value shown at entry cost">'
                        '&mdash;</td>')
        out.append(
            f'<tr><td><a href="{href}{html.escape(str(r["symbol"]))}">'
            f'{html.escape(str(r["symbol"]))}</a></td>'
            f'<td class="num right">{_num(r["qty"]):,.0f}</td>'
            f'<td class="num right">{money}</td>'
            f'{pnl_cell}</tr>'
        )
    return "".join(out)


def _mf_rows_html(rows) -> str:
    """Fund rows for the home mini-table.

    Funds get their own renderer because the row key is a scheme name,
    not a ticker, and units are fractional.
    """
    if not rows:
        return ('<tr><td colspan="4" class="muted pad">'
                'No mutual funds tracked yet.</td></tr>')
    out = []
    for r in rows:
        split = (f'<span class="sub-pct">{int(r["brokers"])} brokers</span>'
                 if int(r.get("brokers") or 1) > 1 else "")
        if r.get("priced", True):
            pnl_cell = (f'<td class="num right {_cls(r["pnl"])}">'
                        f'{_inr(r["pnl"], signed=True)}'
                        f'<span class="sub-pct">{_pct(r["pnl_pct"])}</span></td>')
        else:
            pnl_cell = ('<td class="num right muted" '
                        'title="No NAV resolved — shown at cost">&mdash;</td>')
        out.append(
            f'<tr><td><a href="/mf" title="{html.escape(str(r["fund"]))}">'
            f'{html.escape(str(r["fund"])[:38])}</a>{split}</td>'
            f'<td class="num right">{_num(r["units"]):,.2f}</td>'
            f'<td class="num right">{_inr(r["value"])}</td>'
            f'{pnl_cell}</tr>'
        )
    return "".join(out)


def _books(data: dict) -> str:
    ind, us, sw = data["india"], data["us"], data["swing_india"]
    mf = data.get("mf") or {}

    if not ind.get("available"):
        stale = '<span class="t-chip warn">no analysis run yet</span>'
    elif (ind.get("age_days") or 0) > 1:
        stale = (f'<span class="t-chip warn" title="Run Analyse on the Portfolio '
                 f'page to refresh">snapshot {ind["age_days"]}d old</span>')
    else:
        stale = ""

    swing_tip = ("These shares are already counted inside the Zerodha holdings, "
                 "so this book is never added to net worth.")
    swing_unreal = (_inr_compact(sw['unrealised'], signed=True)
                    if sw.get('live') else 'no live price')

    mf_stamp = (f'<span class="t-chip" title="Funds are marked to the last '
                f'published NAV, not a live price.">NAV '
                f'{html.escape(str(mf.get("nav_as_of") or ""))}</span>'
                if mf.get("nav_as_of") else
                '<span class="t-chip warn">no NAV yet</span>')
    mf_foot = (f'{mf.get("schemes", 0)} schemes &middot; '
               f'{mf.get("external_count", 0)} tracked outside Coin &middot; '
               f'{_inr_compact(mf.get("monthly_sip", 0))}/mo SIP')

    def head(first: str) -> str:
        return (f'<thead><tr><th>{first}</th><th class="right">'
                f'{"Units" if first == "Fund" else "Qty"}</th>'
                '<th class="right">Value</th><th class="right">P&amp;L</th></tr></thead>')

    panels = [
        ("india", "India", "/portfolio", "Open Portfolio",
         stale or '<span>Zerodha holdings, largest first</span>',
         f'{head("Symbol")}<tbody id="book-india">'
         f'{_rows_html(ind.get("top") or [], currency="inr", href="/portfolio/")}</tbody>'),
        ("mf", "Mutual funds", "/mf", "Open Funds",
         f'{mf_stamp}<span id="mf-foot">{mf_foot}</span>',
         f'{head("Fund")}<tbody id="book-mf">{_mf_rows_html(mf.get("rows") or [])}</tbody>'),
        ("us", "US", "/us", "Open US",
         '<span class="t-chip info" title="Long-term investment holdings, not a '
         'short-term trading book.">long-term</span><span>Investment holdings</span>',
         f'{head("Symbol")}<tbody id="book-us">'
         f'{_rows_html(us.get("rows") or [], currency="usd", href="/us/")}</tbody>'),
        ("swing", "Swing tracker", "/swing", "Open Swing",
         f'<span class="t-chip" title="{html.escape(swing_tip)}">not in net worth</span>'
         f'<span id="swing-foot">{sw["positions"]} open &middot; {swing_unreal} unrealised'
         f' &middot; {sw["watchlist"]} on watchlist</span>',
         f'{head("Symbol")}<tbody id="book-swing">'
         f'{_rows_html(sw.get("rows") or [], currency="inr", href="/swing/")}</tbody>'),
    ]
    tabs, bodies = [], []
    for index, (key, label, href, open_label, meta, table) in enumerate(panels):
        selected = index == 0
        tabs.append(
            f'<button class="book-tab" type="button" role="tab" id="book-tab-{key}" '
            f'data-book="{key}" aria-controls="book-panel-{key}" '
            f'aria-selected="{"true" if selected else "false"}" '
            f'tabindex="{0 if selected else -1}">{label}</button>'
        )
        bodies.append(
            f'<div class="book-panel" role="tabpanel" id="book-panel-{key}" '
            f'aria-labelledby="book-tab-{key}"{"" if selected else " hidden"}>'
            f'<div class="book-meta">{meta}<span class="spacer"></span>'
            f'<a class="text-btn" href="{href}">{open_label} &#8594;</a></div>'
            f'<table class="mini">{table}</table></div>'
        )
    return f"""
<section class="card books" aria-label="Holdings by book">
  <div class="card-head"><h2>Holdings</h2><span class="hint">top positions in each book</span></div>
  <div class="book-tabs" role="tablist" aria-label="Books">{''.join(tabs)}</div>
  {''.join(bodies)}
</section>
"""


def _realised(data: dict) -> str:
    sw, us, intra = data["swing_india"], data["us"], data["intraday"]
    t = data["totals"]
    best, worst = intra.get("best_day"), intra.get("worst_day")
    intra_tip = "Current financial year, from the verified intraday tax ledger."
    if best and worst:
        intra_tip += (f" Best day {best[0]} {_inr(best[1], signed=True)};"
                      f" worst day {worst[0]} {_inr(worst[1], signed=True)}.")
    return f"""
<section class="card realised-card">
  <div class="card-head"><h2>Realised</h2><span class="hint">booked, not marked-to-market</span></div>
  <div class="facts realised">
    <div class="fact">
      <div class="fact-label">Swing &middot; India</div>
      <div class="fact-value {_cls(sw['realised_net'])}" id="r-swing">{_inr(sw['realised_net'], signed=True)}</div>
      <div class="fact-sub">{sw['closed']} closed trades, net of charges</div>
    </div>
    <div class="fact">
      <div class="fact-label">US</div>
      <div class="fact-value {_cls(us['realised_usd'])}" id="r-us">{_usd(us['realised_usd'], signed=True)}</div>
      <div class="fact-sub">{us['closed']} closed trades</div>
    </div>
    <div class="fact">
      <div class="fact-label">Intraday {_info(intra_tip)}</div>
      <div class="fact-value {_cls(intra['net_pnl'])}" id="r-intraday">{_inr(intra['net_pnl'], signed=True)}</div>
      <div class="fact-sub">{intra['trades']} trades over {intra['days']} days
        &middot; {html.escape(str(intra['window']))}</div>
    </div>
    <div class="fact total">
      <div class="fact-label">Total realised</div>
      <div class="fact-value {_cls(t['realised_inr'])}" id="r-total">{_inr(t['realised_inr'], signed=True)}</div>
      <div class="fact-sub">All books, converted to INR</div>
    </div>
  </div>
</section>
"""


def _footer() -> str:
    return (
        '<footer class="home-foot">'
        '<span>Read-only overview. The swing tracker is excluded from net worth, '
        'so Zerodha holdings are never double-counted.</span>'
        f'{rupevo_footer_link()}</footer>'
    )


# ── Page ─────────────────────────────────────────────────────────

def _safe_json(data: dict) -> str:
    """JSON for an inline <script> — angle brackets and ampersands are
    escaped so no value can close the tag early."""
    return (json.dumps(data, default=str)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def _fallback_payload(err: str) -> dict:
    from modes.dashboard.home_summary import auth_state, market_state
    return {
        "generated_at": "", "live": False, "error": err,
        "auth": auth_state(), "market": market_state(),
        "fx": {"rate": 0.0, "as_of": "", "source": "unavailable"},
        "india": {"available": False, "holdings": 0, "invested": 0.0,
                  "current": 0.0, "pnl": 0.0, "pnl_pct": 0.0, "top": [],
                  "sectors": [], "age_days": None},
        "swing_india": {"positions": 0, "invested": 0.0, "current": 0.0,
                        "unrealised": 0.0, "unrealised_pct": 0.0,
                        "realised_net": 0.0, "closed": 0, "watchlist": 0,
                        "pending_actions": 0, "rows": []},
        "us": {"positions": 0, "invested_usd": 0.0, "current_usd": 0.0,
               "pnl_usd": 0.0, "pnl_pct": 0.0, "realised_usd": 0.0,
               "closed": 0, "watchlist": 0, "rows": []},
        "mf": {"available": False, "schemes": 0, "invested": 0.0,
               "current": 0.0, "pnl": 0.0, "pnl_pct": 0.0, "nav_as_of": "",
               "rows": [], "monthly_sip": 0.0, "active_sips": 0,
               "paused_sips": 0, "external_count": 0, "unpriced": 0},
        "intraday": {"net_pnl": 0.0, "gross_pnl": 0.0, "charges": 0.0,
                     "trades": 0, "days": 0, "window": "", "best_day": None,
                     "worst_day": None},
        "totals": {"net_worth_inr": 0.0, "india_inr": 0.0, "mf_inr": 0.0,
                   "us_inr": 0.0, "invested_inr": 0.0, "unrealised_inr": 0.0,
                   "unrealised_pct": 0.0, "india_share_pct": 0.0,
                   "mf_share_pct": 0.0, "us_share_pct": 0.0,
                   "realised_inr": 0.0},
    }


def _currency_toggle_html(data: dict) -> str:
    """USD/INR switch for the dollar figures on this page.

    Shares `localStorage['us-currency']` with `/us` on purpose — a
    currency preference that flips back when you change page would be
    worse than no toggle at all.
    """
    rate = _num((data.get("fx") or {}).get("rate"))
    title = (f"Show US dollar values in rupees at {rate:,.2f}/USD"
             if rate > 0 else
             "USD/INR rate unavailable — dollar values cannot be converted")
    return (
        f'<button id="home-currency-toggle" class="cur-toggle" type="button" '
        f'onclick="toggleHomeCurrency()" title="{html.escape(title)}">'
        f'<span id="cur-usd-pill" class="cur-pill active">USD</span>'
        f'<span id="cur-inr-pill" class="cur-pill">INR</span>'
        f'</button>'
    )


def render_home_page(*, login_ok: bool = False, login_err: str = "") -> str:
    from modes.dashboard.chat_widget import chat_section_html
    from modes.dashboard.error_toast import error_toast_html, error_toast_script

    load_error = ""
    try:
        data = build_summary(live=False)
    except Exception as exc:  # one dead book must not blank the page
        load_error = str(exc)[:300]
        data = _fallback_payload(load_error)

    flash = ""
    if login_ok:
        flash = ('<div class="banner ok-flash">Zerodha login successful — '
                 'the access token is saved for today.</div>')
    elif login_err:
        flash = (f'<div class="banner error">Zerodha login failed: '
                 f'{html.escape(login_err[:300])}</div>')

    err_banner = (
        f'<div id="home-error" class="banner error">Some books could not be '
        f'loaded: {html.escape(load_error)}</div>'
        if load_error else
        '<div id="home-error" class="banner error" style="display:none"></div>'
    )
    connected = bool(data["auth"].get("valid"))

    return "".join([
        "<!doctype html><html lang='en'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Dashboard &middot; Portfolio HQ</title>",
        theme_boot_script(),
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/'
        'dist/chart.umd.min.js"></script>',
        "<style>", theme_css(), _STYLE, theme_overrides_css(),
        "</style>",
        "</head><body>",
        error_toast_html(),
        render_topnav("/", after_links=_currency_toggle_html(data)),
        "<main class='wrap'>",
        _page_head(data),
        flash,
        err_banner,
        "" if connected else _connect_card(data),
        _kpis(data),
        "<div class='home-grid'>",
        _books(data),
        _allocation(data),
        "</div>",
        _realised(data),
        chat_section_html("home"),
        _footer(),
        "</main>",
        f'<script id="home-bootstrap" type="application/json">{_safe_json(data)}</script>',
        _SCRIPT,
        error_toast_script(),
        "</body></html>",
    ])


_STYLE = r"""
/* ── Page head ────────────────────────────────────────────── */
.home-head { margin-bottom: 16px; }
.status-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 14px; }
.status-row .spacer { flex: 1; }
.status-row .stamp { font-size: 12.5px; color: var(--muted); }
.status-row .text-btn svg { margin-left: -2px; }
.status-row .switch { margin-left: 4px; }
.t-chip .chip-link { margin-left: 6px; color: inherit; font-weight: 500;
                     text-decoration: underline; text-underline-offset: 2px; }
a.t-chip.warn:hover { box-shadow: inset 0 0 0 1px currentColor; }
.banner { padding: 12px 16px; margin: 0 0 16px; font-size: 13.5px; }
.banner.ok-flash { background: var(--pos-bg); color: var(--pos); font-weight: 500; }

/* ── KPI tiles (base look from theme.py) ──────────────────── */
.kpis { display: grid; gap: 12px; margin: 0 0 16px;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
.k-info { display: inline-grid; place-items: center; color: var(--muted); cursor: help; }

/* ── Connect / login (only while Zerodha is not connected) ── */
.connect .card-head { margin-bottom: 4px; }
.connect-why { margin: 0 0 14px; font-size: 13.5px; color: var(--fg-2); }
.connect-grid { display: grid; gap: 16px;
                grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
.login-form label { display: block; margin-bottom: 6px; font-size: 12.5px;
                    font-weight: 500; color: var(--fg-2); }
.login-form .t-row { flex-wrap: nowrap; }
.login-form input[type=text] { flex: 1 1 180px; min-width: 0; }
.login-form input.otp { flex: 0 0 132px; font-size: 18px; letter-spacing: 5px;
                        text-align: center; }
.login-form .hint { margin: 8px 0 0; font-size: 12px; color: var(--muted); }

/* ── Holdings + allocation ────────────────────────────────── */
.home-grid { display: grid; gap: 16px; margin-bottom: 16px; align-items: start;
             grid-template-columns: minmax(0, 1.65fr) minmax(0, 1fr); }
.home-grid > .card { margin: 0; }
@media (max-width: 1023px) { .home-grid { grid-template-columns: minmax(0, 1fr); } }

.book-tabs { display: flex; gap: 4px; margin: 0 -8px; padding: 0 8px;
             border-bottom: 1px solid var(--line); overflow-x: auto; scrollbar-width: none; }
.book-tabs::-webkit-scrollbar { display: none; }
.book-tab { position: relative; flex: none; height: 44px; padding: 0 14px; border: 0;
            border-radius: 8px 8px 0 0; background: none; color: var(--fg-2);
            font: inherit; font-size: 14px; font-weight: 500; white-space: nowrap; cursor: pointer; }
.book-tab:hover { background: var(--soft); color: var(--fg); }
.book-tab[aria-selected="true"] { color: var(--accent); }
.book-tab[aria-selected="true"]::after { content: ""; position: absolute; left: 10px; right: 10px;
                                         bottom: 0; height: 3px; border-radius: 3px 3px 0 0;
                                         background: var(--accent); }
.book-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px;
             margin: 12px 0 4px; font-size: 12.5px; color: var(--muted); }
.book-meta .spacer { flex: 1; }
.book-meta .text-btn { margin-right: -12px; }

table.mini { width: 100%; border-collapse: collapse; font-size: 13.5px;
             font-variant-numeric: tabular-nums; }
table.mini th { text-align: left; padding: 10px 8px; font-size: 12px; font-weight: 500;
                color: var(--muted); border-bottom: 1px solid var(--line); }
table.mini td { padding: 9px 8px; border-bottom: 1px solid var(--line); }
table.mini tr:last-child td { border-bottom: 0; }
table.mini tbody tr:hover td { background: var(--card-2); }
table.mini .right { text-align: right; }
table.mini td.pad { padding: 24px 8px; text-align: center; }
table.mini a { color: var(--fg); font-weight: 500; text-decoration: none; }
table.mini a:hover { color: var(--accent); text-decoration: underline; }
table.mini .sub-pct { display: block; font-size: 12px; opacity: .85; }

.donut-wrap { position: relative; height: 170px; }
.legend { list-style: none; display: grid; gap: 6px; margin: 14px 0 0; padding: 0;
          font-size: 13px; }
.legend li { display: flex; align-items: center; gap: 8px; color: var(--fg-2); }
.legend i { width: 10px; height: 10px; border-radius: 3px; flex: none; }
.legend b { margin-left: auto; font-weight: 500; color: var(--fg);
            font-variant-numeric: tabular-nums; }
.alloc .sub-head { margin: 20px 0 10px; font-size: 14px; line-height: 20px;
                   font-weight: 500; color: var(--fg); }
.sectors { list-style: none; display: grid; gap: 8px; margin: 0; padding: 0; }
.sectors li { display: grid; grid-template-columns: minmax(0, 120px) 1fr 48px;
              align-items: center; gap: 10px; font-size: 12.5px; }
.sectors .s-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                   color: var(--fg-2); }
.sectors .s-bar { height: 6px; border-radius: 3px; background: var(--soft); overflow: hidden; }
.sectors .s-bar i { display: block; height: 100%; border-radius: 3px; background: var(--accent); }
.sectors .s-val { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }

/* ── Realised ─────────────────────────────────────────────── */
.facts { display: grid; gap: 8px; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.fact { min-width: 0; padding: 12px 14px; border-radius: var(--radius-md); background: var(--soft); }
.fact.total { background: var(--accent-soft); }
.fact-label { display: flex; align-items: center; gap: 6px; font-size: 12.5px;
              font-weight: 500; color: var(--fg-2); }
.fact-value { margin: 2px 0; font-family: var(--font-display); font-size: 20px;
              line-height: 28px; font-weight: 400; font-variant-numeric: tabular-nums; }
.fact-sub { font-size: 12px; line-height: 16px; color: var(--muted); }
@media (max-width: 719px) { .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

/* ── Footer ───────────────────────────────────────────────── */
.home-foot { display: flex; flex-wrap: wrap; gap: 4px 18px; margin-top: 24px;
             padding-top: 16px; border-top: 1px solid var(--line);
             font-size: 12.5px; line-height: 18px; color: var(--muted); }

@media (max-width: 599px) {
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  .kpis .kpi.accent { grid-column: 1 / -1; }
  .status-row .spacer { display: none; }
  .status-row .stamp { order: 10; width: 100%; }
}
"""


_SCRIPT = r"""
<script>
(function () {
  'use strict';

  var AUTO_KEY = 'homeAutoRefresh';
  var CUR_KEY = 'us-currency';   // shared with /us so the choice sticks
  var AUTO_MS = 60000;
  var timer = null;
  var inFlight = false;
  var geoChart = null;
  var lastPayload = null;
  var fxRate = 0;

  function boot() {
    var el = document.getElementById('home-bootstrap');
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  // ── formatting (mirrors the Python helpers) ──────────────
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
  function currency() {
    try { return window.localStorage.getItem(CUR_KEY) || 'USD'; }
    catch (e) { return 'USD'; }
  }
  function usd(v, signed) {
    v = Number(v) || 0;
    // In INR mode every dollar figure is converted in place, so the
    // page never mixes the two currencies in one view.
    if (currency() === 'INR' && fxRate > 0) {
      return inr(v * fxRate, signed);
    }
    return '$' + (v < 0 ? '-' : (signed ? '+' : '')) +
      Math.abs(v).toLocaleString('en-US',
        { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function syncCurrencyPills() {
    var cur = currency();
    var u = document.getElementById('cur-usd-pill');
    var i = document.getElementById('cur-inr-pill');
    if (u) u.classList.toggle('active', cur === 'USD');
    if (i) i.classList.toggle('active', cur === 'INR');
  }
  function toggleHomeCurrency() {
    if (fxRate <= 0) {
      showError('No USD/INR rate available yet, so dollar values cannot ' +
        'be converted. Refresh live prices and try again.');
      return;
    }
    var next = currency() === 'USD' ? 'INR' : 'USD';
    try { window.localStorage.setItem(CUR_KEY, next); } catch (e) {}
    syncCurrencyPills();
    apply(lastPayload);
  }
  window.toggleHomeCurrency = toggleHomeCurrency;
  function pct(v) { return (Number(v) || 0).toFixed(2) + '%'; }
  function signedPct(v) {
    v = Number(v) || 0;
    return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
  }
  function cls(v) { return (Number(v) || 0) >= 0 ? 'pos' : 'neg'; }

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

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function rowsHtml(rows, currency, href) {
    if (!rows || !rows.length) {
      return '<tr><td colspan="4" class="muted pad">Nothing here yet.</td></tr>';
    }
    return rows.map(function (r) {
      var money = currency === 'usd' ? usd(r.value) : inr(r.value);
      var pnlCell;
      if (r.priced === false) {
        pnlCell = '<td class="num right muted" ' +
          'title="No live price yet \u2014 value shown at entry cost">\u2014</td>';
      } else {
        var pnl = currency === 'usd' ? usd(r.pnl, true) : inr(r.pnl, true);
        pnlCell = '<td class="num right ' + cls(r.pnl) + '">' + pnl +
          '<span class="sub-pct">' + signedPct(r.pnl_pct) + '</span></td>';
      }
      return '<tr><td><a href="' + href + encodeURIComponent(r.symbol) + '">' +
        esc(r.symbol) + '</a></td>' +
        '<td class="num right">' + Number(r.qty || 0).toLocaleString('en-IN') + '</td>' +
        '<td class="num right">' + money + '</td>' + pnlCell + '</tr>';
    }).join('');
  }

  function mfRowsHtml(rows) {
    if (!rows || !rows.length) {
      return '<tr><td colspan="4" class="muted pad">' +
        'No mutual funds tracked yet.</td></tr>';
    }
    return rows.map(function (r) {
      var split = (Number(r.brokers) || 1) > 1
        ? '<span class="sub-pct">' + Number(r.brokers) + ' brokers</span>' : '';
      var pnlCell;
      if (r.priced === false) {
        pnlCell = '<td class="num right muted" ' +
          'title="No NAV resolved \u2014 shown at cost">\u2014</td>';
      } else {
        pnlCell = '<td class="num right ' + cls(r.pnl) + '">' + inr(r.pnl, true) +
          '<span class="sub-pct">' + signedPct(r.pnl_pct) + '</span></td>';
      }
      return '<tr><td><a href="/mf" title="' + esc(r.fund) + '">' +
        esc(String(r.fund).slice(0, 38)) + '</a>' + split + '</td>' +
        '<td class="num right">' +
        (Number(r.units) || 0).toLocaleString('en-IN',
          { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '</td>' +
        '<td class="num right">' + inr(r.value) + '</td>' + pnlCell + '</tr>';
    }).join('');
  }

  // ── render ───────────────────────────────────────────────
  function apply(d) {
    if (!d) return;
    lastPayload = d;
    fxRate = Number((d.fx || {}).rate) || 0;
    syncCurrencyPills();
    var t = d.totals || {}, ind = d.india || {}, us = d.us || {}, sw = d.swing_india || {};
    var mf = d.mf || {};

    set('kpi-networth', inrC(t.net_worth_inr), null, inr(t.net_worth_inr));
    set('kpi-networth-split', 'India ' + Math.round(t.india_share_pct || 0) +
        '% \u00B7 MF ' + Math.round(t.mf_share_pct || 0) +
        '% \u00B7 US ' + Math.round(t.us_share_pct || 0) + '%');
    set('kpi-unrealised', inrC(t.unrealised_inr, true), cls(t.unrealised_inr),
        inr(t.unrealised_inr, true));
    set('kpi-unrealised-pct', signedPct(t.unrealised_pct), cls(t.unrealised_pct));
    set('kpi-india', inrC(ind.current), null, inr(ind.current));
    set('kpi-india-pnl', inrC(ind.pnl, true) + ' (' + signedPct(ind.pnl_pct) + ')',
        cls(ind.pnl));
    set('kpi-india-count', String(ind.holdings || 0));
    set('kpi-mf', inrC(mf.current), null, inr(mf.current));
    set('kpi-mf-pnl', inrC(mf.pnl, true) + ' (' + signedPct(mf.pnl_pct) + ')',
        cls(mf.pnl));
    set('kpi-mf-count', String(mf.schemes || 0));
    set('kpi-us', usd(us.current_usd));
    var usPnl = document.getElementById('kpi-us-pnl');
    if (us.live) {
      set('kpi-us-pnl', usd(us.pnl_usd, true) + ' (' + signedPct(us.pnl_pct) + ')',
          cls(us.pnl_usd));
      if (usPnl) usPnl.classList.remove('muted');
    } else {
      set('kpi-us-pnl', '\u2014', '');
      if (usPnl) usPnl.classList.add('muted');
    }
    set('kpi-us-count', String(us.positions || 0));

    set('r-swing', inr(sw.realised_net, true), cls(sw.realised_net));
    set('r-us', usd(us.realised_usd, true), cls(us.realised_usd));
    set('r-intraday', inr((d.intraday || {}).net_pnl, true),
        cls((d.intraday || {}).net_pnl));
    set('r-total', inr(t.realised_inr, true), cls(t.realised_inr));

    var bi = document.getElementById('book-india');
    if (bi) bi.innerHTML = rowsHtml(ind.top, 'inr', '/portfolio/');
    var bu = document.getElementById('book-us');
    if (bu) bu.innerHTML = rowsHtml(us.rows, 'usd', '/us/');
    var bs = document.getElementById('book-swing');
    if (bs) bs.innerHTML = rowsHtml(sw.rows, 'inr', '/swing/');
    var bm = document.getElementById('book-mf');
    if (bm) bm.innerHTML = mfRowsHtml(mf.rows);
    set('mf-foot', (mf.schemes || 0) + ' schemes \u00B7 ' +
        (mf.external_count || 0) + ' tracked outside Coin \u00B7 ' +
        inrC(mf.monthly_sip) + '/mo SIP');
    set('swing-foot', (sw.positions || 0) + ' open \u00B7 ' +
        (sw.live ? inrC(sw.unrealised, true) : 'no live price') +
        ' unrealised \u00B7 ' + (sw.watchlist || 0) + ' on watchlist');

    drawGeo(t);

    var stamp = document.getElementById('home-stamp');
    if (stamp) {
      var when = new Date().toLocaleTimeString('en-IN', { hour12: false });
      stamp.textContent = (d.live ? 'Live prices \u00B7 ' : 'Snapshot \u00B7 ') + when;
    }
  }

  // ── geography doughnut ───────────────────────────────────
  function cssVar(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
  }

  function tooltipLabel(ctx) { return ' ' + ctx.label + ': ' + pct(ctx.parsed); }

  function drawGeo(t) {
    var canvas = document.getElementById('chart-geo');
    if (!canvas || typeof Chart === 'undefined') return;
    var india = Math.max(0, Number(t.india_inr) || 0);
    var mf = Math.max(0, Number(t.mf_inr) || 0);
    var us = Math.max(0, Number(t.us_inr) || 0);
    var legend = document.getElementById('geo-legend');
    // Google's palette reads on both the light and the dark surface.
    var colours = ['#4285f4', '#34a853', '#fbbc04'];
    var labels = ['India equity (Zerodha)', 'Mutual funds', 'US book'];
    var values = [india, mf, us];

    if (india + mf + us <= 0) {
      if (geoChart) { geoChart.destroy(); geoChart = null; }
      if (legend) legend.innerHTML = '<li class="muted">No positions to chart yet.</li>';
      return;
    }
    var total = india + mf + us;
    var percentages = values.map(function (value) { return value / total * 100; });
    if (legend) {
      legend.innerHTML = values.map(function (v, i) {
        return '<li><i style="background:' + colours[i] + '"></i>' +
          labels[i] + ' <b>' + inrC(v) + '</b> (' + pct(v / total * 100) + ')</li>';
      }).join('');
    }
    if (geoChart) {
      geoChart.data.datasets[0].data = percentages;
      geoChart.update();
      return;
    }
    geoChart = new Chart(canvas.getContext('2d'), {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [{
          data: percentages,
          backgroundColor: colours,
          borderColor: cssVar('--card') || '#fff',
          borderWidth: 3,
          hoverOffset: 6
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '64%',
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: tooltipLabel } }
        }
      }
    });
  }

  // ── refresh ──────────────────────────────────────────────
  // The button holds an icon, so only its label text changes.
  function refreshLabel(btn, text) {
    (btn.querySelector('.btn-label') || btn).textContent = text;
  }

  function showError(msg) {
    var box = document.getElementById('home-error');
    if (!box) return;
    if (!msg) { box.style.display = 'none'; return; }
    box.textContent = msg;
    box.style.display = 'block';
  }

  function refreshHome(manual) {
    if (inFlight) return;
    inFlight = true;
    var btn = document.getElementById('home-refresh');
    if (btn) { btn.disabled = true; refreshLabel(btn, 'Refreshing\u2026'); }
    var stamp = document.getElementById('home-stamp');
    if (stamp && manual) stamp.textContent = 'Fetching live prices\u2026';

    fetch('/api/home/summary?live=1', { headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        showError('');
        apply(d);
        // Auth flipped from OK to expired while the page was open —
        // reload so the connect card shows the login form.
        var card = document.getElementById('connect-card');
        if (card && d && d.auth && !d.auth.valid && card.classList.contains('ok')) {
          window.location.reload();
        }
      })
      .catch(function (e) {
        showError('Could not refresh live prices: ' + e.message +
          '. Showing the last snapshot.');
      })
      .then(function () {
        inFlight = false;
        if (btn) { btn.disabled = false; refreshLabel(btn, 'Refresh live prices'); }
      });
  }
  window.refreshHome = refreshHome;

  function setAuto(on) {
    if (timer) { clearInterval(timer); timer = null; }
    if (on) {
      timer = setInterval(function () {
        if (!document.hidden) refreshHome(false);
      }, AUTO_MS);
    }
    try { window.localStorage.setItem(AUTO_KEY, on ? '1' : '0'); } catch (e) {}
  }

  // ── holdings tabs (WAI-ARIA tabs pattern) ────────────────
  var BOOK_KEY = 'homeBookTab';
  function selectBook(name, focus) {
    var tabs = document.querySelectorAll('.book-tab');
    var known = Array.prototype.some.call(tabs, function (t) { return t.dataset.book === name; });
    if (!known) return;
    tabs.forEach(function (tab) {
      var on = tab.dataset.book === name;
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
      tab.tabIndex = on ? 0 : -1;
      var panel = document.getElementById(tab.getAttribute('aria-controls'));
      if (panel) panel.hidden = !on;
      if (on && focus) tab.focus();
    });
    try { window.localStorage.setItem(BOOK_KEY, name); } catch (e) {}
  }
  function bindBookTabs() {
    var list = document.querySelector('.book-tabs');
    if (!list) return;
    list.addEventListener('click', function (event) {
      var tab = event.target.closest('.book-tab');
      if (tab) selectBook(tab.dataset.book, false);
    });
    list.addEventListener('keydown', function (event) {
      var tabs = Array.prototype.slice.call(list.querySelectorAll('.book-tab'));
      var index = tabs.indexOf(document.activeElement);
      if (index < 0) return;
      var next = null;
      if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
      else if (event.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
      else if (event.key === 'Home') next = tabs[0];
      else if (event.key === 'End') next = tabs[tabs.length - 1];
      if (!next) return;
      event.preventDefault();
      selectBook(next.dataset.book, true);
    });
    var saved = null;
    try { saved = window.localStorage.getItem(BOOK_KEY); } catch (e) {}
    if (saved) selectBook(saved, false);
  }

  document.addEventListener('DOMContentLoaded', function () {
    apply(boot());
    bindBookTabs();

    var auto = document.getElementById('home-auto');
    var saved = '0';
    try { saved = window.localStorage.getItem(AUTO_KEY) || '0'; } catch (e) {}
    if (auto) {
      auto.checked = saved === '1';
      auto.addEventListener('change', function () { setAuto(auto.checked); });
      setAuto(auto.checked);
    }

    // One live upgrade after first paint so the page is never blocked
    // on the broker; after that it's on demand / auto-refresh only.
    setTimeout(function () { refreshHome(false); }, 350);

    window.addEventListener('dashthemechange', function () {
      if (geoChart) {
        geoChart.data.datasets[0].borderColor = cssVar('--card') || '#fff';
        geoChart.update();
      }
    });
  });
})();
</script>
"""


__all__ = ["render_home_page"]