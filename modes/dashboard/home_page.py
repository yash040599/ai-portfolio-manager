"""Dashboard home — the one screen that answers "where do I stand?".

Layout (2026-07-30 revamp):

    hero            greeting, IST clock, NSE/US market chips, refresh
    kpi row         net worth, unrealised P&L, India book, US book
    connect card    Zerodha auth status + inline login (TOTP / paste)
    allocation      India-vs-US doughnut + Indian sector weights
    books           India holdings, US positions, swing tracker
    realised strip  swing India, US, intraday
    tiles           navigation into the per-book tools

Book boundaries are enforced in `modes/dashboard/home_summary.py`:
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
from modes.dashboard.nav import render_topnav, topnav_css
from modes.dashboard.theme import (
    theme_boot_script,
    theme_css,
    theme_overrides_css,
)


_TILES = [
    ("Portfolio", "/portfolio", "Holdings, gap analysis, analyse runs.", "in"),
    ("Mutual Funds", "/mf", "Coin funds, external folios, SIPs.", "mf"),
    ("Swing (India)", "/swing", "Daily scan, watchlist, open book.", "in"),
    ("US", "/us", "US long-term holdings and investment ideas.", "us"),
    ("Intraday", "/trading", "Live intraday P&L and day drill-down.", "id"),
    ("Dry Run", "/dryrun", "Per-strategy dry-run P&L and stats.", "dr"),
    ("Tax", "/tax", "Realised P&L, charges, slab projection.", "tx"),
    ("Theory", "/theory/statistics", "Strategy notes and statistics.", "th"),
]


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

def _hero(data: dict) -> str:
    mk = data["market"]
    india_dot = ('<span class="dot live"></span>' if mk["india_open"]
                 else '<span class="dot"></span>')
    us_dot = ('<span class="dot live"></span>' if mk["us_open"]
              else '<span class="dot"></span>')
    fx = data["fx"]
    rate = _num(fx.get("rate"))
    fx_chip = (
        f'<span class="t-chip" title="Source: {html.escape(str(fx.get("source", "")))}">'
        f'USD/INR&nbsp;<strong>{rate:,.2f}</strong></span>'
        if rate > 0 else
        '<span class="t-chip warn">USD/INR unavailable</span>'
    )
    return f"""
<header class="hero">
  <div class="hero-left">
    <div class="eyebrow">{html.escape(mk['ist_date'])} &middot; {html.escape(mk['ist'])} IST</div>
    <h1>Portfolio command centre</h1>
    <p class="sub">Every book in one place — Zerodha holdings, the India swing
       tracker, the US book, and realised intraday P&amp;L.</p>
    <div class="t-row">
      <span class="t-chip {'pos' if mk['india_open'] else ''}">{india_dot}{html.escape(mk['india_label'])}</span>
      <span class="t-chip {'pos' if mk['us_open'] else ''}">{us_dot}{html.escape(mk['us_label'])}</span>
      {fx_chip}
    </div>
  </div>
  <div class="hero-right">
    <div class="t-row hero-actions">
      <button id="home-refresh" class="action" type="button" onclick="refreshHome(true)">
        Refresh live prices</button>
      <button id="home-privacy" class="action alt" type="button"
              onclick="toggleHomePrivacy()" aria-pressed="false"
              title="Hide every figure in the summary cards — for when someone can see your screen">
        Hide amounts</button>
    </div>
    <label class="switch" title="Re-fetch live prices every 60 seconds">
      <input type="checkbox" id="home-auto"> <span>Auto refresh</span>
    </label>
    <div class="stamp" id="home-stamp">Snapshot &middot; not yet refreshed</div>
  </div>
</header>
"""


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
<section class="kpis">
  <article class="kpi accent">
    <div class="k-label">Net worth <span class="k-info" title="{html.escape(net_tip)}">?</span></div>
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
    <div class="k-label">Mutual funds <span class="k-info" title="{html.escape(mf_tip)}">?</span></div>
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
    auth = data["auth"]
    if auth["valid"]:
        return f"""
<section class="t-card connect ok" id="connect-card">
  <div class="connect-head">
    <span class="t-chip pos"><span class="dot"></span>Zerodha connected</span>
    <span class="muted">Token valid for {html.escape(auth['token_date'])} —
      Kite expires it at midnight IST.</span>
    <span class="spacer"></span>
    <a class="action alt" href="/login">Re-login</a>
  </div>
