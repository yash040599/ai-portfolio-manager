#!/usr/bin/env python3
"""Phase 3 — VWAP-as-trailing-stop on TREND/VOLATILE days (OOS).

Phase 1 proved the scorer's only non-negative edge lives in a market
*regime* (VOLATILE-only OOS PF 1.10) and Phase 2 proved a finer timeframe
is a dead end. The remaining lever is a *different exit signal* stacked on
the regime gate. This script tests the cheapest such signal:

    Instead of a fixed ATR target, on directional days use the running
    intraday VWAP as a TRAILING STOP — let winners run while price holds
    its VWAP, exit when a bar closes back through VWAP.

Rationale: FIIs/algos anchor execution to VWAP; on NSE, price that holds
above VWAP post-10:00 tends to follow through, so capping winners at a
fixed 1.8R target throws away the fat tail that pays for the losers.

Method (apples-to-apples, same frozen config, same regime labels, same
2-trade/day portfolio cap as Phase 1):
  Mode A  FIXED   — frozen ATR SL + fixed RR target (current system)
  Mode B  VTRAIL  — frozen ATR SL (hard floor) + VWAP trailing exit, no
                    fixed target (gate_vwap_trail=True)

Both modes are run on each regime keep-set and split TRAIN vs TEST(OOS).
Promotion gate: TEST PF >= 1.15 AND expectancy > 0.

Usage:
    python scripts/trade/backtest_vwap_trail.py
    python scripts/trade/backtest_vwap_trail.py --universe NIFTY50

Read-only: never trades or touches capital. Out-of-sample by construction.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

# scripts/trade on path (regime_analysis/backtest_gates use bare imports)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from backtest_gates import (  # noqa: E402
    INTRADAY_DB,
    DAILY_DB,
    load_15m,
    load_daily,
    group_by_day,
    simulate_trades,
    compute_metrics,
)
from regime_analysis import (  # noqa: E402
    FROZEN,
    PORTFOLIO_DAILY_CAP,
    WINDOWS,
    label_regimes,
    _apply_daily_cap,
)
from shared.nifty_universe import get_universe  # noqa: E402

GATE_PF = 1.15
GATE_EXPECTANCY = 0.0
TRADE_VALUE = 15_000


def _hold_minutes(t: dict) -> float:
    import datetime
    a = datetime.datetime.fromisoformat(t["entry_ts"])
    b = datetime.datetime.fromisoformat(t["exit_ts"])
    return (b - a).total_seconds() / 60.0


def _winner_stats(trades: list[dict]) -> tuple[float, float, float]:
    """Return (avg_winner_pct, avg_loser_pct, avg_hold_min) net of cost."""
    if not trades:
        return 0.0, 0.0, 0.0
    key = "net_pnl_pct"
    wins = [t[key] for t in trades if t.get(key, 0) > 0]
    losses = [t[key] for t in trades if t.get(key, 0) <= 0]
    holds = [_hold_minutes(t) for t in trades]
    avg_w = sum(wins) / len(wins) if wins else 0.0
    avg_l = sum(losses) / len(losses) if losses else 0.0
    avg_h = sum(holds) / len(holds) if holds else 0.0
    return avg_w, avg_l, avg_h


def _split(trades: list[dict], start: str, end: str) -> list[dict]:
    out = trades
    if start:
        out = [t for t in out if t["entry_ts"][:10] >= start]
    if end:
        out = [t for t in out if t["entry_ts"][:10] <= end]
    return out


def _run_mode(per_symbol_days: dict, labels: dict, keep: tuple,
              vwap_trail: bool) -> list[dict]:
    """Run the frozen config over all symbols, keep only trades whose entry
    day is in `keep` regimes, apply the portfolio daily cap."""
    cfg = dict(FROZEN)
    if vwap_trail:
        cfg["gate_vwap_trail"] = True
    all_trades: list[dict] = []
    for sym, days in per_symbol_days.items():
        sim_days = {d: v for d, v in days.items() if not d.startswith("_")}
        if not sim_days:
            continue
        trades = simulate_trades(sim_days, days["_daily"], sym,
                                 with_costs=True, **cfg)
        for t in trades:
            if labels.get(t["entry_ts"][:10]) in keep:
                all_trades.append(t)
    all_trades.sort(key=lambda t: t["entry_ts"])
    return _apply_daily_cap(all_trades, PORTFOLIO_DAILY_CAP)


def _print_block(title: str, trades: list[dict]) -> dict:
    tr_start, tr_end = WINDOWS["TRAIN"]
    te_start, te_end = WINDOWS["TEST"]
    train = _split(trades, tr_start, tr_end)
    test = _split(trades, te_start, te_end)
    print(f"\n  --- {title} ---")
    hdr = (f"{'Window':<8}{'Trades':>8}{'WR%':>7}{'PF':>7}{'Exp%':>9}"
           f"{'AvgW%':>8}{'AvgL%':>8}{'HoldMin':>9}")
    print("  " + hdr)
    print("  " + "-" * len(hdr))
    out = {}
    for lbl, tr in (("TRAIN", train), ("TEST", test)):
        m = compute_metrics(tr, lbl, with_costs=True)
        avg_w, avg_l, avg_h = _winner_stats(tr)
        out[lbl] = m
        print(f"  {lbl:<8}{m['trades']:>8}{m['win_rate']:>7}{m['pf']:>7}"
              f"{m['expectancy']:>9}{avg_w:>8.3f}{avg_l:>8.3f}{avg_h:>9.0f}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3 VWAP-trail backtest")
    ap.add_argument("--universe", default="NIFTY50")
    args = ap.parse_args()

    symbols = get_universe(args.universe)
    print(f"\n  Loading {len(symbols)} symbols...")
    per_symbol_days: dict[str, dict] = {}
    for sym in symbols:
        candles = load_15m(INTRADAY_DB, sym)
        daily = load_daily(DAILY_DB, sym)
        if candles:
            days = group_by_day(candles)
            days["_daily"] = daily
            per_symbol_days[sym] = days
    print(f"  Loaded {len(per_symbol_days)} symbols.")

    labels = label_regimes(per_symbol_days)
    dist = defaultdict(int)
    for r in labels.values():
        dist[r] += 1
    print(f"  Regime days: " + ", ".join(
        f"{r} {dist[r]}" for r in ("TREND", "RANGE", "VOLATILE")))

    keep_sets = {
        "TREND only": ("TREND",),
        "VOLATILE only": ("VOLATILE",),
        "TREND+VOLATILE": ("TREND", "VOLATILE"),
    }

    results = {}
    for name, keep in keep_sets.items():
        print(f"\n  ============================================================")
        print(f"  REGIME KEEP-SET: {name}")
        print(f"  ============================================================")
        fixed = _run_mode(per_symbol_days, labels, keep, vwap_trail=False)
        vtrail = _run_mode(per_symbol_days, labels, keep, vwap_trail=True)
        rf = _print_block("Mode A  FIXED target (current)", fixed)
        rv = _print_block("Mode B  VWAP TRAIL (Phase 3)", vtrail)
        results[name] = (rf, rv)

    # ── Verdict ───────────────────────────────────────────────
    print(f"\n  === PHASE 3 VERDICT (OOS/TEST, gate PF>={GATE_PF}) ===")
    best = None
    for name, (rf, rv) in results.items():
        f_te, v_te = rf["TEST"], rv["TEST"]
        delta = v_te["pf"] - f_te["pf"]
        tag = ""
        if v_te["pf"] >= GATE_PF and v_te["expectancy"] > GATE_EXPECTANCY:
            tag = "  <-- CLEARS GATE"
            if best is None or v_te["pf"] > best[1]:
                best = (name, v_te["pf"])
        print(f"  {name:<16} FIXED PF {f_te['pf']:>5}  ->  VTRAIL PF "
              f"{v_te['pf']:>5}  (delta {delta:+.2f}){tag}")
    print()
    if best:
        print(f"  RESULT: VWAP trail CLEARS the gate on '{best[0]}' "
              f"(TEST PF {best[1]}). Candidate for dry-run.")
    else:
        print(f"  RESULT: VWAP trail does NOT clear PF>={GATE_PF} OOS in any "
              f"regime keep-set. Improvement (if any) is insufficient alone.")
    print()


if __name__ == "__main__":
    main()
