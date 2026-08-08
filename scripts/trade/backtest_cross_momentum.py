#!/usr/bin/env python3
"""Phase 7.1 — Cross-Sectional Momentum backtest (rank-based entry).

THESIS: Instead of scoring each stock independently with 14 indicators,
RANK all NIFTY50 stocks by first-15-min return and enter the top 2
strongest (BUY). This exploits relative strength rather than absolute
score — a fundamentally different signal family with zero overlap with
the current multi-indicator scorer.

Academic basis: Jegadeesh & Titman show intraday cross-sectional
persistence — the stock ranked #1 at 9:30 has elevated probability of
staying strong for 1-3 hours.

METHOD (strict no-lookahead):
  1. Universe = NIFTY50 (15-min candles).
  2. At 09:30 IST (after the first 15-min candle), compute return from
     previous day's close to 09:30 close for all 50 stocks.
  3. Rank by return (descending for BUY).
  4. Enter top K stocks (default 2) — pure momentum ranking.
  5. ATR-based SL/target (ATR×2.0, RR 1.8:1 — same as frozen audit).
  6. Exit: STOP_LOSS / TARGET_HIT / LOSER_EXIT@13:00 / SQUARE_OFF@14:00.
  7. Regime routing: test ALL, skip-RANGE, VOLATILE-only.
  8. Walk-forward: TRAIN year 1, TEST year 2 (OOS).

Usage:
    python scripts/trade/backtest_cross_momentum.py
    python scripts/trade/backtest_cross_momentum.py --top-k 3

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
    compute_metrics, _atr, _make_trade,
)
from regime_analysis import label_regimes  # noqa: E402
from shared.nifty_universe import get_universe  # noqa: E402

# ── Walk-forward windows (same as all prior phases) ───────────
WINDOWS = {
    "FULL": (None, None),
    "TRAIN": ("2024-05-27", "2025-05-31"),
    "TEST": ("2025-06-01", "2026-05-22"),
}

# ── Strategy params ───────────────────────────────────────────
TOP_K = 2                    # how many top-ranked to enter
TRADE_VALUE = 15_000         # Rs per trade
ATR_MULT = 2.0               # SL = ATR × this
RR_RATIO = 1.8               # Target = SL × this
LOSER_EXIT_HOUR = 13         # close losers after this
SQUARE_OFF_HOUR = 14         # hard square-off
SQUARE_OFF_MIN = 0
ENTRY_CANDLE_IDX = 1         # the 09:30 candle (second candle of day; first is 09:15)
GATE_PF = 1.15


def _prior_close(daily_candles: list[dict], date_str: str) -> float | None:
    """Get previous day's close from daily candles (no lookahead)."""
    prior = None
    for d in daily_candles:
        ds = d["ts"].date().isoformat()
        if ds >= date_str:
            break
        prior = d["close"]
    return prior


