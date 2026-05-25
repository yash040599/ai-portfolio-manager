"""
scripts/trade/backtest_orb15.py
================================================================
Backtest: Strategy 2 -- ORB-15 (Opening Range Breakout)

First 15-min candle (9:15-9:30) defines the range. Breakout above
ORB-High or below ORB-Low with volume confirmation triggers entry.
Partial profit at 1.5x range, trail remainder.

Runs on 15-min intraday candles (2-year) and daily candles (10-year
simulated).

Usage:
    python scripts/trade/backtest_orb15.py
    python scripts/trade/backtest_orb15.py --symbol RELIANCE
    python scripts/trade/backtest_orb15.py --mode daily
================================================================
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
except ImportError:
    pass

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from shared.nifty_universe import get_universe  # noqa: E402

# -- Data paths ------------------------------------------------
BT_DATA = os.path.join(os.path.dirname(PROJECT_ROOT), "ai-portfolio-backtest-data", "candles")
INTRADAY_DB = os.path.join(BT_DATA, "intraday_15m.sqlite")
DAILY_DB = os.path.join(BT_DATA, "daily.sqlite")
OUT_DIR = os.path.join(PROJECT_ROOT, "reports", "backtest")

# -- Strategy parameters ----------------------------------------
BREAKOUT_WINDOW_END_HOUR = 10    # breakout must happen by 10:15
BREAKOUT_WINDOW_END_MIN = 15
VOLUME_MULT = 1.5                # breakout candle volume > 1.5x avg
ORB_MAX_RANGE_PCT = 1.5          # skip if ORB range > 1.5% (risk too big)
TARGET1_MULT = 1.5               # first target = 1.5x ORB range
TARGET2_MULT = 2.5               # second target = 2.5x ORB range
PARTIAL_EXIT_PCT = 0.5           # exit 50% at T1
SQUARE_OFF_HOUR = 15
SQUARE_OFF_MINUTE = 5
CAPITAL = 50_000
RISK_PCT = 0.01

# EMA for trend confirmation (computed on 5-min equivalent = every candle)
EMA_FAST = 9


# -- Indicator helpers ------------------------------------------

def _ema(values: list[float], period: int) -> list[float]:
    if not values or period <= 0:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def _atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs[-period:]) / period


# -- Data loading (same as VWAP MR) ----------------------------

def load_15m(db_path: str, symbol: str) -> list[dict]:
    if not os.path.isfile(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ts_ist, open, high, low, close, volume FROM candles "
        "WHERE symbol=? AND interval='15minute' ORDER BY ts_ist",
        (symbol,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            ts = datetime.datetime.fromisoformat(r["ts_ist"])
        except ValueError:
            continue
        out.append({
            "ts": ts, "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": int(r["volume"] or 0),
        })
    return out


def load_daily(db_path: str, symbol: str) -> list[dict]:
    if not os.path.isfile(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ts_ist, open, high, low, close, volume FROM candles "
        "WHERE symbol=? AND interval='day' ORDER BY ts_ist",
        (symbol,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            ts = datetime.datetime.fromisoformat(r["ts_ist"])
        except ValueError:
            continue
        out.append({
            "ts": ts, "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": int(r["volume"] or 0),
        })
    return out


def group_by_day(candles: list[dict]) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = defaultdict(list)
    for c in candles:
        days[c["ts"].date().isoformat()].append(c)
    return dict(days)


# -- Avg volume for RVOL check ---------------------------------

def compute_avg_volume_per_slot(candles_by_day: dict[str, list[dict]],
                                lookback: int = 20) -> dict[str, float]:
    """Average volume of the first candle (9:15) over last N days."""
    dates = sorted(candles_by_day.keys())
    avg = {}
    for i, d in enumerate(dates):
        start = max(0, i - lookback)
        window = dates[start:i] if i > 0 else dates[:1]
        vols = []
        for dd in window:
            day_c = candles_by_day[dd]
            if day_c:
                # Sum volume of first 1-2 candles (opening range)
                vols.append(sum(c["volume"] for c in day_c[:2]))
        avg[d] = sum(vols) / len(vols) if vols else 1
    return avg


# -- Trade simulation -------------------------------------------

def simulate_orb15(candles_by_day: dict[str, list[dict]],
                   avg_or_volumes: dict[str, float],
                   symbol: str) -> list[dict]:
    """Run ORB-15 strategy on intraday candles."""
    trades = []
    dates = sorted(candles_by_day.keys())

    for date_str in dates:
        day_candles = candles_by_day[date_str]
        if len(day_candles) < 5:
            continue

        # First candle = ORB (9:15-9:30)
        orb = day_candles[0]
        orb_high = orb["high"]
        orb_low = orb["low"]
        orb_range = orb_high - orb_low
        mid = (orb_high + orb_low) / 2

        if mid <= 0:
            continue

        orb_range_pct = orb_range / mid * 100
        if orb_range_pct > ORB_MAX_RANGE_PCT:
            continue  # range too wide, risk too big
        if orb_range < 0.01:
            continue  # flat open, no range

        avg_or_vol = avg_or_volumes.get(date_str, 1)

        # Compute EMA(9) on closes for trend confirmation
        closes = [c["close"] for c in day_candles]
        ema9 = _ema(closes, EMA_FAST)

        in_trade = False
        side = ""
        entry_price = 0.0
        sl_price = 0.0
        t1_price = 0.0
        t2_price = 0.0
        entry_ts = None
        partial_exited = False
        trail_sl = 0.0
        entry_qty_factor = 1.0  # 1.0 = full, 0.5 = after partial

        for i in range(1, len(day_candles)):
            c = day_candles[i]
            hour = c["ts"].hour
            minute = c["ts"].minute

            # Square off time
            if hour >= SQUARE_OFF_HOUR or (hour == SQUARE_OFF_HOUR - 1 and minute >= SQUARE_OFF_MINUTE):
                if in_trade:
                    pnl_pct = _pnl(side, entry_price, c["close"]) * entry_qty_factor
                    trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                        c["close"], sl_price, t1_price, pnl_pct, "EOD_SQUARE_OFF"))
                    in_trade = False
                break

            if in_trade:
                # Check SL
                if side == "BUY":
                    if c["low"] <= sl_price:
                        pnl_pct = _pnl("BUY", entry_price, sl_price) * entry_qty_factor
                        trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                            sl_price, sl_price, t1_price, pnl_pct, "STOP_LOSS"))
                        in_trade = False
                        continue

                    # T1: partial exit
                    if not partial_exited and c["high"] >= t1_price:
                        partial_pnl = _pnl("BUY", entry_price, t1_price) * PARTIAL_EXIT_PCT
                        trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                            t1_price, sl_price, t1_price, partial_pnl, "TARGET1_PARTIAL"))
                        partial_exited = True
                        entry_qty_factor = 1.0 - PARTIAL_EXIT_PCT
                        # Move SL to breakeven
                        sl_price = entry_price
                        trail_sl = entry_price
                        continue

                    # T2: full exit
                    if partial_exited and c["high"] >= t2_price:
                        pnl_pct = _pnl("BUY", entry_price, t2_price) * entry_qty_factor
                        trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                            t2_price, sl_price, t2_price, pnl_pct, "TARGET2_FULL"))
                        in_trade = False
                        continue

                    # Trail SL using EMA(9) after partial exit
                    if partial_exited and i < len(ema9):
                        new_sl = ema9[i] * 0.998  # just below EMA
                        if new_sl > sl_price:
                            sl_price = new_sl

                else:  # SELL
                    if c["high"] >= sl_price:
                        pnl_pct = _pnl("SELL", entry_price, sl_price) * entry_qty_factor
                        trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                            sl_price, sl_price, t1_price, pnl_pct, "STOP_LOSS"))
                        in_trade = False
                        continue

                    if not partial_exited and c["low"] <= t1_price:
                        partial_pnl = _pnl("SELL", entry_price, t1_price) * PARTIAL_EXIT_PCT
                        trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                            t1_price, sl_price, t1_price, partial_pnl, "TARGET1_PARTIAL"))
                        partial_exited = True
                        entry_qty_factor = 1.0 - PARTIAL_EXIT_PCT
                        sl_price = entry_price
                        continue

                    if partial_exited and c["low"] <= t2_price:
                        pnl_pct = _pnl("SELL", entry_price, t2_price) * entry_qty_factor
                        trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                            t2_price, sl_price, t2_price, pnl_pct, "TARGET2_FULL"))
                        in_trade = False
                        continue

                    if partial_exited and i < len(ema9):
                        new_sl = ema9[i] * 1.002
                        if new_sl < sl_price:
                            sl_price = new_sl

                continue

            # -- Entry logic (only 9:30 to 10:15) --
            if hour > BREAKOUT_WINDOW_END_HOUR:
                continue
            if hour == BREAKOUT_WINDOW_END_HOUR and minute > BREAKOUT_WINDOW_END_MIN:
                continue

            breakout_vol = c["volume"]
            vol_ok = breakout_vol > VOLUME_MULT * avg_or_vol if avg_or_vol > 0 else True

            # BUY breakout: close above ORB high
            if c["close"] > orb_high and vol_ok:
                if i < len(ema9) and c["close"] > ema9[i]:
                    entry_price = c["close"]
                    sl_price = orb_low
                    t1_price = entry_price + TARGET1_MULT * orb_range
                    t2_price = entry_price + TARGET2_MULT * orb_range
                    side = "BUY"
                    entry_ts = c["ts"]
                    in_trade = True
                    partial_exited = False
                    entry_qty_factor = 1.0

            # SELL breakout: close below ORB low
            elif c["close"] < orb_low and vol_ok:
                if i < len(ema9) and c["close"] < ema9[i]:
                    entry_price = c["close"]
                    sl_price = orb_high
                    t1_price = entry_price - TARGET1_MULT * orb_range
                    t2_price = entry_price - TARGET2_MULT * orb_range
                    side = "SELL"
                    entry_ts = c["ts"]
                    in_trade = True
                    partial_exited = False
                    entry_qty_factor = 1.0

    return trades


def simulate_orb15_daily(daily_candles: list[dict], symbol: str) -> list[dict]:
    """Simplified daily simulation of ORB. Uses open-to-close as
    a proxy for directional breakout days."""
    trades = []
    if len(daily_candles) < 25:
        return trades

    for i in range(20, len(daily_candles)):
        c = daily_candles[i]
        prev = daily_candles[i - 1]

        # Estimate ORB range from prev day's last-hour range as proxy
        # (rough: use 0.3% of prev close as typical ORB range)
        orb_range_est = prev["close"] * 0.003
        if orb_range_est < 0.5:
            continue

        # ATR for context
        atr = _atr(daily_candles[max(0, i-15):i+1])

        # Volume check: today's volume > 1.5x 20-day avg
        avg_vol = sum(daily_candles[j]["volume"] for j in range(i-20, i)) / 20
        vol_ok = c["volume"] > VOLUME_MULT * avg_vol if avg_vol > 0 else True

        if not vol_ok:
            continue

        # Bullish breakout day: gap up or strong open-to-close
        day_move = (c["close"] - c["open"]) / c["open"] * 100
        day_range = (c["high"] - c["low"]) / c["open"] * 100

        if day_range > ORB_MAX_RANGE_PCT * 2:  # very wide day, skip
            continue

        if day_move > 0.5:  # bullish breakout day
            entry = c["open"] + orb_range_est  # breakout above ORB
            sl = c["open"] - orb_range_est
            t1 = entry + TARGET1_MULT * orb_range_est * 2
            if c["high"] >= t1:
                pnl_pct = (t1 - entry) / entry * 100
                reason = "TARGET_HIT"
                exit_p = t1
            elif c["low"] <= sl:
                pnl_pct = (sl - entry) / entry * 100
                reason = "STOP_LOSS"
                exit_p = sl
            else:
                pnl_pct = (c["close"] - entry) / entry * 100
                reason = "EOD_SQUARE_OFF"
                exit_p = c["close"]
            trades.append(_trade(symbol, c["ts"], c["ts"], "BUY", entry,
                               exit_p, sl, t1, pnl_pct, reason))

        elif day_move < -0.5:  # bearish breakout day
            entry = c["open"] - orb_range_est
            sl = c["open"] + orb_range_est
            t1 = entry - TARGET1_MULT * orb_range_est * 2
            if c["low"] <= t1:
                pnl_pct = (entry - t1) / entry * 100
                reason = "TARGET_HIT"
                exit_p = t1
            elif c["high"] >= sl:
                pnl_pct = (entry - sl) / entry * 100
                reason = "STOP_LOSS"
                exit_p = sl
            else:
                pnl_pct = (entry - c["close"]) / entry * 100
                reason = "EOD_SQUARE_OFF"
                exit_p = c["close"]
            trades.append(_trade(symbol, c["ts"], c["ts"], "SELL", entry,
                               exit_p, sl, t1, pnl_pct, reason))

    return trades


def _pnl(side: str, entry: float, exit_p: float) -> float:
    if side == "BUY":
        return (exit_p - entry) / entry * 100
    return (entry - exit_p) / entry * 100


def _trade(symbol, entry_ts, exit_ts, side, entry, exit_price, sl, target, pnl_pct, reason):
    return {
        "symbol": symbol,
        "entry_ts": entry_ts.isoformat() if hasattr(entry_ts, "isoformat") else str(entry_ts),
        "exit_ts": exit_ts.isoformat() if hasattr(exit_ts, "isoformat") else str(exit_ts),
        "side": side,
        "entry": round(entry, 2),
        "exit": round(exit_price, 2),
        "sl": round(sl, 2),
        "target": round(target, 2),
        "pnl_pct": round(pnl_pct, 3),
        "reason": reason,
    }


# -- Metrics (reuse from VWAP MR) ------------------------------

def compute_metrics(trades: list[dict], label: str) -> dict:
    if not trades:
        return {"label": label, "trades": 0, "note": "No trades generated"}

    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total = len(pnls)
    win_rate = len(wins) / total * 100
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    expectancy = sum(pnls) / total

    equity = [0.0]
    for p in pnls:
        equity.append(equity[-1] + p)

    peak = equity[0]
    max_dd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > max_dd:
            max_dd = dd

    total_return = equity[-1]
    first_date = trades[0]["entry_ts"][:10]
    last_date = trades[-1]["exit_ts"][:10]
    try:
        d1 = datetime.date.fromisoformat(first_date)
        d2 = datetime.date.fromisoformat(last_date)
        years = max((d2 - d1).days / 365.25, 0.1)
    except ValueError:
        years = 1.0

    end_value = CAPITAL * (1 + total_return / 100)
    cagr = ((end_value / CAPITAL) ** (1 / years) - 1) * 100 if end_value > 0 else -100

    if len(pnls) > 1:
        mean_pnl = sum(pnls) / len(pnls)
        std_pnl = (sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)) ** 0.5
        trades_per_year = total / years
        sharpe = (mean_pnl / std_pnl * math.sqrt(trades_per_year)) if std_pnl > 0 else 0
    else:
        sharpe = 0

    by_reason = defaultdict(int)
    for t in trades:
        by_reason[t["reason"]] += 1

    monthly_pnl = defaultdict(float)
    for t in trades:
        month = t["entry_ts"][:7]
        monthly_pnl[month] += t["pnl_pct"]

    best_month = max(monthly_pnl.items(), key=lambda x: x[1]) if monthly_pnl else ("N/A", 0)
    worst_month = min(monthly_pnl.items(), key=lambda x: x[1]) if monthly_pnl else ("N/A", 0)

    # Winning months vs losing months
    win_months = sum(1 for v in monthly_pnl.values() if v > 0)
    lose_months = sum(1 for v in monthly_pnl.values() if v <= 0)

    return {
        "label": label,
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3),
        "profit_factor": round(profit_factor, 2),
        "expectancy_pct": round(expectancy, 3),
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "period": f"{first_date} to {last_date}",
        "years": round(years, 1),
        "by_reason": dict(by_reason),
        "best_month": {"month": best_month[0], "pnl_pct": round(best_month[1], 2)},
        "worst_month": {"month": worst_month[0], "pnl_pct": round(worst_month[1], 2)},
        "win_months": win_months,
        "lose_months": lose_months,
    }


def print_summary(metrics: dict):
    m = metrics
    if m.get("note"):
        print(f"\n  {m['label']}: {m['note']}")
        return

    print(f"\n{'='*60}")
    print(f"  ORB-15 Breakout Backtest: {m['label']}")
    print(f"{'='*60}")
    print(f"  Period         : {m['period']} ({m['years']} years)")
    print(f"  Total trades   : {m['trades']}")
    print(f"  Wins / Losses  : {m['wins']} / {m['losses']}")
    print(f"  Win rate       : {m['win_rate']}%")
    print(f"  Avg win        : +{m['avg_win_pct']}%")
    print(f"  Avg loss       : {m['avg_loss_pct']}%")
    print(f"  Profit factor  : {m['profit_factor']}")
    print(f"  Expectancy     : {m['expectancy_pct']}% per trade")
    print(f"  Total return   : {m['total_return_pct']}%")
    print(f"  CAGR           : {m['cagr_pct']}%")
    print(f"  Max drawdown   : {m['max_drawdown_pct']}%")
    print(f"  Sharpe ratio   : {m['sharpe_ratio']}")
    print(f"  Win/Lose months: {m.get('win_months', '?')} / {m.get('lose_months', '?')}")
    print(f"  Best month     : {m['best_month']['month']} (+{m['best_month']['pnl_pct']}%)")
    print(f"  Worst month    : {m['worst_month']['month']} ({m['worst_month']['pnl_pct']}%)")
    print(f"  Exit reasons   : {m['by_reason']}")
    print(f"{'='*60}")


# -- Main -------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Backtest ORB-15 Breakout strategy")
    parser.add_argument("--symbol", default=None, help="Single symbol (default: all NIFTY 50)")
    parser.add_argument("--mode", default="both", choices=["intraday", "daily", "both"])
    parser.add_argument("--universe", default="NIFTY50")
    args = parser.parse_args()

    symbols = [args.symbol.upper()] if args.symbol else get_universe(args.universe)
    os.makedirs(OUT_DIR, exist_ok=True)

    # -- Intraday (15-min) ------------------------------------
    if args.mode in ("intraday", "both"):
        print(f"\n  === INTRADAY (15-min candles) ===")
        print(f"  Symbols: {len(symbols)}, Source: {INTRADAY_DB}")
        all_trades = []
        for i, sym in enumerate(symbols):
            candles = load_15m(INTRADAY_DB, sym)
            if not candles:
                continue
            days = group_by_day(candles)
            avg_vols = compute_avg_volume_per_slot(days)
            trades = simulate_orb15(days, avg_vols, sym)
            all_trades.extend(trades)
            if (i + 1) % 10 == 0:
                print(f"    [{i+1}/{len(symbols)}] processed, {len(all_trades)} trades so far")

        all_trades.sort(key=lambda t: t["entry_ts"])
        metrics_15m = compute_metrics(all_trades, "15-min Intraday (2 years)")
        print_summary(metrics_15m)

        out_path = os.path.join(OUT_DIR, "orb15_intraday_trades.json")
        with open(out_path, "w") as f:
            json.dump({"strategy": "ORB_15_BREAKOUT", "mode": "intraday",
                       "metrics": metrics_15m, "trades": all_trades}, f, indent=2)
        print(f"  Saved: {out_path}")

    # -- Daily -------------------------------------------------
    if args.mode in ("daily", "both"):
        print(f"\n  === DAILY (simulated intraday from daily candles) ===")
        print(f"  Symbols: {len(symbols)}, Source: {DAILY_DB}")
        all_trades_d = []
        for i, sym in enumerate(symbols):
            candles = load_daily(DAILY_DB, sym)
            if not candles:
                continue
            trades = simulate_orb15_daily(candles, sym)
            all_trades_d.extend(trades)
            if (i + 1) % 10 == 0:
                print(f"    [{i+1}/{len(symbols)}] processed, {len(all_trades_d)} trades so far")

        all_trades_d.sort(key=lambda t: t["entry_ts"])
        metrics_daily = compute_metrics(all_trades_d, "Daily Simulated (10 years)")
        print_summary(metrics_daily)

        out_path = os.path.join(OUT_DIR, "orb15_daily_trades.json")
        with open(out_path, "w") as f:
            json.dump({"strategy": "ORB_15_BREAKOUT", "mode": "daily",
                       "metrics": metrics_daily, "trades": all_trades_d}, f, indent=2)
        print(f"  Saved: {out_path}")


if __name__ == "__main__":
    main()
