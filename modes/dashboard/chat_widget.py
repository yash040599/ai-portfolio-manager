# ================================================================
# modes/dashboard/chat_widget.py
# ================================================================
# "Ask AI" prompt-builder widget for dashboard pages.
#
# Our built-in AI is a cheap/free tier, so instead of answering the
# user's question directly this widget assembles a *copy-paste
# prompt*. It folds in the user's PERSONAL data (holdings, buy
# price, qty, entry/stop/target, local thesis) — things a public
# LLM like ChatGPT or Claude cannot know — and leaves globally
# available data (current live price, latest news, macro) open for
# the advanced model to look up itself.
#
# The user pastes the generated prompt into a stronger free model
# to get a better-quality answer at no cost.
#
# Used on: portfolio home/detail, swing home/detail, US home/detail.
# ================================================================

from __future__ import annotations

import html
import re

_VALID_SCOPES = {
    "home",
    "portfolio", "portfolio_detail",
    "swing", "swing_detail",
    "us", "us_detail",
}

# Scopes that require a (validated) stock symbol.
_DETAIL_SCOPES = {"portfolio_detail", "swing_detail", "us_detail"}


# ── Field / value helpers ───────────────────────────────────────

def _fval(field, default=None):
    """Return the `.value` of a Field-like object, else the raw value
    or default. Defensive against None and missing attributes."""
    if field is None:
        return default
    val = getattr(field, "value", field)
    return default if val is None else val


def _num(field, default=0.0) -> float:
    try:
        return float(_fval(field, default))
    except (TypeError, ValueError):
        return default


def _qty_str(field, default=0.0) -> str:
    """Format a share quantity. Whole numbers render without decimals
    (e.g. "128"); fractional holdings keep up to 2 decimals (e.g.
    "0.42") so sub-1-share positions aren't truncated to "0"."""
    q = _num(field, default)
    if q == int(q):
        return str(int(q))
    return f"{q:.2f}"


def _txt(field, default="") -> str:
    val = _fval(field, default)
    return default if val is None else str(val)


def _clean_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if not sym or not re.fullmatch(r"[A-Z0-9.\-&]{1,15}", sym):
        return ""
    return sym


# ── Public: HTML widget ─────────────────────────────────────────

def chat_section_html(scope: str, symbol: str = "") -> str:
    """Render the self-contained "Ask AI (prompt builder)" card.

    Includes its own <script>, so a single injection per page is
    enough — no changes to the page `_wrap()` helpers required.
    """
    scope = scope if scope in _VALID_SCOPES else "portfolio"
    sym = _clean_symbol(symbol)

    if scope in _DETAIL_SCOPES and sym:
        subject = f"about <strong>{html.escape(sym)}</strong>"
        placeholder = (
            f"e.g. I hold {sym} and it dipped after I bought — "
            f"should I buy more, hold, or exit?"
        )
    elif scope == "home":
        subject = "about <strong>everything I own</strong>"
        placeholder = (
            "e.g. Across my Indian stocks, mutual funds and US holdings, "
            "what is over-weight and what should I fix first?"
        )
    else:
        subject = "about this page"
        placeholder = (
            "e.g. Given my holdings and risk, what should I trim "
            "or add to right now?"
        )

    scope_attr = html.escape(scope)
    sym_attr = html.escape(sym)

    return f"""
<div class="chat-widget card" data-chat-scope="{scope_attr}"
     data-chat-symbol="{sym_attr}"
     style="border:1px solid var(--line);border-radius:var(--radius);
            background:var(--card);box-shadow:var(--shadow-sm);
            padding:14px 16px;margin-bottom:16px">
  <div style="font-size:14px;font-weight:600;margin-bottom:4px;color:var(--fg)">
    Ask AI {subject}
  </div>
  <div style="font-size:12px;color:var(--muted);margin-bottom:10px">
    Our built-in AI is a free tier. Type your question and we'll build a
    ready-to-paste prompt — packed with <em>your</em> personal position
    data — that you can drop into ChatGPT, Claude or Gemini (free) for a
    stronger answer. Live prices &amp; news are left for that model to
    look up itself.
  </div>
  <textarea class="chat-question" rows="2"
            placeholder="{html.escape(placeholder)}"
            style="width:100%;box-sizing:border-box;padding:8px;
                   border-radius:6px;font-size:13px;
                   font-family:inherit;resize:vertical"></textarea>
  <div style="display:flex;gap:8px;align-items:center;margin-top:8px;
              flex-wrap:wrap">
    <button class="chat-build action"
            style="padding:6px 14px;font-size:13px">
      Build prompt
    </button>
    <button class="chat-copy action alt" disabled
            style="padding:6px 14px;font-size:13px">
      Copy
    </button>
    <span class="chat-msg" style="font-size:12px;color:var(--muted)"></span>
  </div>
  <textarea class="chat-output" readonly rows="10"
            placeholder="Your generated prompt will appear here…"
            style="width:100%;box-sizing:border-box;padding:8px;margin-top:10px;
                   border-radius:6px;font-size:12px;
                   font-family:var(--mono);
                   resize:vertical;display:none"></textarea>
</div>
{_chat_section_script()}
"""


