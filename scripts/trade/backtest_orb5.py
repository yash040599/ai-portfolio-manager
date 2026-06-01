"""
scripts/trade/backtest_orb5.py
================================================================
Backtest: ORB-5 (Opening Range Breakout on 5-minute candles)

Phase 2.3 of the trade roadmap. The 15-min ORB scored PF 0.97 (just
below breakeven). This re-runs the *same* breakout logic on a tighter
5-minute opening range (first 5-min candle = 9:15-9:20) to test whether
the finer timeframe lifts the edge above the 1.15 promotion gate.

Key differences vs backtest_orb15.py:
  * Loads 5-minute candles (intraday_5m.sqlite, interval='5minute').
  * Splits TRAIN (in-sample) vs TEST (out-of-sample) by date so the
    OOS profit factor is reported separately — the only number that
    matters for the promotion gate.
  * Applies the canonical cost model (Config.calculate_charges via
    backtest_gates.compute_charges) so PF is net of brokerage, STT,
    GST, etc. — same single source of truth as the live engine.

The breakout / partial-exit / EMA-trail simulation is reused verbatim
from backtest_orb15.simulate_orb15 (it keys off day_candles[0] as the
opening range, which on 5-min candles is the 9:15-9:20 bar).

Usage:
    python scripts/trade/backtest_orb5.py
    python scripts/trade/backtest_orb5.py --symbol RELIANCE
    python scripts/trade/backtest_orb5.py --split-date 2025-06-01
================================================================
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
except ImportError:
    pass

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from shared.nifty_universe import get_universe  # noqa: E402
from scripts.trade.backtest_orb15 import (  # noqa: E402
    simulate_orb15,
    compute_avg_volume_per_slot,
    group_by_day,
    compute_metrics,
)
from scripts.trade.backtest_gates import compute_charges  # noqa: E402

# -- Data paths ------------------------------------------------
BT_DATA = os.path.join(os.path.dirname(PROJECT_ROOT), "ai-portfolio-backtest-data", "candles")
INTRADAY_DB = os.path.join(BT_DATA, "intraday_5m.sqlite")
OUT_DIR = os.path.join(PROJECT_ROOT, "reports", "backtest")

# Notional per trade — matches backtest_gates.py so the cost drag is
# computed on the same position size as every other gate backtest.
TRADE_VALUE = 15_000

# Default OOS boundary: year-1 = train, year-2 = test (out-of-sample).
DEFAULT_SPLIT_DATE = "2025-06-01"


def load_5m(db_path: str, symbol: str) -> list[dict]:
    if not os.path.isfile(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ts_ist, open, high, low, close, volume FROM candles "
        "WHERE symbol=? AND interval='5minute' ORDER BY ts_ist",
        (symbol,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            ts = datetime.datetime.fromisoformat(r["ts_ist"])
        except ValueError:
            continue
        out.append({
            "ts": ts, "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": int(r["volume"] or 0),
        })
    return out


def apply_costs(trades: list[dict]) -> list[dict]:
    """Add a net_pnl_pct to each trade row, net of round-trip charges.

    Each row is treated as a round trip on a TRADE_VALUE notional —
    the same convention as backtest_gates.py. Conservative for partial
    exits (charges the full notional), which only makes the edge test
    stricter.
    """
    out = []
    for t in trades:
        entry = t["entry"]
        exit_p = t["exit"]
        qty = max(1, int(TRADE_VALUE / entry)) if entry > 0 else 1
        buy_value = qty * entry
        sell_value = qty * exit_p
        charges = compute_charges(buy_value, sell_value)
        cost_pct = (charges / buy_value * 100) if buy_value > 0 else 0.0
        net = dict(t)
        net["net_pnl_pct"] = round(t["pnl_pct"] - cost_pct, 3)
        out.append(net)
    return out


def _compute_net_metrics(trades: list[dict], label: str) -> dict:
    """compute_metrics keyed on net_pnl_pct (re-map then restore)."""
    remapped = []
    for t in trades:
        r = dict(t)
        r["pnl_pct"] = t["net_pnl_pct"]
        remapped.append(r)
    return compute_metrics(remapped, label)


def _split(trades: list[dict], split_date: str) -> tuple[list[dict], list[dict]]:
    train, test = [], []
    for t in trades:
        if t["entry_ts"][:10] < split_date:
            train.append(t)
        else:
            test.append(t)
    return train, test


def _print(label: str, gross: dict, net: dict):
    print(f"\n{'='*64}")
    print(f"  ORB-5 Breakout — {label}")
    print(f"{'='*64}")
    if gross.get("note"):
        print(f"  {gross['note']}")
        print(f"{'='*64}")
        return
    print(f"  Period            : {gross['period']}")
    print(f"  Trades            : {gross['trades']}")
    print(f"  Win rate          : {gross['win_rate']}%")
    print(f"  PF (gross)        : {gross['profit_factor']}")
    print(f"  PF (net of costs) : {net['profit_factor']}")
    print(f"  Expectancy (gross): {gross['expectancy_pct']}% / trade")
    print(f"  Expectancy (net)  : {net['expectancy_pct']}% / trade")
    print(f"  Total return net  : {net['total_return_pct']}%")
    print(f"  Max drawdown net  : {net['max_drawdown_pct']}%")
    print(f"{'='*64}")


def main():
    parser = argparse.ArgumentParser(description="Backtest ORB-5 Breakout (5-min OOS)")
    parser.add_argument("--symbol", default=None, help="Single symbol (default: all NIFTY 50)")
    parser.add_argument("--universe", default="NIFTY50")
    parser.add_argument("--split-date", default=DEFAULT_SPLIT_DATE,
                        help="OOS boundary (YYYY-MM-DD): trades before = TRAIN, on/after = TEST")
    args = parser.parse_args()

    symbols = [args.symbol.upper()] if args.symbol else get_universe(args.universe)
    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"\n  === ORB-5 (5-min candles) ===")
    print(f"  Symbols    : {len(symbols)}")
    print(f"  Source     : {INTRADAY_DB}")
    print(f"  OOS split  : TRAIN < {args.split_date} <= TEST")

    all_trades = []
    for i, sym in enumerate(symbols):
        candles = load_5m(INTRADAY_DB, sym)
        if not candles:
            continue
        days = group_by_day(candles)
        avg_vols = compute_avg_volume_per_slot(days)
        trades = simulate_orb15(days, avg_vols, sym)
        all_trades.extend(trades)
        if (i + 1) % 10 == 0:
            print(f"    [{i+1}/{len(symbols)}] processed, {len(all_trades)} trades so far")

    all_trades.sort(key=lambda t: t["entry_ts"])
    all_trades = apply_costs(all_trades)

    train, test = _split(all_trades, args.split_date)

    full_gross = compute_metrics(all_trades, "FULL gross")
    full_net = _compute_net_metrics(all_trades, "FULL net")
    train_gross = compute_metrics(train, "TRAIN gross")
    train_net = _compute_net_metrics(train, "TRAIN net")
    test_gross = compute_metrics(test, "TEST gross")
    test_net = _compute_net_metrics(test, "TEST net")

    _print("TRAIN (in-sample)", train_gross, train_net)
    _print("TEST (OUT-OF-SAMPLE)", test_gross, test_net)
    _print("FULL (2 years)", full_gross, full_net)

    out_path = os.path.join(OUT_DIR, "orb5_intraday_trades.json")
    with open(out_path, "w") as f:
        json.dump({
            "strategy": "ORB_5_BREAKOUT",
            "split_date": args.split_date,
            "metrics": {
                "full_gross": full_gross, "full_net": full_net,
                "train_gross": train_gross, "train_net": train_net,
                "test_gross": test_gross, "test_net": test_net,
            },
            "trades": all_trades,
        }, f, indent=2)
    print(f"\n  Saved: {out_path}")


if __name__ == "__main__":
    main()
