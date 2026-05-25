"""
scripts/trade/backtest_vwap_mr.py
================================================================
Backtest: Strategy 1 — VWAP Mean-Reversion (Rubber Band)

Runs on 15-min intraday candles (2-year high-fidelity) and daily
candles (10-year simulated). Outputs per-trade JSON + summary
metrics (CAGR, Sharpe, max drawdown, win rate, profit factor).

Usage:
    python scripts/trade/backtest_vwap_mr.py
    python scripts/trade/backtest_vwap_mr.py --symbol RELIANCE
    python scripts/trade/backtest_vwap_mr.py --mode daily
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

# ── Data paths ────────────────────────────────────────────────
BT_DATA = os.path.join(os.path.dirname(PROJECT_ROOT), "ai-portfolio-backtest-data", "candles")
INTRADAY_DB = os.path.join(BT_DATA, "intraday_15m.sqlite")
DAILY_DB = os.path.join(BT_DATA, "daily.sqlite")
OUT_DIR = os.path.join(PROJECT_ROOT, "reports", "backtest")

# ── Strategy parameters ──────────────────────────────────────
VWAP_BAND_ENTRY = 1.5     # entry at +/- 1.5 sigma
VWAP_BAND_SL = 2.0        # SL at +/- 2.0 sigma
RSI_PERIOD = 14
RSI_BUY_MAX = 35           # RSI < 35 for buy
RSI_SELL_MIN = 65           # RSI > 65 for sell
ADX_PERIOD = 14
ADX_MAX = 25                # only trade when ADX < 25 (range-bound)
RVOL_MIN = 0.8
ENTRY_START_HOUR = 10       # 10:00 IST
ENTRY_END_HOUR = 14         # 14:00 IST
SQUARE_OFF_HOUR = 15
SQUARE_OFF_MINUTE = 5
CAPITAL = 50_000
RISK_PCT = 0.01             # 1% risk per trade


# ── Indicator helpers ─────────────────────────────────────────

def compute_vwap_intraday(candles: list[dict]) -> list[dict]:
    """Add vwap, vwap_std, upper_1_5, lower_1_5, upper_2, lower_2 to each candle."""
    cum_tp_vol = 0.0
    cum_vol = 0
    cum_tp2_vol = 0.0
    for c in candles:
        tp = (c["high"] + c["low"] + c["close"]) / 3
        vol = c["volume"] or 1
        cum_tp_vol += tp * vol
        cum_vol += vol
        cum_tp2_vol += tp * tp * vol
        vwap = cum_tp_vol / cum_vol if cum_vol else tp
        variance = (cum_tp2_vol / cum_vol - vwap * vwap) if cum_vol else 0
        std = math.sqrt(max(0, variance))
        c["vwap"] = vwap
        c["vwap_std"] = std
        c["vwap_upper_1_5"] = vwap + VWAP_BAND_ENTRY * std
        c["vwap_lower_1_5"] = vwap - VWAP_BAND_ENTRY * std
        c["vwap_upper_2"] = vwap + VWAP_BAND_SL * std
        c["vwap_lower_2"] = vwap - VWAP_BAND_SL * std
    return candles


def compute_rsi(candles: list[dict], period: int = RSI_PERIOD) -> list[dict]:
    """Add rsi to each candle."""
    for i, c in enumerate(candles):
        if i < period:
            c["rsi"] = 50.0
            continue
        gains, losses = [], []
        for j in range(i - period, i):
            diff = candles[j + 1]["close"] - candles[j]["close"]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_g = sum(gains) / period
        avg_l = sum(losses) / period
        if avg_l == 0:
            c["rsi"] = 100.0
        else:
            rs = avg_g / avg_l
            c["rsi"] = 100 - 100 / (1 + rs)
    return candles


def compute_adx(candles: list[dict], period: int = ADX_PERIOD) -> list[dict]:
    """Add adx to each candle. Simplified Wilder's ADX."""
    for i in range(len(candles)):
        if i < period * 2:
            candles[i]["adx"] = 20.0  # neutral default
            continue
        # Compute +DM, -DM, TR over window
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
            candles[i]["adx"] = 20.0
            continue
        plus_di = 100 * plus_dm_sum / tr_sum
        minus_di = 100 * minus_dm_sum / tr_sum
        di_sum = plus_di + minus_di
        if di_sum == 0:
            candles[i]["adx"] = 0.0
        else:
            dx = 100 * abs(plus_di - minus_di) / di_sum
            candles[i]["adx"] = dx
    return candles


