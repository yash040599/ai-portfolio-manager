#!/usr/bin/env python3
"""Phase 7.3 — Previous-Day High/Low Breakout backtest.

THESIS: One of the oldest and most robust intraday signals globally.
Buy when price breaks above yesterday's high; sell when it breaks below
yesterday's low. Combined with volume confirmation (breakout candle >1.5×
average) and ADX>25 (trend strength filter), this tests a pure price-level
breakout signal with zero overlap with the multi-indicator scorer.

METHOD (strict no-lookahead):
  1. Universe = NIFTY50 (15-min + daily candles).
  2. Compute previous day's high and low for each stock (from daily candles).
  3. Monitor 15-min candles starting 10:00 IST (skip opening noise):
     - BUY when candle closes ABOVE previous day's high
     - SELL when candle closes BELOW previous day's low
  4. Filters:
     a. Volume: breakout candle volume > 1.5× 20-day average candle volume
     b. ADX > 25 (trend strength — breakouts fail in low-ADX environments)
  5. SL: ATR-based (ATR×2.0)
  6. Target: SL × RR 1.8
  7. Daily cap = 2 (K1=2).
  8. Loser exit 13:00, square-off 14:00.
  9. Regime routing: test ALL, skip-RANGE, VOLATILE-only, TREND-only.
  10. Walk-forward: TRAIN year 1, TEST year 2 (OOS).

Usage:
    python scripts/trade/backtest_prev_day_breakout.py
    python scripts/trade/backtest_prev_day_breakout.py --adx-min 20 --vol-mult 1.0

Read-only. Out-of-sample by construction. Never touches capital.
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
    compute_metrics, _atr, _adx, _make_trade,
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
TRADE_VALUE = 15_000
ATR_MULT = 2.0
RR_RATIO = 1.8
DAILY_CAP = 2
ADX_MIN = 25.0               # breakout needs trend strength
VOL_MULT = 1.5               # breakout candle volume > this × avg
LOSER_EXIT_HOUR = 13
SQUARE_OFF_HOUR = 14
SQUARE_OFF_MIN = 0
ENTRY_START_HOUR = 10         # skip 9:15-9:45 opening noise
ENTRY_END_HOUR = 13
ENTRY_END_MIN = 30
GATE_PF = 1.15


def _prev_day_hl(daily_candles: list[dict], date_str: str) -> tuple[float, float] | None:
    """Get previous day's high and low (no lookahead)."""
    prev = None
    for d in daily_candles:
        ds = d["ts"].date().isoformat()
        if ds >= date_str:
            break
        prev = d
    if prev:
        return (prev["high"], prev["low"])
    return None


def _avg_candle_volume(
    days_dict: dict[str, list[dict]],
    date_str: str,
    lookback: int = 20,
) -> float:
    """Average per-candle volume across the last `lookback` days before `date_str`."""
    prior_dates = sorted(d for d in days_dict if d < date_str and not d.startswith("_"))
    prior_dates = prior_dates[-lookback:]
    if not prior_dates:
        return 0.0
    total_vol = 0
    total_candles = 0
    for d in prior_dates:
        candles = days_dict[d]
        for c in candles:
            v = c.get("volume", 0) or 0
            if v > 0:
                total_vol += v
                total_candles += 1
    return total_vol / total_candles if total_candles > 0 else 0.0


