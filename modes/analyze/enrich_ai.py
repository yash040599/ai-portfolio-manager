# ================================================================
# modes/analyze/enrich_ai.py
# ================================================================
# Claude AI overlay on top of NoAI-enriched StockAnalysis records.
#
# AI does NOT regenerate any deterministic number (price, P&L, RSI,
# beta, sector, P/E). It only writes into the AI-only slots:
#   ai_thesis_long_term
#   ai_qualitative_risks
#   ai_peer_comparison
#   ai_news_context
#   ai_change_vs_prior
#   ai_action / ai_action_detail
#
# The prompt inlines the deterministic numbers as fixed context so
# Claude can cite them but cannot change them.
#
# Cost target (Pro plan, ~30 holdings): ~Rs.150/full-portfolio run.
# ================================================================

from __future__ import annotations

import re
import time

from config              import Config, now_ist
from core.claude_client  import ClaudeClient
from core.logger         import Logger
from modes.analyze.persistence import history_for_symbol
from modes.analyze.types import (
    Field,
    StockAnalysis,
    SRC_CLAUDE_FREE,
    SRC_CLAUDE_MAX,
    SRC_CLAUDE_PRO,
)


PER_STOCK_PAUSE_SECONDS = 1.0   # pause between Claude calls (rate-limit cushion)
MAX_RETRIES             = 2     # transient retry budget per stock


# ── Public entry ───────────────────────────────────────────────

def overlay_ai(
    holdings: list[StockAnalysis],
    *,
    claude: ClaudeClient,
    log: Logger,
    cfg: type[Config] | None = None,
) -> list[StockAnalysis]:
    """Mutates each `StockAnalysis` in `holdings` in place: fills the
    AI-only slots from a single Claude call per stock. Returns the
    same list for chaining."""
    cfg = cfg or Config
    plan_name   = cfg.CLAUDE_PLAN
    plan_source = {
        "free": SRC_CLAUDE_FREE,
        "pro":  SRC_CLAUDE_PRO,
        "max":  SRC_CLAUDE_MAX,
    }.get(plan_name, SRC_CLAUDE_PRO)
    cost_estimate = len(holdings) * float(getattr(cfg, "CLAUDE_COST_PER_CALL", 3.0))
    log.info(
        f"AI overlay running on {len(holdings)} stocks "
        f"(plan={plan_name.upper()}, est. cost ~Rs.{cost_estimate:.0f})"
    )

    failed: list[str] = []
    for stock in holdings:
        try:
            _overlay_one(stock, claude=claude, log=log,
                         plan_source=plan_source)
        except Exception as e:
            log.warning(f"AI overlay failed for {stock.symbol}: {e}")
            failed.append(stock.symbol)
        time.sleep(PER_STOCK_PAUSE_SECONDS)

    if failed:
        log.warning(
            f"AI overlay incomplete on {len(failed)} stocks: "
            f"{', '.join(failed)} (NoAI fields still populated)"
        )
    else:
        log.success(f"AI overlay complete on {len(holdings)} stocks")
    return holdings


# ── Per-stock overlay ──────────────────────────────────────────