def compute_avg_volume(candles_by_day: dict[str, list[dict]], lookback: int = 20) -> dict[str, float]:
    """Average daily total volume over last N days for RVOL."""
    dates = sorted(candles_by_day.keys())
    avg = {}
    for i, d in enumerate(dates):
        start = max(0, i - lookback)
        window = dates[start:i] if i > 0 else dates[:1]
        vols = [sum(c["volume"] for c in candles_by_day[dd]) for dd in window]
        avg[d] = sum(vols) / len(vols) if vols else 1
    return avg


# ── Data loading ──────────────────────────────────────────────

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


# ── Group candles by trading day ──────────────────────────────

def group_by_day(candles: list[dict]) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = defaultdict(list)
    for c in candles:
        days[c["ts"].date().isoformat()].append(c)
    return dict(days)


# ── Trade simulation ──────────────────────────────────────────

def simulate_vwap_mr(candles_by_day: dict[str, list[dict]],
                     avg_volumes: dict[str, float],
                     symbol: str) -> list[dict]:
    """Run VWAP MR strategy on intraday candles. Returns list of trades."""
    trades = []
    dates = sorted(candles_by_day.keys())

    for date_str in dates:
        day_candles = candles_by_day[date_str]
        if len(day_candles) < RSI_PERIOD + 5:
            continue

        # Compute indicators for this day
        compute_vwap_intraday(day_candles)
        compute_rsi(day_candles)
        compute_adx(day_candles)

        avg_vol = avg_volumes.get(date_str, 1)
        day_vol = sum(c["volume"] for c in day_candles)
        rvol = day_vol / avg_vol if avg_vol > 0 else 1.0

        in_trade = False
        entry_price = 0.0
        sl_price = 0.0
        target_price = 0.0
        side = ""
        entry_ts = None

        for i, c in enumerate(day_candles):
            hour = c["ts"].hour

            # Square off
            if hour >= SQUARE_OFF_HOUR or (hour == SQUARE_OFF_HOUR - 1 and c["ts"].minute >= SQUARE_OFF_MINUTE):
                if in_trade:
                    pnl_pct = ((c["close"] - entry_price) / entry_price * 100) if side == "BUY" else ((entry_price - c["close"]) / entry_price * 100)
                    trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price, c["close"], sl_price, target_price, pnl_pct, "EOD_SQUARE_OFF"))
                    in_trade = False
                continue

            # Check exit first
            if in_trade:
                if side == "BUY":
                    if c["low"] <= sl_price:
                        pnl_pct = (sl_price - entry_price) / entry_price * 100
                        trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price, sl_price, sl_price, target_price, pnl_pct, "STOP_LOSS"))
                        in_trade = False
                        continue
                    if c["high"] >= target_price:
                        pnl_pct = (target_price - entry_price) / entry_price * 100
                        trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price, target_price, sl_price, target_price, pnl_pct, "TARGET_HIT"))
                        in_trade = False
                        continue
                    # Trail: if price crossed VWAP, tighten SL to VWAP - 0.1%
                    if c["close"] >= c.get("vwap", 0):
                        new_sl = c["vwap"] * 0.999
                        if new_sl > sl_price:
                            sl_price = new_sl
                else:  # SELL
                    if c["high"] >= sl_price:
                        pnl_pct = (entry_price - sl_price) / entry_price * 100
                        trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price, sl_price, sl_price, target_price, pnl_pct, "STOP_LOSS"))
                        in_trade = False
                        continue
                    if c["low"] <= target_price:
                        pnl_pct = (entry_price - target_price) / entry_price * 100
                        trades.append(_trade(symbol, entry_ts, c["ts"], side, entry_price, target_price, sl_price, target_price, pnl_pct, "TARGET_HIT"))
                        in_trade = False
                        continue
                    if c["close"] <= c.get("vwap", 0):
                        new_sl = c["vwap"] * 1.001
                        if new_sl < sl_price:
                            sl_price = new_sl
                continue

            # Entry conditions (only between 10:00-14:00, not in trade)
            if hour < ENTRY_START_HOUR or hour >= ENTRY_END_HOUR:
                continue
            if i < RSI_PERIOD + 2:
                continue

            rsi = c.get("rsi", 50)
            adx = c.get("adx", 30)
            vwap = c.get("vwap", 0)
            lower_band = c.get("vwap_lower_1_5", 0)
            upper_band = c.get("vwap_upper_1_5", 0)
            sl_lower = c.get("vwap_lower_2", 0)
            sl_upper = c.get("vwap_upper_2", 0)

            if adx >= ADX_MAX:
                continue
            if rvol < RVOL_MIN:
                continue

            # BUY signal: price at lower band, RSI oversold
            if c["close"] <= lower_band and rsi < RSI_BUY_MAX and vwap > 0:
                entry_price = c["close"]
                sl_price = sl_lower
                target_price = vwap  # target = VWAP (mean)
                if sl_price >= entry_price or target_price <= entry_price:
                    continue
                side = "BUY"
                entry_ts = c["ts"]
                in_trade = True

            # SELL signal: price at upper band, RSI overbought
            elif c["close"] >= upper_band and rsi > RSI_SELL_MIN and vwap > 0:
                entry_price = c["close"]
                sl_price = sl_upper
                target_price = vwap
                if sl_price <= entry_price or target_price >= entry_price:
                    continue
                side = "SELL"
                entry_ts = c["ts"]
                in_trade = True

    return trades


