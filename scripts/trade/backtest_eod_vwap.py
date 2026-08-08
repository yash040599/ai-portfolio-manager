#!/usr/bin/env python3
"""D.4 — End-of-Day VWAP Reversion backtest.

THESIS: Institutional VWAP-targeting algos push prices toward VWAP in the
last 30-45 minutes. Stocks trading far from VWAP at 2:30-2:45 PM tend to
revert toward VWAP by close. Very short hold (30 min), different timing
from Gap-and-Go (morning) — can run as an afternoon complement.

METHOD (strict no-lookahead):
  1. Universe = NIFTY100 (15-min candles).
  2. At 14:30 or 14:45 IST, compute intraday VWAP and current price.
  3. If price is >1.5σ from VWAP (σ = intraday std dev of TP from VWAP):
     - Price > VWAP + 1.5σ → SELL (expect reversion down)
     - Price < VWAP - 1.5σ → BUY (expect reversion up)
  4. Target: VWAP price.
  5. SL: 2× distance from entry to VWAP (i.e., price moves further away).
  6. Square-off: 15:15 IST (market close).
  7. Walk-forward: TRAIN year 1, TEST year 2 (OOS).

Usage:
    python scripts/trade/backtest_eod_vwap.py

Read-only. Out-of-sample by construction. Never touches capital.
"""
from __future__ import annotations

import argparse
import math
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

# ── Walk-forward windows ──────────────────────────────────────
WINDOWS = {
    "FULL": (None, None),
    "TRAIN": ("2024-05-27", "2025-05-31"),
    "TEST": ("2025-06-01", "2026-05-22"),
}

# ── Strategy params ───────────────────────────────────────────
ENTRY_HOUR = 14
ENTRY_MIN = 30               # Enter at 14:30 IST
SIGMA_THRESH = 1.5           # minimum distance from VWAP in σ
SL_MULT = 2.0                # SL = SL_MULT × distance-to-VWAP beyond entry
DAILY_CAP = 2
SQUARE_OFF_HOUR = 15
SQUARE_OFF_MIN = 10           # 15:10 market close
GATE_PF = 1.15


def _compute_vwap_and_sigma(candles: list[dict]) -> tuple[float, float]:
    """Compute intraday VWAP and std dev of typical price from VWAP."""
    if not candles:
        return 0.0, 0.0
    cum_tp_vol, cum_vol = 0.0, 0
    tps = []
    for c in candles:
        tp = (c["high"] + c["low"] + c["close"]) / 3
        vol = c.get("volume", 0) or 1
        cum_tp_vol += tp * vol
        cum_vol += vol
        tps.append(tp)
    if cum_vol == 0:
        return 0.0, 0.0
    vwap = cum_tp_vol / cum_vol
    if len(tps) < 3:
        return vwap, 0.0
    # Standard deviation of TP from VWAP
    sq_diffs = [(tp - vwap) ** 2 for tp in tps]
    sigma = math.sqrt(sum(sq_diffs) / len(sq_diffs))
    return vwap, sigma