def _chat_section_script() -> str:
    return """<script>
(function(){
  var widgets = document.querySelectorAll('.chat-widget');
  widgets.forEach(function(w){
    if (w.dataset.chatBound) return;
    w.dataset.chatBound = '1';
    var buildBtn = w.querySelector('.chat-build');
    var copyBtn  = w.querySelector('.chat-copy');
    var qBox     = w.querySelector('.chat-question');
    var outBox   = w.querySelector('.chat-output');
    var msg      = w.querySelector('.chat-msg');

    buildBtn.addEventListener('click', function(){
      var question = (qBox.value || '').trim();
      msg.style.color = 'var(--muted)';
      msg.textContent = 'Building prompt…';
      buildBtn.disabled = true;
      fetch('/api/chat/prompt', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          scope:    w.dataset.chatScope,
          symbol:   w.dataset.chatSymbol,
          question: question
        })
      }).then(function(r){ return r.json(); })
      .then(function(d){
        buildBtn.disabled = false;
        if (d && d.ok) {
          outBox.value = d.prompt;
          outBox.style.display = 'block';
          copyBtn.disabled = false;
          copyBtn.style.opacity = '1';
          msg.style.color = 'var(--pos)';
          msg.textContent = 'Prompt ready — copy & paste into ChatGPT / Claude / Gemini.';
        } else {
          msg.style.color = 'var(--neg)';
          msg.textContent = (d && d.error) || 'Could not build prompt.';
        }
      }).catch(function(e){
        buildBtn.disabled = false;
        msg.style.color = 'var(--neg)';
        msg.textContent = 'Error: ' + e;
      });
    });

    copyBtn.addEventListener('click', function(){
      outBox.select();
      var done = function(){
        msg.style.color = 'var(--pos)';
        msg.textContent = 'Copied to clipboard.';
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(outBox.value).then(done, function(){
          document.execCommand('copy'); done();
        });
      } else {
        document.execCommand('copy'); done();
      }
    });
  });
})();
</script>"""


# ── Public: prompt builder (server side) ────────────────────────

def build_chat_prompt(scope: str, symbol: str, question: str) -> str:
    """Assemble a copy-paste prompt for an external LLM, folding in
    the user's personal data for `scope` (+ `symbol` for detail
    pages). Raises ValueError for an unknown scope."""
    if scope not in _VALID_SCOPES:
        raise ValueError(f"Unknown chat scope: {scope!r}")

    question = (question or "").strip()
    sym = _clean_symbol(symbol)

    if scope == "home":
        return _assemble(
            question,
            "Multi-market: Indian equities and mutual funds (INR) plus "
            "US equities (USD)",
            _home_overview_block(),
            asks=_PORTFOLIO_ASKS,
        )
    if scope == "portfolio_detail":
        body = _portfolio_detail_block(sym)
        market = "Indian (NSE/BSE), prices in INR (Rs.)"
    elif scope == "portfolio":
        body = _portfolio_overview_block()
        market = "Indian (NSE/BSE), prices in INR (Rs.)"
    elif scope == "swing_detail":
        body = _swing_detail_block(sym)
        market = "Indian (NSE) swing trade, prices in INR (Rs.)"
    elif scope == "swing":
        body = _swing_overview_block()
        market = "Indian (NSE) swing trades, prices in INR (Rs.)"
    elif scope == "us_detail":
        body = _us_detail_block(sym)
        market = "US (NASDAQ/NYSE) swing trade, prices in USD ($)"
    else:  # us
        body = _us_overview_block()
        market = "US (NASDAQ/NYSE) swing trades, prices in USD ($)"

    return _assemble(question, market, body)


