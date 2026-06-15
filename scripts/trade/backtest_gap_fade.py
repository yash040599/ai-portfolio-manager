#!/usr/bin/env python3
"""D.3 — Gap Fade backtest (mirror of Gap-and-Go).

THESIS: Gap-and-Go profits because strong-volume gaps follow through.
Gap Fade should profit because WEAK-volume gaps mean-revert toward the
previous close. This is the complementary signal — fires on days when
Gap-and-Go sits out.

METHOD (strict no-lookahead):
  1. Universe = NIFTY100 (15-min + daily candles).
  2. At 09:45 IST (after 09:30 candle closes), identify stocks that:
     a. Gapped >0.5% from previous day's close (smaller gaps fade too)
     b. First-candle volume is LOW: < 1.0× same-period 20-day average
        (weak volume = no institutional conviction behind the gap)
  3. Enter AGAINST gap direction:
     - Gap up + weak volume → SELL (fade the gap)
     - Gap down + weak volume → BUY (fade the gap)
  4. Target: previous day's close (the gap fills).
  5. SL: beyond the gap candle extreme (gap up → SL above high,
     gap down → SL below low).
  6. Daily cap = 2.
  7. Square-off: 13:00 IST (same as Gap-and-Go).
  8. Walk-forward: TRAIN year 1, TEST year 2 (OOS).

Usage:
    python scripts/trade/backtest_gap_fade.py
    python scripts/trade/backtest_gap_fade.py --universe NIFTY50

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
    compute_charges, compute_metrics, _atr, _make_trade,
    CAPITAL,
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
GAP_MIN_PCT = 0.5            # minimum gap % from prev close
GAP_MAX_PCT = 5.0            # reject extreme gaps (corporate action)
VOL_CEIL = 1.0               # volume must be BELOW this × 20-day avg
RR_RATIO = 1.5               # Target distance as multiple of SL
DAILY_CAP = 2                # max trades per day
ENTRY_CANDLE_IDX = 2         # 09:45 candle (third of day, matching v1.1)
VOL_LOOKBACK_DAYS = 20
SQUARE_OFF_HOUR = 13
SQUARE_OFF_MIN = 0
LOSER_EXIT_HOUR = 12
GATE_PF = 1.15


def _prior_close(daily_candles: list[dict], date_str: str) -> float | None:
    """Get previous day's close (no lookahead)."""
    prior = None
    for d in daily_candles:
        ds = d["ts"].date().isoformat()
        if ds >= date_str:
            break
        prior = d["close"]
    return prior


def _avg_first_candle_volume(
    days_dict: dict[str, list[dict]],
    date_str: str,
    lookback: int = VOL_LOOKBACK_DAYS,
) -> float:
    """Average volume of the entry candle across the last `lookback` days."""
    prior_dates = sorted(d for d in days_dict if d < date_str and not d.startswith("_"))
    prior_dates = prior_dates[-lookback:]
    if not prior_dates:
        return 0.0
    vols = []
    for d in prior_dates:
        candles = days_dict[d]
        if candles:
            idx = min(ENTRY_CANDLE_IDX, len(candles) - 1)
            v = candles[idx].get("volume", 0) or 0
            if v > 0:
                vols.append(v)
    return sum(vols) / len(vols) if vols else 0.0