def simulate_eod_vwap(
    all_symbol_days: dict[str, dict],
    regime_labels: dict[str, str],
    *,
    sigma_thresh: float = SIGMA_THRESH,
    sl_mult: float = SL_MULT,
    daily_cap: int = DAILY_CAP,
    entry_hour: int = ENTRY_HOUR,
    entry_min: int = ENTRY_MIN,
    skip_regimes: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """Run EOD VWAP reversion strategy."""
    if skip_regimes is None:
        skip_regimes = set()

    all_dates: set[str] = set()
    for sdata in all_symbol_days.values():
        for d in sdata["days"]:
            all_dates.add(d)

    all_trades: list[dict] = []

    for date_str in sorted(all_dates):
        if start and date_str < start:
            continue
        if end and date_str > end:
            continue

        regime = regime_labels.get(date_str)
        if regime and regime in skip_regimes:
            continue

        candidates: list[tuple[str, str, float, float, float, list[dict], int]] = []

        for sym, sdata in all_symbol_days.items():
            candles = sdata["days"].get(date_str)
            if not candles or len(candles) < 10:
                continue

            # Find the entry candle (the candle at or just after entry_hour:entry_min)
            entry_candle_idx = None
            for i, c in enumerate(candles):
                h, m = c["ts"].hour, c["ts"].minute
                if h * 60 + m >= entry_hour * 60 + entry_min:
                    entry_candle_idx = i
                    break

            if entry_candle_idx is None or entry_candle_idx < 5:
                continue

            # Compute VWAP up to (but not including) entry candle — no lookahead
            pre_entry_candles = candles[:entry_candle_idx]
            vwap, sigma = _compute_vwap_and_sigma(pre_entry_candles)

            if vwap <= 0 or sigma <= 0:
                continue

            entry_candle = candles[entry_candle_idx]
            current_price = entry_candle["close"]

            # Distance from VWAP in sigma units
            dist_sigma = (current_price - vwap) / sigma

            if abs(dist_sigma) < sigma_thresh:
                continue

            # Determine side
            if dist_sigma > sigma_thresh:
                side = "SELL"  # overextended above VWAP → fade down
            else:
                side = "BUY"  # overextended below VWAP → fade up

            candidates.append((
                sym, side, abs(dist_sigma), vwap, sigma,
                candles, entry_candle_idx,
            ))

        if not candidates:
            continue

        # Sort by distance from VWAP (most extended first)
        candidates.sort(key=lambda x: x[2], reverse=True)
        selected = candidates[:daily_cap]

        for sym, side, _dist_s, vwap, _sigma, candles, entry_ci in selected:
            entry_candle = candles[entry_ci]
            entry_price = entry_candle["close"]
            if entry_price <= 0:
                continue

            # Target = VWAP
            target_price = vwap

            # SL = beyond entry by sl_mult × distance-to-VWAP
            dist_to_vwap = abs(entry_price - vwap)
            if side == "SELL":
                sl_price = entry_price + dist_to_vwap * sl_mult
                if target_price >= entry_price:
                    continue
            else:
                sl_price = entry_price - dist_to_vwap * sl_mult
                if target_price <= entry_price:
                    continue

            entry_ts = entry_candle["ts"]
            exited = False

            for ci in range(entry_ci + 1, len(candles)):
                c = candles[ci]
                hour = c["ts"].hour
                minute = c["ts"].minute

                # Square off at close
                if hour * 60 + minute >= SQUARE_OFF_HOUR * 60 + SQUARE_OFF_MIN:
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
                else:  # SELL
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
        print(f"  {label:<32s} {metrics['note']}")
        return
    print(f"  {label:<32s} "
          f"Trades: {metrics['trades']:>5d}  "
          f"WR: {metrics['win_rate']:>5.1f}%  "
          f"PF: {metrics['pf']:>5.2f}  "
          f"Exp: {metrics['expectancy']:>+7.3f}%  "
          f"Ret: {metrics['total_return']:>+8.2f}%  "
          f"MaxDD: {metrics['max_dd']:>6.2f}%  "
          f"Sharpe: {metrics['sharpe']:>+6.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="D.4 — EOD VWAP Reversion backtest")
    ap.add_argument("--universe", default="NIFTY100")
    ap.add_argument("--sigma", type=float, default=SIGMA_THRESH)
    ap.add_argument("--daily-cap", type=int, default=DAILY_CAP)
    ap.add_argument("--sl-mult", type=float, default=SL_MULT)
    args = ap.parse_args()

    symbols = get_universe(args.universe)
    print("\n  D.4 — End-of-Day VWAP Reversion")
    print(f"  Entry at 14:30, Target=VWAP, σ threshold={args.sigma}, "
          f"SL={args.sl_mult}x dist, cap={args.daily_cap}")
    print(f"  Loading {len(symbols)} symbols from {args.universe}...")

    all_symbol_days: dict[str, dict] = {}
    per_symbol_days_for_regime: dict[str, dict] = {}

    for sym in symbols:
        candles = load_15m(INTRADAY_DB, sym)
        daily = load_daily(DAILY_DB, sym)
        if not candles:
            continue
        days = group_by_day(candles)

        all_candles: list[dict] = []
        boundaries: dict[str, tuple[int, int]] = {}
        for d in sorted(days.keys()):
            si = len(all_candles)
            all_candles.extend(days[d])
            boundaries[d] = (si, len(all_candles))

        all_symbol_days[sym] = {
            "days": days,
            "daily": daily,
            "all_candles": all_candles,
            "boundaries": boundaries,
        }
        regime_days = dict(days)
        regime_days["_daily"] = daily
        per_symbol_days_for_regime[sym] = regime_days

    print(f"  Loaded {len(all_symbol_days)} symbols.")

    regime_labels = label_regimes(per_symbol_days_for_regime)
    dist = defaultdict(int)
    for r in regime_labels.values():
        dist[r] += 1
    print("  Regime distribution: "
          + ", ".join(f"{r}={dist[r]}" for r in ("TREND", "RANGE", "VOLATILE")))

    # ── Run strategy ──────────────────────────────────────────
    print(f"\n  {'='*100}")
    print("  Walk-forward results (net of cost)")
    print(f"  {'='*100}")

    for win_name, (w_start, w_end) in WINDOWS.items():
        print(f"\n  ── {win_name} window ──")

        routing_configs = {
            "ALL regimes": set(),
            "Skip RANGE": {"RANGE"},
            "VOLATILE only": {"TREND", "RANGE"},
            "RANGE only": {"TREND", "VOLATILE"},
        }

        for route_name, skip in routing_configs.items():
            trades = simulate_eod_vwap(
                all_symbol_days, regime_labels,
                sigma_thresh=args.sigma,
                sl_mult=args.sl_mult,
                daily_cap=args.daily_cap,
                skip_regimes=skip,
                start=w_start, end=w_end,
            )
            m = compute_metrics(trades, f"{win_name}/{route_name}", with_costs=True)
            _print_table(f"{route_name}", m)

    # ── Sigma threshold sweep ────────────────────────────────
    print("\n  ── Sigma threshold sweep (TEST, ALL) ──")
    for sig in [1.0, 1.2, 1.5, 2.0, 2.5, 3.0]:
        trades = simulate_eod_vwap(
            all_symbol_days, regime_labels,
            sigma_thresh=sig, sl_mult=args.sl_mult,
            daily_cap=args.daily_cap,
            skip_regimes=set(),
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        )
        m = compute_metrics(trades, f"sigma-{sig}", with_costs=True)
        _print_table(f"σ >= {sig}", m)

    # ── Entry time sweep ─────────────────────────────────────
    print(f"\n  ── Entry time sweep (TEST, ALL, σ={args.sigma}) ──")
    for eh, em in [(13, 30), (14, 0), (14, 15), (14, 30), (14, 45)]:
        trades = simulate_eod_vwap(
            all_symbol_days, regime_labels,
            sigma_thresh=args.sigma, sl_mult=args.sl_mult,
            daily_cap=args.daily_cap,
            entry_hour=eh, entry_min=em,
            skip_regimes=set(),
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        )
        m = compute_metrics(trades, f"entry-{eh}:{em:02d}", with_costs=True)
        _print_table(f"entry {eh}:{em:02d}", m)

    # ── Verdict ───────────────────────────────────────────────
    print("\n  === D.4 EOD VWAP REVERSION VERDICT ===")
    test_trades = simulate_eod_vwap(
        all_symbol_days, regime_labels,
        sigma_thresh=args.sigma,
        sl_mult=args.sl_mult,
        daily_cap=args.daily_cap,
        skip_regimes=set(),
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
    )
    test_m = compute_metrics(test_trades, "TEST/ALL", with_costs=True)
    pf = test_m.get("pf", 0)
    if pf >= GATE_PF:
        print(f"  PASS — OOS PF {pf:.2f} >= {GATE_PF}. Candidate for dry-run.")
    elif pf >= 1.0:
        print(f"  MARGINAL — OOS PF {pf:.2f}. Above breakeven but below {GATE_PF} gate.")
    else:
        print(f"  FAIL — OOS PF {pf:.2f} < 1.0. Negative expectancy.")


if __name__ == "__main__":
    main()