</section>
"""

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
      <label>Authenticator code for <strong>{html.escape(auth['user_id'])}</strong></label>
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
<section class="t-card connect bad" id="connect-card">
  <div class="connect-head">
    <span class="t-chip warn"><span class="dot"></span>Zerodha not connected</span>
    <span class="muted">{html.escape(reason)} Live Indian prices, holdings refresh
      and analyse runs stay disabled until you re-login.</span>
  </div>
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
<h2 class="t-section-title">Allocation <span class="hint">where the money actually sits</span></h2>
<section class="split">
  <div class="t-card">
    <h3>Book mix</h3>
    <div class="donut-wrap"><canvas id="chart-geo" height="190"></canvas></div>
    <ul class="legend" id="geo-legend"></ul>
  </div>
  <div class="t-card">
    <h3>Indian sector weight</h3>
    <ul class="sectors">{''.join(rows)}</ul>
  </div>
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

    swing_tip = ("These shares are already counted inside the Zerodha holdings "
                 "above, so this book is never added to net worth.")
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

    return f"""
<h2 class="t-section-title">Books</h2>
<section class="books">
  <div class="t-card book">
    <div class="book-head">
      <h3>India &middot; Zerodha holdings</h3>{stale}
      <span class="spacer"></span><a class="more" href="/portfolio">Open &#8594;</a>
    </div>
    <table class="mini"><thead><tr><th>Symbol</th><th class="right">Qty</th>
      <th class="right">Value</th><th class="right">P&amp;L</th></tr></thead>
      <tbody id="book-india">{_rows_html(ind.get('top') or [], currency='inr', href='/portfolio/')}</tbody></table>
  </div>

  <div class="t-card book">
    <div class="book-head">
      <h3>Mutual funds</h3>{mf_stamp}
      <span class="spacer"></span><a class="more" href="/mf">Open &#8594;</a>
    </div>
    <table class="mini"><thead><tr><th>Fund</th><th class="right">Units</th>
      <th class="right">Value</th><th class="right">P&amp;L</th></tr></thead>
      <tbody id="book-mf">{_mf_rows_html(mf.get('rows') or [])}</tbody></table>
    <div class="book-foot muted"><span id="mf-foot">{mf_foot}</span></div>
  </div>

  <div class="t-card book">
    <div class="book-head">
      <h3>US book</h3>
      <span class="t-chip info" title="Long-term holdings — RSU lots plus deliberate long-horizon buys. Not a trading book.">long-term</span>
      <span class="spacer"></span><a class="more" href="/us">Open &#8594;</a>
    </div>
    <table class="mini"><thead><tr><th>Symbol</th><th class="right">Qty</th>
      <th class="right">Value</th><th class="right">P&amp;L</th></tr></thead>
      <tbody id="book-us">{_rows_html(us.get('rows') or [], currency='usd', href='/us/')}</tbody></table>
  </div>

  <div class="t-card book">
    <div class="book-head">
      <h3>Swing tracker &middot; India</h3>
      <span class="t-chip" title="{html.escape(swing_tip)}">not in net worth</span>
      <span class="spacer"></span><a class="more" href="/swing">Open &#8594;</a>
    </div>
    <table class="mini"><thead><tr><th>Symbol</th><th class="right">Qty</th>
      <th class="right">Value</th><th class="right">P&amp;L</th></tr></thead>
      <tbody id="book-swing">{_rows_html(sw.get('rows') or [], currency='inr', href='/swing/')}</tbody></table>
    <div class="book-foot muted"><span id="swing-foot">{sw['positions']} open &middot;
      {swing_unreal} unrealised &middot;
      {sw['watchlist']} on watchlist</span></div>
  </div>
</section>
"""


def _realised(data: dict) -> str:
    sw, us, intra = data["swing_india"], data["us"], data["intraday"]
    t = data["totals"]
    best, worst = intra.get("best_day"), intra.get("worst_day")
    extra = ""
    if best and worst:
        extra = (f'best {html.escape(str(best[0]))} {_inr(best[1], signed=True)}'
                 f' &middot; worst {html.escape(str(worst[0]))} {_inr(worst[1], signed=True)}')
    return f"""