# Whole-book questions need a different closing brief to a single-stock
# one: "buy more / trim / exit" is the wrong frame for net worth.
_PORTFOLIO_ASKS = """1. Read my overall asset allocation: what is over- or under-weight for a
   long-term investor, and what is the single most important change to make?
2. Flag concentration and overlap risk — names or themes I am exposed to
   more than once across the Indian, mutual-fund and US books.
3. Comment on whether my new money (SIPs) is going where it should, given
   what I already own.
4. Verify current prices/levels and any recent news that changes the read,
   stating the date of the data you used.
5. Call out anything that looks structurally wrong (too many funds, cash
   drag, currency exposure, tax inefficiency).

Do not give me per-stock trade calls unless one is genuinely urgent.
Keep it concise and practical. I am the one deciding and executing."""

_DEFAULT_ASKS = """1. A clear, actionable recommendation (e.g. buy more / hold / trim / exit),
   with the reasoning laid out simply.
2. Verify the CURRENT price and any recent news before concluding, and
   tell me if that changes the picture vs. my snapshot above.
3. Key risks I should watch, and a level/condition that would change your view.
4. If you need data I didn't provide, tell me exactly what to fetch.

Keep it concise and practical. I am the one deciding and executing the trade."""


def _assemble(question: str, market: str, body: str,
              asks: str = _DEFAULT_ASKS) -> str:
    q = question or "(No specific question typed — give me your overall read and the single best action to take.)"
    return f"""You are a seasoned equity research analyst and portfolio advisor.

I'm an individual investor. Below is MY personal position data, exported
from my own portfolio tracker — treat it as ground truth, because you
cannot see my brokerage account. Market: {market}.

For anything market-wide — the current live price, latest news, earnings,
analyst ratings, sector/macro backdrop — look it up yourself or use your
most recent knowledge, and clearly state the date/assumptions of the data
you used. Do NOT assume the prices below are current; they're my last
local snapshot and may be stale.

================ MY QUESTION ================
{q}

================ MY DATA (personal, private) ================
{body}

================ WHAT I NEED FROM YOU ================
{asks}"""


# ── Scope data blocks ───────────────────────────────────────────

# Row caps keep the prompt inside a free model's context window. A
# 24-fund book plus 30 equities plus a US book is already long.
_MAX_ROWS = 30


