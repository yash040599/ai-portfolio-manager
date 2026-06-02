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
     style="border:1px solid #d0d7de;border-radius:8px;background:#f6f8fa;
            padding:14px 16px;margin-bottom:16px">
  <div style="font-size:14px;font-weight:600;margin-bottom:4px">
    Ask AI {subject}
  </div>
  <div style="font-size:12px;color:#666;margin-bottom:10px">
    Our built-in AI is a free tier. Type your question and we'll build a
    ready-to-paste prompt — packed with <em>your</em> personal position
    data — that you can drop into ChatGPT, Claude or Gemini (free) for a
    stronger answer. Live prices &amp; news are left for that model to
    look up itself.
  </div>
  <textarea class="chat-question" rows="2"
            placeholder="{html.escape(placeholder)}"
            style="width:100%;box-sizing:border-box;padding:8px;
                   border:1px solid #ccc;border-radius:6px;font-size:13px;
                   font-family:inherit;resize:vertical"></textarea>
  <div style="display:flex;gap:8px;align-items:center;margin-top:8px;
              flex-wrap:wrap">
    <button class="chat-build"
            style="padding:6px 14px;border-radius:6px;border:1px solid #1a7f37;
                   background:#1a7f37;color:#fff;cursor:pointer;font-size:13px">
      Build prompt
    </button>
    <button class="chat-copy" disabled
            style="padding:6px 14px;border-radius:6px;border:1px solid #1a7f37;
                   background:#fff;color:#1a7f37;cursor:pointer;font-size:13px;
                   opacity:0.5">
      Copy
    </button>
    <span class="chat-msg" style="font-size:12px;color:#666"></span>
  </div>
  <textarea class="chat-output" readonly rows="10"
            placeholder="Your generated prompt will appear here…"
            style="width:100%;box-sizing:border-box;padding:8px;margin-top:10px;
                   border:1px solid #ccc;border-radius:6px;font-size:12px;
                   font-family:ui-monospace,Menlo,Consolas,monospace;
                   background:#fff;resize:vertical;display:none"></textarea>
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
      msg.style.color = '#666';
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
          msg.style.color = '#1a7f37';
          msg.textContent = 'Prompt ready — copy & paste into ChatGPT / Claude / Gemini.';
        } else {
          msg.style.color = '#d1242f';
          msg.textContent = (d && d.error) || 'Could not build prompt.';
        }
      }).catch(function(e){
        buildBtn.disabled = false;
        msg.style.color = '#d1242f';
        msg.textContent = 'Error: ' + e;
      });
    });

    copyBtn.addEventListener('click', function(){
      outBox.select();
      var done = function(){
        msg.style.color = '#1a7f37';
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


def _assemble(question: str, market: str, body: str) -> str:
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
1. A clear, actionable recommendation (e.g. buy more / hold / trim / exit),
   with the reasoning laid out simply.
2. Verify the CURRENT price and any recent news before concluding, and
   tell me if that changes the picture vs. my snapshot above.
3. Key risks I should watch, and a level/condition that would change your view.
4. If you need data I didn't provide, tell me exactly what to fetch.

Keep it concise and practical. I am the one deciding and executing the trade."""


# ── Scope data blocks ───────────────────────────────────────────

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
            f"- I HOLD this stock.",
            f"- Quantity: {int(_num(s.qty))} shares",
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
            f"- {h.symbol}: {int(_num(h.qty))} sh · "
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
            f"  · Quantity: {int(getattr(held, 'managed_qty', 0) or 0)} shares",
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
            f"- Suggested quantity: {int(getattr(cand, 'suggested_qty', 0) or 0)} shares",
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
                f"- {p.symbol}: {int(getattr(p, 'managed_qty', 0) or 0)} sh · "
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
            f"  · Quantity: {int(getattr(held, 'managed_qty', 0) or 0)} shares",
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
            f"- Suggested quantity: {int(row.get('suggested_qty') or 0)} shares",
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
    try:
        from modes.swing.persistence import realised_pnl_summary
        pnl = realised_pnl_summary() or {}
    except Exception:
        pnl = {}

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
                f"- {p.symbol}: {int(getattr(p, 'managed_qty', 0) or 0)} sh · "
                f"entry ${float(getattr(p, 'entry_price', 0) or 0):,.2f} · "
                f"stop ${float(getattr(p, 'stop_price', 0) or 0):,.2f} · "
                f"target ${float(getattr(p, 'target_price', 0) or 0):,.2f}"
            )
    else:
        lines.append("- No open US positions right now.")
    return "\n".join(lines)