<h2 class="t-section-title">Realised <span class="hint">booked, not marked-to-market</span></h2>
<section class="realised">
  <div class="t-card r-card">
    <div class="k-label">Swing &middot; India</div>
    <div class="r-value {_cls(sw['realised_net'])}" id="r-swing">{_inr(sw['realised_net'], signed=True)}</div>
    <div class="k-foot">{sw['closed']} closed trades, net of charges</div>
  </div>
  <div class="t-card r-card">
    <div class="k-label">US</div>
    <div class="r-value {_cls(us['realised_usd'])}" id="r-us">{_usd(us['realised_usd'], signed=True)}</div>
    <div class="k-foot">{us['closed']} closed trades</div>
  </div>
  <div class="t-card r-card">
    <div class="k-label">Intraday <span class="k-info"
      title="Current financial year, from the verified intraday tax ledger.">?</span></div>
    <div class="r-value {_cls(intra['net_pnl'])}" id="r-intraday">{_inr(intra['net_pnl'], signed=True)}</div>
    <div class="k-foot">{intra['trades']} trades over {intra['days']} days
      &middot; {html.escape(str(intra['window']))}<br>{extra}</div>
  </div>
  <div class="t-card r-card total">
    <div class="k-label">Total realised</div>
    <div class="r-value {_cls(t['realised_inr'])}" id="r-total">{_inr(t['realised_inr'], signed=True)}</div>
    <div class="k-foot">All books, converted to INR</div>
  </div>
</section>
"""


def _tiles() -> str:
    cards = [
        f'<a class="tile k-{kind}" href="{html.escape(href)}">'
        f'<strong>{html.escape(title)}</strong>'
        f'<span>{html.escape(desc)}</span>'
        f'<em>Open &#8594;</em></a>'
        for title, href, desc, kind in _TILES
    ]
    return f"""
<h2 class="t-section-title">Tools</h2>
<section class="tiles">{''.join(cards)}</section>
"""


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


def _privacy_boot_script() -> str:
    """Set `data-privacy` before first paint so the KPI figures are never
    briefly readable on a screen someone else can see."""
    return r"""
<script>
(function () {
  try {
    if (window.localStorage.getItem('homePrivacy') === '1') {
      document.documentElement.setAttribute('data-privacy', 'on');
    }
  } catch (e) { /* private mode */ }
})();
</script>
"""


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

    return "".join([
        "<!doctype html><html lang='en'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Dashboard &middot; Portfolio HQ</title>",
        theme_boot_script(),
        _privacy_boot_script(),
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/'
        'dist/chart.umd.min.js"></script>',
        "<style>", theme_css(), topnav_css(), _STYLE, theme_overrides_css(),
        "</style>",
        "</head><body>",
        error_toast_html(),
        "<main class='wrap'>",
        render_topnav("/", after_links=_currency_toggle_html(data)),
        _hero(data),
        flash,
        err_banner,
        _kpis(data),
        _connect_card(data),
        chat_section_html("home"),
        _allocation(data),
        _books(data),
        _realised(data),
        _tiles(),
        "<footer class='home-foot'>Read-only overview &middot; the swing tracker is "
        "excluded from net worth so Zerodha holdings are never double-counted.</footer>",
        "</main>",
        f'<script id="home-bootstrap" type="application/json">{_safe_json(data)}</script>',
        _SCRIPT,
        error_toast_script(),
        "</body></html>",
    ])


_STYLE = r"""
.cur-toggle { display: inline-flex; align-items: center; gap: 0;
              background: var(--card); border: 1px solid var(--line);
              border-radius: 999px; padding: 0; margin-left: 8px;
              cursor: pointer; font: inherit; overflow: hidden; }
.cur-pill { padding: 3px 10px; font-size: 12px; font-weight: 700;
            color: var(--muted); }