def _overlay_one(stock: StockAnalysis, *,
                 claude: ClaudeClient, log: Logger,
                 plan_source: str) -> None:
    """Build the prompt, call Claude, parse, set the AI-only fields."""
    prior = _prior_summary(stock.symbol)
    prompt = _build_prompt(stock, prior=prior)

    last_error: Exception | None = None
    raw = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = claude.call(prompt)
            break
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES:
                log.debug(f"Retrying {stock.symbol} (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                time.sleep(2.0)
    if not raw:
        raise RuntimeError(f"Claude call failed: {last_error}")

    parsed = _parse(raw)
    ts = now_ist()

    stock.ai_thesis_long_term  = Field(value=parsed["thesis"],     source=plan_source, as_of=ts)
    stock.ai_qualitative_risks = Field(value=parsed["risks"],      source=plan_source, as_of=ts)
    stock.ai_peer_comparison   = Field(value=parsed["peers"],      source=plan_source, as_of=ts)
    stock.ai_news_context      = Field(value=parsed["news"],       source=plan_source, as_of=ts)
    stock.ai_change_vs_prior   = Field(value=parsed["change"],     source=plan_source, as_of=ts)
    stock.ai_action            = Field(value=parsed["action"],     source=plan_source, as_of=ts)
    stock.ai_action_detail     = Field(value=parsed["action_why"], source=plan_source, as_of=ts)


# ── Prompt builder ─────────────────────────────────────────────

_OUTPUT_FORMAT = """
You MUST use EXACTLY this format. No text before or after.

THESIS: [3-5 short bullets, '-' prefix, long-term (2-3 year horizon) view ONLY]
RISKS:
- [risk 1 — concise]
- [risk 2]
- [risk 3]
PEERS: [one paragraph (3-5 sentences) comparing this stock to its 2-3 closest sector peers]
NEWS: [one paragraph (max 5 sentences) on news/macro events from the LAST 30 DAYS that materially affect this name. If nothing material, write "No material news events in last 30 days."]
CHANGE_VS_PRIOR: [one paragraph: what (if anything) has changed in your view since the prior analysis. If no prior, write "First analysis on record."]
ACTION: [HOLD | BUY MORE | AVERAGE DOWN | PARTIAL EXIT | FULL EXIT]
ACTION_DETAIL: [one sentence justifying the action choice in long-term context]
---END---
"""


def _build_prompt(stock: StockAnalysis, prior: str) -> str:
    """Build the Claude prompt with NoAI numbers inlined as fixed
    context so the model can cite but not change them."""
    fmt = _format_numbers(stock)
    return f"""You are a senior Indian-equities analyst (CFA + 12 years of buy-side experience).
The user holds the position described below in their long-term portfolio. Your job is to add QUALITATIVE colour:
long-term thesis, risks, peer comparison, recent news context, and a recommended ACTION.

ABSOLUTE RULES:
1. Do NOT restate or recompute the numbers below. They are deterministic and frozen.
2. Long-term horizon ONLY (2-3 years). Do NOT discuss intraday, options, F&O, or week-to-week trades.
3. Indian-market context only (NSE / BSE; INR; SEBI rules).
4. Be concrete. No hedging clauses like "however market conditions may vary".
5. If there is genuinely no material news in the last 30 days, say so.

POSITION & MARKET CONTEXT (deterministic — DO NOT change):
{fmt}

PRIOR ANALYSIS HISTORY:
{prior}

{_OUTPUT_FORMAT}
"""


def _format_numbers(s: StockAnalysis) -> str:
    """Compact, human-readable summary of every NoAI field for the prompt."""
    def v(f, fmt="{}"):
        if f is None or f.value is None:
            return "n/a"
        try:
            return fmt.format(f.value)
        except Exception:
            return str(f.value)

    return (
        f"  Symbol         : {s.symbol} ({s.exchange})\n"
        f"  Sector         : {v(s.sector)}\n"
        f"  Quantity       : {v(s.qty)}\n"
        f"  Avg buy price  : Rs.{v(s.avg_buy_price, '{:.2f}')}\n"
        f"  Current price  : Rs.{v(s.current_price, '{:.2f}')}\n"
        f"  Invested value : Rs.{v(s.invested_value, '{:,.2f}')}\n"
        f"  Current value  : Rs.{v(s.current_value, '{:,.2f}')}\n"
        f"  P&L            : Rs.{v(s.pnl, '{:,.2f}')} ({v(s.pnl_pct, '{:+.2f}')}%)\n"
        f"  Weight in port : {v(s.weight_in_portfolio_pct, '{:.2f}')}%\n"
        f"  52-week range  : Rs.{v(s.low_52w, '{:.2f}')} - Rs.{v(s.high_52w, '{:.2f}')}\n"
        f"  vs 52w high    : {v(s.price_vs_high_52w_pct, '{:+.2f}')}%\n"
        f"  SMA-50         : Rs.{v(s.sma_50, '{:.2f}')}\n"
        f"  SMA-200        : Rs.{v(s.sma_200, '{:.2f}')}\n"
        f"  Above SMA-200  : {v(s.above_sma_200)}\n"
        f"  RSI (daily,14) : {v(s.rsi_daily, '{:.1f}')}\n"
        f"  Beta vs NIFTY  : {v(s.beta_vs_nifty, '{:.2f}')}\n"
        f"  TTM P/E        : {v(s.weighted_pe, '{:.1f}')}\n"
        f"  TTM div yield  : {v(s.dividend_yield_ttm, '{:.2f}')}%\n"
        f"  RULE-BASED ACTION : {v(s.rule_action)} ({v(s.rule_conviction)} conviction, "
        f"{v(s.rule_horizon)} horizon)\n"
        f"  RULE TARGET PRICE : {v(s.rule_target_price)}\n"
    )


def _prior_summary(symbol: str) -> str:
    """Return a short summary of the latest prior analysis (if any)
    so Claude's CHANGE_VS_PRIOR has something concrete to compare to."""
    history = history_for_symbol(symbol, limit=2)
    if len(history) < 2:
        # The most recent record is the run-in-progress (not yet
        # written), so we need at least 2 to have a "prior".
        prior = history[0] if history else None
    else:
        prior = history[1]
    if prior is None:
        return "  (no prior analysis in DB — first run for this symbol)\n"
    when = prior.most_stale_at().strftime("%Y-%m-%d")
    action = prior.effective_action()
    pnl_pct = (prior.pnl_pct.value if prior.pnl_pct else 0.0) or 0.0
    target = (prior.rule_target_price.value if prior.rule_target_price else "") or ""
    return (
        f"  Latest prior on {when}: action={action}, "
        f"P&L was {pnl_pct:+.2f}%, target {target}\n"
    )


# ── Response parser ────────────────────────────────────────────

# Each capture is loose so a missing slot becomes empty rather than failing.
_FIELD_RE = re.compile(
    r"^(THESIS|RISKS|PEERS|NEWS|CHANGE_VS_PRIOR|ACTION|ACTION_DETAIL)\s*:\s*(.*?)$",
    re.MULTILINE,
)


def _parse(raw: str) -> dict:
    """Pull each labelled section out of Claude's response. Tolerates
    line-wrapped values and missing fields."""
    text = raw.split("---END---")[0]
    fields: dict[str, str] = {}
    matches = list(_FIELD_RE.finditer(text))
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        fields[name] = (m.group(2) + "\n" + text[start:end]).strip()

    # THESIS / RISKS arrive as bullet lists; split on lines starting with '-' or '*'.
    thesis = _bullets(fields.get("THESIS", ""))
    risks  = _bullets(fields.get("RISKS",  ""))
    return {
        "thesis":     "\n".join(f"- {b}" for b in thesis) if thesis else "",
        "risks":      risks,
        "peers":      _flatten(fields.get("PEERS", "")),
        "news":       _flatten(fields.get("NEWS",  "")),
        "change":     _flatten(fields.get("CHANGE_VS_PRIOR", "")),
        "action":     _normalise_action(fields.get("ACTION", "")),
        "action_why": _flatten(fields.get("ACTION_DETAIL", "")),
    }


def _bullets(block: str) -> list[str]:
    out = []
    for ln in block.splitlines():
        s = ln.strip()
        if s.startswith(("-", "*")):
            s = s.lstrip("-*").strip()
            if s:
                out.append(s)
        elif s and not out and len(block.splitlines()) == 1:
            out.append(s)
    return out


def _flatten(block: str) -> str:
    return " ".join(p.strip() for p in block.splitlines() if p.strip())


_VALID_ACTIONS = {"HOLD", "BUY MORE", "AVERAGE DOWN", "PARTIAL EXIT", "FULL EXIT"}


def _normalise_action(raw: str) -> str:
    s = (raw or "").strip().upper()
    s = re.sub(r"[^A-Z ]+", "", s)
    if not s:
        return "HOLD"
    for valid in _VALID_ACTIONS:
        if valid in s:
            return valid
    return "HOLD"