def _home_overview_block() -> str:
    """Every book in one prompt: Indian equity, mutual funds, US.

    Built from `home_summary.build_summary()` — the same payload the
    home page renders — so a book added there shows up in the totals
    automatically. Adding a *detailed* section for a new book means
    adding a helper below.
    """
    try:
        from modes.dashboard.home_summary import build_summary
        data = build_summary(live=False)
    except Exception:
        return "(Could not read my portfolio right now.)"

    totals = data.get("totals") or {}
    fx = data.get("fx") or {}
    rate = float(fx.get("rate") or 0)

    lines = ["=== NET WORTH (my last local snapshot) ==="]
    lines.append(f"- Total net worth: Rs.{float(totals.get('net_worth_inr') or 0):,.0f}")
    lines.append(f"- Total invested: Rs.{float(totals.get('invested_inr') or 0):,.0f}")
    lines.append(
        f"- Unrealised P&L: Rs.{float(totals.get('unrealised_inr') or 0):+,.0f} "
        f"({float(totals.get('unrealised_pct') or 0):+.2f}%)")
    lines.append(
        f"- Split: Indian equity {float(totals.get('india_share_pct') or 0):.0f}% · "
        f"mutual funds {float(totals.get('mf_share_pct') or 0):.0f}% · "
        f"US {float(totals.get('us_share_pct') or 0):.0f}%")
    if rate > 0:
        lines.append(f"- USD/INR used for conversion: {rate:,.2f}")
    lines.append(
        f"- Realised P&L to date (all books): "
        f"Rs.{float(totals.get('realised_inr') or 0):+,.0f}")
    if not (data.get("us") or {}).get("live"):
        lines.append("- CAVEAT: no live US quote was available, so the US book "
                     "is counted at cost and the net-worth and P&L figures "
                     "above are understated by however much it has moved.")

    lines.append("")
    lines.append(_india_equity_section(data.get("india") or {}))
    lines.append("")
    lines.append(_mutual_fund_section())
    lines.append("")
    lines.append(_us_section(data.get("us") or {}, rate))

    swing = data.get("swing_india") or {}
    if int(swing.get("positions") or 0) > 0:
        lines.append("")
        lines.append(
            f"=== NOTE ON DOUBLE COUNTING ===\n"
            f"I also track {swing['positions']} Indian swing position(s) "
            f"separately, but those shares already sit inside the Zerodha "
            f"holdings above — do not add them again.")
    return "\n".join(lines)


def _india_equity_section(india: dict) -> str:
    lines = ["=== INDIAN EQUITY (Zerodha demat) ==="]
    if not india.get("available"):
        lines.append("- No analysis snapshot on file yet.")
        return "\n".join(lines)

    lines.append(f"- Invested: Rs.{float(india.get('invested') or 0):,.0f}")
    lines.append(f"- Current value: Rs.{float(india.get('current') or 0):,.0f}")
    lines.append(
        f"- Unrealised P&L: Rs.{float(india.get('pnl') or 0):+,.0f} "
        f"({float(india.get('pnl_pct') or 0):+.2f}%)")
    lines.append(f"- Holdings: {india.get('holdings', 0)}")

    # The home payload only carries the top few rows, so pull the full
    # book from the analyser snapshot when it is available.
    try:
        from modes.analyze.persistence import latest_snapshot
        snap = latest_snapshot()
    except Exception:
        snap = None

    if snap and getattr(snap, "holdings", None):
        ranked = sorted(snap.holdings,
                        key=lambda h: _num(getattr(h, "current_value", None)),
                        reverse=True)
        lines.append("")
        lines.append("Holdings (symbol · qty · avg buy · last · P&L% · sector):")
        for h in ranked[:_MAX_ROWS]:
            lines.append(
                f"- {h.symbol}: {_qty_str(h.qty)} sh · "
                f"avg Rs.{_num(h.avg_buy_price):,.2f} · "
                f"last Rs.{_num(h.current_price):,.2f} · "
                f"{_num(h.pnl_pct):+.1f}% · {_txt(h.sector, 'n/a')}")
        if len(ranked) > _MAX_ROWS:
            lines.append(f"(showing the top {_MAX_ROWS} of {len(ranked)} "
                         f"holdings by value)")
    elif india.get("sectors"):
        lines.append("")
        lines.append("Sector weights:")
        for s in india["sectors"][:12]:
            lines.append(f"- {s.get('sector') or 'OTHER'}: "
                         f"{float(s.get('weight_pct') or 0):.1f}%")
    return "\n".join(lines)