.cur-pill.active { background: var(--accent); color: #fff; }
.wrap { max-width: 1240px; margin: 0 auto; }

/* ── Hero ─────────────────────────────────────────────────── */
.hero { display: flex; gap: 18px; align-items: flex-start; flex-wrap: wrap;
        padding: 22px 24px; margin-bottom: 18px; border-radius: var(--radius);
        border: 1px solid var(--line); box-shadow: var(--shadow-sm);
        background:
          linear-gradient(135deg,
            color-mix(in srgb, var(--accent) 10%, var(--card)) 0%,
            var(--card) 46%,
            color-mix(in srgb, var(--accent-2) 9%, var(--card)) 100%); }
.hero-left { flex: 1 1 420px; min-width: 0; }
.hero-right { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }
.hero .eyebrow { font-size: 11.5px; font-weight: 700; letter-spacing: .1em;
                 text-transform: uppercase; color: var(--muted); }
.hero h1 { margin: 4px 0; font-size: 27px; letter-spacing: -.02em; }
.hero .sub { margin: 0 0 12px; max-width: 62ch; color: var(--muted); font-size: 13.5px; }
.hero .stamp { font-size: 11.5px; color: var(--muted); text-align: right; }
.hero-actions { justify-content: flex-end; }
.switch { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px;
          color: var(--muted); cursor: pointer; user-select: none; }
.switch input { accent-color: var(--accent); cursor: pointer; }

/* ── KPI cards ────────────────────────────────────────────── */
.kpis { display: grid; gap: 14px; margin-bottom: 16px;
        grid-template-columns: repeat(auto-fit, minmax(215px, 1fr)); }
.kpi { position: relative; overflow: hidden; padding: 15px 17px 14px;
       background: var(--card); border: 1px solid var(--line);
       border-radius: var(--radius); box-shadow: var(--shadow-sm); }
.kpi::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 4px;
               background: var(--line-strong); }
.kpi.accent::before { background: linear-gradient(180deg, var(--accent), var(--accent-2)); }
.kpi .k-label { font-size: 11.5px; font-weight: 700; letter-spacing: .07em;
                text-transform: uppercase; color: var(--muted);
                display: flex; align-items: center; gap: 6px; }
.kpi .k-value { font-size: 27px; font-weight: 700; letter-spacing: -.02em;
                margin: 7px 0 5px; font-variant-numeric: tabular-nums; }
.kpi .k-foot { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.k-info { display: inline-flex; align-items: center; justify-content: center;
          width: 15px; height: 15px; border-radius: 50%; font-size: 10px;
          background: var(--soft); color: var(--muted); cursor: help;
          border: 1px solid var(--line); font-weight: 700; }

/* ── Privacy mask (top summary boxes only) ────────────────── */
/* `visibility: hidden` rather than a blur: it removes the glyphs
   outright and also kills the `title` tooltip that would otherwise
   still reveal the exact rupee figure on hover. */
html[data-privacy="on"] .kpis .k-value,
html[data-privacy="on"] .kpis .k-foot { position: relative; visibility: hidden; }
html[data-privacy="on"] .kpis .k-value::after,
html[data-privacy="on"] .kpis .k-foot::after {
  content: "\2022\2022\2022\2022\2022";
  visibility: visible; position: absolute; inset: 0 auto 0 0;
  display: flex; align-items: center; letter-spacing: .12em;
  color: var(--muted); }

/* ── Connect / login ──────────────────────────────────────── */
.connect { margin-bottom: 6px; border-left: 4px solid var(--pos); }
.connect.bad { border-left-color: var(--warn-fg); }
.connect-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
                font-size: 13px; }
.connect-head .spacer { flex: 1; }
.connect-head .action { text-decoration: none; }
.connect-grid { display: grid; gap: 16px; margin-top: 14px;
                grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); }
.login-form label { display: block; font-size: 12px; font-weight: 600;
                    color: var(--muted); margin-bottom: 6px; }
.login-form input[type=text] { flex: 1 1 180px; min-width: 0; }
.login-form input.otp { flex: 0 0 132px; font-size: 18px; letter-spacing: 5px;
                        text-align: center; }
.login-form .hint { margin: 8px 0 0; font-size: 11.5px; color: var(--muted); }

/* ── Allocation ───────────────────────────────────────────── */
.split { display: grid; gap: 14px;
         grid-template-columns: minmax(0, 1fr) minmax(0, 1.35fr); }
@media (max-width: 900px) { .split { grid-template-columns: 1fr; } }
.split h3, .book-head h3 { margin: 0 0 10px; font-size: 13px; font-weight: 700;
                           letter-spacing: .04em; }
.donut-wrap { position: relative; height: 190px; }
.legend { list-style: none; margin: 12px 0 0; padding: 0; display: flex;
          gap: 14px; flex-wrap: wrap; font-size: 12.5px; }
