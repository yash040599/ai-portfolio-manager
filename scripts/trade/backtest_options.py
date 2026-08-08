#!/usr/bin/env python3
"""Options Mode — Directional NIFTY Option Buying Backtest (v1.0).

STRATEGY: "Regime-Gated Directional Option Buying"
  - Buy ATM/OTM NIFTY weekly call or put based on market regime + trend
  - Entry after first 15-min candle (09:30 IST equivalent)
  - SL: 30% of premium paid
  - Target: 75% gain on premium
  - Hard square-off: same day (no overnight hold)
  - Skip RANGE days (theta-heavy, directionless)

EDGE HYPOTHESIS:
  On VOLATILE/TREND days, NIFTY moves 0.5-2.0% intraday. An ATM option
  with delta ~0.50 converts this to 25-100% premium move. With regime
  filtering, we avoid the 39% of days that are RANGE (where theta eats
  the buyer alive). The asymmetric payoff (lose 30%, gain 75%) combined
  with regime routing produces a positive edge.

OPTION PREMIUM MODEL (synthetic — no historical option data available):
  We use Black-Scholes-inspired synthetic premium:
  - ATM option premium ≈ 0.4 × sigma_daily × NIFTY_price × sqrt(DTE/365)
  - Delta(ATM) ≈ 0.50 (by definition)
  - Theta per day ≈ premium / (2 × DTE)
  - Intraday premium change ≈ delta × NIFTY_move + theta_decay
  This is a conservative model — actual ATM premiums on NIFTY are well-
  approximated by this when VIX is 12-18.

DATA: Uses NIFTY 50 daily candles from backtest DB (509 days, 2024-2026).
Since we don't have intraday NIFTY candles, we use daily OHLC to simulate:
  - Entry at open (09:30 proxy)
  - Intraday high/low for SL/target checks
  - Exit at close if neither SL nor target hit

WALK-FORWARD: Train on first half, test on second half (OOS).

Usage:
    python scripts/trade/backtest_options.py
    python scripts/trade/backtest_options.py --sl-pct 25 --target-pct 100
    python scripts/trade/backtest_options.py --window TEST
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest_gates import DAILY_DB  # noqa: E402

# ── Walk-forward windows ──────────────────────────────────────
WINDOWS = {
    "FULL":  (None, None),
    "TRAIN": ("2024-05-01", "2025-05-31"),
    "TEST":  ("2025-06-01", "2026-05-22"),
}

# ── Strategy parameters ──────────────────────────────────────
SL_PCT = 30.0          # Stop-loss: lose 30% of premium
TARGET_PCT = 75.0      # Target: gain 75% on premium
CAPITAL_PER_TRADE = 10_000  # Rs per trade (premium cost)
LOT_SIZE = 25          # NIFTY lot size
DTE_MIN = 1            # Min days to expiry
DTE_MAX = 7            # Max DTE (weekly)
STRIKE_OFFSET = 0      # 0 = ATM, 1 = 1 strike OTM
NIFTY_STRIKE_STEP = 50
VIX_MAX = 25.0         # Skip when VIX proxy > 25
SQUARE_OFF = True      # Always exit same day

# ── Regime skip ───────────────────────────────────────────────
SKIP_REGIMES = {"RANGE"}  # Only trade on VOLATILE + TREND days

# ── NSE Option charges (per lot, buy + sell round-trip) ───────
# STT on sell side: 0.0625% of premium × qty
# Exchange txn charges: 0.053% of premium × qty  (both legs)
# SEBI fee: 0.0001% of turnover
# GST on brokerage: 18% of Rs.40 (flat brokerage per order) = Rs.7.2
# Stamp duty: 0.003% of buy side premium × qty
# ── NSE Option charges ────────────────────────────────────────
# Rates live in option_pricing.py so the directional and condor
# backtests cannot drift apart. See compute_option_charges() below.
BROKERAGE_PER_ORDER = 20.0  # Zerodha flat fee per order


def compute_option_charges(buy_premium: float, sell_premium: float,
                           qty: int) -> float:
    """Total charges for one option round-trip.

    Delegates to option_pricing.Leg so there is a single charge model in
    the repo. The local copy this replaced still used the pre-Oct-2024 STT
    rate of 0.0625% and taxed GST on brokerage only, so it under-charged
    every trade in the v1.0 result.
    """
    from option_pricing import Leg, leg_charges
    return round(leg_charges(
        Leg("CE", 0.0, "BUY", buy_premium, sell_premium, qty)), 2)


def synthetic_premium(nifty_price: float, vol_pct: float,
                      dte: int = 5) -> float:
    """Estimate ATM option premium using Brenner-Subrahmanyam (1988) approximation.

    For ATM options: premium ≈ 0.4 × σ_annual × S × sqrt(T)
    where σ_annual = annualised volatility (decimal), T = DTE/365.

    At NIFTY 24000, vol 15%, DTE 5: premium ≈ 168 — matches real market.
    """
    if nifty_price <= 0 or vol_pct <= 0 or dte <= 0:
        return 0.0
    annual_vol = vol_pct / 100  # Convert % to decimal
    T = dte / 365
    premium = 0.4 * annual_vol * nifty_price * math.sqrt(T)
    return round(premium, 2)


def estimate_vol(daily_candles: list[dict], lookback: int = 20) -> float:
    """Estimate annualised volatility from daily candles (% scale).
    Uses Parkinson high-low estimator (more efficient than close-close).
    """
    if len(daily_candles) < lookback:
        return 15.0  # default 15% annual vol

    recent = daily_candles[-lookback:]
    hl_vars = []
    for c in recent:
        h, l = c["high"], c["low"]
        if l > 0 and h > l:
            hl_vars.append(math.log(h / l) ** 2)

    if not hl_vars:
        return 15.0

    # Parkinson estimator: sigma² = (1/4ln2) × E[ln(H/L)²]
    parkinson_var = sum(hl_vars) / (4 * math.log(2) * len(hl_vars))
    daily_vol = math.sqrt(parkinson_var)
    annual_vol = daily_vol * math.sqrt(252) * 100  # % scale
    return round(annual_vol, 2)


def load_nifty_daily() -> list[dict]:
    """Load NIFTY 50 daily candles from backtest DB."""
    import sqlite3
    conn = sqlite3.connect(DAILY_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ts_ist, open, high, low, close, volume "
        "FROM candles WHERE symbol='NIFTY 50' ORDER BY ts_ist"
    ).fetchall()
    conn.close()

    candles = []
    for r in rows:
        ts = r["ts_ist"]
        # Parse date
        if "T" in ts:
            date_str = ts.split("T")[0]
        else:
            date_str = ts[:10]
        candles.append({
            "date": date_str,
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"] or 0,
        })
    return candles


def classify_day(candle: dict, prev_close: float,
                 recent_candles: list[dict] | None = None) -> tuple[str, str]:
    """Classify a trading day into (regime, trend_direction).

    Uses ONLY information available at market open (no lookahead):
    - Gap from previous close → direction signal
    - Recent 5-day average range → volatility regime proxy

    Returns:
        regime: "VOLATILE" | "TREND" | "RANGE"
        direction: "BULLISH" | "BEARISH" | "NEUTRAL"
    """
    o = candle["open"]

    # Direction from gap (available at open — no lookahead)
    gap_pct = (o - prev_close) / prev_close * 100 if prev_close > 0 else 0

    if gap_pct > 0.3:
        direction = "BULLISH"
    elif gap_pct < -0.3:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    # Regime from recent 5-day average intraday range (no lookahead —
    # uses only completed prior days)
    avg_range = 0.0
    if recent_candles and len(recent_candles) >= 3:
        ranges = []
        for rc in recent_candles[-5:]:
            if rc["open"] > 0:
                ranges.append((rc["high"] - rc["low"]) / rc["open"] * 100)
        if ranges:
            avg_range = sum(ranges) / len(ranges)

    if avg_range > 1.3:
        regime = "VOLATILE"
    elif abs(gap_pct) > 0.3 and avg_range > 0.8:
        regime = "TREND"
    else:
        regime = "RANGE"

    return regime, direction


def simulate_option_day(
    candle: dict,
    prev_close: float,
    vol_pct: float,
    direction: str,
    dte: int = 5,
    sl_pct: float = SL_PCT,
    target_pct: float = TARGET_PCT,
) -> dict | None:
    """Simulate one day of option buying using daily OHLC.

    Models:
    - Entry premium at open (synthetic)
    - Intraday premium movement from NIFTY high/low/close
    - SL/target checks against premium movement
    - Theta decay for intraday (small — ~1/5 of daily theta for DTE=5)

    Returns trade dict or None if no trade signal.
    """
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    date = candle["date"]

    if direction not in ("BULLISH", "BEARISH"):
        return None

    # ── Synthetic premium at entry ────────────────────────────
    entry_premium = synthetic_premium(o, vol_pct, dte)
    if entry_premium <= 0:
        return None

    # ── Delta (ATM ≈ 0.50, slightly OTM ≈ 0.40-0.45) ────────
    delta = 0.50 if STRIKE_OFFSET == 0 else 0.42

    # ── Intraday theta decay (fraction of daily theta) ────────
    # Daily theta ≈ premium / (2 × DTE) for ATM
    # Intraday = ~40% of daily (market is open 6.25 hrs of 24)
    daily_theta = entry_premium / (2 * max(dte, 1))
    intraday_theta = daily_theta * 0.40  # 40% of daily theta

    # ── Simulate premium movement from NIFTY OHLC ────────────
    # For CE (bullish): premium goes UP when NIFTY goes UP
    # For PE (bearish): premium goes UP when NIFTY goes DOWN
    option_type = "CE" if direction == "BULLISH" else "PE"

    if option_type == "CE":
        # Best case: NIFTY hits high → premium peaks
        best_nifty_move = (h - o) / o * 100  # % move up
        # Worst case: NIFTY hits low → premium drops
        worst_nifty_move = (l - o) / o * 100  # % move down (negative)
        # Close: final P&L
        close_nifty_move = (c - o) / o * 100
    else:  # PE
        # Best case: NIFTY hits low → premium peaks (for puts)
        best_nifty_move = (o - l) / o * 100  # % move down (positive for PE)
        worst_nifty_move = (o - h) / o * 100  # % move up (negative for PE)
        close_nifty_move = (o - c) / o * 100

    # Premium change ≈ delta × NIFTY_% × entry_price_per_point
    # Simplified: premium_change% ≈ delta × nifty_move% × (nifty / premium)
    premium_at_best = entry_premium + delta * best_nifty_move / 100 * o - intraday_theta
    premium_at_worst = entry_premium + delta * worst_nifty_move / 100 * o - intraday_theta
    premium_at_close = entry_premium + delta * close_nifty_move / 100 * o - intraday_theta

    # Floor premiums at 0 (can't go negative)
    premium_at_best = max(premium_at_best, 0)
    premium_at_worst = max(premium_at_worst, 0)
    premium_at_close = max(premium_at_close, 0)

    # ── SL / Target levels ────────────────────────────────────
    sl_level = entry_premium * (1 - sl_pct / 100)
    target_level = entry_premium * (1 + target_pct / 100)

    # ── Check SL/target hits ──────────────────────────────────
    # Conservative: check worst first (SL hit before target on same bar)
    exit_premium = premium_at_close
    exit_reason = "SQUARE_OFF"

    if premium_at_worst <= sl_level:
        exit_premium = sl_level
        exit_reason = "SL"
    elif premium_at_best >= target_level:
        exit_premium = target_level
        exit_reason = "TARGET"

    # ── Position sizing ───────────────────────────────────────
    lots = max(1, int(CAPITAL_PER_TRADE / (entry_premium * LOT_SIZE)))
    qty = lots * LOT_SIZE

    # ── P&L calculation ───────────────────────────────────────
    gross_pnl = (exit_premium - entry_premium) * qty
    charges = compute_option_charges(entry_premium, exit_premium, qty)
    net_pnl = round(gross_pnl - charges, 2)

    return {
        "date": date,
        "option_type": option_type,
        "direction": direction,
        "nifty_open": o,
        "nifty_close": c,
        "nifty_high": h,
        "nifty_low": l,
        "entry_premium": round(entry_premium, 2),
        "exit_premium": round(exit_premium, 2),
        "sl_level": round(sl_level, 2),
        "target_level": round(target_level, 2),
        "lots": lots,
        "qty": qty,
        "exit_reason": exit_reason,
        "gross_pnl": round(gross_pnl, 2),
        "charges": charges,
        "net_pnl": net_pnl,
        "vol_pct": vol_pct,
        "dte": dte,
        "delta": delta,
        "theta_decay": round(intraday_theta, 2),
    }


def run_backtest(
    start: str | None = None,
    end: str | None = None,
    sl_pct: float = SL_PCT,
    target_pct: float = TARGET_PCT,
    skip_regimes: set[str] | None = None,
    dte: int = 5,
    strike_offset: int = 0,
    verbose: bool = False,
) -> dict:
    """Run the full options backtest on NIFTY daily data."""
    global STRIKE_OFFSET
    STRIKE_OFFSET = strike_offset

    if skip_regimes is None:
        skip_regimes = SKIP_REGIMES

    candles = load_nifty_daily()
    if not candles:
        print("ERROR: No NIFTY 50 daily candles found in backtest DB.")
        return {}

    # Filter date range
    if start:
        candles = [c for c in candles if c["date"] >= start]
    if end:
        candles = [c for c in candles if c["date"] <= end]

    print(f"  Loaded {len(candles)} NIFTY 50 daily candles")
    print(f"  Date range: {candles[0]['date']} → {candles[-1]['date']}")
    print(f"  SL: {sl_pct}% | Target: {target_pct}% | DTE: {dte}")
    print(f"  Skip regimes: {skip_regimes or 'none'}")
    print()

    trades: list[dict] = []
    regime_counts: dict[str, int] = defaultdict(int)
    regime_trades: dict[str, list] = defaultdict(list)
    skipped_range = 0
    skipped_neutral = 0

    for i in range(1, len(candles)):
        candle = candles[i]
        prev_close = candles[i - 1]["close"]

        # Classify regime and direction (no lookahead — uses only prior data)
        recent = candles[max(0, i - 5):i]
        regime, direction = classify_day(candle, prev_close, recent)
        regime_counts[regime] += 1

        # Skip filtered regimes
        if regime in skip_regimes:
            skipped_range += 1
            continue

        # Skip neutral direction
        if direction == "NEUTRAL":
            skipped_neutral += 1
            continue

        # Estimate volatility from recent history
        lookback_start = max(0, i - 20)
        vol = estimate_vol(candles[lookback_start:i])

        # Days to expiry (approximate — assume mid-week entry)
        actual_dte = max(dte, DTE_MIN)

        trade = simulate_option_day(
            candle, prev_close, vol, direction,
            dte=actual_dte, sl_pct=sl_pct, target_pct=target_pct,
        )
        if trade:
            trade["regime"] = regime
            trades.append(trade)
            regime_trades[regime].append(trade)

    # ── Compute metrics ───────────────────────────────────────
    result = _compute_result(trades, candles, regime_counts,
                             regime_trades, skipped_range,
                             skipped_neutral, sl_pct, target_pct, dte)

    if verbose:
        _print_report(result)

    return result


def _compute_result(
    trades, candles, regime_counts, regime_trades,
    skipped_range, skipped_neutral, sl_pct, target_pct, dte,
) -> dict:
    """Compute aggregate metrics from trade list."""
    if not trades:
        return {
            "total_trades": 0, "error": "No trades generated",
            "params": {"sl_pct": sl_pct, "target_pct": target_pct, "dte": dte},
        }

    net_pnls = [t["net_pnl"] for t in trades]
    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p < 0]
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0

    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")
    win_rate = round(len(wins) / len(trades) * 100, 1)
    avg_win = round(statistics.mean(wins), 2) if wins else 0
    avg_loss = round(statistics.mean(losses), 2) if losses else 0
    total_pnl = round(sum(net_pnls), 2)
    total_charges = round(sum(t["charges"] for t in trades), 2)

    # Sharpe ratio (daily returns)
    if len(net_pnls) > 1:
        mean_r = statistics.mean(net_pnls)
        std_r = statistics.stdev(net_pnls)
        sharpe = round(mean_r / std_r * math.sqrt(252), 2) if std_r > 0 else 0
    else:
        sharpe = 0

    # Max drawdown
    cumulative = 0
    peak = 0
    max_dd = 0
    for pnl in net_pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # Exit reason breakdown
    from collections import Counter
    exit_reasons = Counter(t["exit_reason"] for t in trades)

    # Per-regime breakdown
    regime_stats = {}
    for regime, rtrades in regime_trades.items():
        rpnl = [t["net_pnl"] for t in rtrades]
        rwins = [p for p in rpnl if p > 0]
        rlosses = [p for p in rpnl if p < 0]
        rgp = sum(rwins) if rwins else 0
        rgl = abs(sum(rlosses)) if rlosses else 0
        regime_stats[regime] = {
            "trades": len(rtrades),
            "win_rate": round(len(rwins) / len(rtrades) * 100, 1) if rtrades else 0,
            "pf": round(rgp / rgl, 2) if rgl > 0 else float("inf"),
            "total_pnl": round(sum(rpnl), 2),
        }

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "profit_factor": pf,
        "sharpe": sharpe,
        "total_pnl": total_pnl,
        "total_charges": total_charges,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_drawdown": round(max_dd, 2),
        "exit_reasons": dict(exit_reasons),
        "regime_counts": dict(regime_counts),
        "regime_stats": regime_stats,
        "skipped_range": skipped_range,
        "skipped_neutral": skipped_neutral,
        "date_range": f"{trades[0]['date']} → {trades[-1]['date']}",
        "params": {
            "sl_pct": sl_pct,
            "target_pct": target_pct,
            "dte": dte,
            "capital_per_trade": CAPITAL_PER_TRADE,
            "lot_size": LOT_SIZE,
            "skip_regimes": list(SKIP_REGIMES),
        },
        "trades": trades,
    }


def _print_report(result: dict):
    """Print human-readable backtest report."""
    print("=" * 70)
    print("  OPTIONS BACKTEST REPORT — Directional NIFTY Buying v1.0")
    print("=" * 70)
    print()

    params = result.get("params", {})
    print("  Strategy:    Regime-Gated Directional Option Buying")
    print(f"  SL:          {params.get('sl_pct')}% of premium")
    print(f"  Target:      {params.get('target_pct')}% gain on premium")
    print(f"  DTE:         {params.get('dte')} days")
    print(f"  Capital:     Rs.{params.get('capital_per_trade', 0):,} per trade")
    print(f"  Lot size:    {params.get('lot_size', 25)}")
    print(f"  Skip:        {params.get('skip_regimes', [])}")
    print(f"  Date range:  {result.get('date_range', '-')}")
    print()

    print(f"  {'Metric':<25} {'Value':>15}")
    print(f"  {'-' * 25} {'-' * 15}")
    print(f"  {'Total trades':<25} {result['total_trades']:>15,}")
    print(f"  {'Wins':<25} {result['wins']:>15,}")
    print(f"  {'Losses':<25} {result['losses']:>15,}")
    print(f"  {'Win rate':<25} {result['win_rate']:>14.1f}%")
    print(f"  {'Profit factor':<25} {result['profit_factor']:>15.2f}")
    print(f"  {'Sharpe ratio':<25} {result['sharpe']:>15.2f}")
    total_pnl = result["total_pnl"]
    total_chg = result["total_charges"]
    gross_p = result["gross_profit"]
    gross_l = result["gross_loss"]
    avg_w = result["avg_win"]
    avg_l = result["avg_loss"]
    max_dd = result["max_drawdown"]
    print(f"  {'Total P&L':<25} Rs.{total_pnl:>+14,.2f}")
    print(f"  {'Total charges':<25} Rs.{total_chg:>14,.2f}")
    print(f"  {'Gross profit':<25} Rs.{gross_p:>14,.2f}")
    print(f"  {'Gross loss':<25} Rs.{gross_l:>14,.2f}")
    print(f"  {'Avg win':<25} Rs.{avg_w:>+14,.2f}")
    print(f"  {'Avg loss':<25} Rs.{avg_l:>+14,.2f}")
    print(f"  {'Max drawdown':<25} Rs.{max_dd:>14,.2f}")
    print()

    # Exit reasons
    print("  Exit Breakdown:")
    for reason, count in sorted(result.get("exit_reasons", {}).items()):
        pct = count / result["total_trades"] * 100
        print(f"    {reason:<20} {count:>5} ({pct:.1f}%)")
    print()

    # Regime breakdown
    print("  Regime Distribution (all days):")
    for regime, count in sorted(result.get("regime_counts", {}).items()):
        print(f"    {regime:<12} {count:>5} days")
    print(f"    Skipped RANGE:   {result.get('skipped_range', 0)}")
    print(f"    Skipped NEUTRAL: {result.get('skipped_neutral', 0)}")
    print()

    regime_stats = result.get("regime_stats", {})
    if regime_stats:
        print("  Per-Regime Performance:")
        print(f"    {'Regime':<12} {'Trades':>7} {'WR':>7} {'PF':>7} {'P&L':>12}")
        for regime, stats in sorted(regime_stats.items()):
            rpnl = stats["total_pnl"]
            print(
                f"    {regime:<12} {stats['trades']:>7} "
                f"{stats['win_rate']:>6.1f}% "
                f"{stats['pf']:>6.2f} "
                f"Rs.{rpnl:>+10,.0f}"
            )
    print()
    print("=" * 70)

    # ── Verdict ───────────────────────────────────────────────
    pf = result["profit_factor"]
    if pf >= 1.15:
        verdict = f"PASS — PF {pf:.2f} ≥ 1.15 gate. Proceed to dry-run."
    elif pf >= 1.0:
        verdict = f"MARGINAL — PF {pf:.2f} is positive but below 1.15 gate."
    else:
        verdict = f"FAIL — PF {pf:.2f} < 1.0. Strategy loses money after costs."
    print(f"  VERDICT: {verdict}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Options backtest — directional NIFTY buying")
    parser.add_argument("--window", choices=["FULL", "TRAIN", "TEST"], default="FULL")
    parser.add_argument("--sl-pct", type=float, default=SL_PCT)
    parser.add_argument("--target-pct", type=float, default=TARGET_PCT)
    parser.add_argument("--dte", type=int, default=5)
    parser.add_argument("--strike-offset", type=int, default=0, help="0=ATM, 1=1-strike OTM")
    parser.add_argument("--sweep", action="store_true", help="Sweep SL/target params")
    parser.add_argument("--all-regimes", action="store_true", help="Don't skip any regime")
    args = parser.parse_args()

    start, end = WINDOWS[args.window]
    skip = set() if args.all_regimes else SKIP_REGIMES

    print()
    print(f"  Options Backtest — Window: {args.window}")
    print(f"  {'=' * 50}")
    print()

    if args.sweep:
        # Sweep SL/target combinations
        print(f"  {'SL%':>5} {'TGT%':>5} {'Trades':>7} {'WR':>6} {'PF':>6} "
              f"{'Sharpe':>7} {'P&L':>12} {'MaxDD':>10}")
        print(f"  {'-' * 5} {'-' * 5} {'-' * 7} {'-' * 6} {'-' * 6} "
              f"{'-' * 7} {'-' * 12} {'-' * 10}")

        best_pf = 0
        best_params = {}

        for sl in [20, 25, 30, 35, 40]:
            for tgt in [50, 60, 75, 100, 125, 150]:
                r = run_backtest(
                    start=start, end=end, sl_pct=sl, target_pct=tgt,
                    skip_regimes=skip, dte=args.dte,
                    strike_offset=args.strike_offset,
                )
                if not r.get("total_trades"):
                    continue
                pf = r["profit_factor"]
                marker = " ★" if pf >= 1.15 else ""
                rpnl = r["total_pnl"]
                rdd = r["max_drawdown"]
                print(
                    f"  {sl:>5} {tgt:>5} {r['total_trades']:>7} "
                    f"{r['win_rate']:>5.1f}% {pf:>5.2f} "
                    f"{r['sharpe']:>6.2f} "
                    f"Rs.{rpnl:>+10,.0f} "
                    f"Rs.{rdd:>8,.0f}"
                    f"{marker}"
                )
                if pf > best_pf:
                    best_pf = pf
                    best_params = {"sl": sl, "tgt": tgt}

        print()
        print(f"  Best: SL {best_params.get('sl')}% / Target {best_params.get('tgt')}% → PF {best_pf:.2f}")
        print()

        # Run best with full report
        if best_params:
            print("  Running best params with full report...")
            print()
            run_backtest(
                start=start, end=end,
                sl_pct=best_params["sl"], target_pct=best_params["tgt"],
                skip_regimes=skip, dte=args.dte,
                strike_offset=args.strike_offset,
                verbose=True,
            )
    else:
        # Single run
        result = run_backtest(
            start=start, end=end,
            sl_pct=args.sl_pct, target_pct=args.target_pct,
            skip_regimes=skip, dte=args.dte,
            strike_offset=args.strike_offset,
            verbose=True,
        )

        # Save results
        os.makedirs(os.path.join(PROJECT_ROOT, "reports", "backtest"), exist_ok=True)
        out_path = os.path.join(
            PROJECT_ROOT, "reports", "backtest",
            f"options_bt_{args.window.lower()}.json",
        )
        # Remove trades from JSON (too large)
        save_result = {k: v for k, v in result.items() if k != "trades"}
        with open(out_path, "w") as f:
            json.dump(save_result, f, indent=2, default=str)
        print(f"\n  Results saved: {out_path}")

    # Run all windows
    if not args.sweep and args.window == "FULL":
        print("\n\n  ── Walk-Forward: TRAIN vs TEST ──")
        for wname in ["TRAIN", "TEST"]:
            ws, we = WINDOWS[wname]
            r = run_backtest(
                start=ws, end=we,
                sl_pct=args.sl_pct, target_pct=args.target_pct,
                skip_regimes=skip, dte=args.dte,
                strike_offset=args.strike_offset,
            )
            if r.get("total_trades", 0) > 0:
                print(
                    f"    {wname:<6} | Trades: {r['total_trades']:>4} | "
                    f"WR: {r['win_rate']:>5.1f}% | PF: {r['profit_factor']:>5.2f} | "
                    f"Sharpe: {r['sharpe']:>5.2f} | "
                    f"P&L: Rs.{r['total_pnl']:>+10,.0f}"
                )
            else:
                print(f"    {wname:<6} | No trades")
        print()


if __name__ == "__main__":
    main()