def simulate_vwap_mr_daily(daily_candles: list[dict], symbol: str) -> list[dict]:
    """Simplified daily simulation: use daily H/L/C to estimate
    intraday VWAP stretch opportunities. Less precise but covers
    8 more years."""
    trades = []
    if len(daily_candles) < 30:
        return trades

    for i in range(20, len(daily_candles)):
        c = daily_candles[i]
        # Estimate VWAP as 20-day average of typical price
        tp_sum = sum((daily_candles[j]["high"] + daily_candles[j]["low"] + daily_candles[j]["close"]) / 3
                     for j in range(i - 20, i))
        vwap_est = tp_sum / 20

        # Estimate std from 20-day range
        ranges = [(daily_candles[j]["high"] - daily_candles[j]["low"]) / daily_candles[j]["close"]
                  for j in range(i - 20, i)]
        avg_range = sum(ranges) / len(ranges)
        std_est = vwap_est * avg_range * 0.5  # rough estimate

        # RSI
        closes = [daily_candles[j]["close"] for j in range(i - RSI_PERIOD, i + 1)]
        rsi = _calc_rsi(closes)

        # ADX proxy: use 14-day average range / ATR ratio
        atr_14 = sum(max(daily_candles[j]["high"] - daily_candles[j]["low"],
                        abs(daily_candles[j]["high"] - daily_candles[j - 1]["close"]),
                        abs(daily_candles[j]["low"] - daily_candles[j - 1]["close"]))
                     for j in range(i - 14, i)) / 14
        adx_proxy = min(50, atr_14 / c["close"] * 100 * 20)  # rough ADX proxy

        if adx_proxy >= ADX_MAX:
            continue

        tp = (c["high"] + c["low"] + c["close"]) / 3
        lower_band = vwap_est - VWAP_BAND_ENTRY * std_est
        upper_band = vwap_est + VWAP_BAND_ENTRY * std_est

        # BUY: low touched lower band, RSI < 35
        if c["low"] <= lower_band and rsi < RSI_BUY_MAX:
            entry = lower_band
            sl = vwap_est - VWAP_BAND_SL * std_est
            target = vwap_est
            if sl >= entry or target <= entry:
                continue
            # Did it hit target or SL that day?
            if c["high"] >= target:
                pnl_pct = (target - entry) / entry * 100
                reason = "TARGET_HIT"
            elif c["low"] <= sl:
                pnl_pct = (sl - entry) / entry * 100
                reason = "STOP_LOSS"
            else:
                pnl_pct = (c["close"] - entry) / entry * 100
                reason = "EOD_SQUARE_OFF"
            trades.append(_trade(symbol, c["ts"], c["ts"], "BUY", entry, 
                               target if reason == "TARGET_HIT" else (sl if reason == "STOP_LOSS" else c["close"]),
                               sl, target, pnl_pct, reason))

        # SELL: high touched upper band, RSI > 65
        elif c["high"] >= upper_band and rsi > RSI_SELL_MIN:
            entry = upper_band
            sl = vwap_est + VWAP_BAND_SL * std_est
            target = vwap_est
            if sl <= entry or target >= entry:
                continue
            if c["low"] <= target:
                pnl_pct = (entry - target) / entry * 100
                reason = "TARGET_HIT"
            elif c["high"] >= sl:
                pnl_pct = (entry - sl) / entry * 100
                reason = "STOP_LOSS"
            else:
                pnl_pct = (entry - c["close"]) / entry * 100
                reason = "EOD_SQUARE_OFF"
            trades.append(_trade(symbol, c["ts"], c["ts"], "SELL",
                               entry, target if reason == "TARGET_HIT" else (sl if reason == "STOP_LOSS" else c["close"]),
                               sl, target, pnl_pct, reason))

    return trades


