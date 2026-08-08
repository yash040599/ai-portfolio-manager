# ================================================================
# modes/analyze/report.py
# ================================================================
# Report writer for the Portfolio Analyser (--mode analyze).
#
# Replaces the old `modes/trade/report_writer.py::save_portfolio_report`
# path. Outputs:
#   reports/portfolio/<YYYY>/<MM>/portfolio_report_DD.txt   (human read)
#   reports/portfolio/<YYYY>/<MM>/portfolio_data_DD.json    (machine read)
#
# Drops the `.tsv` "sheet" file entirely (per ANALYZE_ROADMAP P5,
# section "Reports are read once, used once" — nobody reads it,
# the dashboard fully replaces its summary-table use case).
#
# Layout:
#   - Header: most-stale `as_of`, run mode, holdings + value + P&L
#   - PORTFOLIO METRICS (P6)
#   - WHAT'S MISSING (P7)
#   - PER-STOCK ANALYSIS (one card per holding)
# ================================================================

from __future__ import annotations

import datetime
import json
import os

from config              import now_ist
from core.logger         import Logger
from modes.analyze.types import (
    Field,
    PortfolioSnapshot,
    StockAnalysis,
)


# ── Path helpers ───────────────────────────────────────────────

def report_dir(date: datetime.date) -> str:
    return os.path.join(
        "reports", "portfolio", f"{date.year:04d}", f"{date.month:02d}",
    )


def report_txt_path(date: datetime.date) -> str:
    return os.path.join(report_dir(date), f"portfolio_report_{date.day:02d}.txt")


def report_json_path(date: datetime.date) -> str:
    return os.path.join(report_dir(date), f"portfolio_data_{date.day:02d}.json")


# ── Public entry ───────────────────────────────────────────────

def save_report(snapshot: PortfolioSnapshot, log: Logger | None = None) -> tuple[str, str]:
    """Write the .txt + .json reports for a snapshot. Returns
    (txt_path, json_path)."""
    today = snapshot.timestamp.date()
    os.makedirs(report_dir(today), exist_ok=True)
    txt = render_txt(snapshot)
    txt_path = report_txt_path(today)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt)
    json_path = report_json_path(today)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(snapshot.to_dict(), f, indent=2, default=str)
    if log:
        log.success(f"Report saved: {txt_path}")
        log.success(f"Data saved  : {json_path}")
    return txt_path, json_path


# ── Renderer ───────────────────────────────────────────────────

SEP_MAJOR = "=" * 72
SEP_MINOR = "─" * 72
SEP_THIN  = "·" * 72


def render_txt(snap: PortfolioSnapshot) -> str:
    parts: list[str] = []
    parts.append(_render_header(snap))
    parts.append(_render_metrics(snap))
    parts.append(_render_gaps(snap))
    parts.append(_render_holdings(snap))
    return "\n".join(parts) + "\n"


