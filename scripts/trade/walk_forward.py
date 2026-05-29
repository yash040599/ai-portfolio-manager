#!/usr/bin/env python3
"""Phase 0.3 — Out-of-sample / walk-forward validation runner.

The 2026-05-26 gate audit tuned every parameter on the *full* 2-year
dataset (in-sample) and reported PF 0.86. In-sample PF systematically
overstates the real edge. This runner re-measures the **frozen** audit
config on disjoint time windows so we can see whether the edge is
stable out-of-sample or was an artifact of fitting to one regime.

It runs the EXACT live config (config version v2.0-2026-05-26):
    min_score=2.0, atr_multiplier=2.0, rr_ratio=1.8, rr_floor=1.3,
    loser_exit_hour=13, square_off=14:00, portfolio_daily_cap=2,
    RSI/VWAP/ADX/RVOL gates disabled.

Windows:
    FULL      whole dataset (in-sample reference, ~= audit's 0.86)
    TRAIN     first ~year   (2024-05-27 .. 2025-05-31)
    TEST      second ~year  (2025-06-01 .. 2026-05-22)  <- out-of-sample
    plus per-half-year slices for a stability profile.

Usage:
    python scripts/trade/walk_forward.py
    python scripts/trade/walk_forward.py --universe NIFTY50

Nothing here trades or touches capital — it only reads candle history.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from backtest_gates import (
    INTRADAY_DB,
    DAILY_DB,
    load_15m,
    load_daily,
    group_by_day,
    simulate_trades,
    compute_metrics,
)
from shared.nifty_universe import get_universe

# ── Frozen audit config (config version v2.0-2026-05-26) ──────
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

# ── Promotion gate (docs/TRADE_ROADMAP.md) ────────────────────
GATE_PF = 1.15
GATE_EXPECTANCY = 0.0  # expectancy is in pnl-% units here, not Rs; require >0

# ── Validation windows ────────────────────────────────────────
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
    ap = argparse.ArgumentParser(description="Phase 0.3 walk-forward validation")
    ap.add_argument("--universe", default="NIFTY50")
    ap.add_argument("--symbol", default=None)
    args = ap.parse_args()

    symbols = [args.symbol.upper()] if args.symbol else get_universe(args.universe)
    print(f"\n  Loading {len(symbols)} symbols (net-of-cost, frozen audit config)...")

    all_data: dict[str, dict] = {}
    for sym in symbols:
        candles = load_15m(INTRADAY_DB, sym)
        daily = load_daily(DAILY_DB, sym)
        if candles:
            all_data[sym] = {"days": group_by_day(candles), "daily": daily}
    print(f"  Loaded {len(all_data)} symbols.\n")

    rows = []
    for label, start, end in WINDOWS:
        m = _run_window(all_data, start, end, label)
        rows.append(m)

    # ── Report ────────────────────────────────────────────────
    hdr = f"{'Window':<24}{'Trades':>8}{'WR%':>7}{'PF':>7}{'Exp%':>9}{'Ret%':>9}{'MaxDD':>9}{'Sharpe':>8}"
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

    # ── Verdict ───────────────────────────────────────────────
    test = next((m for m in rows if m["label"].startswith("TEST")), None)
    train = next((m for m in rows if m["label"].startswith("TRAIN")), None)
    print("\n  === PHASE 0.3 VERDICT ===")
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
        print(f"  Promotion gate (PF>={GATE_PF}, expectancy>0): "
              f"{'PASS — eligible for dry-run forward-test' if passed else 'FAIL — DO NOT GO LIVE'}")
    else:
        print("  TEST window produced no trades — cannot validate.")
    print()


if __name__ == "__main__":
    main()
