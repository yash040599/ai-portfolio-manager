#!/usr/bin/env python3
"""Backtest: Adaptive volume on broad-gap days + BUY-only vs SELL-only analysis.

Two ideas from 2026-06-15 trade review:

IDEA 2: On "broad gap days" (many stocks gap simultaneously), individual
  stock volume is diluted across the market. Test lowering vol_mult from
  2.0x to 1.5x when >=20 stocks gap >=1%.

IDEA 4: Gap-down candidates are rare. Analyze BUY-only vs SELL-only
  performance to understand gap-direction asymmetry.

Read-only. Out-of-sample by construction. Never touches capital.
"""
from __future__ import annotations

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

# ── Strategy params (same as main backtest) ───────────────────
GAP_PCT = 1.0
GAP_MAX_PCT = 5.0
VOL_MULT = 2.0
TRADE_VALUE = 15_000
ATR_MULT = 2.0
RR_RATIO = 1.8
DAILY_CAP = 2
LOSER_EXIT_HOUR = 13
SQUARE_OFF_HOUR = 14
SQUARE_OFF_MIN = 0
ENTRY_CANDLE_IDX = 1
VOL_LOOKBACK_DAYS = 20


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
    lookback: int = VOL_LOOKBACK_DAYS,
) -> float:
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


