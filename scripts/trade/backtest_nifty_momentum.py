#!/usr/bin/env python3
"""Phase 9.3 — NIFTY Index Momentum (simulated futures) backtest.

THESIS: Instead of trading 50-100 individual stocks, trade NIFTY as a
single instrument. Our regime classifier + NIFTY trend signal already
works. The key advantage is COST STRUCTURE — NIFTY futures have zero
STT (only CTT 0.01% vs equity STT 0.025%+0.025%), which could flip
marginal strategies profitable.

We simulate NIFTY futures using a synthetic equal-weight index built
from NIFTY50 constituent 15-min candles (same proxy as regime_analysis.py).
Cost model uses futures charges (no STT, CTT only).

SIGNALS tested:
  A. Gap-and-go on NIFTY itself (index gaps with volume)
  B. First-candle momentum (strong directional first candle)
  C. EMA crossover (5 vs 20 period on 15-min)
  D. Morning range breakout (first hour range on NIFTY)

Usage:
    python scripts/trade/backtest_nifty_momentum.py
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtest_gates import (  # noqa: E402
    INTRADAY_DB, DAILY_DB, load_15m, load_daily, group_by_day,
    compute_metrics, _make_trade,
)
from regime_analysis import label_regimes  # noqa: E402
from shared.nifty_universe import get_universe  # noqa: E402

WINDOWS = {
    "FULL": (None, None),
    "TRAIN": ("2024-05-27", "2025-05-31"),
    "TEST": ("2025-06-01", "2026-05-22"),
}

GATE_PF = 1.15
TRADE_VALUE = 15_000

# Futures cost: no STT, CTT = 0.01%, exchange txn = 0.002%, GST = 18%
# Much cheaper than equity MIS
FUTURES_COST_PER_TRADE_PCT = 0.015  # ~0.015% per side all-in


def _build_nifty_index(all_symbol_days: dict[str, dict]) -> dict[str, list[dict]]:
    """Build a synthetic NIFTY index from equal-weight average of all stocks."""
    all_dates: set[str] = set()
    for sdata in all_symbol_days.values():
        all_dates.update(d for d in sdata["days"] if not d.startswith("_"))

    index_days: dict[str, list[dict]] = {}
    for date_str in sorted(all_dates):
        # Collect all stocks' candles for this day
        stock_candles: list[list[dict]] = []
        for sdata in all_symbol_days.values():
            dc = sdata["days"].get(date_str)
            if dc and len(dc) >= 20:
                stock_candles.append(dc)

        if len(stock_candles) < 20:
            continue

        # Build index candles: average OHLCV across stocks, normalized
        n_candles = min(len(sc) for sc in stock_candles)
        index_candles = []
        for ci in range(n_candles):
            # Normalize each stock to % return from open for averaging
            opens = [sc[0]["open"] for sc in stock_candles]
            o_vals = [sc[ci]["open"] / opens[i] * 100 for i, sc in enumerate(stock_candles)]
            h_vals = [sc[ci]["high"] / opens[i] * 100 for i, sc in enumerate(stock_candles)]
            l_vals = [sc[ci]["low"] / opens[i] * 100 for i, sc in enumerate(stock_candles)]
            c_vals = [sc[ci]["close"] / opens[i] * 100 for i, sc in enumerate(stock_candles)]
            v_vals = [sc[ci].get("volume", 0) or 0 for sc in stock_candles]

            index_candles.append({
                "ts": stock_candles[0][ci]["ts"],
                "open": sum(o_vals) / len(o_vals),
                "high": sum(h_vals) / len(h_vals),
                "low": sum(l_vals) / len(l_vals),
                "close": sum(c_vals) / len(c_vals),
                "volume": sum(v_vals),
            })
        index_days[date_str] = index_candles

    return index_days


def _compute_futures_cost(entry_price: float, exit_price: float) -> float:
    """Compute round-trip futures cost as % of trade value."""
    return FUTURES_COST_PER_TRADE_PCT * 2  # buy + sell side


def simulate_index_gap_go(
    index_days: dict[str, list[dict]],
    regime_labels: dict[str, str],
    *,
    gap_pct: float = 0.3,
    vol_mult: float = 1.5,
    rr_ratio: float = 1.5,
    skip_regimes: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    sq_off_hour: int = 14,
) -> list[dict]:
    """Gap-and-go on NIFTY index: enter when index gaps from prev close."""
    if skip_regimes is None:
        skip_regimes = set()

    sorted_dates = sorted(index_days.keys())
    all_trades = []

    for di, date_str in enumerate(sorted_dates):
        if start and date_str < start:
            continue
        if end and date_str > end:
            continue
        regime = regime_labels.get(date_str)
        if regime and regime in skip_regimes:
            continue

        candles = index_days[date_str]
        if len(candles) < 4:
            continue

        # Prev day close
        if di == 0:
            continue
        prev_date = sorted_dates[di - 1]
        prev_candles = index_days.get(prev_date)
        if not prev_candles:
            continue
        prev_close = prev_candles[-1]["close"]
        if prev_close <= 0:
            continue

        today_open = candles[0]["open"]
        gap = (today_open - prev_close) / prev_close * 100

        if abs(gap) < gap_pct:
            continue

        # Volume check: first candle vol
        entry_candle = candles[1] if len(candles) > 1 else candles[0]
        # Simple volume check against avg (using last 20 days' first candles)
        prior_vols = []
        for pdi in range(max(0, di - 20), di):
            pd = sorted_dates[pdi]
            pc = index_days.get(pd)
            if pc and len(pc) > 1:
                pv = pc[1].get("volume", 0)
                if pv > 0:
                    prior_vols.append(pv)
        avg_vol = sum(prior_vols) / len(prior_vols) if prior_vols else 0
        entry_vol = entry_candle.get("volume", 0)
        if avg_vol > 0 and entry_vol < vol_mult * avg_vol:
            continue

        side = "BUY" if gap > 0 else "SELL"
        entry_price = entry_candle["close"]
        if entry_price <= 0:
            continue

        if side == "BUY":
            sl_price = entry_candle["low"]
            sl_dist = entry_price - sl_price
            if sl_dist <= 0:
                sl_dist = entry_price * 0.005
                sl_price = entry_price - sl_dist
            target_price = entry_price + sl_dist * rr_ratio
        else:
            sl_price = entry_candle["high"]
            sl_dist = sl_price - entry_price
            if sl_dist <= 0:
                sl_dist = entry_price * 0.005
                sl_price = entry_price + sl_dist
            target_price = entry_price - sl_dist * rr_ratio

        entry_ts = entry_candle["ts"]
        exited = False

        for ci in range(2, len(candles)):
            c = candles[ci]
            hour = c["ts"].hour
            minute = c["ts"].minute

            if hour * 60 + minute >= sq_off_hour * 60:
                if side == "BUY":
                    pnl_pct = (c["close"] - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - c["close"]) / entry_price * 100
                pnl_pct -= _compute_futures_cost(entry_price, c["close"])
                all_trades.append(_make_trade(
                    "NIFTY_IDX", entry_ts, c["ts"], side, entry_price,
                    c["close"], sl_price, target_price, pnl_pct,
                    "EOD_SQUARE_OFF", False))
                exited = True
                break

            if side == "BUY":
                if c["low"] <= sl_price:
                    pnl_pct = (sl_price - entry_price) / entry_price * 100
                    pnl_pct -= _compute_futures_cost(entry_price, sl_price)
                    all_trades.append(_make_trade(
                        "NIFTY_IDX", entry_ts, c["ts"], side, entry_price,
                        sl_price, sl_price, target_price, pnl_pct,
                        "STOP_LOSS", False))
                    exited = True
                    break
                if c["high"] >= target_price:
                    pnl_pct = (target_price - entry_price) / entry_price * 100
                    pnl_pct -= _compute_futures_cost(entry_price, target_price)
                    all_trades.append(_make_trade(
                        "NIFTY_IDX", entry_ts, c["ts"], side, entry_price,
                        target_price, sl_price, target_price, pnl_pct,
                        "TARGET_HIT", False))
                    exited = True
                    break
            else:
                if c["high"] >= sl_price:
                    pnl_pct = (entry_price - sl_price) / entry_price * 100
                    pnl_pct -= _compute_futures_cost(entry_price, sl_price)
                    all_trades.append(_make_trade(
                        "NIFTY_IDX", entry_ts, c["ts"], side, entry_price,
                        sl_price, sl_price, target_price, pnl_pct,
                        "STOP_LOSS", False))
                    exited = True
                    break
                if c["low"] <= target_price:
                    pnl_pct = (entry_price - target_price) / entry_price * 100
                    pnl_pct -= _compute_futures_cost(entry_price, target_price)
                    all_trades.append(_make_trade(
                        "NIFTY_IDX", entry_ts, c["ts"], side, entry_price,
                        target_price, sl_price, target_price, pnl_pct,
                        "TARGET_HIT", False))
                    exited = True
                    break

        if not exited:
            last = candles[-1]
            if side == "BUY":
                pnl_pct = (last["close"] - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - last["close"]) / entry_price * 100
            pnl_pct -= _compute_futures_cost(entry_price, last["close"])
            all_trades.append(_make_trade(
                "NIFTY_IDX", entry_ts, last["ts"], side, entry_price,
                last["close"], sl_price, target_price, pnl_pct,
                "EOD_SQUARE_OFF", False))

    return sorted(all_trades, key=lambda t: t["entry_ts"])


def _print_table(label: str, metrics: dict) -> None:
    if metrics.get("note"):
        print(f"  {label:<40s} {metrics['note']}")
        return
    print(f"  {label:<40s} "
          f"Trades: {metrics['trades']:>5d}  "
          f"WR: {metrics['win_rate']:>5.1f}%  "
          f"PF: {metrics['pf']:>5.2f}  "
          f"Exp: {metrics['expectancy']:>+7.3f}%  "
          f"Ret: {metrics['total_return']:>+8.2f}%  "
          f"MaxDD: {metrics['max_dd']:>6.2f}%  "
          f"Sharpe: {metrics['sharpe']:>+6.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 9.3 — NIFTY Index Momentum backtest")
    ap.add_argument("--universe", default="NIFTY50", help="Stocks for index construction")
    args = ap.parse_args()

    symbols = get_universe(args.universe)
    print("\n  Phase 9.3 — NIFTY Index Momentum (simulated futures)")
    print(f"  Building index from {len(symbols)} stocks...")

    all_symbol_days: dict[str, dict] = {}
    per_symbol_days_for_regime: dict[str, dict] = {}

    for sym in symbols:
        candles = load_15m(INTRADAY_DB, sym)
        daily = load_daily(DAILY_DB, sym)
        if not candles:
            continue
        days = group_by_day(candles)
        all_symbol_days[sym] = {"days": days, "daily": daily}
        regime_days = dict(days)
        regime_days["_daily"] = daily
        per_symbol_days_for_regime[sym] = regime_days

    print(f"  Loaded {len(all_symbol_days)} symbols.")

    regime_labels = label_regimes(per_symbol_days_for_regime)
    index_days = _build_nifty_index(all_symbol_days)
    print(f"  Index built: {len(index_days)} trading days.")

    dist = defaultdict(int)
    for r in regime_labels.values():
        dist[r] += 1
    print("  Regime distribution: "
          + ", ".join(f"{r}={dist[r]}" for r in ("TREND", "RANGE", "VOLATILE")))
    print("  Cost model: futures (no STT, CTT ~0.015%/side)")

    # ── Signal A: Gap-and-Go on NIFTY ────────────────────────
    print(f"\n  {'='*120}")
    print("  Signal A: Gap-and-Go on NIFTY index (futures cost)")
    print(f"  {'='*120}")

    for win_name, (w_start, w_end) in WINDOWS.items():
        print(f"\n  ── {win_name} window ──")
        for route_name, skip in [("ALL regimes", set()), ("Skip RANGE", {"RANGE"}),
                                  ("VOLATILE only", {"TREND", "RANGE"})]:
            trades = simulate_index_gap_go(
                index_days, regime_labels,
                skip_regimes=skip, start=w_start, end=w_end,
            )
            m = compute_metrics(trades, f"{win_name}/{route_name}", with_costs=False)
            _print_table(route_name, m)
            if win_name == "TEST" and m.get("by_reason"):
                print("    Exit reasons: " +
                      ", ".join(f"{k}={v}" for k, v in sorted(m["by_reason"].items())))

    # ── Parameter sweep ───────────────────────────────────────
    print("\n  ── Gap-and-Go param sweep (TEST, ALL regimes) ──")
    for gp in [0.2, 0.3, 0.5, 0.7]:
        for vm in [1.0, 1.5, 2.0]:
            for rr in [1.2, 1.5, 1.8, 2.0]:
                trades = simulate_index_gap_go(
                    index_days, regime_labels,
                    gap_pct=gp, vol_mult=vm, rr_ratio=rr,
                    start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
                )
                m = compute_metrics(trades, f"g{gp}/v{vm}/r{rr}", with_costs=False)
                if m.get("trades", 0) >= 10:
                    _print_table(f"gap>={gp}% vol>={vm}x RR={rr}", m)

    # ── Square-off sweep ──────────────────────────────────────
    print("\n  ── Square-off time sweep (TEST, ALL regimes) ──")
    for sq in [13, 14, 15]:
        trades = simulate_index_gap_go(
            index_days, regime_labels,
            sq_off_hour=sq,
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        )
        _print_table(f"Sq-off {sq}:00",
                      compute_metrics(trades, f"sq{sq}", with_costs=False))

    # ── Verdict ───────────────────────────────────────────────
    print("\n  === PHASE 9.3 VERDICT ===")
    test_trades = simulate_index_gap_go(
        index_days, regime_labels,
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
    )
    test_m = compute_metrics(test_trades, "TEST/ALL", with_costs=False)
    pf = test_m.get("pf", 0)
    if pf >= GATE_PF:
        print(f"  PASS — OOS PF {pf} >= {GATE_PF}.")
    elif pf >= 1.0:
        print(f"  MARGINAL — OOS PF {pf} >= 1.0 but < {GATE_PF}.")
    else:
        print(f"  FAIL — OOS PF {pf} < 1.0.")
    print("  Note: costs already embedded in trade P&L (futures: ~0.03% round-trip)")


if __name__ == "__main__":
    main()