.legend li { display: flex; align-items: center; gap: 7px; color: var(--muted); }
.legend i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.legend b { color: var(--fg); font-variant-numeric: tabular-nums; }
.sectors { list-style: none; margin: 0; padding: 0; display: grid; gap: 9px; }
.sectors li { display: grid; grid-template-columns: 108px 1fr 52px;
              align-items: center; gap: 10px; font-size: 12.5px; }
.sectors .s-name { color: var(--fg-2); font-weight: 600; overflow: hidden;
                   text-overflow: ellipsis; white-space: nowrap; }
.sectors .s-bar { height: 8px; border-radius: 999px; background: var(--soft);
                  overflow: hidden; }
.sectors .s-bar i { display: block; height: 100%; border-radius: 999px;
                    background: linear-gradient(90deg, var(--accent), var(--accent-2)); }
.sectors .s-val { text-align: right; color: var(--muted);
                  font-variant-numeric: tabular-nums; }

/* ── Books ────────────────────────────────────────────────── */
.books { display: grid; gap: 14px;
         grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
.book-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
             margin-bottom: 4px; }
.book-head .spacer { flex: 1; }
.book-head .more { font-size: 12px; font-weight: 600; text-decoration: none; }
.book-head .more:hover { text-decoration: underline; }
table.mini { width: 100%; border-collapse: collapse; font-size: 12.5px;
             font-variant-numeric: tabular-nums; }
table.mini th { text-align: left; font-size: 10.5px; text-transform: uppercase;
                letter-spacing: .05em; color: var(--muted); font-weight: 700;
                padding: 6px 8px; border-bottom: 1px solid var(--line); }
table.mini td { padding: 7px 8px; border-bottom: 1px solid var(--line); }
table.mini tr:last-child td { border-bottom: none; }
table.mini tr:hover td { background: var(--soft); }
table.mini .right { text-align: right; }
table.mini td.pad { padding: 18px 8px; text-align: center; }
table.mini a { text-decoration: none; font-weight: 600; color: var(--fg); }
table.mini a:hover { color: var(--accent); text-decoration: underline; }
table.mini .sub-pct { display: block; font-size: 10.5px; opacity: .78; font-weight: 500; }
.book-foot { margin-top: 10px; font-size: 11.5px; }