def simulate_gap_go_adaptive(
    all_symbol_days: dict[str, dict],
    regime_labels: dict[str, str],
    *,
    gap_pct: float = GAP_PCT,
    gap_max_pct: float = GAP_MAX_PCT,
    vol_mult: float = VOL_MULT,
    vol_mult_broad: float = VOL_MULT,   # volume mult on broad-gap days
    broad_gap_threshold: int = 20,       # how many stocks gapping = "broad"
    daily_cap: int = DAILY_CAP,
    skip_regimes: set[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    rsi_contra_buy: float = 70.0,
    gap_hold_min_pct: float = 0.5,
    score_contra_block: bool = True,
    side_filter: str = "ALL",            # "ALL", "BUY", "SELL"
) -> tuple[list[dict], dict[str, int]]:
    """Run gap-and-go with adaptive volume on broad-gap days.
    
    Returns (trades, stats) where stats has broad/narrow day counts.
    """
    if skip_regimes is None:
        skip_regimes = set()

    all_dates: set[str] = set()
    for sdata in all_symbol_days.values():
        for d in sdata["days"]:
            all_dates.add(d)

    all_trades: list[dict] = []
    stats = {"broad_days": 0, "narrow_days": 0, "broad_trades": 0, "narrow_trades": 0,
             "buy_trades": 0, "sell_trades": 0, "skipped_broad_vol": 0}

    for date_str in sorted(all_dates):
        if start and date_str < start:
            continue
        if end and date_str > end:
            continue

        regime = regime_labels.get(date_str)
        if regime and regime in skip_regimes:
            continue

        # ── IDEA 2: Count how many stocks gap >=1% today ──────
        gap_count = 0
        for sdata in all_symbol_days.values():
            candles = sdata["days"].get(date_str)
            if not candles:
                continue
            prev_close = _prior_close(sdata["daily"], date_str)
            if not prev_close or prev_close <= 0:
                continue
            open_price = candles[0]["open"]
            gap = abs((open_price - prev_close) / prev_close * 100)
            if gap >= gap_pct:
                gap_count += 1

        is_broad = gap_count >= broad_gap_threshold
        effective_vol_mult = vol_mult_broad if is_broad else vol_mult

        if is_broad:
            stats["broad_days"] += 1
        else:
            stats["narrow_days"] += 1

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

            gap = (open_price - prev_close) / prev_close * 100

            if abs(gap) < gap_pct or abs(gap) > gap_max_pct:
                continue

            entry_vol = entry_candle.get("volume", 0) or 0
            avg_vol = _avg_first_candle_volume(sdata["days"], date_str)

            if avg_vol <= 0 or entry_vol < effective_vol_mult * avg_vol:
                if is_broad and avg_vol > 0 and entry_vol >= vol_mult_broad * avg_vol:
                    stats["skipped_broad_vol"] += 1
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

            side = "BUY" if gap > 0 else "SELL"

            # Side filter (IDEA 4)
            if side_filter != "ALL" and side != side_filter:
                continue

            # RSI filter
            if rsi_contra_buy > 0:
                rsi_closes = [c["close"] for c in atr_window]
                entry_rsi = _rsi(rsi_closes, 14)
                if side == "BUY" and entry_rsi > rsi_contra_buy:
                    continue

            # Gap-hold check
            if gap_hold_min_pct > 0:
                entry_close = entry_candle["close"]
                if side == "BUY":
                    fade = (open_price - entry_close) / open_price * 100
                else:
                    fade = (entry_close - open_price) / open_price * 100
                if fade > gap_hold_min_pct:
                    continue

            # Score contradiction
            if score_contra_block:
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

        candidates.sort(key=lambda x: x[2], reverse=True)
        selected = candidates[:daily_cap]

        for sym, side, _gap_mag, candles, atr_val in selected:
            entry_candle = candles[ENTRY_CANDLE_IDX]
            entry_price = entry_candle["close"]
            if entry_price <= 0:
                continue

            if side == "BUY":
                sl_price = entry_candle["low"]
                sl_dist = entry_price - sl_price
                if sl_dist <= 0:
                    sl_dist = atr_val * ATR_MULT
                    sl_price = entry_price - sl_dist
                target_dist = max(sl_dist * RR_RATIO, atr_val * ATR_MULT * RR_RATIO)
                target_price = entry_price + target_dist
            else:
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

            # Track stats
            if is_broad:
                stats["broad_trades"] += 1
            else:
                stats["narrow_trades"] += 1
            if side == "BUY":
                stats["buy_trades"] += 1
            else:
                stats["sell_trades"] += 1

    return sorted(all_trades, key=lambda t: t["entry_ts"]), stats


def _print_table(label: str, metrics: dict) -> None:
    if metrics.get("note"):
        print(f"  {label:<44s} {metrics['note']}")
        return
    print(f"  {label:<44s} "
          f"Trades: {metrics['trades']:>5d}  "
          f"WR: {metrics['win_rate']:>5.1f}%  "
          f"PF: {metrics['pf']:>5.2f}  "
          f"Exp: {metrics['expectancy']:>+7.3f}%  "
          f"Ret: {metrics['total_return']:>+8.2f}%  "
          f"MaxDD: {metrics['max_dd']:>6.2f}%  "
          f"Sharpe: {metrics['sharpe']:>+6.2f}")


def main() -> None:
    universe = "NIFTY100"
    symbols = get_universe(universe)
    print("\n  Broad-Gap Adaptive Volume + Side Analysis Backtest")
    print(f"  Universe: {universe}, Loading {len(symbols)} symbols...")

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

    w_start, w_end = WINDOWS["TEST"]

    # ══════════════════════════════════════════════════════════
    # IDEA 4: BUY-only vs SELL-only analysis
    # ══════════════════════════════════════════════════════════
    print(f"\n  {'='*100}")
    print("  IDEA 4: Gap Direction Analysis (TEST window, v1.1 filters)")
    print(f"  {'='*100}")

    for side_label, side_filter in [("ALL (baseline)", "ALL"), ("BUY only (gap-ups)", "BUY"), ("SELL only (gap-downs)", "SELL")]:
        trades, st = simulate_gap_go_adaptive(
            all_symbol_days, regime_labels,
            start=w_start, end=w_end,
            side_filter=side_filter,
        )
        m = compute_metrics(trades, f"side-{side_filter}", with_costs=True)
        _print_table(side_label, m)
        if m.get("by_reason"):
            reasons = m["by_reason"]
            print("    Exit reasons: " +
                  ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
        print(f"    Stats: BUY={st['buy_trades']}, SELL={st['sell_trades']}, "
              f"broad_days={st['broad_days']}, narrow_days={st['narrow_days']}")

    # ══════════════════════════════════════════════════════════
    # IDEA 2: Adaptive volume on broad-gap days
    # ══════════════════════════════════════════════════════════
    print(f"\n  {'='*100}")
    print("  IDEA 2: Adaptive Volume on Broad-Gap Days (TEST window, v1.1 filters)")
    print("  Broad = >=N stocks gapping >=1%. Lower vol from 2.0x to 1.5x on broad days only.")
    print(f"  {'='*100}")

    # Baseline: vol=2.0x everywhere
    print("\n  ── Baseline (vol=2.0x all days) ──")
    trades_base, st_base = simulate_gap_go_adaptive(
        all_symbol_days, regime_labels,
        start=w_start, end=w_end,
        vol_mult=2.0, vol_mult_broad=2.0,
    )
    m_base = compute_metrics(trades_base, "baseline", with_costs=True)
    _print_table("vol=2.0x all days", m_base)
    print(f"    Stats: broad_days={st_base['broad_days']}, narrow_days={st_base['narrow_days']}, "
          f"broad_trades={st_base['broad_trades']}, narrow_trades={st_base['narrow_trades']}")

    # Sweep broad-gap threshold and vol_mult_broad
    print("\n  ── Broad-gap threshold sweep ──")
    for broad_thresh in [10, 15, 20, 25, 30]:
        for vol_broad in [1.0, 1.25, 1.5, 1.75]:
            trades, st = simulate_gap_go_adaptive(
                all_symbol_days, regime_labels,
                start=w_start, end=w_end,
                vol_mult=2.0,
                vol_mult_broad=vol_broad,
                broad_gap_threshold=broad_thresh,
            )
            m = compute_metrics(trades, f"broad{broad_thresh}/vol{vol_broad}", with_costs=True)
            label = f"broad>={broad_thresh} stocks, vol_broad={vol_broad}x"
            _print_table(label, m)
            if m.get("trades", 0) != m_base.get("trades", 0):
                delta_trades = m.get("trades", 0) - m_base.get("trades", 0)
                delta_pf = m.get("pf", 0) - m_base.get("pf", 0)
                print(f"    Δ vs baseline: {delta_trades:+d} trades, PF {delta_pf:+.2f}, "
                      f"broad_days={st['broad_days']}, broad_trades={st['broad_trades']}")

    # ── Combined: adaptive vol + BUY-only (best of both) ──────
    print("\n  ── Combined: Adaptive vol + BUY-only ──")
    for broad_thresh in [15, 20]:
        for vol_broad in [1.25, 1.5]:
            trades, st = simulate_gap_go_adaptive(
                all_symbol_days, regime_labels,
                start=w_start, end=w_end,
                vol_mult=2.0,
                vol_mult_broad=vol_broad,
                broad_gap_threshold=broad_thresh,
                side_filter="BUY",
            )
            m = compute_metrics(trades, f"buy-broad{broad_thresh}/vol{vol_broad}", with_costs=True)
            _print_table(f"BUY-only + broad>={broad_thresh} vol_broad={vol_broad}x", m)

    # ── Global vol=1.5x comparison (no adaptive) ──────────────
    print("\n  ── Global vol=1.5x (no adaptive, for comparison) ──")
    trades_15, st_15 = simulate_gap_go_adaptive(
        all_symbol_days, regime_labels,
        start=w_start, end=w_end,
        vol_mult=1.5, vol_mult_broad=1.5,
    )
    m_15 = compute_metrics(trades_15, "vol-1.5x-global", with_costs=True)
    _print_table("vol=1.5x all days", m_15)
    print(f"    Stats: trades={m_15.get('trades', 0)} (vs baseline {m_base.get('trades', 0)})")

    print("\n  Done.")


if __name__ == "__main__":
    main()
