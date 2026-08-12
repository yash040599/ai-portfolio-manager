#!/usr/bin/env python3
"""Retest Shantharaj's one-hour ORB configuration on NSE equities.

Publicly described rules: define the first-hour range, require the breakout
candle's directional wick to be at most 10-20% of its full range, enter beyond
that candle, use a 1% stop, and exit at end of day. Results use the repository's
canonical Indian transaction-cost model and frozen walk-forward windows.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtest_gates import (  # noqa: E402
    INTRADAY_DB,
    compute_metrics,
    group_by_day,
    load_15m,
    _rsi,
    _make_trade,
)
from shared.nifty_universe import get_universe  # noqa: E402

WINDOWS = {
    "TRAIN": ("2024-05-27", "2025-05-31"),
    "TEST": ("2025-06-01", "2026-05-22"),
}
FIRST_HOUR_CANDLES = 4
STOP_PCT = 1.0
DAILY_CAP = 2


def _directional_wick_ratio(candle: dict, side: str) -> float:
    candle_range = candle["high"] - candle["low"]
    if candle_range <= 0:
        return 1.0
    if side == "BUY":
        wick = candle["high"] - max(candle["open"], candle["close"])
    else:
        wick = min(candle["open"], candle["close"]) - candle["low"]
    return max(0.0, wick) / candle_range


def _simulate_symbol(
    symbol: str,
    days: dict[str, list[dict]],
    *,
    wick_max: float,
    start: str,
    end: str,
) -> list[dict]:
    candidates: list[dict] = []
    for date_str, candles in days.items():
        if date_str < start or date_str > end or len(candles) < 7:
            continue

        initial_balance = candles[:FIRST_HOUR_CANDLES]
        range_high = max(candle["high"] for candle in initial_balance)
        range_low = min(candle["low"] for candle in initial_balance)

        for index in range(FIRST_HOUR_CANDLES, len(candles) - 1):
            candle = candles[index]
            side = None
            if candle["high"] > range_high and candle["close"] > candle["open"]:
                side = "BUY"
            elif candle["low"] < range_low and candle["close"] < candle["open"]:
                side = "SELL"
            if side is None or _directional_wick_ratio(candle, side) > wick_max:
                continue

            entry_price = candle["high"] if side == "BUY" else candle["low"]
            candidates.append({
                "symbol": symbol,
                "date": date_str,
                "side": side,
                "entry_price": entry_price,
                "entry_ts": candle["ts"],
                "candles": candles,
                "entry_index": index,
                "quality": 1.0 - _directional_wick_ratio(candle, side),
            })
            break
    return candidates


def _execute(candidates: list[dict]) -> list[dict]:
    selected: list[dict] = []
    by_date: dict[str, list[dict]] = {}
    for candidate in candidates:
        by_date.setdefault(candidate["date"], []).append(candidate)
    for day_candidates in by_date.values():
        day_candidates.sort(key=lambda item: (item["entry_ts"], -item["quality"]))
        selected.extend(day_candidates[:DAILY_CAP])

    trades: list[dict] = []
    for candidate in selected:
        side = candidate["side"]
        entry_price = candidate["entry_price"]
        stop_pct = candidate.get("stop_pct", STOP_PCT)
        stop_price = entry_price * (
            1.0 - stop_pct / 100 if side == "BUY" else 1.0 + stop_pct / 100
        )
        exit_price = candidate["candles"][-1]["close"]
        exit_ts = candidate["candles"][-1]["ts"]
        reason = "EOD_SQUARE_OFF"

        for candle in candidate["candles"][candidate["entry_index"] + 1:]:
            if side == "BUY" and candle["low"] <= stop_price:
                exit_price, exit_ts, reason = stop_price, candle["ts"], "STOP_LOSS"
                break
            if side == "SELL" and candle["high"] >= stop_price:
                exit_price, exit_ts, reason = stop_price, candle["ts"], "STOP_LOSS"
                break

        pnl_pct = ((exit_price - entry_price) / entry_price * 100
                   if side == "BUY"
                   else (entry_price - exit_price) / entry_price * 100)
        trades.append(_make_trade(
            candidate["symbol"], candidate["entry_ts"], exit_ts, side,
            entry_price, exit_price, stop_price, 0.0, pnl_pct, reason, True,
        ))
    return sorted(trades, key=lambda trade: trade["entry_ts"])


def _ema(values: list[float], period: int) -> list[float]:
    multiplier = 2.0 / (period + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(output[-1] + multiplier * (value - output[-1]))
    return output


def _continuous_data(days: dict[str, list[dict]]) -> tuple[list[dict], dict[str, int]]:
    candles: list[dict] = []
    starts: dict[str, int] = {}
    for date_str in sorted(days):
        starts[date_str] = len(candles)
        candles.extend(days[date_str])
    return candles, starts


def _simulate_first_candle_rsi(
    symbol: str,
    days: dict[str, list[dict]],
    *,
    rsi_long: float,
    rsi_short: float,
    start: str,
    end: str,
) -> list[dict]:
    all_candles, starts = _continuous_data(days)
    candidates: list[dict] = []
    for date_str, candles in days.items():
        if date_str < start or date_str > end or len(candles) < 3:
            continue
        start_index = starts[date_str]
        history = all_candles[max(0, start_index - 50):start_index + 1]
        if len(history) < 15:
            continue
        rsi_value = _rsi([candle["close"] for candle in history], 14)
        side = "BUY" if rsi_value > rsi_long else "SELL" if rsi_value < rsi_short else None
        if side is None:
            continue

        first = candles[0]
        for index in range(1, len(candles) - 1):
            candle = candles[index]
            triggered = (candle["high"] > first["high"] if side == "BUY"
                         else candle["low"] < first["low"])
            if not triggered:
                continue
            candidates.append({
                "symbol": symbol,
                "date": date_str,
                "side": side,
                "entry_price": first["high"] if side == "BUY" else first["low"],
                "entry_ts": candle["ts"],
                "candles": candles,
                "entry_index": index,
                "quality": abs(rsi_value - 50.0),
                "stop_pct": 0.5,
            })
            break
    return candidates


def _simulate_ema_cross(
    symbol: str,
    days: dict[str, list[dict]],
    *,
    fast_period: int,
    slow_period: int,
    start: str,
    end: str,
) -> list[dict]:
    all_candles, starts = _continuous_data(days)
    closes = [candle["close"] for candle in all_candles]
    fast_ema = _ema(closes, fast_period)
    slow_ema = _ema(closes, slow_period)
    candidates: list[dict] = []

    for date_str, candles in days.items():
        if date_str < start or date_str > end or len(candles) < 3:
            continue
        day_start = starts[date_str]
        for cross_index in range(len(candles) - 1):
            absolute_index = day_start + cross_index
            if absolute_index < slow_period:
                continue
            crossed_up = (fast_ema[absolute_index - 1] <= slow_ema[absolute_index - 1]
                          and fast_ema[absolute_index] > slow_ema[absolute_index])
            crossed_down = (fast_ema[absolute_index - 1] >= slow_ema[absolute_index - 1]
                            and fast_ema[absolute_index] < slow_ema[absolute_index])
            side = "BUY" if crossed_up else "SELL" if crossed_down else None
            if side is None:
                continue

            cross_candle = candles[cross_index]
            for trigger_index in range(cross_index + 1, len(candles) - 1):
                trigger = candles[trigger_index]
                triggered = (trigger["high"] > cross_candle["high"] if side == "BUY"
                             else trigger["low"] < cross_candle["low"])
                if not triggered:
                    continue
                candidates.append({
                    "symbol": symbol,
                    "date": date_str,
                    "side": side,
                    "entry_price": (cross_candle["high"] if side == "BUY"
                                    else cross_candle["low"]),
                    "entry_ts": trigger["ts"],
                    "candles": candles,
                    "entry_index": trigger_index,
                    "quality": abs(fast_ema[absolute_index] - slow_ema[absolute_index]),
                    "stop_pct": 0.5,
                })
                break
            break
    return candidates


def _print_metrics(label: str, metrics: dict) -> None:
    print(
        f"  {label:<22} trades={metrics.get('trades', 0):>4} "
        f"WR={metrics.get('win_rate', 0):>5.1f}% "
        f"PF={metrics.get('pf', 0):>5.2f} "
        f"Exp={metrics.get('expectancy', 0):>+7.3f}% "
        f"MaxDD={metrics.get('max_dd', 0):>6.2f}% "
        f"Sharpe={metrics.get('sharpe', 0):>+6.2f}"
    )


def main() -> None:
    symbols = get_universe("NIFTY100")
    symbol_days = {
        symbol: group_by_day(candles)
        for symbol in symbols
        if (candles := load_15m(INTRADAY_DB, symbol))
    }
    print(f"\nShantharaj one-hour ORB retest: {len(symbol_days)} NIFTY100 symbols")
    print("Entry beyond breakout candle; 1% stop; EOD exit; cap=2; net of costs")

    for window_name, (start, end) in WINDOWS.items():
        print(f"\n{window_name} {start} to {end}")
        for wick_max in (1.0, 0.20, 0.10):
            candidates = []
            for symbol, days in symbol_days.items():
                candidates.extend(_simulate_symbol(
                    symbol, days, wick_max=wick_max, start=start, end=end,
                ))
            trades = _execute(candidates)
            label = "No wick filter" if wick_max == 1.0 else f"Wick <= {wick_max:.0%}"
            _print_metrics(label, compute_metrics(trades, label, with_costs=True))

        ema_candidates = []
        for symbol, days in symbol_days.items():
            ema_candidates.extend(_simulate_ema_cross(
                symbol, days, fast_period=6, slow_period=60,
                start=start, end=end,
            ))
        ema_trades = _execute(ema_candidates)
        for rsi_long, rsi_short in ((60.0, 40.0), (55.0, 35.0), (55.0, 45.0)):
            first_candle_candidates = []
            for symbol, days in symbol_days.items():
                first_candle_candidates.extend(_simulate_first_candle_rsi(
                    symbol, days, rsi_long=rsi_long, rsi_short=rsi_short,
                    start=start, end=end,
                ))
            label = f"First candle RSI {rsi_long:.0f}/{rsi_short:.0f}"
            _print_metrics(
                label,
                compute_metrics(_execute(first_candle_candidates), label, with_costs=True),
            )
        _print_metrics(
            "EMA 6/60 crossover",
            compute_metrics(ema_trades, "ema-6-60", with_costs=True),
        )


if __name__ == "__main__":
    main()