/* ── Realised ─────────────────────────────────────────────── */
.realised { display: grid; gap: 14px;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
.r-card .k-label { font-size: 11.5px; font-weight: 700; letter-spacing: .07em;
                   text-transform: uppercase; color: var(--muted);
                   display: flex; align-items: center; gap: 6px; }
.r-card .r-value { font-size: 21px; font-weight: 700; margin: 7px 0 5px;
                   font-variant-numeric: tabular-nums; }
.r-card .k-foot { font-size: 11.5px; color: var(--muted); line-height: 1.5; }
.r-card.total { background: linear-gradient(135deg,
                  color-mix(in srgb, var(--accent) 9%, var(--card)), var(--card)); }

/* ── Tiles ────────────────────────────────────────────────── */
.tiles { display: grid; gap: 12px;
         grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
.tile { position: relative; display: flex; flex-direction: column; gap: 6px;
        padding: 16px; min-height: 116px; text-decoration: none; overflow: hidden;
        color: var(--fg); background: var(--card); border: 1px solid var(--line);
        border-radius: var(--radius); box-shadow: var(--shadow-sm);
        transition: transform .12s ease, box-shadow .16s ease, border-color .16s ease; }
.tile:hover { transform: translateY(-2px); box-shadow: var(--shadow-md);
              border-color: var(--accent-line); }
.tile strong { font-size: 14.5px; }
.tile span { color: var(--muted); font-size: 12.5px; line-height: 1.45; flex: 1; }
.tile em { font-style: normal; font-size: 11.5px; font-weight: 700;
           color: var(--accent); letter-spacing: .02em; }
.tile::after { content: ""; position: absolute; top: 0; left: 0; right: 0;
               height: 3px; background: var(--accent); }
.tile.k-in::after { background: linear-gradient(90deg, #ff9933, #138808); }
.tile.k-mf::after { background: linear-gradient(90deg, #0b8a5b, #41a05e); }
.tile.k-us::after { background: linear-gradient(90deg, #3c3b6e, #b22234); }
.tile.k-id::after { background: linear-gradient(90deg, #2f5fe0, #6d5ae0); }
.tile.k-dr::after { background: linear-gradient(90deg, #64748b, #94a3b8); }
.tile.k-tx::after { background: linear-gradient(90deg, #0b8a5b, #34d39f); }
.tile.k-th::after { background: linear-gradient(90deg, #a5610a, #f0b45e); }

.home-foot { margin-top: 30px; text-align: center; font-size: 11.5px; color: var(--muted); }
.banner { padding: 10px 14px; margin-bottom: 14px; border-radius: var(--radius-sm);
          font-size: 13px; }
.banner.ok-flash { background: var(--pos-bg); border: 1px solid var(--pos-line);
                   color: var(--pos); font-weight: 600; }

@media (max-width: 640px) {
  .hero { padding: 18px; }
  .hero-right { align-items: flex-start; width: 100%; }
  .hero .stamp { text-align: left; }
  .kpi .k-value { font-size: 23px; }
}
"""


_SCRIPT = r"""
<script>
(function () {
  'use strict';

  var AUTO_KEY = 'homeAutoRefresh';
  var CUR_KEY = 'us-currency';   // shared with /us so the choice sticks
  var PRIVACY_KEY = 'homePrivacy';
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

  function tooltipLabel(ctx) { return ' ' + ctx.label + ': ' + inr(ctx.parsed); }

  function drawGeo(t) {
    var canvas = document.getElementById('chart-geo');
    if (!canvas || typeof Chart === 'undefined') return;
    var india = Math.max(0, Number(t.india_inr) || 0);
    var mf = Math.max(0, Number(t.mf_inr) || 0);
    var us = Math.max(0, Number(t.us_inr) || 0);
    var legend = document.getElementById('geo-legend');
    var colours = ['#2f5fe0', '#41a05e', '#f0913a'];
    var labels = ['India equity (Zerodha)', 'Mutual funds', 'US book'];
    var values = [india, mf, us];

    if (india + mf + us <= 0) {
      if (geoChart) { geoChart.destroy(); geoChart = null; }
      if (legend) legend.innerHTML = '<li class="muted">No positions to chart yet.</li>';
      return;
    }
    var total = india + mf + us;
    if (legend) {
      legend.innerHTML = values.map(function (v, i) {
        return '<li><i style="background:' + colours[i] + '"></i>' +
          labels[i] + ' <b>' + inrC(v) + '</b> (' + pct(v / total * 100) + ')</li>';
      }).join('');
    }
    if (geoChart) {
      geoChart.data.datasets[0].data = values;
      geoChart.update();
      return;
    }
    geoChart = new Chart(canvas.getContext('2d'), {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [{
          data: values,
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
    if (btn) { btn.disabled = true; btn.textContent = 'Refreshing\u2026'; }
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
        if (btn) { btn.disabled = false; btn.textContent = 'Refresh live prices'; }
      });
  }
  window.refreshHome = refreshHome;

  // ── privacy mask (KPI cards only) ────────────────────────
  function privacyOn() {
    try { return window.localStorage.getItem(PRIVACY_KEY) === '1'; }
    catch (e) { return false; }
  }
  function syncPrivacy() {
    var on = privacyOn();
    document.documentElement.setAttribute('data-privacy', on ? 'on' : 'off');
    var btn = document.getElementById('home-privacy');
    if (btn) {
      btn.textContent = on ? 'Show amounts' : 'Hide amounts';
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
  }
  function toggleHomePrivacy() {
    try { window.localStorage.setItem(PRIVACY_KEY, privacyOn() ? '0' : '1'); }
    catch (e) {}
    syncPrivacy();
  }
  window.toggleHomePrivacy = toggleHomePrivacy;

  function setAuto(on) {
    if (timer) { clearInterval(timer); timer = null; }
    if (on) {
      timer = setInterval(function () {
        if (!document.hidden) refreshHome(false);
      }, AUTO_MS);
    }
    try { window.localStorage.setItem(AUTO_KEY, on ? '1' : '0'); } catch (e) {}
  }

  document.addEventListener('DOMContentLoaded', function () {
    apply(boot());
    syncPrivacy();

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