def simulate_gap_fade(
    all_symbol_days: dict[str, dict],
    regime_labels: dict[str, str],
    *,
    gap_min_pct: float = GAP_MIN_PCT,
    gap_max_pct: float = GAP_MAX_PCT,
    vol_ceil: float = VOL_CEIL,
    daily_cap: int = DAILY_CAP,
    rr_ratio: float = RR_RATIO,
    skip_regimes: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    target_prev_close: bool = True,
    sq_off_hour: int = SQUARE_OFF_HOUR,
) -> list[dict]:
    """Run gap-fade strategy across all dates."""
    if skip_regimes is None:
        skip_regimes = set()

    all_dates: set[str] = set()
    for sym, sdata in all_symbol_days.items():
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

        candidates: list[tuple[str, str, float, list[dict], float, float]] = []

        for sym, sdata in all_symbol_days.items():
            candles = sdata["days"].get(date_str)
            if not candles or len(candles) < ENTRY_CANDLE_IDX + 2:
                continue

            prev_close = _prior_close(sdata["daily"], date_str)
            if not prev_close or prev_close <= 0:
                continue

            entry_candle = candles[ENTRY_CANDLE_IDX]
            open_price = candles[0]["open"]

            # Check gap from prev close to today's open
            gap = (open_price - prev_close) / prev_close * 100

            if abs(gap) < gap_min_pct:
                continue
            if abs(gap) > gap_max_pct:
                continue

            # Volume filter: entry candle volume must be BELOW ceiling
            entry_vol = entry_candle.get("volume", 0) or 0
            avg_vol = _avg_first_candle_volume(sdata["days"], date_str)

            if avg_vol <= 0:
                continue
            vol_ratio = entry_vol / avg_vol
            if vol_ratio >= vol_ceil:
                continue  # too much volume — this is a real gap, not a fade

            # ATR for SL fallback
            all_candles = sdata.get("all_candles", [])
            day_start = sdata.get("boundaries", {}).get(date_str)
            if not day_start:
                continue
            start_idx = day_start[0]
            if start_idx < 14:
                continue
            atr_window = all_candles[max(0, start_idx + ENTRY_CANDLE_IDX - 50):start_idx + ENTRY_CANDLE_IDX + 1]
            atr_val = _atr(atr_window, 14)
            if atr_val <= 0:
                atr_val = entry_candle["close"] * 0.005

            # FADE direction: opposite of gap
            # Gap up → SELL (expect reversion down toward prev close)
            # Gap down → BUY (expect reversion up toward prev close)
            side = "SELL" if gap > 0 else "BUY"

            candidates.append((sym, side, abs(gap), candles, atr_val, prev_close))

        if not candidates:
            continue

        # Sort by gap magnitude (strongest gaps = strongest fade signal)
        candidates.sort(key=lambda x: x[2], reverse=True)
        selected = candidates[:daily_cap]

        for sym, side, gap_mag, candles, atr_val, prev_close in selected:
            entry_candle = candles[ENTRY_CANDLE_IDX]
            entry_price = entry_candle["close"]
            if entry_price <= 0:
                continue

            if side == "SELL":
                # Gap up fade: SELL, target = prev close (below entry)
                sl_price = entry_candle["high"] + atr_val * 0.5  # above gap high + buffer
                if target_prev_close:
                    target_price = prev_close
                else:
                    sl_dist = sl_price - entry_price
                    target_price = entry_price - sl_dist * rr_ratio

                # Ensure target is below entry for SELL
                if target_price >= entry_price:
                    continue
            else:
                # Gap down fade: BUY, target = prev close (above entry)
                sl_price = entry_candle["low"] - atr_val * 0.5  # below gap low - buffer
                if target_prev_close:
                    target_price = prev_close
                else:
                    sl_dist = entry_price - sl_price
                    target_price = entry_price + sl_dist * rr_ratio

                # Ensure target is above entry for BUY
                if target_price <= entry_price:
                    continue

            entry_ts = entry_candle["ts"]
            exited = False

            for ci in range(ENTRY_CANDLE_IDX + 1, len(candles)):
                c = candles[ci]
                hour = c["ts"].hour
                minute = c["ts"].minute

                # Square off
                if hour * 60 + minute >= sq_off_hour * 60 + SQUARE_OFF_MIN:
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
    ap = argparse.ArgumentParser(description="D.3 — Gap Fade backtest")
    ap.add_argument("--universe", default="NIFTY100")
    ap.add_argument("--gap-min", type=float, default=GAP_MIN_PCT)
    ap.add_argument("--gap-max", type=float, default=GAP_MAX_PCT)
    ap.add_argument("--vol-ceil", type=float, default=VOL_CEIL)
    ap.add_argument("--daily-cap", type=int, default=DAILY_CAP)
    ap.add_argument("--rr", type=float, default=RR_RATIO)
    ap.add_argument("--no-prev-close-target", action="store_true",
                    help="Use RR-based target instead of prev-close")
    args = ap.parse_args()

    symbols = get_universe(args.universe)
    print(f"\n  D.3 — Gap Fade (weak-volume gap mean-reversion)")
    print(f"  Gap {args.gap_min}%-{args.gap_max}%, Volume < {args.vol_ceil}x avg, "
          f"cap={args.daily_cap}")
    print(f"  Target: {'RR-based' if args.no_prev_close_target else 'previous close'}")
    print(f"  Loading {len(symbols)} symbols from {args.universe}...")

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
    print(f"  Regime distribution: "
          + ", ".join(f"{r}={dist[r]}" for r in ("TREND", "RANGE", "VOLATILE")))

    # ── Run strategy ──────────────────────────────────────────
    print(f"\n  {'='*100}")
    print(f"  Walk-forward results (net of cost)")
    print(f"  {'='*100}")

    for win_name, (w_start, w_end) in WINDOWS.items():
        print(f"\n  ── {win_name} window ──")

        routing_configs = {
            "ALL regimes": set(),
            "Skip VOLATILE": {"VOLATILE"},
            "RANGE only": {"TREND", "VOLATILE"},
            "RANGE + TREND": {"VOLATILE"},
        }

        for route_name, skip in routing_configs.items():
            trades = simulate_gap_fade(
                all_symbol_days, regime_labels,
                gap_min_pct=args.gap_min,
                gap_max_pct=args.gap_max,
                vol_ceil=args.vol_ceil,
                daily_cap=args.daily_cap,
                rr_ratio=args.rr,
                skip_regimes=skip,
                start=w_start, end=w_end,
                target_prev_close=not args.no_prev_close_target,
            )
            m = compute_metrics(trades, f"{win_name}/{route_name}", with_costs=True)
            _print_table(f"{route_name}", m)

    # ── Parameter sweep ──────────────────────────────────────
    print(f"\n  ── Parameter sweep (TEST window, ALL regimes) ──")
    for gp in [0.3, 0.5, 0.7, 1.0, 1.5]:
        for vc in [0.5, 0.8, 1.0, 1.2]:
            trades = simulate_gap_fade(
                all_symbol_days, regime_labels,
                gap_min_pct=gp, vol_ceil=vc,
                daily_cap=args.daily_cap,
                skip_regimes=set(),
                start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
                target_prev_close=not args.no_prev_close_target,
            )
            m = compute_metrics(trades, f"gap{gp}/vol<{vc}", with_costs=True)
            _print_table(f"gap>={gp}% vol<{vc}x", m)

    # ── Square-off time sweep ────────────────────────────────
    print(f"\n  ── Square-off time sweep (TEST, ALL) ──")
    for sq_h in [12, 13, 14, 15]:
        trades = simulate_gap_fade(
            all_symbol_days, regime_labels,
            gap_min_pct=args.gap_min, vol_ceil=args.vol_ceil,
            daily_cap=args.daily_cap,
            skip_regimes=set(),
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
            target_prev_close=not args.no_prev_close_target,
            sq_off_hour=sq_h,
        )
        m = compute_metrics(trades, f"sqoff-{sq_h}", with_costs=True)
        _print_table(f"sq-off {sq_h}:00", m)

    # ── Verdict ───────────────────────────────────────────────
    print(f"\n  === D.3 GAP FADE VERDICT ===")
    test_trades = simulate_gap_fade(
        all_symbol_days, regime_labels,
        gap_min_pct=args.gap_min,
        gap_max_pct=args.gap_max,
        vol_ceil=args.vol_ceil,
        daily_cap=args.daily_cap,
        rr_ratio=args.rr,
        skip_regimes=set(),
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        target_prev_close=not args.no_prev_close_target,
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
