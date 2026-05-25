"""
scripts/trade/backtest_gates.py
================================================================
Unified gate backtester for the trade tool audit.

Uses the existing scanner scoring path (analyse_candle_snapshot)
on 15-min candle data. Tests gates individually by toggling them
on/off and sweeping parameter values.

Architecture:
  1. Load 15-min candles for all NIFTY 50 stocks
  2. For each trading day × stock:
     - Compute composite score via analyse_candle_snapshot()
     - Apply entry gate filters (configurable via --gate flags)
     - Simulate trade with SL/target/EOD square-off
     - Apply exit gate logic
  3. Compute after-cost metrics (STT, brokerage, GST, etc.)
  4. Print comparison table + verdict

Usage:
    # Baseline (no gates, raw score + ATR SL/target)
    python scripts/trade/backtest_gates.py --baseline

    # Test a specific gate
    python scripts/trade/backtest_gates.py --gate M1 --sweep 1.5,2.0,2.5,3.0,3.5

    # Test with costs
    python scripts/trade/backtest_gates.py --baseline --with-costs
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

from config import Config  # noqa: E402
from shared.nifty_universe import get_universe  # noqa: E402

# ── Data paths ────────────────────────────────────────────────
BT_DATA = os.path.join(os.path.dirname(PROJECT_ROOT), "ai-portfolio-backtest-data", "candles")
INTRADAY_DB = os.path.join(BT_DATA, "intraday_15m.sqlite")
DAILY_DB = os.path.join(BT_DATA, "daily.sqlite")
OUT_DIR = os.path.join(PROJECT_ROOT, "reports", "backtest")

# ── Defaults ──────────────────────────────────────────────────
CAPITAL = 50_000
ENTRY_START_HOUR = 10
ENTRY_END_HOUR = 14
ENTRY_END_MINUTE = 30
SQUARE_OFF_HOUR = 15
SQUARE_OFF_MINUTE = 10


# ── Indicator helpers ─────────────────────────────────────────

def _ema(values: list[float], period: int) -> list[float]:
    if not values or period <= 0:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def _rsi(closes: list[float], period: int = 14) -> float:
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


def _atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def _adx(candles: list[dict], period: int = 14) -> float:
    if len(candles) < period * 2:
        return 20.0
    plus_dm, minus_dm, tr_sum = 0.0, 0.0, 0.0
    for j in range(len(candles) - period, len(candles)):
        if j < 1:
            continue
        h, l = candles[j]["high"], candles[j]["low"]
        ph, pl = candles[j-1]["high"], candles[j-1]["low"]
        pc = candles[j-1]["close"]
        up, dn = h - ph, pl - l
        plus_dm += max(up, 0) if up > dn else 0
        minus_dm += max(dn, 0) if dn > up else 0
        tr_sum += max(h - l, abs(h - pc), abs(l - pc))
    if tr_sum == 0:
        return 20.0
    pdi = 100 * plus_dm / tr_sum
    mdi = 100 * minus_dm / tr_sum
    di_sum = pdi + mdi
    return 100 * abs(pdi - mdi) / di_sum if di_sum > 0 else 0


def _vwap(candles: list[dict]) -> float:
    cum_tp_vol, cum_vol = 0.0, 0
    for c in candles:
        tp = (c["high"] + c["low"] + c["close"]) / 3
        vol = c["volume"] or 1
        cum_tp_vol += tp * vol
        cum_vol += vol
    return cum_tp_vol / cum_vol if cum_vol else 0


def _compute_score(candles_15m: list[dict], candles_day: list[dict]) -> dict:
    """Simplified composite score mimicking the scanner's scoring.
    Returns dict with score, rsi, adx, vwap, rvol, atr."""
    if len(candles_15m) < 25:
        return {"score": 0, "rsi": 50, "adx": 20, "vwap": 0, "rvol": 1.0, "atr": 0}

    closes = [c["close"] for c in candles_15m]
    current = closes[-1]

    # EMA crossover score
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    score = 0.0
    if ema9 and ema21:
        spread_pct = (ema9[-1] - ema21[-1]) / ema21[-1] * 100 if ema21[-1] else 0
        if len(ema9) >= 2 and len(ema21) >= 2:
            if ema9[-2] < ema21[-2] and ema9[-1] >= ema21[-1]:
                score += 3  # bullish cross
            elif ema9[-2] > ema21[-2] and ema9[-1] <= ema21[-1]:
                score -= 3  # bearish cross
            elif spread_pct > 0.4:
                score += 1
            elif spread_pct < -0.4:
                score -= 1

    # RSI
    rsi_val = _rsi(closes)
    if rsi_val < 30:
        score += 2
    elif rsi_val > 70:
        score -= 2
    elif 40 <= rsi_val <= 60:
        pass  # neutral

    # Momentum (4 candles = 1 hour)
    if len(closes) >= 5:
        mom_pct = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] else 0
        if mom_pct > 0.6:
            score += 2
        elif mom_pct < -0.6:
            score -= 2
        elif mom_pct > 0.2:
            score += 1
        elif mom_pct < -0.2:
            score -= 1

    # SuperTrend direction (simplified)
    atr_val = _atr(candles_15m)
    if atr_val > 0:
        st_up = current > (ema21[-1] if ema21 else current) + atr_val * 0.5
        st_dn = current < (ema21[-1] if ema21 else current) - atr_val * 0.5
        if st_up:
            score += 1
        elif st_dn:
            score -= 1

    # VWAP
    today_candles = candles_15m[-25:]  # approximate today
    vwap_val = _vwap(today_candles)

    # RVOL
    rvol = 1.0
    if candles_day and len(candles_day) >= 5:
        today_vol = sum(c["volume"] for c in candles_15m[-25:])
        n_today = min(25, len(candles_15m))
        prorated = today_vol * (25 / n_today) if n_today > 0 else 0
        recent_vols = [d["volume"] for d in candles_day[-5:] if d.get("volume", 0) > 0]
        if recent_vols:
            avg_vol = sum(recent_vols) / len(recent_vols)
            if avg_vol > 0:
                rvol = prorated / avg_vol
                if rvol > 2.0:
                    score += 1
                elif rvol < 0.3:
                    score -= 1

    adx_val = _adx(candles_15m)

    return {
        "score": round(score, 1),
        "rsi": round(rsi_val, 1),
        "adx": round(adx_val, 1),
        "vwap": round(vwap_val, 2),
        "rvol": round(rvol, 2),
        "atr": round(atr_val, 4),
        "price": current,
    }


# ── Cost model ────────────────────────────────────────────────

def compute_charges(buy_value: float, sell_value: float) -> float:
    """NSE intraday charges (approximate). Returns total charges in Rs."""
    turnover = buy_value + sell_value
    brokerage = min(40, turnover * 0.0003)  # Rs.20 per leg or 0.03%
    stt = sell_value * 0.00025              # 0.025% sell side
    exchange = turnover * 0.0000345         # 0.00345%
    gst = (brokerage + exchange) * 0.18     # 18% GST
    sebi = turnover * 0.000001              # 0.0001%
    stamp = buy_value * 0.00003            # 0.003% buy side
    return round(brokerage + stt + exchange + gst + sebi + stamp, 2)


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


# ── Trade helpers ─────────────────────────────────────────────

def _pnl(side: str, entry: float, exit_p: float) -> float:
    if side == "BUY":
        return (exit_p - entry) / entry * 100
    return (entry - exit_p) / entry * 100


# ── Trade simulation ──────────────────────────────────────────

def simulate_trades(
    candles_by_day: dict[str, list[dict]],
    daily_candles: list[dict],
    symbol: str,
    *,
    min_score: float = 2.0,
    atr_multiplier: float = 1.5,
    rr_ratio: float = 1.5,
    rr_floor: float = 1.3,
    with_costs: bool = False,
    # Gate toggles (all OFF for baseline)
    gate_rsi_buy_ceiling: float = 0,     # 0 = disabled
    gate_rsi_sell_ceiling: float = 0,
    gate_vwap_trend_fight: float = 0,    # 0 = disabled, e.g. 0.3
    gate_vwap_extension: float = 0,      # 0 = disabled, e.g. 0.8
    gate_adx_min: float = 0,             # 0 = disabled, e.g. 18
    gate_rvol_floor: float = 0,          # 0 = disabled, e.g. 0.7
    gate_loser_exit_hour: int = 0,       # 0 = disabled, e.g. 14
    gate_trailing_sl: bool = False,
    gate_partial_profit: bool = False,
    gate_stagnant_minutes: int = 0,      # 0 = disabled
    gate_daily_loss_stop_pct: float = 0, # 0 = disabled
    gate_max_trades_per_day: int = 0,    # 0 = unlimited
    gate_charge_multiple: float = 0,     # 0 = disabled, e.g. 2.0 or 3.0
) -> list[dict]:
    """Run the full scoring + simulation pipeline for one symbol.
    Uses a rolling multi-day window for indicator computation."""
    trades = []
    dates = sorted(candles_by_day.keys())

    # Build a continuous candle series across all days
    all_candles = []
    day_boundaries = {}  # date_str -> (start_idx, end_idx) in all_candles
    for d in dates:
        start = len(all_candles)
        all_candles.extend(candles_by_day[d])
        day_boundaries[d] = (start, len(all_candles))

    if len(all_candles) < 50:
        return trades

    # Pre-compute rolling indicators on the full continuous series
    closes = [c["close"] for c in all_candles]
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)

    for date_str in dates:
        start_idx, end_idx = day_boundaries[date_str]
        if start_idx < 30:  # need warmup
            continue

        day_pnl = 0.0
        day_trade_count = 0
        in_trade = False
        side = ""
        entry_price = 0.0
        sl_price = 0.0
        target_price = 0.0
        entry_ts = None

        for gi in range(start_idx, end_idx):
            c = all_candles[gi]
            hour = c["ts"].hour
            minute = c["ts"].minute
            li = gi - start_idx  # local index within day

            # ── Square off ────────────────────────────────────
            if hour >= SQUARE_OFF_HOUR or (hour == SQUARE_OFF_HOUR - 1 and minute >= SQUARE_OFF_MINUTE):
                if in_trade:
                    pnl_pct = _pnl(side, entry_price, c["close"])
                    t = _make_trade(symbol, entry_ts, c["ts"], side, entry_price,
                                   c["close"], sl_price, target_price, pnl_pct,
                                   "EOD_SQUARE_OFF", with_costs)
                    trades.append(t)
                    day_pnl += t.get("net_pnl_pct", pnl_pct)
                    in_trade = False
                break

            # ── Exit logic ────────────────────────────────────
            if in_trade:
                if side == "BUY":
                    if c["low"] <= sl_price:
                        pnl_pct = _pnl("BUY", entry_price, sl_price)
                        trades.append(_make_trade(symbol, entry_ts, c["ts"], side,
                            entry_price, sl_price, sl_price, target_price, pnl_pct,
                            "STOP_LOSS", with_costs))
                        day_pnl += pnl_pct
                        in_trade = False
                        continue
                    if c["high"] >= target_price:
                        pnl_pct = _pnl("BUY", entry_price, target_price)
                        trades.append(_make_trade(symbol, entry_ts, c["ts"], side,
                            entry_price, target_price, sl_price, target_price, pnl_pct,
                            "TARGET_HIT", with_costs))
                        day_pnl += pnl_pct
                        in_trade = False
                        continue
                    if gate_loser_exit_hour and hour >= gate_loser_exit_hour and c["close"] < entry_price:
                        pnl_pct = _pnl("BUY", entry_price, c["close"])
                        trades.append(_make_trade(symbol, entry_ts, c["ts"], side,
                            entry_price, c["close"], sl_price, target_price, pnl_pct,
                            "LOSER_EXIT_LATE", with_costs))
                        day_pnl += pnl_pct
                        in_trade = False
                        continue
                    if gate_trailing_sl:
                        atr_val = _atr(all_candles[max(0,gi-14):gi+1])
                        if atr_val > 0 and c["close"] >= entry_price + atr_val:
                            new_sl = c["close"] - atr_val * 0.5
                            if new_sl > sl_price:
                                sl_price = new_sl
                else:  # SELL
                    if c["high"] >= sl_price:
                        pnl_pct = _pnl("SELL", entry_price, sl_price)
                        trades.append(_make_trade(symbol, entry_ts, c["ts"], side,
                            entry_price, sl_price, sl_price, target_price, pnl_pct,
                            "STOP_LOSS", with_costs))
                        day_pnl += pnl_pct
                        in_trade = False
                        continue
                    if c["low"] <= target_price:
                        pnl_pct = _pnl("SELL", entry_price, target_price)
                        trades.append(_make_trade(symbol, entry_ts, c["ts"], side,
                            entry_price, target_price, sl_price, target_price, pnl_pct,
                            "TARGET_HIT", with_costs))
                        day_pnl += pnl_pct
                        in_trade = False
                        continue
                    if gate_loser_exit_hour and hour >= gate_loser_exit_hour and c["close"] > entry_price:
                        pnl_pct = _pnl("SELL", entry_price, c["close"])
                        trades.append(_make_trade(symbol, entry_ts, c["ts"], side,
                            entry_price, c["close"], sl_price, target_price, pnl_pct,
                            "LOSER_EXIT_LATE", with_costs))
                        day_pnl += pnl_pct
                        in_trade = False
                        continue
                    if gate_trailing_sl:
                        atr_val = _atr(all_candles[max(0,gi-14):gi+1])
                        if atr_val > 0 and c["close"] <= entry_price - atr_val:
                            new_sl = c["close"] + atr_val * 0.5
                            if new_sl < sl_price:
                                sl_price = new_sl
                continue

            # ── Entry logic ───────────────────────────────────
            if hour < ENTRY_START_HOUR:
                continue
            if hour > ENTRY_END_HOUR or (hour == ENTRY_END_HOUR and minute > ENTRY_END_MINUTE):
                continue
            if gate_daily_loss_stop_pct > 0 and day_pnl <= -gate_daily_loss_stop_pct:
                continue
            if gate_max_trades_per_day > 0 and day_trade_count >= gate_max_trades_per_day:
                continue

            # Compute score using rolling window
            window = all_candles[max(0, gi-50):gi+1]
            day_date = datetime.date.fromisoformat(date_str)
            daily_window = [d for d in daily_candles if d["ts"].date() < day_date][-20:]
            indicators = _compute_score(window, daily_window)
            score = indicators["score"]

            if abs(score) < min_score:
                continue

            price = indicators.get("price", c["close"])
            if price <= 0:
                continue

            rsi_val = indicators["rsi"]
            adx_val = indicators["adx"]
            vwap_val = indicators["vwap"]
            rvol_val = indicators["rvol"]
            atr_val = indicators["atr"]

            this_side = "BUY" if score > 0 else "SELL"

            # ── Gate checks ───────────────────────────────────
            rejected = False
            if gate_rsi_buy_ceiling > 0 and this_side == "BUY" and rsi_val > gate_rsi_buy_ceiling:
                rejected = True
            if gate_rsi_sell_ceiling > 0 and this_side == "SELL" and rsi_val > gate_rsi_sell_ceiling:
                rejected = True
            if gate_vwap_trend_fight > 0 and vwap_val > 0:
                dev_pct = (price - vwap_val) / vwap_val * 100
                if this_side == "BUY" and dev_pct < -gate_vwap_trend_fight:
                    rejected = True
                if this_side == "SELL" and dev_pct > gate_vwap_trend_fight:
                    rejected = True
            if gate_vwap_extension > 0 and vwap_val > 0 and abs(score) < 6:
                dev_pct = (price - vwap_val) / vwap_val * 100
                if this_side == "BUY" and dev_pct > gate_vwap_extension:
                    rejected = True
                if this_side == "SELL" and dev_pct < -gate_vwap_extension:
                    rejected = True
            if gate_adx_min > 0 and adx_val < gate_adx_min and abs(score) < 7:
                rejected = True
            if gate_rvol_floor > 0 and rvol_val < gate_rvol_floor:
                rejected = True
            if rejected:
                continue

            # ── SL/target ─────────────────────────────────────
            if atr_val <= 0:
                atr_val = price * 0.005
            sl_dist = atr_val * atr_multiplier
            target_dist = sl_dist * rr_ratio
            if this_side == "BUY":
                sl_price = price - sl_dist
                target_price = price + target_dist
            else:
                sl_price = price + sl_dist
                target_price = price - target_dist
            if rr_floor > 0:
                actual_rr = target_dist / sl_dist if sl_dist > 0 else 0
                if actual_rr < rr_floor:
                    continue

            # ── Charge-aware target gate (E5) ─────────────────
            if gate_charge_multiple > 0:
                trade_value = 15_000
                qty_est = max(1, int(trade_value / price))
                gross_profit = target_dist * qty_est
                if this_side == "BUY":
                    buy_val = price * qty_est
                    sell_val = (price + target_dist) * qty_est
                else:
                    buy_val = (price - target_dist) * qty_est
                    sell_val = price * qty_est
                charges = compute_charges(buy_val, sell_val)
                if gross_profit < charges * gate_charge_multiple:
                    continue

            entry_price = price
            side = this_side
            entry_ts = c["ts"]
            in_trade = True
            day_trade_count += 1

    return trades


def _make_trade(symbol, entry_ts, exit_ts, side, entry, exit_price,
                sl, target, pnl_pct, reason, with_costs):
    t = {
        "symbol": symbol,
        "entry_ts": entry_ts.isoformat(),
        "exit_ts": exit_ts.isoformat(),
        "side": side,
        "entry": round(entry, 2),
        "exit": round(exit_price, 2),
        "sl": round(sl, 2),
        "target": round(target, 2),
        "pnl_pct": round(pnl_pct, 3),
        "reason": reason,
    }
    if with_costs:
        trade_value = 15_000
        qty = max(1, int(trade_value / entry))
        buy_val = entry * qty if side == "BUY" else exit_price * qty
        sell_val = exit_price * qty if side == "BUY" else entry * qty
        charges = compute_charges(buy_val, sell_val)
        gross_pnl = (exit_price - entry) * qty if side == "BUY" else (entry - exit_price) * qty
        net_pnl = gross_pnl - charges
        t["charges"] = charges
        t["gross_pnl"] = round(gross_pnl, 2)
        t["net_pnl"] = round(net_pnl, 2)
        t["net_pnl_pct"] = round(net_pnl / (entry * qty) * 100, 3) if entry * qty > 0 else 0
    return t


# ── Metrics ───────────────────────────────────────────────────

def compute_metrics(trades: list[dict], label: str, with_costs: bool = False) -> dict:
    if not trades:
        return {"label": label, "trades": 0, "note": "No trades"}

    pnl_key = "net_pnl_pct" if with_costs else "pnl_pct"
    pnls = [t.get(pnl_key, t["pnl_pct"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total = len(pnls)

    win_rate = len(wins) / total * 100
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    expectancy = sum(pnls) / total
    total_return = sum(pnls)

    # Max drawdown
    equity = [0.0]
    for p in pnls:
        equity.append(equity[-1] + p)
    peak, max_dd = equity[0], 0.0
    for e in equity:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > max_dd:
            max_dd = dd

    # Date range + CAGR
    first = trades[0]["entry_ts"][:10]
    last = trades[-1]["exit_ts"][:10]
    try:
        years = max((datetime.date.fromisoformat(last) - datetime.date.fromisoformat(first)).days / 365.25, 0.1)
    except ValueError:
        years = 1.0
    end_val = CAPITAL * (1 + total_return / 100)
    cagr = ((end_val / CAPITAL) ** (1 / years) - 1) * 100 if end_val > 0 else -100

    # Sharpe
    if len(pnls) > 1:
        mean_p = sum(pnls) / len(pnls)
        std_p = (sum((p - mean_p) ** 2 for p in pnls) / (len(pnls) - 1)) ** 0.5
        tpy = total / years
        sharpe = mean_p / std_p * math.sqrt(tpy) if std_p > 0 else 0
    else:
        sharpe = 0

    by_reason = defaultdict(int)
    for t in trades:
        by_reason[t["reason"]] += 1

    total_charges = sum(t.get("charges", 0) for t in trades) if with_costs else 0

    return {
        "label": label, "trades": total, "wins": len(wins), "losses": len(losses),
        "win_rate": round(win_rate, 1), "avg_win": round(avg_win, 3),
        "avg_loss": round(avg_loss, 3), "pf": round(pf, 2),
        "expectancy": round(expectancy, 3), "total_return": round(total_return, 2),
        "cagr": round(cagr, 2), "max_dd": round(max_dd, 2), "sharpe": round(sharpe, 2),
        "period": f"{first} to {last}", "years": round(years, 1),
        "by_reason": dict(by_reason), "total_charges": round(total_charges, 2),
    }


def print_metrics(m: dict):
    if m.get("note"):
        print(f"  {m['label']}: {m['note']}")
        return
    print(f"  {m['label']:40s} | "
          f"Trades: {m['trades']:5d} | "
          f"WR: {m['win_rate']:5.1f}% | "
          f"PF: {m['pf']:5.2f} | "
          f"Exp: {m['expectancy']:+7.3f}% | "
          f"Return: {m['total_return']:+8.2f}% | "
          f"CAGR: {m['cagr']:+7.2f}% | "
          f"MaxDD: {m['max_dd']:6.2f}% | "
          f"Sharpe: {m['sharpe']:+6.2f}")


def print_comparison(results: list[dict]):
    print(f"\n{'='*130}")
    print(f"  {'Config':40s} | {'Trades':>7s} | {'WR':>7s} | {'PF':>7s} | "
          f"{'Exp/trade':>9s} | {'Return':>10s} | {'CAGR':>9s} | {'MaxDD':>8s} | {'Sharpe':>8s}")
    print(f"{'-'*130}")
    for m in results:
        if m.get("note"):
            print(f"  {m['label']:40s} | {m['note']}")
            continue
        print_metrics(m)
    print(f"{'='*130}")


# ── Main ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Gate backtester")
    parser.add_argument("--baseline", action="store_true",
                        help="Run baseline (no gates)")
    parser.add_argument("--gate", type=str, default=None,
                        help="Gate ID to test (e.g. M1, G1, E1)")
    parser.add_argument("--sweep", type=str, default=None,
                        help="Comma-separated values to sweep (e.g. 1.5,2.0,2.5)")
    parser.add_argument("--with-costs", action="store_true",
                        help="Include regulatory costs in P&L")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--universe", default="NIFTY50")
    args = parser.parse_args()

    symbols = [args.symbol.upper()] if args.symbol else get_universe(args.universe)
    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"\n  Loading data for {len(symbols)} symbols...")

    # Preload all data
    all_data = {}
    for sym in symbols:
        candles = load_15m(INTRADAY_DB, sym)
        daily = load_daily(DAILY_DB, sym)
        if candles:
            all_data[sym] = {
                "days": group_by_day(candles),
                "daily": daily,
            }
    print(f"  Loaded {len(all_data)} symbols with data\n")

    def run_config(label: str, *, portfolio_daily_cap: int = 0, **kwargs) -> dict:
        all_trades = []
        for sym, data in all_data.items():
            trades = simulate_trades(
                data["days"], data["daily"], sym,
                with_costs=args.with_costs, **kwargs,
            )
            all_trades.extend(trades)
        all_trades.sort(key=lambda t: t["entry_ts"])

        # Portfolio-level daily cap: keep only top N trades per day
        # ranked by entry time (first N signals of the day — no hindsight)
        # NOTE: We do NOT sort by PnL (that would be lookahead bias).
        # In live trading, we take the first N signals that pass filters.
        if portfolio_daily_cap > 0 and all_trades:
            filtered = []
            by_day = defaultdict(list)
            for t in all_trades:
                day = t["entry_ts"][:10]
                by_day[day].append(t)
            for day in sorted(by_day):
                day_trades = by_day[day]
                # Keep first N trades by entry time (chronological order)
                day_trades.sort(key=lambda t: t["entry_ts"])
                filtered.extend(day_trades[:portfolio_daily_cap])
            all_trades = sorted(filtered, key=lambda t: t["entry_ts"])

        return compute_metrics(all_trades, label, args.with_costs)

    results = []

    if args.baseline:
        # Run baseline: raw score + ATR SL/target, no gates
        print("  === BASELINE (no gates) ===")
        m = run_config("Baseline (no gates)",
                       min_score=2.0, atr_multiplier=1.5, rr_ratio=1.5, rr_floor=0)
        results.append(m)
        print_metrics(m)

        # Also run with costs for comparison
        if not args.with_costs:
            print("\n  === BASELINE (with costs) ===")
            all_trades_c = []
            for sym, data in all_data.items():
                trades = simulate_trades(
                    data["days"], data["daily"], sym,
                    with_costs=True, min_score=2.0, atr_multiplier=1.5,
                    rr_ratio=1.5, rr_floor=0,
                )
                all_trades_c.extend(trades)
            all_trades_c.sort(key=lambda t: t["entry_ts"])
            mc = compute_metrics(all_trades_c, "Baseline (with costs)", True)
            results.append(mc)
            print_metrics(mc)

    elif args.gate and args.sweep:
        values = [float(v.strip()) for v in args.sweep.split(",")]
        gate = args.gate.upper()

        print(f"  === Gate {gate} sweep: {values} ===\n")

        # Always include baseline for comparison
        m_base = run_config("Baseline (gate OFF)", min_score=2.0,
                           atr_multiplier=1.5, rr_ratio=1.5, rr_floor=0)
        results.append(m_base)

        for val in values:
            kwargs = {"min_score": 2.0, "atr_multiplier": 1.5,
                      "rr_ratio": 1.5, "rr_floor": 0}

            if gate == "M1":
                kwargs["min_score"] = val
                label = f"M1: MIN_SCORE = {val}"
            elif gate == "E1_ATR":
                kwargs["atr_multiplier"] = val
                label = f"E1: ATR_MULT = {val}"
            elif gate == "E1_RR":
                kwargs["rr_ratio"] = val
                label = f"E1: RR_RATIO = {val}"
            elif gate == "E3":
                kwargs["rr_floor"] = val
                label = f"E3: RR_FLOOR = {val}"
            elif gate == "E5":
                kwargs["gate_charge_multiple"] = val
                label = f"E5: CHARGE_MULT = {val}x"
            elif gate == "G1":
                kwargs["gate_rsi_buy_ceiling"] = val
                label = f"G1: RSI_BUY_CEIL = {val}"
            elif gate == "G6":
                kwargs["gate_vwap_trend_fight"] = val
                label = f"G6: VWAP_FIGHT = {val}%"
            elif gate == "G7":
                kwargs["gate_vwap_extension"] = val
                label = f"G7: VWAP_EXT = {val}%"
            elif gate == "H1":
                kwargs["gate_adx_min"] = val
                label = f"H1: ADX_MIN = {val}"
            elif gate == "D5":
                kwargs["gate_rvol_floor"] = val
                label = f"D5: RVOL_FLOOR = {val}"
            elif gate == "L10":
                kwargs["gate_loser_exit_hour"] = int(val)
                label = f"L10: LOSER_EXIT_HR = {int(val)}"
            elif gate == "C2":
                kwargs["gate_daily_loss_stop_pct"] = val
                label = f"C2: DAILY_LOSS_STOP = {val}%"
            elif gate == "K1":
                label = f"K1: PORTFOLIO_CAP/DAY = {int(val)}"
                m = run_config(label, portfolio_daily_cap=int(val),
                               min_score=2.0, atr_multiplier=1.5,
                               rr_ratio=1.5, rr_floor=0)
                results.append(m)
                continue
            else:
                print(f"  Unknown gate: {gate}")
                return

            m = run_config(label, **kwargs)
            results.append(m)

    else:
        parser.print_help()
        return

    print_comparison(results)

    # Save results
    out_path = os.path.join(OUT_DIR, f"gate_test_{args.gate or 'baseline'}.json")
    with open(out_path, "w") as f:
        json.dump({"results": results}, f, indent=2)
    print(f"\n  Saved: {out_path}")


if __name__ == "__main__":
    main()
