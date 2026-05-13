# ================================================================
# modes/swing/ath_backtest.py
# ================================================================
# Backtester for the ATH dip-buy strategy.
#
# Runs simulations for X% dip (buy) × Y% rise (sell) combinations
# across NIFTY 50 stocks using historical daily data.
#
# Output: matrix of returns, top/worst combos, trade stats.
# ================================================================

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from typing import Any

from config import now_ist
from core.logger import Logger
from core.zerodha_client import ZerodhaClient
from shared.candle_cache import CandleCache
from modes.trade.stock_scanner import NIFTY50


BUY_AMOUNT = 10_000.0  # Rs.10,000 per position
LOOKBACK_DAYS = 3650    # ~10 years


@dataclass
class Trade:
    symbol: str
    buy_date: str
    buy_price: float
    qty: int
    sell_date: str = ""
    sell_price: float = 0.0
    pnl: float = 0.0
    holding_days: int = 0
    won: bool = False


@dataclass
class BacktestResult:
    dip_pct: float
    target_pct: float
    trades: list[Trade] = field(default_factory=list)
    total_return: float = 0.0
    cagr: float = 0.0
    xirr: float = 0.0
    num_trades: int = 0
    win_rate: float = 0.0
    avg_holding_days: float = 0.0
    max_drawdown: float = 0.0
    capital_deployed: float = 0.0


@dataclass
class BacktestMatrix:
    """Full X×Y matrix of backtest results."""
    dip_range: list[int] = field(default_factory=list)
    target_range: list[int] = field(default_factory=list)
    results: dict[tuple[int, int], BacktestResult] = field(default_factory=dict)
    top_5: list[BacktestResult] = field(default_factory=list)
    worst_5: list[BacktestResult] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        matrix = {}
        for (x, y), r in self.results.items():
            matrix[f"{x},{y}"] = {
                "dip_pct": x, "target_pct": y,
                "cagr": round(r.cagr, 2),
                "xirr": round(r.xirr, 2),
                "total_return": round(r.total_return, 2),
                "num_trades": r.num_trades,
                "win_rate": round(r.win_rate, 1),
                "avg_holding_days": round(r.avg_holding_days, 1),
                "max_drawdown": round(r.max_drawdown, 2),
                "capital_deployed": round(r.capital_deployed, 2),
            }
        return {
            "dip_range": self.dip_range,
            "target_range": self.target_range,
            "matrix": matrix,
            "top_5": [{"x": r.dip_pct, "y": r.target_pct,
                       "cagr": round(r.cagr, 2), "xirr": round(r.xirr, 2),
                       "trades": r.num_trades, "win_rate": round(r.win_rate, 1)}
                      for r in self.top_5],
            "worst_5": [{"x": r.dip_pct, "y": r.target_pct,
                         "cagr": round(r.cagr, 2), "xirr": round(r.xirr, 2),
                         "trades": r.num_trades}
                        for r in self.worst_5],
            "assumptions": self.assumptions,
        }


