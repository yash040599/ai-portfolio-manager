"""Live trading statistics computed from the trade DB.

Computes the same industry-standard metrics described in
docs/TRADE_STATISTICS.md so the dashboard can show
theoretical-vs-live side-by-side at a glance.

Pure read of the same DB the home dashboard uses (intraday_tax_ledger
via Dashboard.data_layer).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass

from modes.dashboard.data_layer import (
    TradeRow,
    current_fy_window,
    fetch_trades,
)


@dataclass(frozen=True)
class LiveStats:
    """Industry-standard metrics over the configured window."""
    window_from:    str
    window_to:      str
    trade_count:    int
    trading_days:   int
    gross_profit:   float
    gross_loss:     float          # absolute (positive) value of losing-trade sum
    total_charges:  float
    net_pnl:        float
    win_count:      int
    loss_count:     int
    win_rate:       float | None   # 0..1
    profit_factor:  float | None
    expectancy:     float | None   # avg net P&L per trade (Rs)
    avg_win:        float | None
    avg_loss:       float | None   # absolute
    payoff_ratio:   float | None   # avg_win / avg_loss
    max_drawdown:   float          # absolute Rs (worst peak-to-trough on cumulative net)
    sharpe_daily:   float | None   # annualised, daily returns, rf=0
    sortino_daily:  float | None   # annualised
    profitable_days: int
    total_days:      int
    day_win_rate:    float | None   # P(profitable day) empirical


def _daily_net(trades: list[TradeRow]) -> dict[str, float]:
    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t.date] += t.net_pnl
    return by_day


def _max_drawdown(daily_net_sorted: list[float]) -> float:
    """Largest peak-to-trough on the cumulative-equity curve, in Rs."""
    if not daily_net_sorted:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in daily_net_sorted:
        cum += x
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _safe_div(num: float, den: float) -> float | None:
    return (num / den) if den else None


def compute_live_stats(date_from: str | None = None,
                       date_to:   str | None = None,
                       *, include_provisional: bool = True) -> LiveStats:
    """Return industry-standard metrics for the requested window.

    Default window is the current Indian FY (matches the home dashboard).
    """
    if not (date_from and date_to):
        date_from, date_to = current_fy_window()

    trades = fetch_trades(date_from, date_to, include_provisional=include_provisional)
    n = len(trades)

    if n == 0:
        return LiveStats(
            window_from=date_from, window_to=date_to,
            trade_count=0, trading_days=0,
            gross_profit=0.0, gross_loss=0.0, total_charges=0.0, net_pnl=0.0,
            win_count=0, loss_count=0,
            win_rate=None, profit_factor=None, expectancy=None,
            avg_win=None, avg_loss=None, payoff_ratio=None,
            max_drawdown=0.0, sharpe_daily=None, sortino_daily=None,
            profitable_days=0, total_days=0, day_win_rate=None,
        )

    wins   = [t.net_pnl for t in trades if t.net_pnl > 0]
    losses = [t.net_pnl for t in trades if t.net_pnl < 0]

    gross_profit = round(sum(wins), 2)
    gross_loss   = round(abs(sum(losses)), 2)   # positive number
    total_charges = round(sum(t.total_charges for t in trades), 2)
    net_pnl       = round(sum(t.net_pnl for t in trades), 2)

    win_rate     = len(wins) / n
    profit_factor = _safe_div(gross_profit, gross_loss)
    expectancy    = round(net_pnl / n, 2)
    avg_win       = round(statistics.mean(wins), 2)   if wins   else None
    avg_loss      = round(abs(statistics.mean(losses)), 2) if losses else None
    payoff_ratio  = _safe_div(avg_win or 0, avg_loss or 0) if (avg_win and avg_loss) else None

    by_day = _daily_net(trades)
    daily_dates  = sorted(by_day.keys())
    daily_values = [by_day[d] for d in daily_dates]

    max_dd = _max_drawdown(daily_values)

    profitable_days = sum(1 for v in daily_values if v > 0)
    total_days = len(daily_values)
    day_win_rate = profitable_days / total_days if total_days else None

    # Sharpe / Sortino: daily returns annualised. Treat each day's net P&L
    # as a "return" without normalising by capital (DB doesn't track per-day
    # capital deployed cleanly; this gives a directional signal, which is
    # what the dashboard needs). rf=0.
    sharpe = None
    sortino = None
    if total_days >= 2:
        mean_d = statistics.mean(daily_values)
        try:
            sd = statistics.stdev(daily_values)
        except statistics.StatisticsError:
            sd = 0.0
        if sd > 0:
            sharpe = round((mean_d / sd) * math.sqrt(252), 2)
        downside = [v for v in daily_values if v < 0]
        if len(downside) >= 2:
            try:
                d_sd = statistics.stdev(downside)
            except statistics.StatisticsError:
                d_sd = 0.0
            if d_sd > 0:
                sortino = round((mean_d / d_sd) * math.sqrt(252), 2)

    return LiveStats(
        window_from   = date_from,
        window_to     = date_to,
        trade_count   = n,
        trading_days  = total_days,
        gross_profit  = gross_profit,
        gross_loss    = gross_loss,
        total_charges = total_charges,
        net_pnl       = net_pnl,
        win_count     = len(wins),
        loss_count    = len(losses),
        win_rate      = round(win_rate, 4) if win_rate is not None else None,
        profit_factor = round(profit_factor, 3) if profit_factor is not None else None,
        expectancy    = expectancy,
        avg_win       = avg_win,
        avg_loss      = avg_loss,
        payoff_ratio  = round(payoff_ratio, 3) if payoff_ratio is not None else None,
        max_drawdown  = max_dd,
        sharpe_daily  = sharpe,
        sortino_daily = sortino,
        profitable_days = profitable_days,
        total_days      = total_days,
        day_win_rate    = round(day_win_rate, 4) if day_win_rate is not None else None,
    )


__all__ = ["LiveStats", "compute_live_stats"]