def _calc_rsi(closes: list[float], period: int = RSI_PERIOD) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


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


# ── Metrics ───────────────────────────────────────────────────

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

    # Cumulative equity curve for max drawdown + CAGR
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

    # Approximate CAGR from cumulative return %
    # Get date range
    first_date = trades[0]["entry_ts"][:10]
    last_date = trades[-1]["exit_ts"][:10]
    try:
        d1 = datetime.date.fromisoformat(first_date)
        d2 = datetime.date.fromisoformat(last_date)
        years = max((d2 - d1).days / 365.25, 0.1)
    except ValueError:
        years = 1.0

    # CAGR: if starting with CAPITAL and total return is cumulative %
    end_value = CAPITAL * (1 + total_return / 100)
    cagr = ((end_value / CAPITAL) ** (1 / years) - 1) * 100 if end_value > 0 else -100

    # Sharpe ratio (annualized, daily pnl% as proxy)
    if len(pnls) > 1:
        mean_pnl = sum(pnls) / len(pnls)
        std_pnl = (sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)) ** 0.5
        # Approximate trades per year
        trades_per_year = total / years
        sharpe = (mean_pnl / std_pnl * math.sqrt(trades_per_year)) if std_pnl > 0 else 0
    else:
        sharpe = 0

    # By exit reason
    by_reason = defaultdict(int)
    for t in trades:
        by_reason[t["reason"]] += 1

    # By month
    monthly_pnl = defaultdict(float)
    for t in trades:
        month = t["entry_ts"][:7]
        monthly_pnl[month] += t["pnl_pct"]

    best_month = max(monthly_pnl.items(), key=lambda x: x[1]) if monthly_pnl else ("N/A", 0)
    worst_month = min(monthly_pnl.items(), key=lambda x: x[1]) if monthly_pnl else ("N/A", 0)

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
    }