def _mutual_fund_section() -> str:
    lines = ["=== MUTUAL FUNDS (Zerodha Coin + other brokers) ==="]
    try:
        from modes.mf.book import build_book
        book = build_book(live=False)
    except Exception:
        lines.append("- Could not read the mutual-fund book.")
        return "\n".join(lines)

    if not book.schemes:
        lines.append("- No mutual funds tracked.")
        return "\n".join(lines)

    lines.append(f"- Invested: Rs.{book.invested_value:,.0f}")
    lines.append(f"- Current value: Rs.{book.current_value:,.0f}")
    lines.append(f"- Unrealised P&L: Rs.{book.pnl:+,.0f} ({book.pnl_pct:+.2f}%)")
    lines.append(f"- Schemes: {len(book.schemes)}")
    lines.append(
        f"- Active SIPs: {len(book.active_sips)} "
        f"(Rs.{book.monthly_sip_outflow:,.0f}/month); "
        f"paused: {len(book.paused_sips)} (paused deliberately, not neglected)")
    if book.nav_as_of:
        lines.append(f"- Valued on NAV published {book.nav_as_of} "
                     f"(mutual funds have no intraday price)")

    lines.append("")
    lines.append("Funds (name · units · avg NAV · current NAV · P&L% · monthly SIP):")
    for s in book.schemes[:_MAX_ROWS]:
        sip = (f" · SIP Rs.{s.sip_amount:,.0f}/mo" if s.sip_amount > 0
               else " · no new money")
        broker = (f" · held at {', '.join(s.brokers)}"
                  if len(s.brokers) > 1 else "")
        lines.append(
            f"- {s.fund}: {s.units:,.3f} units · avg {s.avg_nav:,.2f} · "
            f"NAV {s.nav:,.2f} · {s.pnl_pct:+.1f}%{sip}{broker}")
    if len(book.schemes) > _MAX_ROWS:
        lines.append(f"(showing the top {_MAX_ROWS} of {len(book.schemes)} "
                     f"funds by value)")

    # The exposure map is the most useful thing an advisor can react to.
    try:
        from modes.mf.insights import exposure_clusters
        clusters = exposure_clusters(book)
        redundant = [c for c in clusters if c.is_redundant]
        if redundant:
            lines.append("")
            lines.append("Same exposure held more than once:")
            for c in redundant:
                names = ", ".join(s.fund for s in c.schemes)
                lines.append(f"- {c.label} ({c.weight_pct:.1f}% of the fund "
                             f"book): {names}")
    except Exception:
        pass
    return "\n".join(lines)


def _us_section(us: dict, rate: float) -> str:
    lines = ["=== US EQUITY (long-term book, not swing) ==="]
    invested = float(us.get("invested_usd") or 0)
    current = float(us.get("current_usd") or 0)
    priced = bool(us.get("live"))

    lines.append(f"- Invested (cost basis): ${invested:,.2f}")
    if priced:
        lines.append(f"- Current value: ${current:,.2f}")
        lines.append(
            f"- Unrealised P&L: ${float(us.get('pnl_usd') or 0):+,.2f} "
            f"({float(us.get('pnl_pct') or 0):+.2f}%)")
        if rate > 0:
            lines.append(f"- In rupees: Rs.{current * rate:,.0f}")
    else:
        # Without live quotes this book is marked at cost. Reporting
        # "+0.00%" here would be a fabricated number, and the net-worth
        # figure above inherits the same understatement.
        lines.append("- Current value: NOT PRICED — no live US quote was "
                     "available, so this book is shown at cost and its "
                     "unrealised P&L is unknown. Please look up current "
                     "prices for the holdings below.")
        if rate > 0:
            lines.append(f"- At cost, in rupees: Rs.{invested * rate:,.0f}")
    lines.append(f"- Positions: {us.get('positions', 0)}")
    lines.append("- These are long-term holdings (RSU lots plus deliberate "
                 "long-horizon buys), held to compound — not trades.")

    try:
        from modes.dashboard.us_page import _us_positions
        positions = _us_positions()
    except Exception:
        positions = []

    if positions:
        lines.append("")
        lines.append("Holdings (symbol · qty · avg cost):")
        for p in positions[:_MAX_ROWS]:
            lines.append(
                f"- {p.symbol}: {_qty_str(getattr(p, 'managed_qty', 0))} sh · "
                f"avg ${float(getattr(p, 'entry_price', 0) or 0):,.2f}")
        if len(positions) > _MAX_ROWS:
            lines.append(f"(showing the first {_MAX_ROWS} of "
                         f"{len(positions)} positions)")
    return "\n".join(lines)


