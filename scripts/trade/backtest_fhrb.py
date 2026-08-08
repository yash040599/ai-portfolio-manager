#!/usr/bin/env python3
"""Phase 9.1 — First Hour Range Breakout (FHRB) backtest.

THESIS: ORB-15 was PF 0.97 — the closest any intraday strategy came to
breakeven before Gap-and-Go. FHRB uses a WIDER range (first 1 hour,
9:15-10:15 = 4x 15-min candles) which filters more noise and gives
a more reliable support/resistance level. Entry on breakout of the
1-hour high/low with volume confirmation.

METHOD (strict no-lookahead):
  1. Universe = NIFTY100 (15-min + daily candles).
  2. Compute first-hour range: high/low of candles 0-3 (9:15-10:15).
  3. Wait for breakout after 10:15:
     - BUY: price breaks above first-hour high
     - SELL: price breaks below first-hour low
  4. Volume filter: first-hour volume > vol_mult × 20-day avg first-hour vol.
  5. ADX filter (optional): ADX > 25 (trending day, not range).
  6. SL: opposite side of first-hour range.
  7. Target: SL distance × RR ratio (1.5-2.0).
  8. Daily cap = 2.
  9. Square off at 14:00 (or configurable).
  10. Walk-forward: TRAIN year 1, TEST year 2 (OOS).

Usage:
    python scripts/trade/backtest_fhrb.py
    python scripts/trade/backtest_fhrb.py --universe NIFTY50
    python scripts/trade/backtest_fhrb.py --vol-mult 1.5 --rr 2.0

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
    compute_metrics, _adx, _make_trade,
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
FIRST_HOUR_CANDLES = 4       # candles 0-3 = 9:15-10:15 (4 × 15-min)
VOL_MULT = 1.5               # first-hour vol > this × 20-day avg
ADX_MIN = 0                  # 0 = disabled; 25 = trending filter
RR_RATIO = 1.5               # Target = SL × this
DAILY_CAP = 2
LOSER_EXIT_HOUR = 13
SQUARE_OFF_HOUR = 14
SQUARE_OFF_MIN = 0
VOL_LOOKBACK = 20
GATE_PF = 1.15
MIN_RANGE_PCT = 0.3          # minimum first-hour range as % of price
MAX_RANGE_PCT = 3.0           # reject very wide ranges (choppy/news)


def _avg_first_hour_volume(
    days_dict: dict[str, list[dict]],
    date_str: str,
    n_candles: int = FIRST_HOUR_CANDLES,
    lookback: int = VOL_LOOKBACK,
) -> float:
    """Average total volume of first-hour candles across last `lookback` days."""
    prior_dates = sorted(d for d in days_dict if d < date_str and not d.startswith("_"))
    prior_dates = prior_dates[-lookback:]
    if not prior_dates:
        return 0.0
    vols = []
    for d in prior_dates:
        candles = days_dict[d]
        if len(candles) >= n_candles:
            v = sum((c.get("volume", 0) or 0) for c in candles[:n_candles])
            if v > 0:
                vols.append(v)
    return sum(vols) / len(vols) if vols else 0.0


def simulate_fhrb(
    all_symbol_days: dict[str, dict],
    regime_labels: dict[str, str],
    *,
    vol_mult: float = VOL_MULT,
    adx_min: float = ADX_MIN,
    rr_ratio: float = RR_RATIO,
    daily_cap: int = DAILY_CAP,
    min_range_pct: float = MIN_RANGE_PCT,
    max_range_pct: float = MAX_RANGE_PCT,
    sq_off_hour: int = SQUARE_OFF_HOUR,
    sq_off_min: int = SQUARE_OFF_MIN,
    skip_regimes: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """Run FHRB strategy across all dates."""
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

        candidates: list[tuple[str, str, float, float, float, list[dict], float]] = []

        for sym, sdata in all_symbol_days.items():
            candles = sdata["days"].get(date_str)
            if not candles or len(candles) < FIRST_HOUR_CANDLES + 2:
                continue

            # First hour range (candles 0 to FIRST_HOUR_CANDLES-1)
            fh_candles = candles[:FIRST_HOUR_CANDLES]
            fh_high = max(c["high"] for c in fh_candles)
            fh_low = min(c["low"] for c in fh_candles)
            fh_range = fh_high - fh_low
            fh_mid = (fh_high + fh_low) / 2

            if fh_mid <= 0:
                continue

            range_pct = fh_range / fh_mid * 100
            if range_pct < min_range_pct or range_pct > max_range_pct:
                continue

            # Volume filter
            fh_vol = sum((c.get("volume", 0) or 0) for c in fh_candles)
            avg_vol = _avg_first_hour_volume(sdata["days"], date_str)
            if avg_vol <= 0 or fh_vol < vol_mult * avg_vol:
                continue

            # ADX filter (optional)
            if adx_min > 0:
                all_candles = sdata.get("all_candles", [])
                day_start = sdata.get("boundaries", {}).get(date_str)
                if not day_start:
                    continue
                si = day_start[0]
                if si < 28:
                    continue
                adx_window = all_candles[max(0, si - 50):si + FIRST_HOUR_CANDLES]
                adx_val = _adx(adx_window, 14)
                if adx_val < adx_min:
                    continue

            # Scan post-first-hour candles for breakout
            for ci in range(FIRST_HOUR_CANDLES, len(candles)):
                c = candles[ci]
                hour = c["ts"].hour
                minute = c["ts"].minute

                # Don't enter too late
                if hour * 60 + minute >= LOSER_EXIT_HOUR * 60:
                    break

                if c["high"] > fh_high:
                    # BUY breakout
                    candidates.append((
                        sym, "BUY", range_pct, fh_high, fh_low,
                        candles, ci,
                    ))
                    break
                elif c["low"] < fh_low:
                    # SELL breakout
                    candidates.append((
                        sym, "SELL", range_pct, fh_high, fh_low,
                        candles, ci,
                    ))
                    break

        if not candidates:
            continue

        # Sort by range % (tighter ranges = stronger conviction) ascending
        # Actually, wider range breakouts may be stronger — let's sort by
        # range descending for now (strongest move)
        candidates.sort(key=lambda x: x[2], reverse=True)
        selected = candidates[:daily_cap]

        for sym, side, _range_pct_val, fh_high, fh_low, candles, breakout_ci in selected:
            breakout_candle = candles[breakout_ci]

            if side == "BUY":
                entry_price = fh_high  # enter at breakout level
                sl_price = fh_low      # SL at opposite side of range
                sl_dist = entry_price - sl_price
                target_price = entry_price + sl_dist * rr_ratio
            else:
                entry_price = fh_low
                sl_price = fh_high
                sl_dist = sl_price - entry_price
                target_price = entry_price - sl_dist * rr_ratio

            if sl_dist <= 0:
                continue

            entry_ts = breakout_candle["ts"]
            exited = False

            for ci in range(breakout_ci + 1, len(candles)):
                c = candles[ci]
                hour = c["ts"].hour
                minute = c["ts"].minute

                # Square off
                if hour * 60 + minute >= sq_off_hour * 60 + sq_off_min:
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
        print(f"  {label:<36s} {metrics['note']}")
        return
    print(f"  {label:<36s} "
          f"Trades: {metrics['trades']:>5d}  "
          f"WR: {metrics['win_rate']:>5.1f}%  "
          f"PF: {metrics['pf']:>5.2f}  "
          f"Exp: {metrics['expectancy']:>+7.3f}%  "
          f"Ret: {metrics['total_return']:>+8.2f}%  "
          f"MaxDD: {metrics['max_dd']:>6.2f}%  "
          f"Sharpe: {metrics['sharpe']:>+6.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 9.1 — First Hour Range Breakout backtest")
    ap.add_argument("--universe", default="NIFTY100")
    ap.add_argument("--vol-mult", type=float, default=VOL_MULT)
    ap.add_argument("--adx-min", type=float, default=ADX_MIN)
    ap.add_argument("--rr", type=float, default=RR_RATIO)
    ap.add_argument("--daily-cap", type=int, default=DAILY_CAP)
    ap.add_argument("--sq-off", type=int, default=SQUARE_OFF_HOUR)
    ap.add_argument("--min-range", type=float, default=MIN_RANGE_PCT)
    ap.add_argument("--max-range", type=float, default=MAX_RANGE_PCT)
    args = ap.parse_args()

    symbols = get_universe(args.universe)
    print("\n  Phase 9.1 — First Hour Range Breakout (FHRB)")
    print(f"  Vol >= {args.vol_mult}x 20-day avg, RR = {args.rr}, "
          f"daily cap = {args.daily_cap}, sq-off = {args.sq_off}:00")
    print(f"  Range filter: {args.min_range}% - {args.max_range}%")
    if args.adx_min > 0:
        print(f"  ADX filter: >= {args.adx_min}")
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

    regime_labels = label_regimes(per_symbol_days_for_regime)
    dist = defaultdict(int)
    for r in regime_labels.values():
        dist[r] += 1
    print("  Regime distribution: "
          + ", ".join(f"{r}={dist[r]}" for r in ("TREND", "RANGE", "VOLATILE")))

    # ── Walk-forward results ──────────────────────────────────
    print(f"\n  {'='*110}")
    print(f"  Walk-forward results (net of cost, vol>={args.vol_mult}x, "
          f"RR={args.rr}, cap={args.daily_cap})")
    print(f"  {'='*110}")

    for win_name, (w_start, w_end) in WINDOWS.items():
        print(f"\n  ── {win_name} window ──")

        routing_configs = {
            "ALL regimes": set(),
            "Skip RANGE": {"RANGE"},
            "VOLATILE only": {"TREND", "RANGE"},
        }

        for route_name, skip in routing_configs.items():
            trades = simulate_fhrb(
                all_symbol_days, regime_labels,
                vol_mult=args.vol_mult,
                adx_min=args.adx_min,
                rr_ratio=args.rr,
                daily_cap=args.daily_cap,
                min_range_pct=args.min_range,
                max_range_pct=args.max_range,
                skip_regimes=skip,
                start=w_start, end=w_end,
            )
            m = compute_metrics(trades, f"{win_name}/{route_name}", with_costs=True)
            _print_table(f"{route_name}", m)

            if win_name == "TEST" and m.get("by_reason"):
                reasons = m["by_reason"]
                print("    Exit reasons: " +
                      ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))

    # ── Parameter sweep ───────────────────────────────────────
    print("\n  ── Parameter sweep (TEST window, ALL regimes) ──")
    for vm in [1.0, 1.5, 2.0, 3.0]:
        for rr in [1.2, 1.5, 1.8, 2.0]:
            trades = simulate_fhrb(
                all_symbol_days, regime_labels,
                vol_mult=vm, rr_ratio=rr,
                daily_cap=args.daily_cap,
                min_range_pct=args.min_range,
                max_range_pct=args.max_range,
                skip_regimes=set(),
                start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
            )
            m = compute_metrics(trades, f"vol{vm}/rr{rr}", with_costs=True)
            _print_table(f"vol>={vm}x RR={rr}", m)

    # ── ADX filter sweep ──────────────────────────────────────
    print(f"\n  ── ADX filter sweep (TEST window, ALL regimes, vol>={args.vol_mult}x, RR={args.rr}) ──")
    for adx in [0, 20, 25, 30]:
        trades = simulate_fhrb(
            all_symbol_days, regime_labels,
            vol_mult=args.vol_mult, rr_ratio=args.rr,
            daily_cap=args.daily_cap, adx_min=float(adx),
            min_range_pct=args.min_range,
            max_range_pct=args.max_range,
            skip_regimes=set(),
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        )
        label = f"ADX>={adx}" if adx > 0 else "No ADX filter"
        _print_table(label, compute_metrics(trades, f"ADX-{adx}", True))

    # ── Square-off time sweep ─────────────────────────────────
    print("\n  ── Square-off time sweep (TEST window, ALL regimes) ──")
    for sq in [13, 14, 15]:
        trades = simulate_fhrb(
            all_symbol_days, regime_labels,
            vol_mult=args.vol_mult, rr_ratio=args.rr,
            daily_cap=args.daily_cap,
            min_range_pct=args.min_range,
            max_range_pct=args.max_range,
            sq_off_hour=sq,
            skip_regimes=set(),
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        )
        _print_table(f"Sq-off {sq}:00", compute_metrics(trades, f"sq{sq}", True))

    # ── Verdict ───────────────────────────────────────────────
    print("\n  === PHASE 9.1 VERDICT ===")
    test_trades = simulate_fhrb(
        all_symbol_days, regime_labels,
        vol_mult=args.vol_mult, rr_ratio=args.rr,
        daily_cap=args.daily_cap,
        min_range_pct=args.min_range,
        max_range_pct=args.max_range,
        sq_off_hour=args.sq_off,
        skip_regimes=set(),
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
    )
    test_m = compute_metrics(test_trades, "TEST/ALL", with_costs=True)
    pf = test_m.get("pf", 0)
    if pf >= GATE_PF:
        print(f"  PASS — OOS PF {pf} >= {GATE_PF}. Candidate for dry-run.")
    elif pf >= 1.0:
        print(f"  MARGINAL — OOS PF {pf} >= 1.0 but < {GATE_PF}. Not ready for dry-run.")
    else:
        print(f"  FAIL — OOS PF {pf} < 1.0. Strategy is not profitable after costs.")


if __name__ == "__main__":
    main()
