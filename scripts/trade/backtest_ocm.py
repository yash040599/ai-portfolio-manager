#!/usr/bin/env python3
"""Phase 9.4 — Opening Candle Momentum (OCM) backtest.

THESIS: The first 15-min candle (9:15-9:30) captures the opening auction
and early institutional flow. When the first candle has:
  1. Very high volume (>2x 20-day avg first-candle volume)
  2. Strong directional close (close near high = bullish, close near low = bearish)
  3. Confirmed by gap direction from prev close
This signals institutional conviction. Enter at 9:30 candle close in the
direction of the first candle.

Different from Gap-and-Go: Gap-and-Go requires a gap from prev close.
OCM focuses on the SHAPE and VOLUME of the first candle itself, regardless
of gap size. A stock can open flat but have a massive bullish first candle
with huge volume = institutional buying at open.

METHOD (strict no-lookahead):
  1. Universe = NIFTY100.
  2. At 09:30, compute first candle metrics:
     a. Body ratio = |close - open| / (high - low). High = directional candle.
     b. Side: close > open = BUY, close < open = SELL.
     c. Volume: first candle vol > vol_mult x 20-day avg first candle vol.
  3. Filter: body_ratio > min_body (e.g., 0.5 = close in top/bottom half).
  4. Entry at 09:30 candle close.
  5. SL: opposite side of first candle (high for SELL, low for BUY).
  6. Target: SL × RR ratio.
  7. Daily cap = 2, square off at 14:00.
  8. Walk-forward OOS.

Usage:
    python scripts/trade/backtest_ocm.py
    python scripts/trade/backtest_ocm.py --universe NIFTY50 --min-body 0.6
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
    compute_metrics, _rsi, _make_trade,
)
from regime_analysis import label_regimes  # noqa: E402
from shared.nifty_universe import get_universe  # noqa: E402

WINDOWS = {
    "FULL": (None, None),
    "TRAIN": ("2024-05-27", "2025-05-31"),
    "TEST": ("2025-06-01", "2026-05-22"),
}

# ── Strategy params ───────────────────────────────────────────
VOL_MULT = 2.0           # first candle vol > this x 20-day avg
MIN_BODY_RATIO = 0.5     # |close-open| / (high-low) must exceed this
RR_RATIO = 1.5
DAILY_CAP = 2
LOSER_EXIT_HOUR = 13
SQUARE_OFF_HOUR = 14
VOL_LOOKBACK = 20
MIN_MOVE_PCT = 0.3       # first candle must move > this % (filters dojis)
GATE_PF = 1.15


def _avg_first_candle_volume(
    days_dict: dict[str, list[dict]],
    date_str: str,
    lookback: int = VOL_LOOKBACK,
) -> float:
    prior_dates = sorted(d for d in days_dict if d < date_str and not d.startswith("_"))
    prior_dates = prior_dates[-lookback:]
    if not prior_dates:
        return 0.0
    vols = []
    for d in prior_dates:
        candles = days_dict[d]
        if candles:
            v = candles[0].get("volume", 0) or 0
            if v > 0:
                vols.append(v)
    return sum(vols) / len(vols) if vols else 0.0


def _prior_close(daily_candles: list[dict], date_str: str) -> float | None:
    prior = None
    for d in daily_candles:
        ds = d["ts"].date().isoformat()
        if ds >= date_str:
            break
        prior = d["close"]
    return prior


def simulate_ocm(
    all_symbol_days: dict[str, dict],
    regime_labels: dict[str, str],
    *,
    vol_mult: float = VOL_MULT,
    min_body_ratio: float = MIN_BODY_RATIO,
    min_move_pct: float = MIN_MOVE_PCT,
    rr_ratio: float = RR_RATIO,
    daily_cap: int = DAILY_CAP,
    sq_off_hour: int = SQUARE_OFF_HOUR,
    require_gap_confirm: bool = False,  # require gap in same direction
    rsi_buy_ceiling: float = 0,         # block BUY if RSI > X
    skip_regimes: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
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

        candidates = []

        for sym, sdata in all_symbol_days.items():
            candles = sdata["days"].get(date_str)
            if not candles or len(candles) < 4:
                continue

            c0 = candles[0]  # 9:15 candle
            body = abs(c0["close"] - c0["open"])
            wick = c0["high"] - c0["low"]
            if wick <= 0:
                continue

            body_ratio = body / wick
            if body_ratio < min_body_ratio:
                continue

            # Check move size
            move_pct = body / c0["open"] * 100 if c0["open"] > 0 else 0
            if move_pct < min_move_pct:
                continue

            # Volume filter
            vol = c0.get("volume", 0) or 0
            avg_vol = _avg_first_candle_volume(sdata["days"], date_str)
            if avg_vol <= 0 or vol < vol_mult * avg_vol:
                continue

            side = "BUY" if c0["close"] > c0["open"] else "SELL"

            # Gap confirmation (optional)
            if require_gap_confirm:
                prev_close = _prior_close(sdata["daily"], date_str)
                if prev_close and prev_close > 0:
                    gap = (c0["open"] - prev_close) / prev_close * 100
                    if side == "BUY" and gap < 0:
                        continue
                    if side == "SELL" and gap > 0:
                        continue

            # RSI filter
            if rsi_buy_ceiling > 0 and side == "BUY":
                all_c = sdata.get("all_candles", [])
                bd = sdata.get("boundaries", {}).get(date_str)
                if bd:
                    si = bd[0]
                    if si >= 14:
                        rsi_window = all_c[max(0, si - 50):si + 1]
                        rsi_closes = [c["close"] for c in rsi_window]
                        entry_rsi = _rsi(rsi_closes, 14)
                        if entry_rsi > rsi_buy_ceiling:
                            continue

            # Score: volume relative strength * body ratio
            vol_score = vol / avg_vol if avg_vol > 0 else 0
            score = vol_score * body_ratio

            candidates.append((sym, side, score, candles))

        if not candidates:
            continue

        candidates.sort(key=lambda x: x[2], reverse=True)
        selected = candidates[:daily_cap]

        for sym, side, _score, candles in selected:
            c0 = candles[0]
            entry_price = c0["close"]  # enter at 9:15 candle close = 9:30
            if entry_price <= 0:
                continue

            if side == "BUY":
                sl_price = c0["low"]
                sl_dist = entry_price - sl_price
                if sl_dist <= 0:
                    continue
                target_price = entry_price + sl_dist * rr_ratio
            else:
                sl_price = c0["high"]
                sl_dist = sl_price - entry_price
                if sl_dist <= 0:
                    continue
                target_price = entry_price - sl_dist * rr_ratio

            entry_ts = c0["ts"]
            exited = False

            for ci in range(1, len(candles)):
                c = candles[ci]
                hour = c["ts"].hour
                minute = c["ts"].minute

                if hour * 60 + minute >= sq_off_hour * 60:
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
        print(f"  {label:<40s} {metrics['note']}")
        return
    print(f"  {label:<40s} "
          f"Trades: {metrics['trades']:>5d}  "
          f"WR: {metrics['win_rate']:>5.1f}%  "
          f"PF: {metrics['pf']:>5.2f}  "
          f"Exp: {metrics['expectancy']:>+7.3f}%  "
          f"Ret: {metrics['total_return']:>+8.2f}%  "
          f"MaxDD: {metrics['max_dd']:>6.2f}%  "
          f"Sharpe: {metrics['sharpe']:>+6.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 9.4 — Opening Candle Momentum backtest")
    ap.add_argument("--universe", default="NIFTY100")
    ap.add_argument("--vol-mult", type=float, default=VOL_MULT)
    ap.add_argument("--min-body", type=float, default=MIN_BODY_RATIO)
    ap.add_argument("--min-move", type=float, default=MIN_MOVE_PCT)
    ap.add_argument("--rr", type=float, default=RR_RATIO)
    ap.add_argument("--daily-cap", type=int, default=DAILY_CAP)
    ap.add_argument("--sq-off", type=int, default=SQUARE_OFF_HOUR)
    ap.add_argument("--gap-confirm", action="store_true")
    ap.add_argument("--rsi-buy", type=float, default=0)
    args = ap.parse_args()

    symbols = get_universe(args.universe)
    print("\n  Phase 9.4 — Opening Candle Momentum (OCM)")
    print(f"  Vol >= {args.vol_mult}x, Body >= {args.min_body}, Move >= {args.min_move}%, "
          f"RR = {args.rr}, cap = {args.daily_cap}, sq-off = {args.sq_off}:00")
    if args.gap_confirm:
        print("  Gap confirmation: ON")
    if args.rsi_buy > 0:
        print(f"  RSI BUY ceiling: {args.rsi_buy}")
    print(f"  Loading {len(symbols)} symbols...")

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
            "days": days, "daily": daily,
            "all_candles": all_candles, "boundaries": boundaries,
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

    # ── Walk-forward ──────────────────────────────────────────
    print(f"\n  {'='*120}")
    print("  Walk-forward results (net of cost)")
    print(f"  {'='*120}")

    for win_name, (w_start, w_end) in WINDOWS.items():
        print(f"\n  ── {win_name} window ──")
        for route_name, skip in [("ALL regimes", set()), ("Skip RANGE", {"RANGE"}),
                                  ("VOLATILE only", {"TREND", "RANGE"})]:
            trades = simulate_ocm(
                all_symbol_days, regime_labels,
                vol_mult=args.vol_mult, min_body_ratio=args.min_body,
                min_move_pct=args.min_move, rr_ratio=args.rr,
                daily_cap=args.daily_cap, sq_off_hour=args.sq_off,
                require_gap_confirm=args.gap_confirm,
                rsi_buy_ceiling=args.rsi_buy,
                skip_regimes=skip, start=w_start, end=w_end,
            )
            m = compute_metrics(trades, f"{win_name}/{route_name}", with_costs=True)
            _print_table(route_name, m)
            if win_name == "TEST" and m.get("by_reason"):
                print("    Exit reasons: " +
                      ", ".join(f"{k}={v}" for k, v in sorted(m["by_reason"].items())))

    # ── Parameter sweep ───────────────────────────────────────
    print("\n  ── Parameter sweep (TEST, ALL regimes) ──")
    for vm in [1.5, 2.0, 3.0]:
        for br in [0.4, 0.5, 0.6, 0.7]:
            for rr in [1.2, 1.5, 1.8]:
                trades = simulate_ocm(
                    all_symbol_days, regime_labels,
                    vol_mult=vm, min_body_ratio=br, rr_ratio=rr,
                    daily_cap=args.daily_cap, sq_off_hour=args.sq_off,
                    skip_regimes=set(),
                    start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
                )
                m = compute_metrics(trades, f"v{vm}/b{br}/r{rr}", with_costs=True)
                if m.get("trades", 0) >= 20:
                    _print_table(f"vol>={vm}x body>={br} RR={rr}", m)

    # ── Gap confirmation test ─────────────────────────────────
    print("\n  ── Gap confirmation filter (TEST, ALL regimes) ──")
    for gc in [False, True]:
        trades = simulate_ocm(
            all_symbol_days, regime_labels,
            vol_mult=args.vol_mult, min_body_ratio=args.min_body,
            rr_ratio=args.rr, daily_cap=args.daily_cap,
            sq_off_hour=args.sq_off, require_gap_confirm=gc,
            skip_regimes=set(),
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        )
        label = "With gap confirm" if gc else "No gap confirm"
        _print_table(label, compute_metrics(trades, f"gap-{gc}", True))

    # ── RSI filter ────────────────────────────────────────────
    print("\n  ── RSI BUY ceiling sweep (TEST, ALL regimes) ──")
    for rsi in [0, 65, 70, 75]:
        trades = simulate_ocm(
            all_symbol_days, regime_labels,
            vol_mult=args.vol_mult, min_body_ratio=args.min_body,
            rr_ratio=args.rr, daily_cap=args.daily_cap,
            sq_off_hour=args.sq_off, rsi_buy_ceiling=float(rsi),
            skip_regimes=set(),
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        )
        label = f"RSI BUY<={rsi}" if rsi > 0 else "No RSI filter"
        _print_table(label, compute_metrics(trades, f"rsi-{rsi}", True))

    # ── Verdict ───────────────────────────────────────────────
    print("\n  === PHASE 9.4 VERDICT ===")
    test_trades = simulate_ocm(
        all_symbol_days, regime_labels,
        vol_mult=args.vol_mult, min_body_ratio=args.min_body,
        rr_ratio=args.rr, daily_cap=args.daily_cap,
        sq_off_hour=args.sq_off,
        require_gap_confirm=args.gap_confirm,
        rsi_buy_ceiling=args.rsi_buy,
        skip_regimes=set(),
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
    )
    test_m = compute_metrics(test_trades, "TEST/ALL", with_costs=True)
    pf = test_m.get("pf", 0)
    if pf >= GATE_PF:
        print(f"  PASS — OOS PF {pf} >= {GATE_PF}.")
    elif pf >= 1.0:
        print(f"  MARGINAL — OOS PF {pf} >= 1.0 but < {GATE_PF}.")
    else:
        print(f"  FAIL — OOS PF {pf} < 1.0.")


if __name__ == "__main__":
    main()