class ATHBacktester:
    """Run the ATH dip-buy backtest across X/Y parameter grid."""

    def __init__(self, zerodha: ZerodhaClient, log: Logger):
        self.zerodha = zerodha
        self.log = log
        self._cache = CandleCache()

    def run_matrix(
        self,
        dip_min: int = 10,
        dip_max: int = 20,
        target_min: int = 10,
        target_max: int = 20,
    ) -> BacktestMatrix:
        """Run the full X×Y backtest matrix."""
        self.log.info(f"ATH backtest: X={dip_min}-{dip_max}%, "
                      f"Y={target_min}-{target_max}%")

        # Fetch all NIFTY 50 candles upfront
        all_candles = self._fetch_all_candles()
        self.log.info(f"Loaded candles for {len(all_candles)} symbols")

        dip_range = list(range(dip_min, dip_max + 1))
        target_range = list(range(target_min, target_max + 1))

        matrix = BacktestMatrix(
            dip_range=dip_range,
            target_range=target_range,
            assumptions=[
                "Using current NIFTY 50 constituents (survivorship bias caveat)",
                f"Buy amount: Rs.{BUY_AMOUNT:,.0f} per position",
                "ATH computed dynamically from available history",
                "Ignoring brokerage, taxes, slippage, liquidity",
                "Using close prices for buy/sell signals",
                "Multiple stocks held simultaneously, no capital cap",
                "After sell, stock eligible for fresh buy immediately",
            ],
        )

        for x in dip_range:
            for y in target_range:
                result = self._run_single(all_candles, x, y)
                matrix.results[(x, y)] = result

        # Rank by CAGR
        ranked = sorted(matrix.results.values(),
                        key=lambda r: r.cagr, reverse=True)
        matrix.top_5 = ranked[:5]
        matrix.worst_5 = ranked[-5:]

        self.log.info(f"Backtest complete: {len(matrix.results)} combinations")
        return matrix

    def _run_single(self, all_candles: dict[str, list[dict]],
                    dip_pct: float, target_pct: float) -> BacktestResult:
        """Run one X,Y combination across all symbols."""
        result = BacktestResult(dip_pct=dip_pct, target_pct=target_pct)
        all_trades: list[Trade] = []
        total_invested = 0.0
        total_returned = 0.0

        for symbol, candles in all_candles.items():
            if len(candles) < 50:
                continue

            trades = self._simulate_symbol(
                symbol, candles, dip_pct, target_pct)
            all_trades.extend(trades)

            for t in trades:
                total_invested += t.buy_price * t.qty
                if t.sell_date:
                    total_returned += t.sell_price * t.qty

        result.trades = all_trades
        result.num_trades = len(all_trades)

        closed = [t for t in all_trades if t.sell_date]
        if closed:
            result.win_rate = (sum(1 for t in closed if t.won)
                               / len(closed)) * 100
            result.avg_holding_days = (
                sum(t.holding_days for t in closed) / len(closed))

        result.capital_deployed = total_invested
        if total_invested > 0:
            result.total_return = (
                (total_returned - total_invested) / total_invested) * 100

        # CAGR approximation
        if all_trades and total_invested > 0:
            first_date = min(t.buy_date for t in all_trades)
            last_date = max(t.sell_date or t.buy_date for t in all_trades)
            try:
                d1 = datetime.datetime.fromisoformat(first_date[:10])
                d2 = datetime.datetime.fromisoformat(last_date[:10])
                years = max(0.1, (d2 - d1).days / 365.25)
                ratio = total_returned / total_invested if total_invested > 0 else 1
                if ratio > 0:
                    result.cagr = (ratio ** (1 / years) - 1) * 100
            except (ValueError, TypeError):
                pass

        # XIRR approximation (simplified — use CAGR as proxy)
        result.xirr = result.cagr

        # Max drawdown from cumulative P&L
        result.max_drawdown = self._max_drawdown(closed)

        return result

    def _simulate_symbol(self, symbol: str, candles: list[dict],
                         dip_pct: float, target_pct: float,
                         ) -> list[Trade]:
        """Simulate the ATH strategy on one symbol's candle history."""
        trades: list[Trade] = []
        ath = 0.0
        position: Trade | None = None

        for candle in candles:
            high = candle["high"]
            close = candle["close"]
            date_str = str(candle["date"])[:10]

            # Update ATH
            if high > ath:
                ath = high

            if ath <= 0:
                continue

            # Check sell condition first
            if position is not None:
                target = position.buy_price * (1 + target_pct / 100)
                if close >= target:
                    position.sell_date = date_str
                    position.sell_price = close
                    position.pnl = (close - position.buy_price) * position.qty
                    try:
                        bd = datetime.datetime.fromisoformat(position.buy_date)
                        sd = datetime.datetime.fromisoformat(date_str)
                        position.holding_days = (sd - bd).days
                    except (ValueError, TypeError):
                        pass
                    position.won = position.pnl > 0
                    trades.append(position)
                    position = None
                continue  # don't buy on same day as sell check

            # Check buy condition (only if no position)
            dip = ((ath - close) / ath) * 100
            if dip >= dip_pct:
                qty = max(1, int(BUY_AMOUNT / close))
                position = Trade(
                    symbol=symbol,
                    buy_date=date_str,
                    buy_price=close,
                    qty=qty,
                )

        # Close any open position at last available price
        if position is not None:
            last = candles[-1]
            position.sell_date = str(last["date"])[:10]
            position.sell_price = last["close"]
            position.pnl = (last["close"] - position.buy_price) * position.qty
            position.won = position.pnl > 0
            try:
                bd = datetime.datetime.fromisoformat(position.buy_date)
                sd = datetime.datetime.fromisoformat(position.sell_date)
                position.holding_days = (sd - bd).days
            except (ValueError, TypeError):
                pass
            trades.append(position)

        return trades

    def _max_drawdown(self, trades: list[Trade]) -> float:
        """Max drawdown from cumulative P&L of closed trades."""
        if not trades:
            return 0.0
        sorted_trades = sorted(trades, key=lambda t: t.sell_date or t.buy_date)
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in sorted_trades:
            cumulative += t.pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 2)

    def _fetch_all_candles(self) -> dict[str, list[dict]]:
        """Fetch daily candles for all NIFTY 50 stocks."""
        end_date = now_ist().date()
        from_date = end_date - datetime.timedelta(days=LOOKBACK_DAYS)
        result: dict[str, list[dict]] = {}

        for symbol in NIFTY50:
            try:
                cached = self._cache.get_cached_candles(
                    symbol, "NSE", "day", from_date, end_date)
                if len(cached) >= 200:
                    result[symbol] = cached
                    continue

                candles = self.zerodha.get_historical(
                    symbol, "NSE",
                    from_date=from_date, to_date=end_date,
                    interval="day",
                )
                if candles:
                    self._cache.store_candles(symbol, "NSE", "day", candles)
                    result[symbol] = candles
                elif cached:
                    result[symbol] = cached
            except Exception as exc:
                self.log.warning(f"ATH backtest fetch {symbol}: {exc}")
                if cached:
                    result[symbol] = cached

        return result