def simulate_cross_momentum(
    all_symbol_days: dict[str, dict],
    regime_labels: dict[str, str],
    *,
    top_k: int = TOP_K,
    skip_regimes: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """Run the cross-sectional momentum strategy across all dates.

    For each trading day:
      1. Compute first-candle return vs prev close for all stocks.
      2. Rank by return, pick top_k for BUY.
      3. Enter at 09:30 candle close, ATR-based SL/target.
      4. Monitor bar-by-bar for SL/target/loser-exit/square-off.
    """
    if skip_regimes is None:
        skip_regimes = set()

    # Collect all trading dates present across symbols
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

        # Regime gate
        regime = regime_labels.get(date_str)
        if regime and regime in skip_regimes:
            continue

        # Step 1: compute first-candle return for all stocks
        ranked: list[tuple[str, float, list[dict], float]] = []
        for sym, sdata in all_symbol_days.items():
            candles = sdata["days"].get(date_str)
            if not candles or len(candles) < 3:
                continue

            prev_close = _prior_close(sdata["daily"], date_str)
            if not prev_close or prev_close <= 0:
                continue

            # First 15-min candle is 09:15; second is 09:30
            # We want the return through the 09:30 candle
            if len(candles) < ENTRY_CANDLE_IDX + 1:
                continue
            entry_candle = candles[ENTRY_CANDLE_IDX]
            first_ret = (entry_candle["close"] - prev_close) / prev_close * 100

            # Need enough history for ATR
            all_candles = sdata.get("all_candles", [])
            day_start = sdata.get("boundaries", {}).get(date_str)
            if not day_start:
                continue
            start_idx = day_start[0]
            if start_idx < 14:  # need ATR warmup
                continue

            # Compute ATR from the window ending at entry candle
            atr_window = all_candles[max(0, start_idx + ENTRY_CANDLE_IDX - 50):start_idx + ENTRY_CANDLE_IDX + 1]
            atr_val = _atr(atr_window, 14)
            if atr_val <= 0:
                atr_val = entry_candle["close"] * 0.005

            ranked.append((sym, first_ret, candles, atr_val))

        if not ranked:
            continue

        # Step 2: rank by return (top = strongest BUY candidates)
        ranked.sort(key=lambda x: x[1], reverse=True)
        selected = ranked[:top_k]

        # Step 3-4: simulate trades for each selected stock
        for sym, _ret, candles, atr_val in selected:
            entry_candle = candles[ENTRY_CANDLE_IDX]
            entry_price = entry_candle["close"]
            if entry_price <= 0:
                continue

            side = "BUY"
            sl_dist = atr_val * ATR_MULT
            target_dist = sl_dist * RR_RATIO
            sl_price = entry_price - sl_dist
            target_price = entry_price + target_dist
            entry_ts = entry_candle["ts"]

            # Walk through remaining candles of the day
            exited = False
            for ci in range(ENTRY_CANDLE_IDX + 1, len(candles)):
                c = candles[ci]
                hour = c["ts"].hour
                minute = c["ts"].minute

                # Square off
                if hour * 60 + minute >= SQUARE_OFF_HOUR * 60 + SQUARE_OFF_MIN:
                    pnl_pct = (c["close"] - entry_price) / entry_price * 100
                    all_trades.append(_make_trade(
                        sym, entry_ts, c["ts"], side, entry_price,
                        c["close"], sl_price, target_price, pnl_pct,
                        "EOD_SQUARE_OFF", True))
                    exited = True
                    break

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

                # Loser exit after 13:00
                if hour >= LOSER_EXIT_HOUR and c["close"] < entry_price:
                    pnl_pct = (c["close"] - entry_price) / entry_price * 100
                    all_trades.append(_make_trade(
                        sym, entry_ts, c["ts"], side, entry_price,
                        c["close"], sl_price, target_price, pnl_pct,
                        "LOSER_EXIT_LATE", True))
                    exited = True
                    break

            if not exited:
                # Shouldn't happen if data is complete — close at last candle
                last = candles[-1]
                pnl_pct = (last["close"] - entry_price) / entry_price * 100
                all_trades.append(_make_trade(
                    sym, entry_ts, last["ts"], side, entry_price,
                    last["close"], sl_price, target_price, pnl_pct,
                    "EOD_SQUARE_OFF", True))

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
    ap = argparse.ArgumentParser(description="Phase 7.1 — Cross-Sectional Momentum backtest")
    ap.add_argument("--universe", default="NIFTY50")
    ap.add_argument("--top-k", type=int, default=TOP_K, help="Top K stocks to enter each day")
    args = ap.parse_args()

    symbols = get_universe(args.universe)
    print(f"\n  Phase 7.1 — Cross-Sectional Momentum (top-{args.top_k} by first-candle return)")
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

        # Build continuous series + boundaries (for ATR computation)
        all_candles: list[dict] = []
        boundaries: dict[str, tuple[int, int]] = {}
        for d in sorted(days.keys()):
            start_idx = len(all_candles)
            all_candles.extend(days[d])
            boundaries[d] = (start_idx, len(all_candles))

        all_symbol_days[sym] = {
            "days": days,
            "daily": daily,
            "all_candles": all_candles,
            "boundaries": boundaries,
        }

        # Also prepare for regime labeler
        regime_days = dict(days)
        regime_days["_daily"] = daily
        per_symbol_days_for_regime[sym] = regime_days

    print(f"  Loaded {len(all_symbol_days)} symbols.")

    # Label regimes
    regime_labels = label_regimes(per_symbol_days_for_regime)
    dist = defaultdict(int)
    for r in regime_labels.values():
        dist[r] += 1
    print("  Regime distribution: "
          + ", ".join(f"{r}={dist[r]}" for r in ("TREND", "RANGE", "VOLATILE")))

    # ── Run strategy across windows × regime routing ──────────
    print(f"\n  {'='*100}")
    print(f"  Walk-forward results (net of cost, ATR×{ATR_MULT}, RR {RR_RATIO}, "
          f"loser-exit {LOSER_EXIT_HOUR}:00, sq-off {SQUARE_OFF_HOUR}:00)")
    print(f"  {'='*100}")

    for win_name, (w_start, w_end) in WINDOWS.items():
        print(f"\n  ── {win_name} window ──")

        routing_configs = {
            "ALL regimes": set(),
            "Skip RANGE": {"RANGE"},
            "VOLATILE only": {"TREND", "RANGE"},
        }

        for route_name, skip in routing_configs.items():
            trades = simulate_cross_momentum(
                all_symbol_days, regime_labels,
                top_k=args.top_k,
                skip_regimes=skip,
                start=w_start, end=w_end,
            )
            m = compute_metrics(trades, f"{win_name}/{route_name}", with_costs=True)
            _print_table(f"{route_name}", m)

            # Exit reason breakdown for TEST window
            if win_name == "TEST" and m.get("by_reason"):
                reasons = m["by_reason"]
                print("    Exit reasons: " +
                      ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))

    # ── Also try SELL side (bottom K) ─────────────────────────
    print(f"\n  ── TEST window — SELL side (bottom-{args.top_k} weakest) ──")

    # Re-run with negative ranking (pick weakest for SELL)
    sell_trades = _simulate_sell_side(
        all_symbol_days, regime_labels,
        top_k=args.top_k,
        skip_regimes=set(),
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
    )
    m = compute_metrics(sell_trades, "TEST/SELL/ALL", with_costs=True)
    _print_table("SELL ALL regimes", m)

    # ── Verdict ───────────────────────────────────────────────
    print("\n  === PHASE 7.1 VERDICT ===")
    # Run TEST/ALL as the primary result
    test_trades = simulate_cross_momentum(
        all_symbol_days, regime_labels,
        top_k=args.top_k,
        skip_regimes=set(),
        start=WINDOWS["TEST"][0], end=WINDOWS["TEST"][1],
    )
    test_m = compute_metrics(test_trades, "TEST/ALL", with_costs=True)
    pf = test_m.get("pf", 0)
    if pf >= GATE_PF:
        print(f"  PASS — OOS PF {pf} >= {GATE_PF}. Candidate for dry-run validation.")
    elif pf >= 1.0:
        print(f"  MARGINAL — OOS PF {pf} (1.0 <= PF < {GATE_PF}). May benefit from ML filter.")
    else:
        print(f"  FAIL — OOS PF {pf} < 1.0. Cross-sectional momentum does not have edge.")
    print(f"  Total charges (TEST/ALL): Rs.{test_m.get('total_charges', 0):,.2f}")