def _render_header(snap: PortfolioSnapshot) -> str:
    m = snap.metrics
    most_stale = snap.most_stale_at()
    age_min = max(0, int((now_ist() - most_stale).total_seconds() // 60))
    age_label = _age_label(age_min)
    holdings_count = len(snap.holdings)
    invested = _v(m.total_invested)
    current  = _v(m.total_current_value)
    pnl      = _v(m.total_pnl)
    pnl_pct  = _v(m.total_pnl_pct)
    when = snap.timestamp.strftime("%Y-%m-%d %H:%M IST")
    pnl_marker = "+" if pnl >= 0 else ""
    return (
        f"{SEP_MAJOR}\n"
        f"  PORTFOLIO ANALYSIS — {when}   [{snap.mode} run]\n"
        f"  Most stale field: {most_stale.strftime('%Y-%m-%d %H:%M')} ({age_label})\n"
        f"  Holdings: {holdings_count}  ·  "
        f"Invested: {_inr(invested)}  ·  "
        f"Current: {_inr(current)}  ·  "
        f"P&L: {pnl_marker}{_inr(pnl)} ({pnl_pct:+.2f}%)\n"
        f"{SEP_MAJOR}"
    )


def _render_metrics(snap: PortfolioSnapshot) -> str:
    m = snap.metrics
    out = ["\nPORTFOLIO METRICS", SEP_MINOR]

    if m.sector_weights:
        out.append("Sector weights (by current value):")
        for sw in m.sector_weights:
            bar = "▇" * max(1, int(round(sw.weight_pct / 2.5)))
            out.append(f"  {sw.sector:<10s} {sw.weight_pct:>5.1f}%  {bar}  ({sw.holdings_count} stock"
                       f"{'s' if sw.holdings_count != 1 else ''})")
        out.append("")

    # Market-cap tier breakdown (P9).
    if m.cap_tier_weights and isinstance(m.cap_tier_weights.value, dict) \
            and m.cap_tier_weights.value:
        out.append("Market-cap tier breakdown (AMFI):")
        # Render in fixed order so the eye finds each tier in the same place.
        for tier in ("LARGE", "MID", "SMALL", "ETF", "UNKNOWN"):
            pct = m.cap_tier_weights.value.get(tier)
            if pct is None:
                continue
            bar = "▇" * max(1, int(round(pct / 2.5)))
            tag = "  ⚠ refresh seed" if tier == "UNKNOWN" else ""
            out.append(f"  {tier:<8s} {pct:>5.1f}%  {bar}{tag}")
        out.append("")

    out.append(f"  HHI concentration       : {_v(m.hhi_concentration):>8.1f}    "
               f"({m.hhi_concentration.note or ''})".rstrip())
    out.append(f"  Top-5 concentration     : {_v(m.top_5_concentration_pct):>7.1f}%")
    out.append(f"  Largest single name     : {_v(m.single_name_max_pct):>7.1f}%   "
               f"({_v_str(m.single_name_max_symbol)})")

    if isinstance(m.group_concentration.value, dict) and m.group_concentration.value:
        groups_str = ", ".join(
            f"{g}={p:.1f}%" for g, p in sorted(
                m.group_concentration.value.items(),
                key=lambda kv: -kv[1],
            )
        )
        out.append(f"  Group concentration     : {groups_str}")

    out.append(f"  Weighted P/E (TTM)      : {_v(m.weighted_pe):>7.2f}     "
               f"{m.weighted_pe.note or ''}".rstrip())
    out.append(f"  Weighted div yield (TTM): {_v(m.weighted_dividend_yield):>7.2f}%    "
               f"{m.weighted_dividend_yield.note or ''}".rstrip())
    if m.annual_dividend_estimate and m.annual_dividend_estimate.value is not None:
        out.append(f"  Est. annual dividends   : {_inr(_v(m.annual_dividend_estimate)):>10s}     "
                   f"{m.annual_dividend_estimate.note or ''}".rstrip())
    out.append(f"  Beta vs NIFTY           : {_v(m.portfolio_beta_vs_nifty):>7.2f}")

    # ── Risk / return (P8 industry-standard) ──
    out.append("")
    out.append("Risk / return:")
    have_risk = any(getattr(m, f, None) and getattr(m, f).value is not None
                    for f in ("volatility_30d_pct", "sharpe_ratio",
                              "max_drawdown_pct", "xirr_pct"))
    if have_risk:
        if m.volatility_30d_pct and m.volatility_30d_pct.value is not None:
            out.append(f"  Volatility (annualised) : {m.volatility_30d_pct.value:>7.2f}%    "
                       f"{m.volatility_30d_pct.note or ''}".rstrip())
        else:
            out.append("  Volatility (annualised) :     n/a    needs >= 60 daily candles cached per held name")
        if m.sharpe_ratio and m.sharpe_ratio.value is not None:
            out.append(f"  Sharpe ratio            : {m.sharpe_ratio.value:>7.2f}     "
                       f"{m.sharpe_ratio.note or ''}".rstrip())
        else:
            out.append("  Sharpe ratio            :     n/a")
        if m.max_drawdown_pct and m.max_drawdown_pct.value is not None:
            out.append(f"  Max drawdown            : {m.max_drawdown_pct.value:>7.2f}%    "
                       f"{m.max_drawdown_pct.note or ''}".rstrip())
        else:
            out.append("  Max drawdown            :     n/a    needs >= 2 prior runs")
        if m.xirr_pct and m.xirr_pct.value is not None:
            out.append(f"  CAGR (compound annual)  : {m.xirr_pct.value:>+7.2f}%    "
                       f"{m.xirr_pct.note or ''}".rstrip())
        else:
            out.append("  CAGR (compound annual)  :     n/a    needs oldest snapshot >= 30d old")
    else:
        out.append("  Volatility (annualised) :     n/a    needs >= 60 daily candles cached per held name")
        out.append("  Sharpe ratio            :     n/a")
        out.append("  Max drawdown            :     n/a    needs >= 2 prior runs")
        out.append("  CAGR (compound annual)  :     n/a    needs oldest snapshot >= 30d old")

    # ── Cash position ──
    if m.cash_balance and m.cash_balance.value is not None:
        out.append("")
        out.append("Cash position:")
        out.append(f"  Cash balance            : {_inr(_v(m.cash_balance)):>10s}     "
                   f"{m.cash_balance.note or ''}".rstrip())
        if m.cash_drag_pct and m.cash_drag_pct.value is not None:
            tag = ""
            try:
                from config import Config as _Cfg
                cd_thresh = float(getattr(_Cfg, "CASH_DRAG_FLAG_PCT", 25.0))
                if m.cash_drag_pct.value > cd_thresh:
                    tag = f"  ⚠ above {cd_thresh:.0f}% — under-invested"
            except Exception:
                pass
            out.append(f"  Cash drag               : {m.cash_drag_pct.value:>7.2f}%{tag}")
    return "\n".join(out)


def _render_gaps(snap: PortfolioSnapshot) -> str:
    g = snap.gaps
    if not g.flags:
        return (
            "\nWHAT'S MISSING\n"
            f"{SEP_MINOR}\n"
            "  No structural gaps detected against benchmark. Portfolio "
            "diversification looks healthy.\n"
        )
    out = ["\nWHAT'S MISSING", SEP_MINOR,
           f"  Benchmark: {g.benchmark_label}", ""]
    for f in g.flags:
        marker = {"RISK": "⚠", "WARN": "⚠", "INFO": "·"}.get(f.severity, "·")
        out.append(f"  {marker} [{f.severity:<4s}] {f.headline}")
        out.append(f"      {f.detail}")
        if f.suggested_symbols:
            out.append(f"      Suggested: {' / '.join(f.suggested_symbols)}")
        out.append("")
    return "\n".join(out)


def _render_holdings(snap: PortfolioSnapshot) -> str:
    out = ["\nPER-STOCK ANALYSIS", SEP_MAJOR]
    for s in snap.holdings:
        out.append(_render_one_stock(s, mode=snap.mode))
    return "\n".join(out)


def _render_one_stock(s: StockAnalysis, *, mode: str) -> str:
    age_min = max(0, int((now_ist() - s.most_stale_at()).total_seconds() // 60))
    age_label = _age_label(age_min)

    pnl     = _v(s.pnl)
    pnl_pct = _v(s.pnl_pct)
    weight  = _v(s.weight_in_portfolio_pct)
    pnl_sign = "+" if pnl >= 0 else ""

    lines = [
        SEP_MINOR,
        f"  {s.symbol} ({s.exchange})  ·  {age_label}  ·  weight {weight:.1f}%",
        SEP_MINOR,
        f"  Position    : {_v(s.qty):>5.0f} sh  ·  Avg Rs.{_v(s.avg_buy_price):,.2f}  "
        f"Current Rs.{_v(s.current_price):,.2f}  {_src_tag(s.current_price)}",
        f"  P&L         : {pnl_sign}{_inr(pnl)}  ({pnl_pct:+.2f}%)  "
        f"Invested {_inr(_v(s.invested_value))}  Current {_inr(_v(s.current_value))}",
        f"  52-week     : Rs.{_v(s.low_52w):,.2f} – Rs.{_v(s.high_52w):,.2f}  "
        f"({_v(s.price_vs_high_52w_pct):+.2f}% from high)  {_src_tag(s.high_52w)}",
        f"  Sector      : {_v_str(s.sector)}  {_src_tag(s.sector)}",
        f"  Beta vs NTY : {_v(s.beta_vs_nifty):.2f}  {_src_tag(s.beta_vs_nifty)}",
        f"  Div yield   : {_v(s.dividend_yield_ttm):.2f}%  {_src_tag(s.dividend_yield_ttm)}",
        f"  P/E (TTM)   : {_v(s.weighted_pe):.1f}  {_src_tag(s.weighted_pe)}",
        f"  RSI(14d)    : {_v(s.rsi_daily):.1f}  ·  Above SMA-200: "
        f"{'yes' if (_v_bool(s.above_sma_200)) else 'no'}  "
        f"(SMA50 Rs.{_v(s.sma_50):,.2f} / SMA200 Rs.{_v(s.sma_200):,.2f})",
        "",
        f"  RULE-BASED ACTION : {_v_str(s.rule_action)}  "
        f"·  Conviction {_v_str(s.rule_conviction)}  "
        f"·  Horizon {_v_str(s.rule_horizon)}",
        f"  Target price      : {_v_str(s.rule_target_price)}",
        f"  Why               : {_v_str(s.rule_reasoning)}",
    ]

    # AI overlay block (always emitted; placeholder hint when NoAI).
    lines.append("")
    if mode == "AI" and s.ai_thesis_long_term and s.ai_thesis_long_term.value:
        lines.append("  AI OVERLAY (Claude):")
        lines.append("  Thesis (long-term)")
        for ln in str(s.ai_thesis_long_term.value).splitlines():
            lines.append(f"      {ln}")
        if s.ai_qualitative_risks and s.ai_qualitative_risks.value:
            lines.append("  Risks")
            for r in (s.ai_qualitative_risks.value or []):
                lines.append(f"      - {r}")
        if s.ai_peer_comparison and s.ai_peer_comparison.value:
            lines.append("  Peer comparison")
            lines.append(f"      {s.ai_peer_comparison.value}")
        if s.ai_news_context and s.ai_news_context.value:
            lines.append("  Recent news (30d)")
            lines.append(f"      {s.ai_news_context.value}")
        if s.ai_change_vs_prior and s.ai_change_vs_prior.value:
            lines.append("  Change vs prior")
            lines.append(f"      {s.ai_change_vs_prior.value}")
        if s.ai_action and s.ai_action.value:
            lines.append(
                f"  AI ACTION         : {s.ai_action.value}"
                + (f"  ({s.ai_action_detail.value})" if s.ai_action_detail and s.ai_action_detail.value else "")
            )
    else:
        lines.append("  AI overlay (Claude): [run with --ai to populate]")
        lines.append("    Adds: long-term thesis, qualitative risks, peer comparison,")
        lines.append("    recent-news context, and an AI action recommendation.")
    lines.append("")
    return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────

def _v(field: Field | None, default: float = 0.0) -> float:
    if field is None or field.value is None:
        return default
    try:
        return float(field.value)
    except (TypeError, ValueError):
        return default


def _v_str(field: Field | None, default: str = "n/a") -> str:
    if field is None or field.value is None:
        return default
    return str(field.value)


def _v_bool(field: Field | None) -> bool:
    if field is None or field.value is None:
        return False
    return bool(field.value)


def _src_tag(field: Field | None) -> str:
    if field is None or field.value is None:
        return "[missing]"
    return f"[{field.source} · {field.staleness_label}]"


def _inr(amount: float) -> str:
    """Format amount as Rs.X,XX,XXX (Indian grouping). Negative => Rs.-X,XXX."""
    sign = "-" if amount < 0 else ""
    n = abs(int(round(amount)))
    s = str(n)
    if len(s) <= 3:
        return f"Rs.{sign}{s}"
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return f"Rs.{sign}{','.join(parts)},{tail}"


def _age_label(age_min: int) -> str:
    if age_min < 1:
        return "just now"
    if age_min < 60:
        return f"{age_min} min ago"
    h, m = divmod(age_min, 60)
    if h < 24:
        return f"{h}h {m}m ago" if m else f"{h}h ago"
    d, h = divmod(h, 24)
    if d < 30:
        return f"{d}d ago"
    return ">30d ago"