# ── Text report for CLI / file output ──────────────────────────

def format_backtest_report(m: BacktestMatrix) -> str:
    """Format the backtest matrix as a readable text report."""
    lines: list[str] = []
    sep = "=" * 72

    lines.append(sep)
    lines.append("  ATH Dip-Buy Strategy — Backtest Report")
    lines.append(sep)

    lines.append("\n  Assumptions:")
    for a in m.assumptions:
        lines.append(f"    • {a}")

    # CAGR matrix
    lines.append(f"\n  CAGR (%) Matrix — Dip X% (rows) × Target Y% (columns)")
    lines.append(f"  {'':>6s}" + "".join(f"{y:>8d}%" for y in m.target_range))
    lines.append("  " + "-" * (6 + 9 * len(m.target_range)))
    for x in m.dip_range:
        row = f"  {x:>4d}%|"
        for y in m.target_range:
            r = m.results.get((x, y))
            cagr = r.cagr if r else 0
            row += f"{cagr:>8.1f}"
        lines.append(row)

    # Top 5
    lines.append("\n  Top 5 Combinations:")
    for i, r in enumerate(m.top_5[:5], 1):
        lines.append(
            f"    {i}. Dip {r.dip_pct:.0f}% / Target {r.target_pct:.0f}% "
            f"→ CAGR {r.cagr:.1f}%, {r.num_trades} trades, "
            f"win rate {r.win_rate:.0f}%")

    # Worst 5
    lines.append("\n  Worst 5 Combinations:")
    for i, r in enumerate(m.worst_5[:5], 1):
        lines.append(
            f"    {i}. Dip {r.dip_pct:.0f}% / Target {r.target_pct:.0f}% "
            f"→ CAGR {r.cagr:.1f}%, {r.num_trades} trades")

    lines.append(f"\n{sep}")
    return "\n".join(lines) + "\n"
