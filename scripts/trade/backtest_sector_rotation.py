#!/usr/bin/env python3
"""Phase 9.6 — Sector Rotation Intraday backtest.

THESIS: Institutional flows rotate between sectors intraday. The sector
with the strongest first-30-min return continues outperforming. Buy the
top sector's strongest stock, sell the weakest sector's weakest stock.

METHOD (strict no-lookahead):
  1. Group NIFTY100 stocks by sector (using SECTOR_MAP).
  2. At 10:00 (after 3x 15-min candles), compute each sector's
     average first-30-min return.
  3. Rank sectors by return. Pick top sector (BUY) and bottom sector (SELL).
  4. Within top sector: pick stock with highest first-30-min return → BUY.
  5. Within bottom sector: pick stock with lowest return → SELL.
  6. SL: 1.5% from entry. Target: SL × RR.
  7. Square off at 14:00.

Usage:
    python scripts/trade/backtest_sector_rotation.py
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
    compute_metrics, _make_trade, CAPITAL,
)
from regime_analysis import label_regimes  # noqa: E402
from shared.nifty_universe import get_universe  # noqa: E402

# Import sector map
sys.path.insert(0, os.path.join(PROJECT_ROOT, "modes", "trade"))
from stock_scanner import SECTOR_MAP  # noqa: E402

WINDOWS = {
    "FULL": (None, None),
    "TRAIN": ("2024-05-27", "2025-05-31"),
    "TEST": ("2025-06-01", "2026-05-22"),
}

GATE_PF = 1.15
SCAN_CANDLES = 3            # first 3 candles = 45 min (9:15-10:00)
SL_PCT = 1.5                # fixed SL %
RR_RATIO = 1.5
DAILY_CAP = 2               # 1 BUY + 1 SELL
SQ_OFF_HOUR = 14
LOSER_EXIT_HOUR = 13
MIN_SECTOR_STOCKS = 3       # need at least 3 stocks to rank a sector
MIN_SECTOR_SPREAD = 0.3     # top vs bottom sector must differ by this %


def simulate_sector_rotation(
    all_symbol_days: dict[str, dict],
    regime_labels: dict[str, str],
    *,
    scan_candles: int = SCAN_CANDLES,
    sl_pct: float = SL_PCT,
    rr_ratio: float = RR_RATIO,
    min_spread: float = MIN_SECTOR_SPREAD,
    sq_off_hour: int = SQ_OFF_HOUR,
    skip_regimes: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    if skip_regimes is None:
        skip_regimes = set()

    all_dates: set[str] = set()
    for sdata in all_symbol_days.values():
        all_dates.update(d for d in sdata["days"] if not d.startswith("_"))

    all_trades = []

    for date_str in sorted(all_dates):
        if start and date_str < start:
            continue
        if end and date_str > end:
            continue
        regime = regime_labels.get(date_str)
        if regime and regime in skip_regimes:
            continue

        # Compute per-stock first-N-candle returns
        stock_returns: dict[str, tuple[float, list[dict]]] = {}
        for sym, sdata in all_symbol_days.items():
            candles = sdata["days"].get(date_str)
            if not candles or len(candles) < scan_candles + 2:
                continue
            open_price = candles[0]["open"]
            scan_close = candles[scan_candles - 1]["close"]
            if open_price <= 0:
                continue
            ret = (scan_close - open_price) / open_price * 100
            stock_returns[sym] = (ret, candles)

        if not stock_returns:
            continue

        # Group by sector
        sector_returns: dict[str, list[tuple[str, float, list[dict]]]] = defaultdict(list)
        for sym, (ret, candles) in stock_returns.items():
            sector = SECTOR_MAP.get(sym, "OTHER")
            if sector == "OTHER":
                continue
            sector_returns[sector].append((sym, ret, candles))

        # Filter sectors with enough stocks
        valid_sectors = {
            sec: stocks for sec, stocks in sector_returns.items()
            if len(stocks) >= MIN_SECTOR_STOCKS
        }
        if len(valid_sectors) < 2:
            continue

        # Rank sectors by average return
        sector_avg = {}
        for sec, stocks in valid_sectors.items():
            sector_avg[sec] = sum(r for _, r, _ in stocks) / len(stocks)

        sorted_sectors = sorted(sector_avg.items(), key=lambda x: x[1])
        worst_sector = sorted_sectors[0]
        best_sector = sorted_sectors[-1]

        # Check spread
        spread = best_sector[1] - worst_sector[1]
        if spread < min_spread:
            continue

        # Pick best stock from best sector (BUY)
        best_stocks = valid_sectors[best_sector[0]]
        best_stocks.sort(key=lambda x: x[1], reverse=True)
        buy_sym, buy_ret, buy_candles = best_stocks[0]

        # Pick worst stock from worst sector (SELL)
        worst_stocks = valid_sectors[worst_sector[0]]
        worst_stocks.sort(key=lambda x: x[1])
        sell_sym, sell_ret, sell_candles = worst_stocks[0]

        # Enter trades
        for sym, side, candles in [(buy_sym, "BUY", buy_candles),
                                    (sell_sym, "SELL", sell_candles)]:
            entry_candle = candles[scan_candles]
            entry_price = entry_candle["close"]
            if entry_price <= 0:
                continue

            if side == "BUY":
                sl_price = entry_price * (1 - sl_pct / 100)
                target_price = entry_price * (1 + sl_pct * rr_ratio / 100)
            else:
                sl_price = entry_price * (1 + sl_pct / 100)
                target_price = entry_price * (1 - sl_pct * rr_ratio / 100)

            entry_ts = entry_candle["ts"]
            exited = False

            for ci in range(scan_candles + 1, len(candles)):
                c = candles[ci]
                hour = c["ts"].hour
                minute = c["ts"].minute

                if hour * 60 + minute >= sq_off_hour * 60:
                    if side == "BUY":
                        pnl_pct = (c["close"] - entry_price) / entry_price * 100
                    else:
                        pnl_pct = (entry_price - c["close"]) / entry_price * 100
                    all_trades.append(_make_trade(
                        sym, entry_ts, c["ts"], side, entry_price,
                        c["close"], sl_price, target_price, pnl_pct,
                        "EOD_SQUARE_OFF", True))
                    exited = True
                    break

                if side == "BUY":
                    if c["low"] <= sl_price:
                        pnl_pct = (sl_price - entry_price) / entry_price * 100
                        all_trades.append(_make_trade(
                            sym, entry_ts, c["ts"], side, entry_price,
                            sl_price, sl_price, target_price, pnl_pct,
                            "STOP_LOSS", True))
                        exited = True
                        break
                    if c["high"] >= target_price:
                        pnl_pct = (target_price - entry_price) / entry_price * 100
                        all_trades.append(_make_trade(
                            sym, entry_ts, c["ts"], side, entry_price,
                            target_price, sl_price, target_price, pnl_pct,
                            "TARGET_HIT", True))
                        exited = True
                        break
                    if hour >= LOSER_EXIT_HOUR and c["close"] < entry_price:
                        pnl_pct = (c["close"] - entry_price) / entry_price * 100
                        all_trades.append(_make_trade(
                            sym, entry_ts, c["ts"], side, entry_price,
                            c["close"], sl_price, target_price, pnl_pct,
                            "LOSER_EXIT_LATE", True))
                        exited = True
                        break
                else:
                    if c["high"] >= sl_price:
                        pnl_pct = (entry_price - sl_price) / entry_price * 100
                        all_trades.append(_make_trade(
                            sym, entry_ts, c["ts"], side, entry_price,
                            sl_price, sl_price, target_price, pnl_pct,
                            "STOP_LOSS", True))
                        exited = True
                        break
                    if c["low"] <= target_price:
                        pnl_pct = (entry_price - target_price) / entry_price * 100
                        all_trades.append(_make_trade(
                            sym, entry_ts, c["ts"], side, entry_price,
                            target_price, sl_price, target_price, pnl_pct,
                            "TARGET_HIT", True))
                        exited = True
                        break
                    if hour >= LOSER_EXIT_HOUR and c["close"] > entry_price:
                        pnl_pct = (entry_price - c["close"]) / entry_price * 100
                        all_trades.append(_make_trade(
                            sym, entry_ts, c["ts"], side, entry_price,
                            c["close"], sl_price, target_price, pnl_pct,
                            "LOSER_EXIT_LATE", True))
                        exited = True
                        break

            if not exited:
                last = candles[-1]
                if side == "BUY":
                    pnl_pct = (last["close"] - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - last["close"]) / entry_price * 100
                all_trades.append(_make_trade(
                    sym, entry_ts, last["ts"], side, entry_price,
                    last["close"], sl_price, target_price, pnl_pct,
                    "EOD_SQUARE_OFF", True))

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
    ap = argparse.ArgumentParser(description="Phase 9.6 — Sector Rotation backtest")
    ap.add_argument("--universe", default="NIFTY100")
    ap.add_argument("--sl", type=float, default=SL_PCT)
    ap.add_argument("--rr", type=float, default=RR_RATIO)
    ap.add_argument("--min-spread", type=float, default=MIN_SECTOR_SPREAD)
    ap.add_argument("--sq-off", type=int, default=SQ_OFF_HOUR)
    args = ap.parse_args()

    symbols = get_universe(args.universe)
    print(f"\n  Phase 9.6 — Sector Rotation Intraday")
    print(f"  SL={args.sl}%, RR={args.rr}, min sector spread={args.min_spread}%")
    print(f"  Loading {len(symbols)} symbols...")

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
    dist = defaultdict(int)
    for r in regime_labels.values():
        dist[r] += 1
    print(f"  Regime distribution: "
          + ", ".join(f"{r}={dist[r]}" for r in ("TREND", "RANGE", "VOLATILE")))

    # ── Walk-forward ──────────────────────────────────────────
    print(f"\n  {'='*120}")
    print(f"  Walk-forward results (net of cost)")
    print(f"  {'='*120}")

    for win_name, (w_start, w_end) in WINDOWS.items():
        print(f"\n  ── {win_name} window ──")
        for route_name, skip in [("ALL regimes", set()), ("Skip RANGE", {"RANGE"}),
                                  ("VOLATILE only", {"TREND", "RANGE"})]:
            trades = simulate_sector_rotation(
                all_symbol_days, regime_labels,
                sl_pct=args.sl, rr_ratio=args.rr,
                min_spread=args.min_spread, sq_off_hour=args.sq_off,
                skip_regimes=skip, start=w_start, end=w_end,
            )
            m = compute_metrics(trades, f"{win_name}/{route_name}", with_costs=True)
            _print_table(route_name, m)
            if win_name == "TEST" and m.get("by_reason"):
                print(f"    Exit reasons: " +
                      ", ".join(f"{k}={v}" for k, v in sorted(m["by_reason"].items())))

    # ── Parameter sweep ───────────────────────────────────────
    print(f"\n  ── Param sweep (TEST, ALL regimes) ──")
    for sl in [1.0, 1.5, 2.0]:
        for rr in [1.0, 1.5, 2.0]:
            for ms in [0.2, 0.3, 0.5]:
                trades = simulate_sector_rotation(
                    all_symbol_days, regime_labels,
                    sl_pct=sl, rr_ratio=rr, min_spread=ms,
                    sq_off_hour=args.sq_off,
                    start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
                )
                m = compute_metrics(trades, f"sl{sl}/rr{rr}/ms{ms}", with_costs=True)
                if m.get("trades", 0) >= 20:
                    _print_table(f"SL={sl}% RR={rr} spread>={ms}%", m)

    # ── Verdict ───────────────────────────────────────────────
    print(f"\n  === PHASE 9.6 VERDICT ===")
    test_trades = simulate_sector_rotation(
        all_symbol_days, regime_labels,
        sl_pct=args.sl, rr_ratio=args.rr,
        min_spread=args.min_spread, sq_off_hour=args.sq_off,
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
    )
    test_m = compute_metrics(test_trades, "TEST/ALL", with_costs=True)
    pf = test_m.get("pf", 0)
    if pf >= GATE_PF:
        print(f"  PASS — OOS PF {pf} >= {GATE_PF}.")
    elif pf >= 1.0:
        print(f"  MARGINAL — OOS PF {pf} >= 1.0 but < {GATE_PF}.")
    else:
        print(f"  FAIL — OOS PF {pf} < 1.0.")


if __name__ == "__main__":
    main()
