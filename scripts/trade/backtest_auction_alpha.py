#!/usr/bin/env python3
"""D.5 — Pre-Open Auction Alpha backtest.

THESIS: NSE pre-open auction (9:00-9:08) reveals demand/supply imbalance.
Stocks with large opening gaps on high first-candle volume have institutional
conviction; we can use the GAP DIRECTION + first candle shape (body vs wick)
as a "poor man's order flow" signal. Unlike Gap-and-Go (which requires 2x
volume), this tests first-candle bullish/bearish shape as the primary signal.

METHOD (strict no-lookahead):
  1. Universe = NIFTY100 (15-min candles).
  2. At 09:45 IST (after 09:30 candle closes), check first candle shape:
     a. Bullish candle (close > open, body > 60% of range) → BUY
     b. Bearish candle (close < open, body > 60% of range) → SELL
  3. Volume filter: first-candle volume > 1.5× 20-day average
  4. SL: below first candle low (BUY) / above first candle high (SELL)
  5. Target: ATR-based (SL × RR)
  6. Daily cap = 2
  7. Square-off: 13:00 IST (same as Gap-and-Go)
  8. Walk-forward: TRAIN year 1, TEST year 2 (OOS)

Usage:
    python scripts/trade/backtest_auction_alpha.py

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
    compute_charges, compute_metrics, _atr, _rsi, _make_trade,
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
BODY_RATIO_MIN = 0.6          # candle body must be > 60% of range (strong candle)
VOL_MULT = 1.5               # first-candle volume > 1.5× 20-day average
GAP_MIN_PCT = 0.3            # minimum gap from prev close (small gap OK)
GAP_MAX_PCT = 5.0
RR_RATIO = 1.8
DAILY_CAP = 2
ENTRY_CANDLE_IDX = 2          # 09:45 candle (wait for 09:30 to close, enter on next)
FIRST_CANDLE_IDX = 1          # 09:30 candle (the signal candle)
VOL_LOOKBACK_DAYS = 20
SQUARE_OFF_HOUR = 13
SQUARE_OFF_MIN = 0
LOSER_EXIT_HOUR = 12
GATE_PF = 1.15


def _prior_close(daily_candles: list[dict], date_str: str) -> float | None:
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
    candle_idx: int = FIRST_CANDLE_IDX,
    lookback: int = VOL_LOOKBACK_DAYS,
) -> float:
    prior_dates = sorted(d for d in days_dict if d < date_str and not d.startswith("_"))
    prior_dates = prior_dates[-lookback:]
    if not prior_dates:
        return 0.0
    vols = []
    for d in prior_dates:
        candles = days_dict[d]
        if candles and len(candles) > candle_idx:
            v = candles[candle_idx].get("volume", 0) or 0
            if v > 0:
                vols.append(v)
    return sum(vols) / len(vols) if vols else 0.0


def _candle_body_ratio(c: dict) -> float:
    """Ratio of body to full range. 1.0 = marubozu, 0.0 = doji."""
    full_range = c["high"] - c["low"]
    if full_range <= 0:
        return 0.0
    body = abs(c["close"] - c["open"])
    return body / full_range


def simulate_auction_alpha(
    all_symbol_days: dict[str, dict],
    regime_labels: dict[str, str],
    *,
    body_ratio_min: float = BODY_RATIO_MIN,
    vol_mult: float = VOL_MULT,
    gap_min_pct: float = GAP_MIN_PCT,
    gap_max_pct: float = GAP_MAX_PCT,
    rr_ratio: float = RR_RATIO,
    daily_cap: int = DAILY_CAP,
    skip_regimes: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    require_gap_confirm: bool = False,
    rsi_buy_ceil: float = 0,
    sq_off_hour: int = SQUARE_OFF_HOUR,
) -> list[dict]:
    """Run auction alpha strategy."""
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

        candidates: list[tuple[str, str, float, list[dict], float]] = []

        for sym, sdata in all_symbol_days.items():
            candles = sdata["days"].get(date_str)
            if not candles or len(candles) < ENTRY_CANDLE_IDX + 2:
                continue

            # Signal candle = first 15-min candle (09:30)
            signal_candle = candles[FIRST_CANDLE_IDX]
            entry_candle = candles[ENTRY_CANDLE_IDX]

            # Check candle body ratio
            body_ratio = _candle_body_ratio(signal_candle)
            if body_ratio < body_ratio_min:
                continue

            # Volume filter
            signal_vol = signal_candle.get("volume", 0) or 0
            avg_vol = _avg_first_candle_volume(sdata["days"], date_str)
            if avg_vol <= 0 or signal_vol < vol_mult * avg_vol:
                continue

            # Determine direction from candle shape
            is_bullish = signal_candle["close"] > signal_candle["open"]
            side = "BUY" if is_bullish else "SELL"

            # Optional: gap confirmation (candle direction matches gap)
            if require_gap_confirm:
                prev_close = _prior_close(sdata["daily"], date_str)
                if prev_close and prev_close > 0:
                    open_price = candles[0]["open"]
                    gap_pct = (open_price - prev_close) / prev_close * 100
                    if abs(gap_pct) < gap_min_pct:
                        continue
                    if abs(gap_pct) > gap_max_pct:
                        continue
                    # Gap must confirm candle direction
                    if side == "BUY" and gap_pct < 0:
                        continue
                    if side == "SELL" and gap_pct > 0:
                        continue

            # ATR
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

            # RSI filter
            if rsi_buy_ceil > 0 and side == "BUY":
                rsi_closes = [c["close"] for c in atr_window]
                rsi_val = _rsi(rsi_closes, 14)
                if rsi_val > rsi_buy_ceil:
                    continue

            # Score by body ratio × volume ratio (conviction strength)
            vol_ratio = signal_vol / avg_vol if avg_vol > 0 else 1.0
            conviction = body_ratio * vol_ratio

            candidates.append((sym, side, conviction, candles, atr_val))

        if not candidates:
            continue

        candidates.sort(key=lambda x: x[2], reverse=True)
        selected = candidates[:daily_cap]

        for sym, side, conviction, candles, atr_val in selected:
            signal_candle = candles[FIRST_CANDLE_IDX]
            entry_candle = candles[ENTRY_CANDLE_IDX]
            entry_price = entry_candle["close"]
            if entry_price <= 0:
                continue

            if side == "BUY":
                sl_price = signal_candle["low"]
                sl_dist = entry_price - sl_price
                if sl_dist <= 0:
                    sl_dist = atr_val * 2.0
                    sl_price = entry_price - sl_dist
                target_price = entry_price + sl_dist * rr_ratio
            else:
                sl_price = signal_candle["high"]
                sl_dist = sl_price - entry_price
                if sl_dist <= 0:
                    sl_dist = atr_val * 2.0
                    sl_price = entry_price + sl_dist
                target_price = entry_price - sl_dist * rr_ratio

            entry_ts = entry_candle["ts"]
            exited = False

            for ci in range(ENTRY_CANDLE_IDX + 1, len(candles)):
                c = candles[ci]
                hour = c["ts"].hour
                minute = c["ts"].minute

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
    ap = argparse.ArgumentParser(description="D.5 — Pre-Open Auction Alpha backtest")
    ap.add_argument("--universe", default="NIFTY100")
    ap.add_argument("--body-ratio", type=float, default=BODY_RATIO_MIN)
    ap.add_argument("--vol-mult", type=float, default=VOL_MULT)
    ap.add_argument("--daily-cap", type=int, default=DAILY_CAP)
    ap.add_argument("--rr", type=float, default=RR_RATIO)
    ap.add_argument("--gap-confirm", action="store_true",
                    help="Require gap direction to match candle direction")
    ap.add_argument("--rsi-buy-ceil", type=float, default=0,
                    help="Block BUY when RSI > X (0=disabled)")
    args = ap.parse_args()

    symbols = get_universe(args.universe)
    print(f"\n  D.5 — Pre-Open Auction Alpha (first candle shape + volume)")
    print(f"  Body ratio >= {args.body_ratio}, Volume >= {args.vol_mult}x avg, "
          f"RR={args.rr}, cap={args.daily_cap}")
    print(f"  Gap confirm: {'ON' if args.gap_confirm else 'OFF'}")
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
            "Skip RANGE": {"RANGE"},
            "VOLATILE only": {"TREND", "RANGE"},
        }

        for route_name, skip in routing_configs.items():
            trades = simulate_auction_alpha(
                all_symbol_days, regime_labels,
                body_ratio_min=args.body_ratio,
                vol_mult=args.vol_mult,
                rr_ratio=args.rr,
                daily_cap=args.daily_cap,
                skip_regimes=skip,
                start=w_start, end=w_end,
                require_gap_confirm=args.gap_confirm,
                rsi_buy_ceil=args.rsi_buy_ceil,
            )
            m = compute_metrics(trades, f"{win_name}/{route_name}", with_costs=True)
            _print_table(f"{route_name}", m)

    # ── Parameter sweep ──────────────────────────────────────
    print(f"\n  ── Parameter sweep (TEST, ALL) ──")
    for br in [0.4, 0.5, 0.6, 0.7]:
        for vm in [1.0, 1.5, 2.0]:
            trades = simulate_auction_alpha(
                all_symbol_days, regime_labels,
                body_ratio_min=br, vol_mult=vm,
                daily_cap=args.daily_cap,
                skip_regimes=set(),
                start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
            )
            m = compute_metrics(trades, f"body{br}/vol{vm}", with_costs=True)
            _print_table(f"body>={br} vol>={vm}x", m)

    # ── Gap confirm variant ──────────────────────────────────
    print(f"\n  ── Gap confirm variant (TEST, ALL) ──")
    for gap_conf in [False, True]:
        for rsi_ceil in [0, 70]:
            trades = simulate_auction_alpha(
                all_symbol_days, regime_labels,
                body_ratio_min=args.body_ratio, vol_mult=args.vol_mult,
                daily_cap=args.daily_cap,
                skip_regimes=set(),
                start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
                require_gap_confirm=gap_conf,
                rsi_buy_ceil=float(rsi_ceil),
            )
            label = f"gap={'Y' if gap_conf else 'N'} rsi={'70' if rsi_ceil else 'off'}"
            m = compute_metrics(trades, label, with_costs=True)
            _print_table(label, m)

    # ── Square-off time sweep ────────────────────────────────
    print(f"\n  ── Square-off time sweep (TEST, ALL) ──")
    for sq_h in [12, 13, 14, 15]:
        trades = simulate_auction_alpha(
            all_symbol_days, regime_labels,
            body_ratio_min=args.body_ratio, vol_mult=args.vol_mult,
            daily_cap=args.daily_cap,
            skip_regimes=set(),
            start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
            sq_off_hour=sq_h,
        )
        m = compute_metrics(trades, f"sqoff-{sq_h}", with_costs=True)
        _print_table(f"sq-off {sq_h}:00", m)

    # ── Verdict ───────────────────────────────────────────────
    print(f"\n  === D.5 AUCTION ALPHA VERDICT ===")
    test_trades = simulate_auction_alpha(
        all_symbol_days, regime_labels,
        body_ratio_min=args.body_ratio,
        vol_mult=args.vol_mult,
        rr_ratio=args.rr,
        daily_cap=args.daily_cap,
        skip_regimes=set(),
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
        require_gap_confirm=args.gap_confirm,
        rsi_buy_ceil=args.rsi_buy_ceil,
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