def _portfolio_detail_block(sym: str) -> str:
    if not sym:
        return "(No valid stock symbol provided.)"
    try:
        from modes.analyze.persistence import latest_for_symbol
        s = latest_for_symbol(sym)
    except Exception:
        s = None
    if s is None:
        return (f"Stock: {sym} (Indian equity)\n"
                f"(No analysis on file yet in my tracker — please research "
                f"this stock from scratch.)")

    held = _num(getattr(s, "qty", None)) > 0
    lines = [f"Stock: {s.symbol} ({getattr(s, 'exchange', 'NSE')}) — Indian equity"]
    if held:
        lines += [
            "- I HOLD this stock.",
            f"- Quantity: {_qty_str(s.qty)} shares",
            f"- My average buy price: Rs.{_num(s.avg_buy_price):,.2f}",
            f"- Total invested: Rs.{_num(s.invested_value):,.0f}",
            f"- Last recorded price (verify current): Rs.{_num(s.current_price):,.2f}",
            f"- Current value (snapshot): Rs.{_num(s.current_value):,.0f}",
            f"- Unrealised P&L (snapshot): Rs.{_num(s.pnl):+,.0f} ({_num(s.pnl_pct):+.2f}%)",
        ]
    else:
        lines.append("- I do NOT currently hold this (watchlist / considering a buy).")

    lines += [
        "",
        "Market context from my last snapshot (verify yourself):",
        f"- Sector: {_txt(s.sector, 'n/a')}",
        f"- 52-week range: Rs.{_num(s.low_52w):,.2f} – Rs.{_num(s.high_52w):,.2f} "
        f"({_num(s.price_vs_high_52w_pct):+.2f}% from high)",
        f"- P/E (TTM): {_num(s.weighted_pe):.1f}",
        f"- Dividend yield (TTM): {_num(s.dividend_yield_ttm):.2f}%",
        f"- Beta vs NIFTY: {_num(s.beta_vs_nifty):.2f}",
        f"- RSI (14, daily): {_num(s.rsi_daily):.1f}",
        f"- SMA-50: Rs.{_num(s.sma_50):,.2f} · SMA-200: Rs.{_num(s.sma_200):,.2f}",
    ]

    rule_action = _txt(getattr(s, "rule_action", None))
    if rule_action:
        lines += [
            "",
            "My tool's deterministic (non-AI) rule-based view:",
            f"- Action: {rule_action} · conviction {_txt(s.rule_conviction, 'n/a')} "
            f"· horizon {_txt(s.rule_horizon, 'n/a')}",
            f"- Target: {_txt(s.rule_target_price, 'n/a')}",
            f"- Reasoning: {_txt(s.rule_reasoning, 'n/a')}",
        ]
    return "\n".join(lines)


def _portfolio_overview_block() -> str:
    try:
        from modes.analyze.persistence import latest_snapshot
        snap = latest_snapshot()
    except Exception:
        snap = None
    if snap is None or not getattr(snap, "holdings", None):
        return "(No portfolio snapshot on file yet.)"

    m = snap.metrics
    lines = [
        "My overall Indian equity portfolio (snapshot — verify live values):",
        f"- Total invested: Rs.{_num(m.total_invested):,.0f}",
        f"- Current value: Rs.{_num(m.total_current_value):,.0f}",
        f"- Unrealised P&L: Rs.{_num(m.total_pnl):+,.0f} ({_num(m.total_pnl_pct):+.2f}%)",
        f"- Number of holdings: {len(snap.holdings)}",
        "",
        "Holdings (symbol · qty · avg buy · last price · P&L% · sector):",
    ]
    holdings = sorted(
        snap.holdings,
        key=lambda h: _num(getattr(h, "current_value", None)),
        reverse=True,
    )
    for h in holdings:
        lines.append(
            f"- {h.symbol}: {_qty_str(h.qty)} sh · "
            f"avg Rs.{_num(h.avg_buy_price):,.2f} · "
            f"last Rs.{_num(h.current_price):,.2f} · "
            f"{_num(h.pnl_pct):+.1f}% · {_txt(h.sector, 'n/a')}"
        )
    return "\n".join(lines)


