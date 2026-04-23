"""Headline P&L metrics (Roadmap D1).

Bare-minimum set for the first dashboard cut — Section A in the
layout sketch. Trade-quality (D2), risk (D2), and diagnostic (D3)
metrics get their own helpers in later sessions.

Pure functions over `list[TradeRow]`. No DB / IO.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from Dashboard.data_layer import TradeRow

Granularity = Literal["daily", "weekly", "monthly"]


@dataclass(frozen=True)
class HeadlinePnL:
    """Section A — headline P&L over the analysis window."""
    trade_count:   int
    trading_days:  int
    gross_pnl:     float
    total_charges: float
    net_pnl:       float
    best_day:      tuple[str, float] | None   # (date, net_pnl) or None
    worst_day:     tuple[str, float] | None


def headline_pnl(trades: list[TradeRow]) -> HeadlinePnL:
    """Aggregate net/gross/charges across the window plus best/worst day.

    Returns zero-valued metrics with `best_day=None` when `trades` is
    empty (callers render an "insufficient data" banner instead of
    crashing on a missing day).
    """
    if not trades:
        return HeadlinePnL(0, 0, 0.0, 0.0, 0.0, None, None)

    gross   = sum(t.gross_pnl     for t in trades)
    charges = sum(t.total_charges for t in trades)
    net     = sum(t.net_pnl       for t in trades)

    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t.date] += t.net_pnl

    best_day  = max(by_day.items(), key=lambda kv: kv[1])
    worst_day = min(by_day.items(), key=lambda kv: kv[1])

    return HeadlinePnL(
        trade_count   = len(trades),
        trading_days  = len(by_day),
        gross_pnl     = round(gross, 2),
        total_charges = round(charges, 2),
        net_pnl       = round(net, 2),
        best_day      = (best_day[0],  round(best_day[1],  2)),
        worst_day     = (worst_day[0], round(worst_day[1], 2)),
    )


def net_pnl_pct(net_pnl: float, budget: float) -> float | None:
    """Net P&L as a fraction of `budget`, or None if budget <= 0.

    Kept separate from `headline_pnl` because the dashboard may render
    in budget-less contexts (e.g. tax-only view, FY summary) and we
    don't want the metric calculation to depend on a Config import.
    """
    if budget is None or budget <= 0:
        return None
    return net_pnl / budget


# ── Time-bucketed series (for charts) ─────────────────────────────

def _bucket_key(date_str: str, granularity: Granularity) -> str:
    """Return the bucket label that `date_str` belongs to.

    Daily   -> '2026-04-22'
    Weekly  -> '2026-W17' (ISO week, Monday-anchored — matches NSE habit)
    Monthly -> '2026-04'
    """
    d = datetime.date.fromisoformat(date_str)
    if granularity == "daily":
        return d.isoformat()
    if granularity == "weekly":
        iso = d.isocalendar()
        return f"{iso.year:04d}-W{iso.week:02d}"
    if granularity == "monthly":
        return f"{d.year:04d}-{d.month:02d}"
    raise ValueError(f"unknown granularity: {granularity!r}")


def bucketed_pnl(
    trades: list[TradeRow],
    granularity: Granularity = "daily",
) -> list[tuple[str, float, int]]:
    """Group trades into buckets and return [(label, net_pnl, trade_count), ...].

    Sorted ascending by label. Empty buckets are not synthesised — the
    chart layer can interpolate gaps if it wants to.
    """
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        buckets[_bucket_key(t.date, granularity)].append(t.net_pnl)
    out: list[tuple[str, float, int]] = []
    for label in sorted(buckets):
        pnls = buckets[label]
        out.append((label, round(sum(pnls), 2), len(pnls)))
    return out


def cumulative_series(
    trades: list[TradeRow],
) -> list[tuple[str, float]]:
    """Per-trading-day cumulative net P&L, sorted ascending by date.

    Always daily granularity (cumulative curves don't make sense at
    weekly buckets — they'd hide intra-week drawdowns).
    """
    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t.date] += t.net_pnl
    cum = 0.0
    out: list[tuple[str, float]] = []
    for d in sorted(by_day):
        cum += by_day[d]
        out.append((d, round(cum, 2)))
    return out


__all__ = [
    "Granularity",
    "HeadlinePnL",
    "headline_pnl",
    "net_pnl_pct",
    "bucketed_pnl",
    "cumulative_series",
]