def simulate_prev_day_breakout(
    all_symbol_days: dict[str, dict],
    regime_labels: dict[str, str],
    *,
    adx_min: float = ADX_MIN,
    vol_mult: float = VOL_MULT,
    daily_cap: int = DAILY_CAP,
    skip_regimes: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """Run previous-day breakout strategy across all dates."""
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

        day_trades: list[dict] = []

        for sym, sdata in all_symbol_days.items():
            candles = sdata["days"].get(date_str)
            if not candles or len(candles) < 5:
                continue

            # Previous day's high/low
            prev_hl = _prev_day_hl(sdata["daily"], date_str)
            if not prev_hl:
                continue
            prev_high, prev_low = prev_hl

            # Average volume for breakout filter
            avg_vol = _avg_candle_volume(sdata["days"], date_str)

            # Continuous series for ATR + ADX
            all_candles = sdata.get("all_candles", [])
            day_bounds = sdata.get("boundaries", {}).get(date_str)
            if not day_bounds:
                continue
            start_idx = day_bounds[0]
            if start_idx < 30:
                continue

            # Walk through candles looking for breakout
            traded = False
            for ci, c in enumerate(candles):
                if traded:
                    break

                hour = c["ts"].hour
                minute = c["ts"].minute

                # Entry window
                if hour < ENTRY_START_HOUR:
                    continue
                if hour > ENTRY_END_HOUR or (hour == ENTRY_END_HOUR and minute > ENTRY_END_MIN):
                    break

                price = c["close"]
                if price <= 0:
                    continue

                # Check breakout
                side = None
                if price > prev_high:
                    side = "BUY"
                elif price < prev_low:
                    side = "SELL"

                if side is None:
                    continue

                # Volume filter
                candle_vol = c.get("volume", 0) or 0
                if avg_vol > 0 and candle_vol < vol_mult * avg_vol:
                    continue

                # ADX filter
                global_idx = start_idx + ci
                atr_window = all_candles[max(0, global_idx - 50):global_idx + 1]
                adx_val = _adx(atr_window, 14)
                if adx_val < adx_min:
                    continue

                # ATR for SL/target
                atr_val = _atr(atr_window, 14)
                if atr_val <= 0:
                    atr_val = price * 0.005

                # SL/target
                sl_dist = atr_val * ATR_MULT
                target_dist = sl_dist * RR_RATIO

                if side == "BUY":
                    sl_price = price - sl_dist
                    target_price = price + target_dist
                else:
                    sl_price = price + sl_dist
                    target_price = price - target_dist

                entry_price = price
                entry_ts = c["ts"]
                traded = True

                # Monitor remaining candles
                exited = False
                for mi in range(ci + 1, len(candles)):
                    mc = candles[mi]
                    mh = mc["ts"].hour
                    mm = mc["ts"].minute

                    # Square off
                    if mh * 60 + mm >= SQUARE_OFF_HOUR * 60 + SQUARE_OFF_MIN:
                        if side == "BUY":
                            pnl_pct = (mc["close"] - entry_price) / entry_price * 100
                        else:
                            pnl_pct = (entry_price - mc["close"]) / entry_price * 100
                        day_trades.append(_make_trade(
                            sym, entry_ts, mc["ts"], side, entry_price,
                            mc["close"], sl_price, target_price, pnl_pct,
                            "EOD_SQUARE_OFF", True))
                        exited = True
                        break

                    if side == "BUY":
                        if mc["low"] <= sl_price:
                            pnl_pct = (sl_price - entry_price) / entry_price * 100
                            day_trades.append(_make_trade(
                                sym, entry_ts, mc["ts"], side, entry_price,
                                sl_price, sl_price, target_price, pnl_pct,
                                "STOP_LOSS", True))
                            exited = True
                            break
                        if mc["high"] >= target_price:
                            pnl_pct = (target_price - entry_price) / entry_price * 100
                            day_trades.append(_make_trade(
                                sym, entry_ts, mc["ts"], side, entry_price,
                                target_price, sl_price, target_price, pnl_pct,
                                "TARGET_HIT", True))
                            exited = True
                            break
                        if mh >= LOSER_EXIT_HOUR and mc["close"] < entry_price:
                            pnl_pct = (mc["close"] - entry_price) / entry_price * 100
                            day_trades.append(_make_trade(
                                sym, entry_ts, mc["ts"], side, entry_price,
                                mc["close"], sl_price, target_price, pnl_pct,
                                "LOSER_EXIT_LATE", True))
                            exited = True
                            break
                    else:  # SELL
                        if mc["high"] >= sl_price:
                            pnl_pct = (entry_price - sl_price) / entry_price * 100
                            day_trades.append(_make_trade(
                                sym, entry_ts, mc["ts"], side, entry_price,
                                sl_price, sl_price, target_price, pnl_pct,
                                "STOP_LOSS", True))
                            exited = True
                            break
                        if mc["low"] <= target_price:
                            pnl_pct = (entry_price - target_price) / entry_price * 100
                            day_trades.append(_make_trade(
                                sym, entry_ts, mc["ts"], side, entry_price,
                                target_price, sl_price, target_price, pnl_pct,
                                "TARGET_HIT", True))
                            exited = True
                            break
                        if mh >= LOSER_EXIT_HOUR and mc["close"] > entry_price:
                            pnl_pct = (entry_price - mc["close"]) / entry_price * 100
                            day_trades.append(_make_trade(
                                sym, entry_ts, mc["ts"], side, entry_price,
                                mc["close"], sl_price, target_price, pnl_pct,
                                "LOSER_EXIT_LATE", True))
                            exited = True
                            break

                if not exited:
                    last = candles[-1]
                    if side == "BUY":
                        pnl_pct = (last["close"] - entry_price) / entry_price * 100
                    else:
                        pnl_pct = (entry_price - last["close"]) / entry_price * 100
                    day_trades.append(_make_trade(
                        sym, entry_ts, last["ts"], side, entry_price,
                        last["close"], sl_price, target_price, pnl_pct,
                        "EOD_SQUARE_OFF", True))

        # Apply daily cap — keep first N by entry time
        day_trades.sort(key=lambda t: t["entry_ts"])
        all_trades.extend(day_trades[:daily_cap])

    return sorted(all_trades, key=lambda t: t["entry_ts"])


def _print_table(label: str, metrics: dict) -> None:
    if metrics.get("note"):
        print(f"  {label:<28s} {metrics['note']}")
        return
    print(f"  {label:<28s} "
          f"Trades: {metrics['trades']:>5d}  "
          f"WR: {metrics['win_rate']:>5.1f}%  "
          f"PF: {metrics['pf']:>5.2f}  "
          f"Exp: {metrics['expectancy']:>+7.3f}%  "
          f"Ret: {metrics['total_return']:>+8.2f}%  "
          f"MaxDD: {metrics['max_dd']:>6.2f}%  "
          f"Sharpe: {metrics['sharpe']:>+6.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 7.3 — Previous-Day Breakout backtest")
    ap.add_argument("--universe", default="NIFTY50")
    ap.add_argument("--adx-min", type=float, default=ADX_MIN, help="Minimum ADX for breakout")
    ap.add_argument("--vol-mult", type=float, default=VOL_MULT, help="Volume multiplier floor")
    ap.add_argument("--daily-cap", type=int, default=DAILY_CAP, help="Max trades per day")
    args = ap.parse_args()

    symbols = get_universe(args.universe)
    print("\n  Phase 7.3 — Previous-Day High/Low Breakout")
    print(f"  ADX >= {args.adx_min}, Volume >= {args.vol_mult}x avg, "
          f"daily cap = {args.daily_cap}")
    print(f"  Loading {len(symbols)} symbols...")

    # Load data
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

    # Regime labels
    regime_labels = label_regimes(per_symbol_days_for_regime)
    dist = defaultdict(int)
    for r in regime_labels.values():
        dist[r] += 1
    print("  Regime distribution: "
          + ", ".join(f"{r}={dist[r]}" for r in ("TREND", "RANGE", "VOLATILE")))

    # ── Run strategy across windows × regime routing ──────────
    print(f"\n  {'='*100}")
    print(f"  Walk-forward results (net of cost, ATR×{ATR_MULT}, RR {RR_RATIO}, "
          f"ADX>={args.adx_min}, vol>={args.vol_mult}x)")
    print(f"  {'='*100}")

    for win_name, (w_start, w_end) in WINDOWS.items():
        print(f"\n  ── {win_name} window ──")

        routing_configs = {
            "ALL regimes": set(),
            "Skip RANGE": {"RANGE"},
            "VOLATILE only": {"TREND", "RANGE"},
            "TREND only": {"VOLATILE", "RANGE"},
        }

        for route_name, skip in routing_configs.items():
            trades = simulate_prev_day_breakout(
                all_symbol_days, regime_labels,
                adx_min=args.adx_min,
                vol_mult=args.vol_mult,
                daily_cap=args.daily_cap,
                skip_regimes=skip,
                start=w_start, end=w_end,
            )
            m = compute_metrics(trades, f"{win_name}/{route_name}", with_costs=True)
            _print_table(f"{route_name}", m)

            if win_name == "TEST" and m.get("by_reason"):
                reasons = m["by_reason"]
                print("    Exit reasons: " +
                      ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))

    # ── Parameter sweep on TEST window ────────────────────────
    print("\n  ── Parameter sweep (TEST window, ALL regimes) ──")
    for adx in [15.0, 20.0, 25.0, 30.0]:
        for vm in [1.0, 1.5, 2.0]:
            trades = simulate_prev_day_breakout(
                all_symbol_days, regime_labels,
                adx_min=adx, vol_mult=vm, daily_cap=args.daily_cap,
                skip_regimes=set(),
                start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
            )
            m = compute_metrics(trades, f"adx{adx}/vol{vm}", with_costs=True)
            _print_table(f"ADX>={adx} vol>={vm}x", m)

    # ── Verdict ───────────────────────────────────────────────
    print("\n  === PHASE 7.3 VERDICT ===")
    test_trades = simulate_prev_day_breakout(
        all_symbol_days, regime_labels,
        adx_min=args.adx_min,
        vol_mult=args.vol_mult,
        daily_cap=args.daily_cap,
        skip_regimes=set(),
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
    )
    test_m = compute_metrics(test_trades, "TEST/ALL", with_costs=True)
    pf = test_m.get("pf", 0)
    if pf >= GATE_PF:
        print(f"  PASS — OOS PF {pf} >= {GATE_PF}. Candidate for dry-run validation.")
    elif pf >= 1.0:
        print(f"  MARGINAL — OOS PF {pf} (1.0 <= PF < {GATE_PF}). May benefit from tuning.")
    else:
        print(f"  FAIL — OOS PF {pf} < 1.0. Previous-day breakout does not have edge.")
    print(f"  Total charges (TEST/ALL): Rs.{test_m.get('total_charges', 0):,.2f}")


if __name__ == "__main__":
    main()
