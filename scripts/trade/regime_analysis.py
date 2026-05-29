#!/usr/bin/env python3
"""Phase 1.1 + 1.2 — Regime labeler and per-regime PF breakdown.

Phase 0 proved the system has negative out-of-sample expectancy (PF 0.82).
The #1 diagnosed edge gap is that ONE strategy runs in ALL market
conditions. This script answers the gating question for Phase 1:

    "Is the scorer's edge concentrated in a particular market regime,
     or is it uniformly negative everywhere?"

If a regime exists where the frozen config is clearly profitable
(PF > 1.0 net of cost), regime ROUTING (trade only in that regime) is a
real lever. If PF is sub-1.0 in every regime, routing alone cannot save
it and we need a different strategy.

── Regime labeling (Phase 1.1) ──────────────────────────────────
No NIFTY index / VIX exists in the backtest data, so we build a
SYNTHETIC equal-weight market proxy from the NIFTY50 constituents and
label each trading day using MORNING-ONLY data (first `MORNING_CANDLES`
15-min bars, i.e. ~09:15–10:45). Morning-only => the label is realizable
live (no lookahead) and can later feed a predictive classifier.

Per-day market features (averaged across constituents):
  gap%            first-bar open vs prior-day close (signed)
  morning_range%  (max high - min low) / open over the morning window
                  — realized-volatility proxy that stands in for VIX
  morning_ret%    (close@N - open@0) / open@0 (signed)
  breadth         fraction of constituents with positive morning return
  dir_efficiency  |morning_ret%| / morning_range%  (0..1)
                  high = net directional (trend), low = chop (range)

Rules (thresholds = terciles/median over the labeled days):
  VOLATILE  morning_range% in the top tercile (biggest swings)
  TREND     not volatile AND dir_efficiency >= median (directional)
  RANGE     not volatile AND dir_efficiency <  median (choppy)

NOTE: for this *analysis* we set thresholds on the full sample (standard
for opportunity-sizing). For a LIVE gate (Phase 1.3) the thresholds must
be frozen on the train window only.

── Usage ────────────────────────────────────────────────────────
    python scripts/trade/regime_analysis.py
    python scripts/trade/regime_analysis.py --window TEST   # OOS year only

Read-only: never trades or touches capital.
"""
from __future__ import annotations

import argparse
import statistics
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

# Morning window for regime labeling: 6 x 15-min bars = 09:15–10:45.
MORNING_CANDLES = 6

WINDOWS = {
    "FULL": (None, None),
    "TRAIN": ("2024-05-27", "2025-05-31"),
    "TEST": ("2025-06-01", "2026-05-22"),
}


# ── Phase 1.1: regime labeler ─────────────────────────────────

def _day_market_features(per_symbol_days: dict, date: str) -> dict | None:
    """Aggregate morning features across constituents for one date."""
    gaps, mret, mrange, ups = [], [], [], 0
    n = 0
    for sym, days in per_symbol_days.items():
        candles = days.get(date)
        if not candles or len(candles) < MORNING_CANDLES:
            continue
        prior_close = _prior_close(per_symbol_days[sym]["_daily"], date)
        morning = candles[:MORNING_CANDLES]
        o = morning[0]["open"]
        if o <= 0:
            continue
        hi = max(c["high"] for c in morning)
        lo = min(c["low"] for c in morning)
        close_n = morning[-1]["close"]
        ret = (close_n - o) / o * 100
        rng = (hi - lo) / o * 100
        if prior_close and prior_close > 0:
            gaps.append((o - prior_close) / prior_close * 100)
        mret.append(ret)
        mrange.append(rng)
        if ret > 0:
            ups += 1
        n += 1
    if n < 10:  # need a quorum of constituents to define "the market"
        return None
    market_ret = statistics.fmean(mret)
    market_range = statistics.fmean(mrange)
    dir_eff = abs(market_ret) / market_range if market_range > 0 else 0.0
    return {
        "date": date,
        "gap": statistics.fmean(gaps) if gaps else 0.0,
        "morning_ret": market_ret,
        "morning_range": market_range,
        "breadth": ups / n,
        "dir_efficiency": dir_eff,
        "n": n,
    }