def print_summary(metrics: dict):
    m = metrics
    if m.get("note"):
        print(f"\n  {m['label']}: {m['note']}")
        return

    print(f"\n{'='*60}")
    print(f"  VWAP Mean-Reversion Backtest: {m['label']}")
    print(f"{'='*60}")
    print(f"  Period        : {m['period']} ({m['years']} years)")
    print(f"  Total trades  : {m['trades']}")
    print(f"  Wins / Losses : {m['wins']} / {m['losses']}")
    print(f"  Win rate      : {m['win_rate']}%")
    print(f"  Avg win       : +{m['avg_win_pct']}%")
    print(f"  Avg loss      : {m['avg_loss_pct']}%")
    print(f"  Profit factor : {m['profit_factor']}")
    print(f"  Expectancy    : {m['expectancy_pct']}% per trade")
    print(f"  Total return  : {m['total_return_pct']}%")
    print(f"  CAGR          : {m['cagr_pct']}%")
    print(f"  Max drawdown  : {m['max_drawdown_pct']}%")
    print(f"  Sharpe ratio  : {m['sharpe_ratio']}")
    print(f"  Best month    : {m['best_month']['month']} (+{m['best_month']['pnl_pct']}%)")
    print(f"  Worst month   : {m['worst_month']['month']} ({m['worst_month']['pnl_pct']}%)")
    print(f"  Exit reasons  : {m['by_reason']}")
    print(f"{'='*60}")


# ── Main ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backtest VWAP Mean-Reversion strategy")
    parser.add_argument("--symbol", default=None, help="Single symbol (default: all NIFTY 50)")
    parser.add_argument("--mode", default="both", choices=["intraday", "daily", "both"],
                        help="Data mode: intraday (15-min), daily, or both")
    parser.add_argument("--universe", default="NIFTY50", help="Stock universe")
    args = parser.parse_args()

    symbols = [args.symbol.upper()] if args.symbol else get_universe(args.universe)
    os.makedirs(OUT_DIR, exist_ok=True)

    # ── Intraday (15-min) backtest ────────────────────────────
    if args.mode in ("intraday", "both"):
        print(f"\n  === INTRADAY (15-min candles) ===")
        print(f"  Symbols: {len(symbols)}, Source: {INTRADAY_DB}")
        all_trades = []
        for i, sym in enumerate(symbols):
            candles = load_15m(INTRADAY_DB, sym)
            if not candles:
                continue
            days = group_by_day(candles)
            avg_vols = compute_avg_volume(days)
            trades = simulate_vwap_mr(days, avg_vols, sym)
            all_trades.extend(trades)
            if (i + 1) % 10 == 0:
                print(f"    [{i+1}/{len(symbols)}] processed, {len(all_trades)} trades so far")

        all_trades.sort(key=lambda t: t["entry_ts"])
        metrics_15m = compute_metrics(all_trades, "15-min Intraday (2 years)")
        print_summary(metrics_15m)

        # Save trades
        out_path = os.path.join(OUT_DIR, "vwap_mr_intraday_trades.json")
        with open(out_path, "w") as f:
            json.dump({"strategy": "VWAP_MEAN_REVERSION", "mode": "intraday",
                       "metrics": metrics_15m, "trades": all_trades}, f, indent=2)
        print(f"  Saved: {out_path}")

    # ── Daily backtest ────────────────────────────────────────
    if args.mode in ("daily", "both"):
        print(f"\n  === DAILY (simulated intraday from daily candles) ===")
        print(f"  Symbols: {len(symbols)}, Source: {DAILY_DB}")
        all_trades_d = []
        for i, sym in enumerate(symbols):
            candles = load_daily(DAILY_DB, sym)
            if not candles:
                continue
            trades = simulate_vwap_mr_daily(candles, sym)
            all_trades_d.extend(trades)
            if (i + 1) % 10 == 0:
                print(f"    [{i+1}/{len(symbols)}] processed, {len(all_trades_d)} trades so far")

        all_trades_d.sort(key=lambda t: t["entry_ts"])
        metrics_daily = compute_metrics(all_trades_d, "Daily Simulated (10 years)")
        print_summary(metrics_daily)

        out_path = os.path.join(OUT_DIR, "vwap_mr_daily_trades.json")
        with open(out_path, "w") as f:
            json.dump({"strategy": "VWAP_MEAN_REVERSION", "mode": "daily",
                       "metrics": metrics_daily, "trades": all_trades_d}, f, indent=2)
        print(f"  Saved: {out_path}")


if __name__ == "__main__":
    main()
