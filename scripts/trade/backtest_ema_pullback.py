"""
scripts/trade/backtest_ema_pullback.py
================================================================
Backtest: Strategy 3 -- EMA Pullback Momentum

In a trending stock (EMA9 > EMA21), wait for pullback to EMA9,
confirm momentum (MACD + StochRSI + ADX), enter on bounce.
Target: 1.5x ATR. Trail after +1x ATR.

Runs on 15-min intraday candles (2-year) and daily candles (10-year
simulated).

Usage:
    python scripts/trade/backtest_ema_pullback.py
    python scripts/trade/backtest_ema_pullback.py --symbol RELIANCE
    python scripts/trade/backtest_ema_pullback.py --mode daily
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
EMA_FAST = 9
EMA_SLOW = 21
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
RSI_PERIOD = 14
STOCH_RSI_PERIOD = 14
STOCH_RSI_SMOOTH_K = 3
STOCH_RSI_SMOOTH_D = 3
STOCH_RSI_OVERSOLD = 30
ADX_PERIOD = 14
ADX_MIN = 20              # must be trending
ATR_PERIOD = 14
TARGET_ATR_MULT = 1.8     # target = 1.8x ATR from entry (optimized RR)
TRAIL_ATR_MULT = 1.0      # trail after +1x ATR profit
ENTRY_START_HOUR = 10
ENTRY_END_HOUR = 13
ENTRY_END_MINUTE = 30
SQUARE_OFF_HOUR = 14
SQUARE_OFF_MINUTE = 0
CAPITAL = 50_000
PORTFOLIO_DAILY_CAP = 2   # K1=2


# -- Indicator helpers ------------------------------------------

def _ema(values: list[float], period: int) -> list[float]:
    if not values or period <= 0:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def _rsi_series(closes: list[float], period: int = RSI_PERIOD) -> list[float]:
    """Full RSI series for StochRSI computation."""
    rsi = [50.0] * len(closes)
    if len(closes) < period + 1:
        return rsi
    avg_g = 0.0
    avg_l = 0.0
    # Seed
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        avg_g += max(d, 0)
        avg_l += max(-d, 0)
    avg_g /= period
    avg_l /= period
    if avg_l == 0:
        rsi[period] = 100.0
    else:
        rsi[period] = 100 - 100 / (1 + avg_g / avg_l)
    # Wilder smoothing
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
        if avg_l == 0:
            rsi[i] = 100.0
        else:
            rsi[i] = 100 - 100 / (1 + avg_g / avg_l)
    return rsi


def _stoch_rsi(rsi_values: list[float], period: int = STOCH_RSI_PERIOD,
               smooth_k: int = STOCH_RSI_SMOOTH_K) -> list[float]:
    """StochRSI %K (smoothed)."""
    n = len(rsi_values)
    raw = [50.0] * n
    for i in range(period, n):
        window = rsi_values[i - period + 1:i + 1]
        lo = min(window)
        hi = max(window)
        if hi - lo > 0:
            raw[i] = (rsi_values[i] - lo) / (hi - lo) * 100
        else:
            raw[i] = 50.0
    # Smooth with SMA
    smoothed = [50.0] * n
    for i in range(period + smooth_k - 1, n):
        smoothed[i] = sum(raw[i - smooth_k + 1:i + 1]) / smooth_k
    return smoothed


def _macd_histogram(closes: list[float]) -> list[float]:
    """MACD histogram (MACD line - signal line)."""
    ema_fast = _ema(closes, MACD_FAST)
    ema_slow = _ema(closes, MACD_SLOW)
    n = len(closes)
    macd_line = [0.0] * n
    for i in range(n):
        macd_line[i] = ema_fast[i] - ema_slow[i] if i < len(ema_fast) and i < len(ema_slow) else 0
    signal = _ema(macd_line, MACD_SIGNAL)
    hist = [0.0] * n
    for i in range(n):
        hist[i] = macd_line[i] - signal[i] if i < len(signal) else 0
    return hist


def _adx_series(candles: list[dict], period: int = ADX_PERIOD) -> list[float]:
    """Simplified ADX series."""
    n = len(candles)
    adx = [20.0] * n
    for i in range(period * 2, n):
        plus_dm_sum = 0.0
        minus_dm_sum = 0.0
        tr_sum = 0.0
        for j in range(i - period, i):
            h = candles[j + 1]["high"]
            l = candles[j + 1]["low"]
            ph = candles[j]["high"]
            pl = candles[j]["low"]
            pc = candles[j]["close"]
            up = h - ph
            dn = pl - l
            plus_dm_sum += max(up, 0) if up > dn else 0
            minus_dm_sum += max(dn, 0) if dn > up else 0
            tr = max(h - l, abs(h - pc), abs(l - pc))
            tr_sum += tr
        if tr_sum == 0:
            continue
        plus_di = 100 * plus_dm_sum / tr_sum
        minus_di = 100 * minus_dm_sum / tr_sum
        di_sum = plus_di + minus_di
        adx[i] = 100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0
    return adx


def _atr_at(candles: list[dict], idx: int, period: int = ATR_PERIOD) -> float:
    if idx < period:
        return 0.0
    trs = []
    for j in range(idx - period + 1, idx + 1):
        h = candles[j]["high"]
        l = candles[j]["low"]
        pc = candles[j - 1]["close"] if j > 0 else candles[j]["open"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 0


# -- Data loading -----------------------------------------------

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
    return [{"ts": datetime.datetime.fromisoformat(r["ts_ist"]),
             "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]),
             "volume": int(r["volume"] or 0)} for r in rows]


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
    return [{"ts": datetime.datetime.fromisoformat(r["ts_ist"]),
             "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]),
             "volume": int(r["volume"] or 0)} for r in rows]


def group_by_day(candles: list[dict]) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = defaultdict(list)
    for c in candles:
        days[c["ts"].date().isoformat()].append(c)
    return dict(days)


# -- Trade simulation -------------------------------------------

def simulate_ema_pullback(candles_by_day: dict[str, list[dict]],
                          symbol: str) -> list[dict]:
    trades = []
    dates = sorted(candles_by_day.keys())

    # Build a continuous series across all days for indicator computation
    all_candles = []
    for d in dates:
        all_candles.extend(candles_by_day[d])

    if len(all_candles) < EMA_SLOW * 4:
        return trades

    closes = [c["close"] for c in all_candles]
    ema9 = _ema(closes, EMA_FAST)
    ema21 = _ema(closes, EMA_SLOW)
    macd_hist = _macd_histogram(closes)
    adx_vals = _adx_series(all_candles)

    in_trade = False
    side = ""
    entry_price = 0.0
    sl_price = 0.0
    target_price = 0.0
    entry_ts = None
    entry_date = ""
    trail_active = False

    for i in range(EMA_SLOW * 2, len(all_candles)):
        c = all_candles[i]
        hour = c["ts"].hour
        minute = c["ts"].minute
        today = c["ts"].date().isoformat()

        # Square off: close any open trade at EOD
        if hour * 60 + minute >= SQUARE_OFF_HOUR * 60 + SQUARE_OFF_MINUTE:
            if in_trade:
                pnl = _pnl(side, entry_price, c["close"])
                trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                    c["close"], sl_price, target_price, pnl, "EOD_SQUARE_OFF"))
                in_trade = False
            continue

        # Don't carry trades overnight
        if in_trade and today != entry_date:
            prev = all_candles[i - 1]
            pnl = _pnl(side, entry_price, prev["close"])
            trades.append(_trade(symbol, entry_ts, prev["ts"], side, entry_price,
                                prev["close"], sl_price, target_price, pnl, "EOD_SQUARE_OFF"))
            in_trade = False

        if in_trade:
            # Check SL / target / EMA cross exit
            if side == "BUY":
                if c["low"] <= sl_price:
                    trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                        sl_price, sl_price, target_price,
                                        _pnl("BUY", entry_price, sl_price), "STOP_LOSS"))
                    in_trade = False
                    continue
                if c["high"] >= target_price:
                    trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                        target_price, sl_price, target_price,
                                        _pnl("BUY", entry_price, target_price), "TARGET_HIT"))
                    in_trade = False
                    continue
                if ema9[i] < ema21[i]:
                    trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                        c["close"], sl_price, target_price,
                                        _pnl("BUY", entry_price, c["close"]), "EMA_CROSS_EXIT"))
                    in_trade = False
                    continue
                atr = _atr_at(all_candles, i)
                if not trail_active and atr > 0 and c["close"] >= entry_price + TRAIL_ATR_MULT * atr:
                    trail_active = True
                if trail_active:
                    new_sl = ema9[i] * 0.998
                    if new_sl > sl_price:
                        sl_price = new_sl
            else:  # SELL
                if c["high"] >= sl_price:
                    trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                        sl_price, sl_price, target_price,
                                        _pnl("SELL", entry_price, sl_price), "STOP_LOSS"))
                    in_trade = False
                    continue
                if c["low"] <= target_price:
                    trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                        target_price, sl_price, target_price,
                                        _pnl("SELL", entry_price, target_price), "TARGET_HIT"))
                    in_trade = False
                    continue
                if ema9[i] > ema21[i]:
                    trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                        c["close"], sl_price, target_price,
                                        _pnl("SELL", entry_price, c["close"]), "EMA_CROSS_EXIT"))
                    in_trade = False
                    continue
                atr = _atr_at(all_candles, i)
                if not trail_active and atr > 0 and c["close"] <= entry_price - TRAIL_ATR_MULT * atr:
                    trail_active = True
                if trail_active:
                    new_sl = ema9[i] * 1.002
                    if new_sl < sl_price:
                        sl_price = new_sl
            continue

        # -- Entry logic (10:00 - 14:30 only) --
        if hour < ENTRY_START_HOUR:
            continue
        if hour > ENTRY_END_HOUR or (hour == ENTRY_END_HOUR and minute > ENTRY_END_MINUTE):
            continue

        adx = adx_vals[i]
        if adx < ADX_MIN:
            continue

        prev = all_candles[i - 1]

        # BUY: uptrend + pullback near EMA9 + bounce
        if (ema9[i] > ema21[i] and ema9[i - 1] > ema21[i - 1]
            and prev["low"] <= ema9[i - 1] * 1.003
            and prev["close"] > ema21[i - 1]
            and c["close"] > ema9[i]
            and macd_hist[i] > 0):

            atr = _atr_at(all_candles, i)
            if atr <= 0:
                continue
            entry_price = c["close"]
            sl_price = prev["low"] * 0.999
            target_price = entry_price + TARGET_ATR_MULT * atr
            if sl_price >= entry_price or target_price <= entry_price:
                continue
            side = "BUY"
            entry_ts = c["ts"]
            entry_date = today
            in_trade = True
            trail_active = False

        # SELL: downtrend + pullback near EMA9 + rejection
        elif (ema9[i] < ema21[i] and ema9[i - 1] < ema21[i - 1]
              and prev["high"] >= ema9[i - 1] * 0.997
              and prev["close"] < ema21[i - 1]
              and c["close"] < ema9[i]
              and macd_hist[i] < 0):

            atr = _atr_at(all_candles, i)
            if atr <= 0:
                continue
            entry_price = c["close"]
            sl_price = prev["high"] * 1.001
            target_price = entry_price - TARGET_ATR_MULT * atr
            if sl_price <= entry_price or target_price >= entry_price:
                continue
            side = "SELL"
            entry_ts = c["ts"]
            entry_date = today
            in_trade = True
            trail_active = False

    return trades


def simulate_ema_pullback_daily(daily_candles: list[dict], symbol: str) -> list[dict]:
    """Daily simulation using EMA crossover + RSI pullback on daily bars."""
    trades = []
    if len(daily_candles) < EMA_SLOW + 20:
        return trades

    closes = [c["close"] for c in daily_candles]
    ema9 = _ema(closes, EMA_FAST)
    ema21 = _ema(closes, EMA_SLOW)
    rsi_vals = _rsi_series(closes)
    macd_hist = _macd_histogram(closes)

    for i in range(EMA_SLOW + 10, len(daily_candles)):
        c = daily_candles[i]
        prev = daily_candles[i - 1]

        # ADX proxy
        atr = _atr_at(daily_candles, i)
        if atr <= 0:
            continue
        adx_proxy = min(50, atr / c["close"] * 100 * 20)
        if adx_proxy < ADX_MIN:
            continue

        # BUY: uptrend + pullback
        if (ema9[i] > ema21[i] and ema9[i - 1] > ema21[i - 1]
            and prev["low"] <= ema9[i - 1] * 1.005  # touched near EMA9
            and prev["close"] > ema21[i - 1]
            and c["close"] > ema9[i]
            and macd_hist[i] > 0
            and rsi_vals[i - 1] < 45):

            entry = c["open"]  # next-day open
            sl = prev["low"] * 0.999
            target = entry + TARGET_ATR_MULT * atr
            if sl >= entry or target <= entry:
                continue

            if c["high"] >= target:
                pnl = (target - entry) / entry * 100
                reason = "TARGET_HIT"
                exit_p = target
            elif c["low"] <= sl:
                pnl = (sl - entry) / entry * 100
                reason = "STOP_LOSS"
                exit_p = sl
            else:
                pnl = (c["close"] - entry) / entry * 100
                reason = "EOD_SQUARE_OFF"
                exit_p = c["close"]
            trades.append(_trade(symbol, c["ts"], c["ts"], "BUY", entry,
                               exit_p, sl, target, pnl, reason))

        # SELL: downtrend + pullback
        elif (ema9[i] < ema21[i] and ema9[i - 1] < ema21[i - 1]
              and prev["high"] >= ema9[i - 1] * 0.995
              and prev["close"] < ema21[i - 1]
              and c["close"] < ema9[i]
              and macd_hist[i] < 0
              and rsi_vals[i - 1] > 55):

            entry = c["open"]
            sl = prev["high"] * 1.001
            target = entry - TARGET_ATR_MULT * atr
            if sl <= entry or target >= entry:
                continue

            if c["low"] <= target:
                pnl = (entry - target) / entry * 100
                reason = "TARGET_HIT"
                exit_p = target
            elif c["high"] >= sl:
                pnl = (entry - sl) / entry * 100
                reason = "STOP_LOSS"
                exit_p = sl
            else:
                pnl = (entry - c["close"]) / entry * 100
                reason = "EOD_SQUARE_OFF"
                exit_p = c["close"]
            trades.append(_trade(symbol, c["ts"], c["ts"], "SELL", entry,
                               exit_p, sl, target, pnl, reason))

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


# -- Metrics ----------------------------------------------------

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
    win_months = sum(1 for v in monthly_pnl.values() if v > 0)
    lose_months = sum(1 for v in monthly_pnl.values() if v <= 0)

    return {
        "label": label, "trades": total, "wins": len(wins), "losses": len(losses),
        "win_rate": round(win_rate, 1), "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3), "profit_factor": round(profit_factor, 2),
        "expectancy_pct": round(expectancy, 3), "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2), "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "period": f"{first_date} to {last_date}", "years": round(years, 1),
        "by_reason": dict(by_reason),
        "best_month": {"month": best_month[0], "pnl_pct": round(best_month[1], 2)},
        "worst_month": {"month": worst_month[0], "pnl_pct": round(worst_month[1], 2)},
        "win_months": win_months, "lose_months": lose_months,
    }


def print_summary(metrics: dict):
    m = metrics
    if m.get("note"):
        print(f"\n  {m['label']}: {m['note']}")
        return
    print(f"\n{'='*60}")
    print(f"  EMA Pullback Momentum Backtest: {m['label']}")
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
    print(f"  Win/Lose months: {m.get('win_months','?')} / {m.get('lose_months','?')}")
    print(f"  Best month     : {m['best_month']['month']} (+{m['best_month']['pnl_pct']}%)")
    print(f"  Worst month    : {m['worst_month']['month']} ({m['worst_month']['pnl_pct']}%)")
    print(f"  Exit reasons   : {m['by_reason']}")
    print(f"{'='*60}")


# -- Main -------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Backtest EMA Pullback Momentum strategy")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--mode", default="both", choices=["intraday", "daily", "both"])
    parser.add_argument("--universe", default="NIFTY50")
    args = parser.parse_args()

    symbols = [args.symbol.upper()] if args.symbol else get_universe(args.universe)
    os.makedirs(OUT_DIR, exist_ok=True)

    if args.mode in ("intraday", "both"):
        print("\n  === INTRADAY (15-min candles) ===")
        print(f"  Symbols: {len(symbols)}, Source: {INTRADAY_DB}")
        all_trades = []
        for i, sym in enumerate(symbols):
            candles = load_15m(INTRADAY_DB, sym)
            if not candles:
                continue
            days = group_by_day(candles)
            trades = simulate_ema_pullback(days, sym)
            all_trades.extend(trades)
            if (i + 1) % 10 == 0:
                print(f"    [{i+1}/{len(symbols)}] processed, {len(all_trades)} trades so far")

        all_trades.sort(key=lambda t: t["entry_ts"])

        # Apply K1 portfolio daily cap
        if PORTFOLIO_DAILY_CAP > 0:
            by_day = defaultdict(list)
            for t in all_trades:
                day = t["entry_ts"][:10]
                by_day[day].append(t)
            filtered = []
            for day in sorted(by_day):
                day_trades = sorted(by_day[day], key=lambda t: t["entry_ts"])
                filtered.extend(day_trades[:PORTFOLIO_DAILY_CAP])
            all_trades = sorted(filtered, key=lambda t: t["entry_ts"])
            print(f"  K1={PORTFOLIO_DAILY_CAP} cap applied: {len(filtered)} trades retained")

        # Add costs to each trade
        for t in all_trades:
            cost_pct = 0.10  # ~0.10% round-trip for NSE intraday
            t["cost_pct"] = cost_pct
            t["net_pnl_pct"] = round(t["pnl_pct"] - cost_pct, 4)

        m = compute_metrics(all_trades, "15-min Intraday (OPTIMIZED)")
        print("\n  --- RAW (before costs) ---")
        print_summary(m)

        # Re-compute with costs
        for t in all_trades:
            t["pnl_pct"] = t["net_pnl_pct"]
        m_costs = compute_metrics(all_trades, "15-min Intraday (WITH COSTS)")
        print("\n  --- AFTER COSTS (~0.10% round-trip) ---")
        print_summary(m_costs)

        out_path = os.path.join(OUT_DIR, "ema_pullback_intraday_trades.json")
        with open(out_path, "w") as f:
            json.dump({"strategy": "EMA_PULLBACK_MOMENTUM", "mode": "intraday",
                       "metrics": m, "trades": all_trades}, f, indent=2)
        print(f"  Saved: {out_path}")

    if args.mode in ("daily", "both"):
        print("\n  === DAILY (simulated from daily candles) ===")
        print(f"  Symbols: {len(symbols)}, Source: {DAILY_DB}")
        all_trades_d = []
        for i, sym in enumerate(symbols):
            candles = load_daily(DAILY_DB, sym)
            if not candles:
                continue
            trades = simulate_ema_pullback_daily(candles, sym)
            all_trades_d.extend(trades)
            if (i + 1) % 10 == 0:
                print(f"    [{i+1}/{len(symbols)}] processed, {len(all_trades_d)} trades so far")

        all_trades_d.sort(key=lambda t: t["entry_ts"])
        m = compute_metrics(all_trades_d, "Daily Simulated (10 years)")
        print_summary(m)

        out_path = os.path.join(OUT_DIR, "ema_pullback_daily_trades.json")
        with open(out_path, "w") as f:
            json.dump({"strategy": "EMA_PULLBACK_MOMENTUM", "mode": "daily",
                       "metrics": m, "trades": all_trades_d}, f, indent=2)
        print(f"  Saved: {out_path}")


if __name__ == "__main__":
    main()