def _prior_close(daily: list[dict], date: str) -> float | None:
    prior = None
    for d in daily:
        ds = d["ts"].date().isoformat()
        if ds >= date:
            break
        prior = d["close"]
    return prior


def label_regimes(per_symbol_days: dict) -> dict[str, str]:
    """Return {date: regime} for every date with a market quorum."""
    all_dates = set()
    for days in per_symbol_days.values():
        all_dates.update(d for d in days if not d.startswith("_"))

    feats = []
    for date in sorted(all_dates):
        f = _day_market_features(per_symbol_days, date)
        if f:
            feats.append(f)
    if not feats:
        return {}

    ranges = sorted(f["morning_range"] for f in feats)
    effs = sorted(f["dir_efficiency"] for f in feats)
    vol_hi = ranges[int(len(ranges) * 2 / 3)]          # top tercile cut
    eff_med = effs[len(effs) // 2]                      # median cut

    labels: dict[str, str] = {}
    for f in feats:
        if f["morning_range"] >= vol_hi:
            labels[f["date"]] = "VOLATILE"
        elif f["dir_efficiency"] >= eff_med:
            labels[f["date"]] = "TREND"
        else:
            labels[f["date"]] = "RANGE"
    return labels


# ── Phase 1.2: per-regime PF breakdown ────────────────────────

def _apply_daily_cap(all_trades: list[dict], cap: int) -> list[dict]:
    if cap <= 0 or not all_trades:
        return all_trades
    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in all_trades:
        by_day[t["entry_ts"][:10]].append(t)
    kept: list[dict] = []
    for day in sorted(by_day):
        kept.extend(sorted(by_day[day], key=lambda t: t["entry_ts"])[:cap])
    return sorted(kept, key=lambda t: t["entry_ts"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 1.1/1.2 regime analysis")
    ap.add_argument("--universe", default="NIFTY50")
    ap.add_argument("--window", choices=list(WINDOWS), default="FULL",
                    help="Date window for the PF breakdown")
    args = ap.parse_args()

    symbols = get_universe(args.universe)
    print(f"\n  Loading {len(symbols)} symbols...")
    per_symbol_days: dict[str, dict] = {}
    for sym in symbols:
        candles = load_15m(INTRADAY_DB, sym)
        daily = load_daily(DAILY_DB, sym)
        if candles:
            days = group_by_day(candles)
            days["_daily"] = daily  # stash daily under reserved key
            per_symbol_days[sym] = days
    print(f"  Loaded {len(per_symbol_days)} symbols.")

    # Phase 1.1 — label every day's regime (morning-only, whole sample).
    labels = label_regimes(per_symbol_days)
    dist = defaultdict(int)
    for r in labels.values():
        dist[r] += 1
    total_days = len(labels)
    print(f"\n  === PHASE 1.1: regime distribution ({total_days} days labeled) ===")
    for r in ("TREND", "RANGE", "VOLATILE"):
        c = dist[r]
        print(f"    {r:<10} {c:>4} days  ({c/total_days*100:4.1f}%)")

    # Phase 1.2 — run frozen config, tag trades by regime, break down PF.
    start, end = WINDOWS[args.window]
    all_trades: list[dict] = []
    for sym, days in per_symbol_days.items():
        sim_days = {d: v for d, v in days.items() if not d.startswith("_")}
        if start:
            sim_days = {d: v for d, v in sim_days.items() if d >= start}
        if end:
            sim_days = {d: v for d, v in sim_days.items() if d <= end}
        if not sim_days:
            continue
        trades = simulate_trades(sim_days, days["_daily"], sym,
                                 with_costs=True, **FROZEN)
        all_trades.extend(trades)
    all_trades = _apply_daily_cap(all_trades, PORTFOLIO_DAILY_CAP)

    by_regime: dict[str, list[dict]] = defaultdict(list)
    unlabeled = 0
    for t in all_trades:
        r = labels.get(t["entry_ts"][:10])
        if r is None:
            unlabeled += 1
            continue
        by_regime[r].append(t)

    print(f"\n  === PHASE 1.2: scorer PF by regime ({args.window} window, net of cost) ===")
    hdr = f"{'Regime':<12}{'Trades':>8}{'WR%':>7}{'PF':>7}{'Exp%':>9}{'Ret%':>9}{'Sharpe':>8}"
    print("  " + hdr)
    print("  " + "-" * len(hdr))

    overall = compute_metrics(all_trades, "ALL", with_costs=True)
    print(f"  {'ALL':<12}{overall['trades']:>8}{overall['win_rate']:>7}"
          f"{overall['pf']:>7}{overall['expectancy']:>9}"
          f"{overall['total_return']:>9}{overall['sharpe']:>8}")

    regime_metrics = {}
    for r in ("TREND", "RANGE", "VOLATILE"):
        trades = by_regime.get(r, [])
        if not trades:
            print(f"  {r:<12}{'(none)':>8}")
            continue
        m = compute_metrics(trades, r, with_costs=True)
        regime_metrics[r] = m
        print(f"  {r:<12}{m['trades']:>8}{m['win_rate']:>7}{m['pf']:>7}"
              f"{m['expectancy']:>9}{m['total_return']:>9}{m['sharpe']:>8}")
    if unlabeled:
        print(f"  (unlabeled trades skipped: {unlabeled})")

    # ── Routing scenarios: what each "skip" rule would deliver ──
    print(f"\n  === ROUTING SCENARIOS ({args.window} window, net of cost) ===")
    scenarios = {
        "Trade ALL regimes": ("TREND", "RANGE", "VOLATILE"),
        "Skip RANGE": ("TREND", "VOLATILE"),
        "VOLATILE only": ("VOLATILE",),
    }
    seen = set()
    print("  " + hdr)
    print("  " + "-" * len(hdr))
    for name, keep in scenarios.items():
        key = tuple(sorted(keep))
        if key in seen:
            continue
        seen.add(key)
        trades = [t for r in keep for t in by_regime.get(r, [])]
        trades.sort(key=lambda t: t["entry_ts"])
        if not trades:
            print(f"  {name:<24}{'(none)':>8}")
            continue
        m = compute_metrics(trades, name, with_costs=True)
        print(f"  {name:<24}{m['trades']:>8}{m['win_rate']:>7}{m['pf']:>7}"
              f"{m['expectancy']:>9}{m['total_return']:>9}{m['sharpe']:>8}")

    # Verdict
    print(f"\n  === PHASE 1.2 VERDICT ===")
    profitable = {r: m for r, m in regime_metrics.items() if m["pf"] >= 1.0}
    if profitable:
        best = max(profitable.items(), key=lambda kv: kv[1]["pf"])
        print(f"  Profitable regime(s): " +
              ", ".join(f"{r} (PF {m['pf']}, {m['trades']} trades)"
                        for r, m in profitable.items()))
        print(f"  => Regime ROUTING has potential. Best: {best[0]} "
              f"(PF {best[1]['pf']}).")
        print(f"  Next: backtest 'trade only in {'/'.join(profitable)}' OOS and "
              f"check it survives the {PORTFOLIO_DAILY_CAP}-trade cap + sample size.")
    else:
        worst_pf = max((m['pf'] for m in regime_metrics.values()), default=0)
        print(f"  No regime reaches PF >= 1.0 (best = {worst_pf}).")
        print(f"  => Regime routing ALONE will not create edge. The scorer is")
        print(f"     negative-expectancy in every regime — needs a different")
        print(f"     entry signal, not just better timing.")
    print()


if __name__ == "__main__":
    main()
