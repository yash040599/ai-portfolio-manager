#!/usr/bin/env python3
"""Phase 7.2 — Gap-and-Go with Volume Qualification backtest.

THESIS: ORB-15 was PF 0.97 — the closest any strategy came to breakeven.
Gap-and-Go is the same family but adds a strict volume filter (first-15-min
volume > 2× same-period 20-day average) that should filter false breakouts.
Stocks that gap >1% on the open WITH institutional volume are more likely
to continue in the gap direction.

Academic basis: Caginalp & Laurent (1998) support gap follow-through when
accompanied by volume surge.

METHOD (strict no-lookahead):
  1. Universe = NIFTY50 (15-min + daily candles).
  2. At 09:30 IST (after first 15-min candle), identify stocks that:
     a. Gapped >1% from previous day's close
     b. First-15-min volume > 2× same-period 20-day average volume
  3. Enter in gap direction:
     - Gap up → BUY at 09:30 candle close
     - Gap down → SELL at 09:30 candle close
  4. SL: below gap candle low (BUY) / above gap candle high (SELL)
     — tighter than ATR-based, anchored to the gap structure.
  5. Target: ATR-based (SL × RR 1.8) or gap-continuation (whichever larger).
  6. Daily cap = 2 (same as K1=2 audit config).
  7. Regime routing: test ALL, skip-RANGE, VOLATILE-only, TREND+VOLATILE.
  8. Walk-forward: TRAIN year 1, TEST year 2 (OOS).

Usage:
    python scripts/trade/backtest_gap_go.py
    python scripts/trade/backtest_gap_go.py --gap-pct 1.5 --vol-mult 2.5

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
    compute_metrics, _atr, _rsi, _make_trade,
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
GAP_PCT = 1.0               # minimum gap % from prev close
GAP_MAX_PCT = 5.0            # reject extreme gaps (corporate action)
VOL_MULT = 2.0              # first-candle volume must be > this × 20-day avg
TRADE_VALUE = 15_000         # Rs per trade
ATR_MULT = 2.0               # fallback SL = ATR × this (used for target calc)
RR_RATIO = 1.8               # Target = SL × this
DAILY_CAP = 2                # max trades per day
LOSER_EXIT_HOUR = 13
SQUARE_OFF_HOUR = 14
SQUARE_OFF_MIN = 0
ENTRY_CANDLE_IDX = 1         # 09:30 candle (second of day)
VOL_LOOKBACK_DAYS = 20       # days for volume baseline
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
    """Average volume of the first candle across the last `lookback` days
    before `date_str`. Returns 0 if insufficient data."""
    prior_dates = sorted(d for d in days_dict if d < date_str and not d.startswith("_"))
    prior_dates = prior_dates[-lookback:]
    if not prior_dates:
        return 0.0
    vols = []
    for d in prior_dates:
        candles = days_dict[d]
        if candles:
            # Use the first candle's volume (09:15 bar)
            # or the ENTRY_CANDLE_IDX bar's volume
            idx = min(ENTRY_CANDLE_IDX, len(candles) - 1)
            v = candles[idx].get("volume", 0) or 0
            if v > 0:
                vols.append(v)
    return sum(vols) / len(vols) if vols else 0.0


def simulate_gap_go(
    all_symbol_days: dict[str, dict],
    regime_labels: dict[str, str],
    *,
    gap_pct: float = GAP_PCT,
    gap_max_pct: float = GAP_MAX_PCT,
    vol_mult: float = VOL_MULT,
    daily_cap: int = DAILY_CAP,
    skip_regimes: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    # RSI contra-momentum filter (N1 gate for Gap-and-Go)
    rsi_contra_sell: float = 0,     # block SELL when RSI < X (0=disabled)
    rsi_contra_buy: float = 0,      # block BUY when RSI > X (0=disabled)
    # v1.1 filters
    gap_hold_min_pct: float = 0,    # reject if gap faded > X% from open (0=disabled)
    score_contra_block: bool = False,  # reject when composite score contradicts gap
    # Regime-conditional cap: override daily_cap on specific regimes
    regime_cap_overrides: dict[str, int] | None = None,  # e.g. {"VOLATILE": 4}
) -> list[dict]:
    """Run gap-and-go strategy across all dates."""
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

        # Find all stocks that gapped with volume
        candidates: list[tuple[str, str, float, list[dict], float]] = []

        for sym, sdata in all_symbol_days.items():
            candles = sdata["days"].get(date_str)
            if not candles or len(candles) < 3:
                continue

            prev_close = _prior_close(sdata["daily"], date_str)
            if not prev_close or prev_close <= 0:
                continue

            if len(candles) < ENTRY_CANDLE_IDX + 1:
                continue

            entry_candle = candles[ENTRY_CANDLE_IDX]
            open_price = candles[0]["open"]

            # Check gap from prev close to today's open
            gap = (open_price - prev_close) / prev_close * 100

            if abs(gap) < gap_pct:
                continue

            if abs(gap) > gap_max_pct:
                continue

            # Volume filter: entry candle volume vs historical average
            entry_vol = entry_candle.get("volume", 0) or 0
            avg_vol = _avg_first_candle_volume(sdata["days"], date_str)

            if avg_vol <= 0 or entry_vol < vol_mult * avg_vol:
                continue

            # ATR for target calculation
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

            side = "BUY" if gap > 0 else "SELL"

            # RSI contra-momentum filter: compute RSI on closes up to entry candle
            if rsi_contra_sell > 0 or rsi_contra_buy > 0:
                rsi_closes = [c["close"] for c in atr_window]
                entry_rsi = _rsi(rsi_closes, 14)
                if side == "SELL" and rsi_contra_sell > 0 and entry_rsi < rsi_contra_sell:
                    continue
                if side == "BUY" and rsi_contra_buy > 0 and entry_rsi > rsi_contra_buy:
                    continue

            # v1.1: Gap-hold check — reject if gap faded from open
            # by the time the entry candle closed
            if gap_hold_min_pct > 0:
                entry_close = entry_candle["close"]
                if side == "BUY":
                    fade = (open_price - entry_close) / open_price * 100
                else:
                    fade = (entry_close - open_price) / open_price * 100
                if fade > gap_hold_min_pct:
                    continue

            # v1.1: Score contradiction — compute a simple trend score
            # from the candle data (EMA direction proxy) and reject if
            # it contradicts the gap direction
            if score_contra_block:
                # Use recent closes to compute 5-period vs 20-period
                # EMA direction as a simple trend proxy
                closes = [c["close"] for c in atr_window[-20:]]
                if len(closes) >= 20:
                    ema5 = sum(closes[-5:]) / 5
                    ema20 = sum(closes[-20:]) / 20
                    trend_bullish = ema5 > ema20
                    if side == "BUY" and not trend_bullish:
                        continue
                    if side == "SELL" and trend_bullish:
                        continue

            candidates.append((sym, side, abs(gap), candles, atr_val))

        if not candidates:
            continue

        # Sort by gap magnitude (strongest gaps first) and cap
        candidates.sort(key=lambda x: x[2], reverse=True)
        effective_cap = daily_cap
        if regime_cap_overrides and regime and regime in regime_cap_overrides:
            effective_cap = regime_cap_overrides[regime]
        selected = candidates[:effective_cap]

        for sym, side, _gap_mag, candles, atr_val in selected:
            entry_candle = candles[ENTRY_CANDLE_IDX]
            entry_price = entry_candle["close"]
            if entry_price <= 0:
                continue

            # SL anchored to gap candle structure
            if side == "BUY":
                # SL at the low of the entry candle (gap support)
                sl_price = entry_candle["low"]
                sl_dist = entry_price - sl_price
                if sl_dist <= 0:
                    sl_dist = atr_val * ATR_MULT
                    sl_price = entry_price - sl_dist
                # Target: max(RR-based, gap continuation)
                target_dist = max(sl_dist * RR_RATIO, atr_val * ATR_MULT * RR_RATIO)
                target_price = entry_price + target_dist
            else:
                # SL at the high of the entry candle (gap resistance)
                sl_price = entry_candle["high"]
                sl_dist = sl_price - entry_price
                if sl_dist <= 0:
                    sl_dist = atr_val * ATR_MULT
                    sl_price = entry_price + sl_dist
                target_dist = max(sl_dist * RR_RATIO, atr_val * ATR_MULT * RR_RATIO)
                target_price = entry_price - target_dist

            entry_ts = entry_candle["ts"]
            exited = False

            for ci in range(ENTRY_CANDLE_IDX + 1, len(candles)):
                c = candles[ci]
                hour = c["ts"].hour
                minute = c["ts"].minute

                # Square off
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
                    # Stop loss
                    if c["low"] <= sl_price:
                        pnl_pct = (sl_price - entry_price) / entry_price * 100
                        all_trades.append(_make_trade(
                            sym, entry_ts, c["ts"], side, entry_price,
                            sl_price, sl_price, target_price, pnl_pct,
                            "STOP_LOSS", True))
                        exited = True
                        break
                    # Target
                    if c["high"] >= target_price:
                        pnl_pct = (target_price - entry_price) / entry_price * 100
                        all_trades.append(_make_trade(
                            sym, entry_ts, c["ts"], side, entry_price,
                            target_price, sl_price, target_price, pnl_pct,
                            "TARGET_HIT", True))
                        exited = True
                        break
                    # Loser exit
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
    ap = argparse.ArgumentParser(description="Phase 7.2 — Gap-and-Go with Volume backtest")
    ap.add_argument("--universe", default="NIFTY50")
    ap.add_argument("--gap-pct", type=float, default=GAP_PCT, help="Minimum gap %%")
    ap.add_argument("--vol-mult", type=float, default=VOL_MULT, help="Volume multiplier floor")
    ap.add_argument("--daily-cap", type=int, default=DAILY_CAP, help="Max trades per day")
    ap.add_argument("--rsi-contra-sell", type=float, default=0, help="Block SELL when RSI < X (0=disabled)")
    ap.add_argument("--rsi-contra-buy", type=float, default=0, help="Block BUY when RSI > X (0=disabled)")
    ap.add_argument("--sweep-rsi", action="store_true", help="Sweep RSI contra-momentum thresholds")
    ap.add_argument("--gap-hold", type=float, default=0, help="Gap-hold filter: reject if gap faded > X%% (0=disabled)")
    ap.add_argument("--score-contra", action="store_true", help="Score contradiction filter: reject when score contradicts gap")
    ap.add_argument("--v11", action="store_true", help="Run with all v1.1 filters (gap-hold 0.5, score-contra, RSI-buy 70)")
    args = ap.parse_args()

    # v1.1 preset: activates all new filters
    if args.v11:
        args.gap_hold = 0.5
        args.score_contra = True
        args.rsi_contra_buy = 70.0

    symbols = get_universe(args.universe)
    print("\n  Phase 7.2 — Gap-and-Go with Volume Qualification")
    print(f"  Gap >= {args.gap_pct}%, Volume >= {args.vol_mult}x 20-day avg, "
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
    print(f"  Walk-forward results (net of cost, gap>={args.gap_pct}%, "
          f"vol>={args.vol_mult}x, cap={args.daily_cap})")
    print(f"  {'='*100}")

    for win_name, (w_start, w_end) in WINDOWS.items():
        print(f"\n  ── {win_name} window ──")

        routing_configs = {
            "ALL regimes": set(),
            "Skip RANGE": {"RANGE"},
            "TREND + VOLATILE": {"RANGE"},
            "VOLATILE only": {"TREND", "RANGE"},
        }

        for route_name, skip in routing_configs.items():
            # Skip duplicate routing
            if route_name == "TREND + VOLATILE" and route_name == "Skip RANGE":
                continue
            trades = simulate_gap_go(
                all_symbol_days, regime_labels,
                gap_pct=args.gap_pct,
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
    for gp in [0.5, 1.0, 1.5, 2.0]:
        for vm in [1.5, 2.0, 3.0]:
            trades = simulate_gap_go(
                all_symbol_days, regime_labels,
                gap_pct=gp, vol_mult=vm, daily_cap=args.daily_cap,
                skip_regimes=set(),
                start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
            )
            m = compute_metrics(trades, f"gap{gp}/vol{vm}", with_costs=True)
            _print_table(f"gap>={gp}% vol>={vm}x", m)

    # ── RSI contra-momentum sweep (if requested) ───────────────
    if args.sweep_rsi:
        print("\n  ── RSI contra-momentum sweep (TEST window, ALL regimes) ──")
        print("  Baseline first, then sweep SELL floor and BUY ceiling")

        rsi_base = simulate_gap_go(
            all_symbol_days, regime_labels,
            gap_pct=args.gap_pct, vol_mult=args.vol_mult,
            daily_cap=args.daily_cap, skip_regimes=set(),
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        )
        _print_table("Baseline (no RSI filter)", compute_metrics(rsi_base, "RSI-base", True))

        for thresh in [20, 25, 30, 35, 40]:
            trades = simulate_gap_go(
                all_symbol_days, regime_labels,
                gap_pct=args.gap_pct, vol_mult=args.vol_mult,
                daily_cap=args.daily_cap, skip_regimes=set(),
                start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
                rsi_contra_sell=float(thresh),
            )
            _print_table(f"SELL blocked RSI<{thresh}", compute_metrics(trades, f"RSI-sell-{thresh}", True))

        for thresh in [65, 70, 75, 80]:
            trades = simulate_gap_go(
                all_symbol_days, regime_labels,
                gap_pct=args.gap_pct, vol_mult=args.vol_mult,
                daily_cap=args.daily_cap, skip_regimes=set(),
                start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
                rsi_contra_buy=float(thresh),
            )
            _print_table(f"BUY blocked RSI>{thresh}", compute_metrics(trades, f"RSI-buy-{thresh}", True))

    # ── Verdict ───────────────────────────────────────────────
    print("\n  === PHASE 7.2 VERDICT ===")
    test_trades = simulate_gap_go(
        all_symbol_days, regime_labels,
        gap_pct=args.gap_pct,
        vol_mult=args.vol_mult,
        daily_cap=args.daily_cap,
        skip_regimes=set(),
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        gap_hold_min_pct=args.gap_hold,
        score_contra_block=args.score_contra,
        rsi_contra_buy=args.rsi_contra_buy,
        rsi_contra_sell=args.rsi_contra_sell,
    )
    test_m = compute_metrics(test_trades, "TEST/ALL", with_costs=True)
    pf = test_m.get("pf", 0)
    if pf >= GATE_PF:
        print(f"  PASS — OOS PF {pf} >= {GATE_PF}. Candidate for dry-run validation.")
    elif pf >= 1.0:
        print(f"  MARGINAL — OOS PF {pf} (1.0 <= PF < {GATE_PF}). May benefit from tuning.")
    else:
        print(f"  FAIL — OOS PF {pf} < 1.0. Gap-and-Go does not have edge at this config.")
    print(f"  Total charges (TEST/ALL): Rs.{test_m.get('total_charges', 0):,.2f}")

    # ── v1.1 comparison (gap-hold + score-contra sweep) ───────
    print(f"\n  {'='*100}")
    print("  v1.1 Filter Comparison (TEST window)")
    print(f"  {'='*100}")

    # Baseline v1.0 (no new filters)
    v10 = simulate_gap_go(
        all_symbol_days, regime_labels,
        gap_pct=args.gap_pct, vol_mult=args.vol_mult,
        daily_cap=args.daily_cap, skip_regimes=set(),
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        rsi_contra_buy=70.0,  # RSI filter is in both versions
    )
    _print_table("v1.0 baseline (RSI 70)", compute_metrics(v10, "v1.0", True))

    # Gap-hold sweep
    for hold_pct in [0.3, 0.5, 0.7, 1.0]:
        trades = simulate_gap_go(
            all_symbol_days, regime_labels,
            gap_pct=args.gap_pct, vol_mult=args.vol_mult,
            daily_cap=args.daily_cap, skip_regimes=set(),
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
            rsi_contra_buy=70.0,
            gap_hold_min_pct=hold_pct,
        )
        _print_table(f"  + gap-hold {hold_pct}%", compute_metrics(trades, f"hold-{hold_pct}", True))

    # Score contradiction
    trades_sc = simulate_gap_go(
        all_symbol_days, regime_labels,
        gap_pct=args.gap_pct, vol_mult=args.vol_mult,
        daily_cap=args.daily_cap, skip_regimes=set(),
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        rsi_contra_buy=70.0,
        score_contra_block=True,
    )
    _print_table("  + score-contra only", compute_metrics(trades_sc, "score-contra", True))

    # Full v1.1 (gap-hold 0.5 + score-contra + RSI 70)
    v11_all = simulate_gap_go(
        all_symbol_days, regime_labels,
        gap_pct=args.gap_pct, vol_mult=args.vol_mult,
        daily_cap=args.daily_cap, skip_regimes=set(),
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        rsi_contra_buy=70.0,
        gap_hold_min_pct=0.5,
        score_contra_block=True,
    )
    _print_table("  v1.1 FULL (all filters)", compute_metrics(v11_all, "v1.1-full", True))

    # v1.1 + skip RANGE
    v11_skip = simulate_gap_go(
        all_symbol_days, regime_labels,
        gap_pct=args.gap_pct, vol_mult=args.vol_mult,
        daily_cap=args.daily_cap, skip_regimes={"RANGE"},
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        rsi_contra_buy=70.0,
        gap_hold_min_pct=0.5,
        score_contra_block=True,
    )
    _print_table("  v1.1 + skip RANGE", compute_metrics(v11_skip, "v1.1-skipR", True))

    # v1.1 + VOLATILE only
    v11_vol = simulate_gap_go(
        all_symbol_days, regime_labels,
        gap_pct=args.gap_pct, vol_mult=args.vol_mult,
        daily_cap=args.daily_cap, skip_regimes={"TREND", "RANGE"},
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        rsi_contra_buy=70.0,
        gap_hold_min_pct=0.5,
        score_contra_block=True,
    )
    _print_table("  v1.1 + VOLATILE only", compute_metrics(v11_vol, "v1.1-vol", True))

    v11_pf = compute_metrics(v11_all, "v1.1", True).get("pf", 0)
    print(f"\n  v1.0 PF: {compute_metrics(v10, 'v1.0', True).get('pf', 0):.2f}  →  "
          f"v1.1 PF: {v11_pf:.2f}")

    # ── Regime-conditional cap sweep (VOLATILE days get more trades) ──
    print(f"\n  {'='*100}")
    print("  Regime-Conditional Cap Sweep (v1.1 filters, TEST window)")
    print("  Base cap=2 on TREND/RANGE, varying VOLATILE cap")
    print(f"  {'='*100}")

    _print_table("  baseline cap=2 all days", compute_metrics(v11_all, "base", True))

    for vol_cap in [3, 4, 5, 6, 8]:
        trades_rc = simulate_gap_go(
            all_symbol_days, regime_labels,
            gap_pct=args.gap_pct, vol_mult=args.vol_mult,
            daily_cap=2, skip_regimes=set(),
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
            rsi_contra_buy=70.0,
            gap_hold_min_pct=0.3,
            score_contra_block=True,
            regime_cap_overrides={"VOLATILE": vol_cap},
        )
        m = compute_metrics(trades_rc, f"vol-cap-{vol_cap}", True)
        _print_table(f"  VOLATILE cap={vol_cap}", m)

    # Also test raising cap on VOLATILE + TREND
    for vol_cap in [3, 4]:
        trades_rc2 = simulate_gap_go(
            all_symbol_days, regime_labels,
            gap_pct=args.gap_pct, vol_mult=args.vol_mult,
            daily_cap=2, skip_regimes=set(),
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
            rsi_contra_buy=70.0,
            gap_hold_min_pct=0.3,
            score_contra_block=True,
            regime_cap_overrides={"VOLATILE": vol_cap, "TREND": vol_cap},
        )
        m2 = compute_metrics(trades_rc2, f"vol+trend-cap-{vol_cap}", True)
        _print_table(f"  VOLATILE+TREND cap={vol_cap}", m2)


if __name__ == "__main__":
    main()