def _swing_detail_block(sym: str) -> str:
    if not sym:
        return "(No valid stock symbol provided.)"
    cand = None
    try:
        from modes.swing.persistence import (
            candidate_by_symbol, dip_candidate_by_symbol,
        )
        cand = candidate_by_symbol(sym) or dip_candidate_by_symbol(sym)
    except Exception:
        cand = None

    held = None
    try:
        from modes.swing.persistence import open_positions
        for p in open_positions(exchange="NSE"):
            if p.symbol.strip().upper() == sym and p.status == "OPEN":
                held = p
                break
    except Exception:
        held = None

    lines = [f"Swing-trade candidate: {sym} (Indian equity, NSE)"]
    if held is not None:
        lines += [
            "- I already HOLD an open swing position in this:",
            f"  · Quantity: {_qty_str(getattr(held, 'managed_qty', 0))} shares",
            f"  · My entry price: Rs.{float(getattr(held, 'entry_price', 0) or 0):,.2f}",
            f"  · My stop: Rs.{float(getattr(held, 'stop_price', 0) or 0):,.2f}",
            f"  · My target: Rs.{float(getattr(held, 'target_price', 0) or 0):,.2f}",
            f"  · Entered: {getattr(held, 'entry_date', 'n/a')}",
        ]
    else:
        lines.append("- I do NOT currently hold this (evaluating a new entry).")

    if cand is not None:
        lines += [
            "",
            "My tool's swing setup for this stock (verify price yourself):",
            f"- Setup type: {getattr(cand, 'setup_type', 'n/a')}",
            f"- Sector: {getattr(cand, 'sector', 'n/a')}",
            f"- Suggested entry: Rs.{float(getattr(cand, 'entry_price', 0) or 0):,.2f}",
            f"- Stop loss: Rs.{float(getattr(cand, 'stop_price', 0) or 0):,.2f}",
            f"- Target: Rs.{float(getattr(cand, 'target_price', 0) or 0):,.2f}",
            f"- Risk:reward: {float(getattr(cand, 'rr_ratio', 0) or 0):.1f}x "
            f"(risk Rs.{float(getattr(cand, 'risk_rupees', 0) or 0):,.0f} / "
            f"reward Rs.{float(getattr(cand, 'reward_rupees', 0) or 0):,.0f})",
            f"- Suggested quantity: {_qty_str(getattr(cand, 'suggested_qty', 0))} shares",
            f"- Composite score: {float(getattr(cand, 'composite_score', 0) or 0):.2f}",
        ]
        reasons = getattr(cand, "reasons", None) or []
        if reasons:
            lines.append("- Reasons flagged: " + "; ".join(str(r) for r in reasons))
    elif held is None:
        lines.append("(No swing setup on file for this symbol — research from scratch.)")
    return "\n".join(lines)


def _swing_overview_block() -> str:
    lines = ["My Indian (NSE) swing-trading book:"]
    try:
        from modes.swing.persistence import open_positions, realised_pnl_summary
        positions = open_positions(exchange="NSE")
        pnl = realised_pnl_summary(exchange="NSE") or {}
    except Exception:
        positions, pnl = [], {}

    lines.append(
        f"- Realised P&L to date: net Rs.{float(pnl.get('net_pnl', 0) or 0):+,.0f} "
        f"across {int(pnl.get('count', 0) or 0)} closed trades"
    )
    if positions:
        lines.append("")
        lines.append("Open swing positions (symbol · qty · entry · stop · target):")
        for p in positions:
            lines.append(
                f"- {p.symbol}: {_qty_str(getattr(p, 'managed_qty', 0))} sh · "
                f"entry Rs.{float(getattr(p, 'entry_price', 0) or 0):,.2f} · "
                f"stop Rs.{float(getattr(p, 'stop_price', 0) or 0):,.2f} · "
                f"target Rs.{float(getattr(p, 'target_price', 0) or 0):,.2f}"
            )
    else:
        lines.append("- No open swing positions right now.")
    return "\n".join(lines)


