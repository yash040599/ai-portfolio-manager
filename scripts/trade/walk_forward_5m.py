#!/usr/bin/env python3
"""Phase 2.1 / 2.2 — 5-minute entry-timing out-of-sample validation.

The live engine and the Phase 0.3 walk-forward both run on 15-minute
candles (OOS PF 0.82). This runner feeds the EXACT same frozen audit
config and cost model into the same simulator, but on 5-minute candles,
to test whether finer entry timing changes the out-of-sample edge.

It mirrors walk_forward.py one-for-one (same FROZEN config, same
PORTFOLIO_DAILY_CAP, same WINDOWS, same net-of-cost metrics) so the
only variable is the candle interval. Compare the TEST (yr2 OOS) PF
here against the 15-min walk_forward TEST PF (0.82).

Note: indicators (EMA9/21, ATR, score window) are computed in *candle*
units, so on 5-min candles they react ~3x faster than on 15-min. That
is precisely the effect Phase 2.1 is measuring.

Usage:
    python scripts/trade/walk_forward_5m.py
    python scripts/trade/walk_forward_5m.py --universe NIFTY50

Nothing here trades or touches capital — it only reads candle history.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
except ImportError:
    pass

from scripts.trade.backtest_gates import (  # noqa: E402
    DAILY_DB,
    load_daily,
    group_by_day,
    simulate_trades,
    compute_metrics,
)
from shared.nifty_universe import get_universe  # noqa: E402

BT_DATA = os.path.join(os.path.dirname(PROJECT_ROOT), "ai-portfolio-backtest-data", "candles")
INTRADAY_5M_DB = os.path.join(BT_DATA, "intraday_5m.sqlite")

# ── Frozen audit config — kept identical to walk_forward.py so the
#    only variable between the 15-min and 5-min runs is the interval.
FROZEN = dict(
    min_score=2.0,
    atr_multiplier=2.0,
    rr_ratio=1.8,
    rr_floor=1.3,
    gate_loser_exit_hour=13,
    gate_square_off_hour=14,
    gate_square_off_minute=0,
)
PORTFOLIO_DAILY_CAP = 2
GATE_PF = 1.15
GATE_EXPECTANCY = 0.0

WINDOWS = [
    ("FULL  (in-sample ref)", None, None),
    ("TRAIN (yr1)", "2024-05-27", "2025-05-31"),
    ("TEST  (yr2, OOS)", "2025-06-01", "2026-05-22"),
    ("H1 2024-H2", "2024-05-27", "2024-12-31"),
    ("H2 2025-H1", "2025-01-01", "2025-06-30"),
    ("H3 2025-H2", "2025-07-01", "2025-12-31"),
    ("H4 2026-H1", "2026-01-01", "2026-05-22"),
]


def _apply_daily_cap(all_trades: list[dict], cap: int) -> list[dict]:
    """Keep the first `cap` trades per day by entry time (no hindsight)."""
    if cap <= 0 or not all_trades:
        return all_trades
    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in all_trades:
        by_day[t["entry_ts"][:10]].append(t)
    kept: list[dict] = []
    for day in sorted(by_day):
        day_trades = sorted(by_day[day], key=lambda t: t["entry_ts"])
        kept.extend(day_trades[:cap])
    return sorted(kept, key=lambda t: t["entry_ts"])


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


def _run_window(all_data: dict, start: str | None, end: str | None,
                label: str) -> dict:
    all_trades: list[dict] = []
    for sym, data in all_data.items():
        days = data["days"]
        if start:
            days = {d: v for d, v in days.items() if d >= start}
        if end:
            days = {d: v for d, v in days.items() if d <= end}
        if not days:
            continue
        trades = simulate_trades(
            days, data["daily"], sym, with_costs=True, **FROZEN,
        )
        all_trades.extend(trades)
    all_trades = _apply_daily_cap(all_trades, PORTFOLIO_DAILY_CAP)
    return compute_metrics(all_trades, label, with_costs=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 2.1 5-min entry-timing OOS validation")
    ap.add_argument("--universe", default="NIFTY50")
    ap.add_argument("--symbol", default=None)
    args = ap.parse_args()

    symbols = [args.symbol.upper()] if args.symbol else get_universe(args.universe)
    print(f"\n  Loading {len(symbols)} symbols on 5-MIN candles "
          f"(net-of-cost, frozen audit config)...")

    all_data: dict[str, dict] = {}
    for sym in symbols:
        candles = load_5m(INTRADAY_5M_DB, sym)
        daily = load_daily(DAILY_DB, sym)
        if candles:
            all_data[sym] = {"days": group_by_day(candles), "daily": daily}
    print(f"  Loaded {len(all_data)} symbols.\n")

    rows = []
    for label, start, end in WINDOWS:
        rows.append(_run_window(all_data, start, end, label))

    hdr = (f"{'Window':<24}{'Trades':>8}{'WR%':>7}{'PF':>7}"
           f"{'Exp%':>9}{'Ret%':>9}{'MaxDD':>9}{'Sharpe':>8}")
    print("  " + hdr)
    print("  " + "-" * len(hdr))
    for m in rows:
        if m.get("trades", 0) == 0:
            print(f"  {m['label']:<24}{'(no trades)':>8}")
            continue
        print(
            f"  {m['label']:<24}{m['trades']:>8}{m['win_rate']:>7}"
            f"{m['pf']:>7}{m['expectancy']:>9}{m['total_return']:>9}"
            f"{m['max_dd']:>9}{m['sharpe']:>8}"
        )

    test = next((m for m in rows if m["label"].startswith("TEST")), None)
    train = next((m for m in rows if m["label"].startswith("TRAIN")), None)
    print("\n  === PHASE 2.1 VERDICT (5-min entry timing) ===")
    if test and test.get("trades", 0) > 0:
        pf = test["pf"]
        exp = test["expectancy"]
        passed = pf >= GATE_PF and exp > GATE_EXPECTANCY
        print(f"  Out-of-sample (TEST yr2): PF={pf}  expectancy={exp}%  trades={test['trades']}")
        if train and train.get("trades", 0) > 0:
            print(f"  In-sample   (TRAIN yr1): PF={train['pf']}  expectancy={train['expectancy']}%")
            drift = round(train["pf"] - pf, 2)
            print(f"  PF drift train->test: {drift:+.2f}  "
                  f"({'STABLE' if abs(drift) <= 0.15 else 'UNSTABLE / regime-dependent'})")
        print(f"  vs 15-min baseline OOS PF 0.82")
        print(f"  Promotion gate (PF>={GATE_PF}, expectancy>0): "
              f"{'PASS' if passed else 'FAIL — DO NOT GO LIVE'}")
    else:
        print("  TEST window produced no trades — cannot validate.")
    print()


if __name__ == "__main__":
    main()