def _simulate_sell_side(
    all_symbol_days: dict[str, dict],
    regime_labels: dict[str, str],
    *,
    top_k: int = TOP_K,
    skip_regimes: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """Same as cross-momentum but pick bottom-K (weakest) for SELL."""
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

        ranked: list[tuple[str, float, list[dict], float]] = []
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
            first_ret = (entry_candle["close"] - prev_close) / prev_close * 100

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
            ranked.append((sym, first_ret, candles, atr_val))

        if not ranked:
            continue

        # Pick BOTTOM K (weakest = most negative return)
        ranked.sort(key=lambda x: x[1])
        selected = ranked[:top_k]

        for sym, _ret, candles, atr_val in selected:
            entry_candle = candles[ENTRY_CANDLE_IDX]
            entry_price = entry_candle["close"]
            if entry_price <= 0:
                continue

            side = "SELL"
            sl_dist = atr_val * ATR_MULT
            target_dist = sl_dist * RR_RATIO
            sl_price = entry_price + sl_dist
            target_price = entry_price - target_dist
            entry_ts = entry_candle["ts"]

            exited = False
            for ci in range(ENTRY_CANDLE_IDX + 1, len(candles)):
                c = candles[ci]
                hour = c["ts"].hour
                minute = c["ts"].minute

                if hour * 60 + minute >= SQUARE_OFF_HOUR * 60 + SQUARE_OFF_MIN:
                    pnl_pct = (entry_price - c["close"]) / entry_price * 100
                    all_trades.append(_make_trade(
                        sym, entry_ts, c["ts"], side, entry_price,
                        c["close"], sl_price, target_price, pnl_pct,
                        "EOD_SQUARE_OFF", True))
                    exited = True
                    break

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
                pnl_pct = (entry_price - last["close"]) / entry_price * 100
                all_trades.append(_make_trade(
                    sym, entry_ts, last["ts"], side, entry_price,
                    last["close"], sl_price, target_price, pnl_pct,
                    "EOD_SQUARE_OFF", True))

    return sorted(all_trades, key=lambda t: t["entry_ts"])


if __name__ == "__main__":
    main()