def _us_detail_block(sym: str) -> str:
    if not sym:
        return "(No valid stock symbol provided.)"
    row = None
    try:
        from modes.dashboard import us_config
        from modes.dashboard.us_analysis import analyse_us_symbol
        row = analyse_us_symbol(sym, float(us_config.US_TICKET_AMOUNT))
    except Exception:
        row = None

    held = None
    try:
        from modes.dashboard.us_page import _us_positions
        for p in _us_positions():
            if p.symbol.strip().upper() == sym:
                held = p
                break
    except Exception:
        held = None

    lines = [f"US swing-trade candidate: {sym} (US equity)"]
    if held is not None:
        lines += [
            "- I already HOLD an open position in this:",
            f"  · Quantity: {_qty_str(getattr(held, 'managed_qty', 0))} shares",
            f"  · My entry price: ${float(getattr(held, 'entry_price', 0) or 0):,.2f}",
            f"  · My stop: ${float(getattr(held, 'stop_price', 0) or 0):,.2f}",
            f"  · My target: ${float(getattr(held, 'target_price', 0) or 0):,.2f}",
        ]
    else:
        lines.append("- I do NOT currently hold this (evaluating a new entry).")

    if row:
        ind = row.get("indicators") or {}
        lines += [
            "",
            "My tool's US setup for this stock (verify price/news yourself):",
            f"- Name: {row.get('stock_name', 'n/a')} · Exchange: {row.get('exchange', 'n/a')}",
            f"- Setup type: {row.get('setup_type', 'n/a')}",
            f"- Suggested entry: ${float(row.get('entry_price') or 0):,.2f}",
            f"- Stop loss: ${float(row.get('stop_price') or 0):,.2f}",
            f"- Target: ${float(row.get('target_price') or 0):,.2f}",
            f"- Risk:reward: {float(row.get('rr_ratio') or 0):.2f}x",
            f"- Suggested quantity: {_qty_str(row.get('suggested_qty'))} shares",
            f"- Composite score: {float(row.get('score') or 0):.2f}",
            f"- 52w high: ${float(ind.get('high_52w') or 0):,.2f} "
            f"({float(ind.get('dip_from_52w_high_pct') or 0):.2f}% below high)",
            f"- Data through: {row.get('as_of', 'n/a')}",
        ]
        reasons = row.get("reasons") or []
        if reasons:
            lines.append("- Reasons flagged: " + "; ".join(str(r) for r in reasons))
        warnings = row.get("warnings") or []
        if warnings:
            lines.append("- Warnings: " + "; ".join(str(w) for w in warnings))
    elif held is None:
        lines.append("(No US setup on file for this symbol — research from scratch.)")
    return "\n".join(lines)


def _us_overview_block() -> str:
    lines = ["My US (NASDAQ/NYSE) swing-trading book:"]
    try:
        from modes.dashboard.us_page import _us_positions
        positions = _us_positions()
    except Exception:
        positions = []

    fx_note = ""
    try:
        from modes.dashboard.us_analysis import get_usd_inr_rate
        fx = get_usd_inr_rate() or {}
        if fx.get("rate"):
            fx_note = f" (USD/INR ~{float(fx['rate']):.2f})"
    except Exception:
        fx_note = ""

    lines.append(f"- Currency: USD{fx_note}")
    if positions:
        lines.append("")
        lines.append("Open US positions (symbol · qty · entry · stop · target):")
        for p in positions:
            lines.append(
                f"- {p.symbol}: {_qty_str(getattr(p, 'managed_qty', 0))} sh · "
                f"entry ${float(getattr(p, 'entry_price', 0) or 0):,.2f} · "
                f"stop ${float(getattr(p, 'stop_price', 0) or 0):,.2f} · "
                f"target ${float(getattr(p, 'target_price', 0) or 0):,.2f}"
            )
    else:
        lines.append("- No open US positions right now.")
    return "\n".join(lines)
