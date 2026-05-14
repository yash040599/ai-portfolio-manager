"""Plain-text dashboard renderer (Roadmap D1).

Sections rendered:
  - Header (window + data quality summary)
  - Section A: Headline P&L  (sheet-verified by default)
  - Section E: Capital-ladder verdict
  - Pending-verification banner (if any)

D2/D3 will append trade-quality, risk, and diagnostics sections.
D4 introduces the HTML renderer; this text path stays for `--text`.
"""

from __future__ import annotations

from config import Config
from modes.dashboard.metrics import HeadlinePnL, net_pnl_pct
from modes.dashboard.verdict import VerdictResult


_BAR = "─" * 68


def render(
    *,
    date_from: str,
    date_to: str,
    trading_day_count: int,
    verified_day_count: int,
    pending_dates: list[str],
    headline: HeadlinePnL,
    verdict: VerdictResult,
    budget: int,
    include_provisional: bool,
) -> str:
    """Build the full text dashboard as a single string."""
    parts: list[str] = []

    parts.append(_header(
        date_from, date_to, trading_day_count,
        verified_day_count, pending_dates, include_provisional,
    ))
    parts.append(_section_verdict(verdict))
    parts.append(_section_headline(headline, budget))
    if pending_dates:
        parts.append(_section_pending(pending_dates, include_provisional))
    parts.append("")
    return "\n".join(parts)


# ── Sections ──────────────────────────────────────────────────────

def _header(
    date_from: str,
    date_to: str,
    trading_day_count: int,
    verified_day_count: int,
    pending_dates: list[str],
    include_provisional: bool,
) -> str:
    qual = (
        f"verified {verified_day_count}  "
        f"pending {len(pending_dates)}"
    )
    src = "sheet-verified + provisional" if include_provisional else "sheet-verified ONLY"
    stage = str(getattr(Config, "TRADE_RESEARCH_STAGE", "") or "")
    label = str(getattr(Config, "TRADE_RESEARCH_PHASE_LABEL", "") or "")
    paused = bool(getattr(Config, "TRADE_LIVE_TRADING_PAUSED", False))
    research_line = ""
    if label:
        phase = f"{stage} - {label}" if stage else label
        pause = "live trading paused" if paused else "live trading enabled"
        research_line = f"  Research: {phase}  |  {pause}\n"
    return (
        f"\n{_BAR}\n"
        f"  AI Portfolio Manager — Profitability Dashboard\n"
        f"{research_line}"
        f"  Window: {date_from} → {date_to}  ({trading_day_count} trading days)\n"
        f"  Data:   {qual}  |  source: {src}\n"
        f"{_BAR}\n"
    )


def _section_verdict(v: VerdictResult) -> str:
    icon = {
        "GREEN": "[GREEN]",
        "AMBER": "[AMBER]",
        "RED":   "[RED]  ",
        "GREY":  "[GREY] ",
    }[v.verdict]
    rec_line = (
        f"  Current budget: Rs.{v.current_budget:,}   "
        f"Recommended: Rs.{v.recommended_budget:,}"
    )
    body = [
        "",
        "── VERDICT " + "─" * 57,
        f"  {icon}  {v.headline}",
        rec_line,
        f"  {v.rationale}",
    ]
    if v.failed_thresholds and v.verdict != "GREEN":
        body.append("  Failed thresholds:")
        for t in v.failed_thresholds:
            body.append(f"    - {t}")
    body.append("")
    return "\n".join(body)


def _section_headline(h: HeadlinePnL, budget: int) -> str:
    if h.trade_count == 0:
        return (
            "── HEADLINE P&L " + "─" * 53 + "\n"
            "  No trades in window.\n"
        )
    pct = net_pnl_pct(h.net_pnl, budget)
    pct_str = f"  ({pct * 100:+.2f}% of Rs.{budget:,})" if pct is not None else ""
    best  = h.best_day  or ("—", 0.0)
    worst = h.worst_day or ("—", 0.0)
    return (
        "── HEADLINE P&L " + "─" * 53 + "\n"
        f"  Trades:        {h.trade_count}  over {h.trading_days} days\n"
        f"  Gross:         Rs.{h.gross_pnl:>+12,.2f}\n"
        f"  Charges:       Rs.{-h.total_charges:>+12,.2f}\n"
        f"  Net:           Rs.{h.net_pnl:>+12,.2f}{pct_str}\n"
        f"  Best day:      Rs.{best[1]:>+12,.2f}  ({best[0]})\n"
        f"  Worst day:     Rs.{worst[1]:>+12,.2f}  ({worst[0]})\n"
    )


def _section_pending(dates: list[str], include_provisional: bool) -> str:
    if include_provisional:
        intro = (
            f"  {len(dates)} trading day(s) included as PROVISIONAL "
            f"(numbers above may shift on T+1 sheet reconciliation):"
        )
    else:
        intro = (
            f"  {len(dates)} trading day(s) excluded from headline numbers above:"
        )
    body = [
        "── PENDING SHEET VERIFICATION " + "─" * 39,
        intro,
    ]
    for d in dates:
        body.append(f"    - {d}")
    body.append("")
    body.append("  To finalise:")
    body.append("    1. Download Zerodha Tax P&L (Console -> Reports -> Tax P&L)")
    body.append("    2. Save xlsx into data/ZerodhaTaxPL/")
    body.append("    3. Run: python scripts/shared/import_zerodha_taxpnl.py --fy <YYYY>")
    body.append("")
    return "\n".join(body)


__all__ = ["render"]
