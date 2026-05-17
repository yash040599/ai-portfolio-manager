"""
scripts/trade/backtest.py
================================================================
Offline replay harness for the NoAI strategy (Roadmap #24).

What it does
------------
For each (symbol × date × 15-min bar) in the requested window, this
script:
  1. Pulls cached 15-min and daily candles from `data/candle_cache.db`
  2. Computes either the legacy simplified replay score or the scanner-style
      candle score via `--score-mode scanner` — see the "Scoring fidelity"
      note below
  3. If |score| ≥ MIN_SCORE and time is in [10:00, 14:30] IST,
     opens a synthetic position with:
        SL     = entry ± ATR × Config.ATR_MULTIPLIER
        target = SL × Config.RR_TARGET_RATIO
  4. Walks forward 15-min bars, exits on SL hit / target hit / EOD
     square-off (Config.SQUARE_OFF_TIME)
  5. Records the trade, stamped with Config.snapshot_hash() so you
     can compare runs across config edits

Output
------
Per-trade JSON to `reports/backtest/` and a summary table (WR, PF,
expectancy, max-DD) printed to stdout. The output filename includes the
date range, symbol scope, score mode, minimum score, and config hash so
separate replay runs do not overwrite each other. The same JSON now also
includes a config-hash-stamped `candidates` ledger showing which replay
candidates entered and which were rejected by the replay floor/cap checks.
Entered trades include an after-cost model using Config.calculate_charges(),
adverse slippage, and an explicit bid/ask spread assumption.

Scoring fidelity (read this!)
-----------------------------
`--score-mode scanner` runs the scanner-style candle scoring path with an
injected historical timestamp, so VWAP, ORB, gap, hourly/short-cutoff, and
intraday-volume features bind to the replay session instead of wall-clock
today. `--score-mode simple` keeps the old simplified replay score
(EMA-cross + RSI + 1h momentum) for comparison until scanner parity is fully
inspected. This is still not the final Chan-grade replay because the live-vs-
replay comparison arrives in a later Stage 1 item.

Usage
-----
    python scripts/trade/backtest.py --from 2026-04-01 --to 2026-05-09
    python scripts/trade/backtest.py --from 2026-04-01 --to 2026-05-09 \
        --symbol RELIANCE --min-score 6 --score-mode scanner
    # Default symbol set = every symbol present in Stage 1 `backtest_data/`
    # when available, otherwise `data/candle_cache.db`. Use `--symbol` to
    # restrict to one name; multi-name filtering will arrive with the
    # backtest v1 universe-loader follow-up.
================================================================
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import statistics
import sys

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
from modes.trade.stock_scanner import analyse_candle_snapshot  # noqa: E402

CANDLE_DB = os.path.join(PROJECT_ROOT, "data", "candle_cache.db")
DEFAULT_BACKTEST_DATA_ROOT = os.path.join(os.path.dirname(PROJECT_ROOT), "ai-portfolio-backtest-data")
LEGACY_BACKTEST_DATA_ROOT = os.path.join(PROJECT_ROOT, "backtest_data")
CONFIGURED_BACKTEST_DATA_ROOT = os.getenv("BACKTEST_DATA_PATH", "").strip()
BACKTEST_DATA_ROOT = os.path.abspath(
    os.path.join(PROJECT_ROOT, CONFIGURED_BACKTEST_DATA_ROOT or DEFAULT_BACKTEST_DATA_ROOT)
)
OUT_DIR = os.path.join(PROJECT_ROOT, "reports", "backtest")
DEFAULT_REPLAY_SPREAD_PCT = 0.05


# ────────────────────────────────────────────────────────────────
# Lightweight indicator helpers (replay-safe)
# ────────────────────────────────────────────────────────────────
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
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


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


def score_bar(candles_15m: list[dict]) -> float:
    """Simplified directional score in roughly [-10, +10]."""
    if len(candles_15m) < 25:
        return 0.0
    closes = [c["close"] for c in candles_15m]
    fast = _ema(closes, 9)
    slow = _ema(closes, 21)
    if not fast or not slow:
        return 0.0
    s = 0.0
    spread_pct = (fast[-1] - slow[-1]) / slow[-1] * 100 if slow[-1] else 0
    # Cross: fast crossed slow on the last bar
    if fast[-2] < slow[-2] and fast[-1] >= slow[-1]:
        s += 3
    elif fast[-2] > slow[-2] and fast[-1] <= slow[-1]:
        s -= 3
    elif spread_pct > 0.4:
        s += 1
    elif spread_pct < -0.4:
        s -= 1
    rsi_val = _rsi(closes, 14)
    if rsi_val < 30:
        s += 2
    elif rsi_val > 70:
        s -= 2
    elif 45 <= rsi_val <= 55:
        s += 0  # neutral
    # 1-hour momentum (4 candles)
    mom_pct = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] else 0
    if mom_pct > 0.6:
        s += 2
    elif mom_pct < -0.6:
        s -= 2
    elif mom_pct > 0.2:
        s += 1
    elif mom_pct < -0.2:
        s -= 1
    return round(s, 2)


# ────────────────────────────────────────────────────────────────
# Candle readers
# ────────────────────────────────────────────────────────────────
def _resolve_candle_source(data_root: str | None = None) -> tuple[str, str]:
    roots = [data_root] if data_root else [BACKTEST_DATA_ROOT, LEGACY_BACKTEST_DATA_ROOT]
    for root in roots:
        if not root:
            continue
        backtest_db = os.path.join(root, "candles", "intraday_15m.sqlite")
        if os.path.isfile(backtest_db):
            return "backtest_data", backtest_db
    return "legacy_candle_cache", CANDLE_DB


def _load_15m(
    symbol: str,
    exchange: str,
    start: datetime.date,
    end: datetime.date,
    db_path: str,
    source_kind: str,
) -> list[dict]:
    if source_kind == "backtest_data":
        return _load_15m_backtest_data(symbol, exchange, start, end, db_path)
    return _load_15m_legacy_cache(symbol, exchange, start, end, db_path)


def _resolve_daily_source(intraday_db_path: str, source_kind: str) -> str:
    if source_kind == "backtest_data":
        data_root = os.path.dirname(os.path.dirname(intraday_db_path))
        return os.path.join(data_root, "candles", "daily.sqlite")
    return intraday_db_path


def _load_15m_backtest_data(
    symbol: str,
    exchange: str,
    start: datetime.date,
    end: datetime.date,
    db_path: str,
) -> list[dict]:
    if not os.path.isfile(db_path):
        return []
    start_ts = f"{start.isoformat()}T00:00:00+05:30"
    end_ts = f"{end.isoformat()}T23:59:59+05:30"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT ts_ist, open, high, low, close, volume
                 FROM candles
                WHERE symbol = ? AND exchange = ? AND interval = '15minute'
                  AND ts_ist BETWEEN ? AND ?
                ORDER BY ts_ist ASC""",
            (symbol, exchange, start_ts, end_ts),
        ).fetchall()
    out = []
    for r in rows:
        try:
            ts = datetime.datetime.fromisoformat(r["ts_ist"])
        except ValueError:
            continue
        out.append({
            "ts": ts,
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]),   "close": float(r["close"]),
            "volume": int(r["volume"] or 0),
        })
    return out


def _load_15m_legacy_cache(
    symbol: str,
    exchange: str,
    start: datetime.date,
    end: datetime.date,
    db_path: str,
) -> list[dict]:
    if not os.path.isfile(db_path):
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT candle_date, open, high, low, close, volume
                 FROM candle_cache
                WHERE symbol = ? AND exchange = ? AND interval = '15minute'
                  AND candle_date BETWEEN ? AND ?
                ORDER BY candle_date ASC""",
            (symbol, exchange, str(start), str(end) + " 23:59:59"),
        ).fetchall()
    out = []
    for r in rows:
        try:
            ts = datetime.datetime.fromisoformat(r["candle_date"])
        except ValueError:
            continue
        out.append({
            "ts": ts,
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]),   "close": float(r["close"]),
            "volume": int(r["volume"] or 0),
        })
    return out


def _load_day(
    symbol: str,
    exchange: str,
    start: datetime.date,
    end: datetime.date,
    db_path: str,
    source_kind: str,
) -> list[dict]:
    if source_kind == "backtest_data":
        return _load_day_backtest_data(symbol, exchange, start, end, db_path)
    return _load_day_legacy_cache(symbol, exchange, start, end, db_path)


def _load_day_backtest_data(
    symbol: str,
    exchange: str,
    start: datetime.date,
    end: datetime.date,
    db_path: str,
) -> list[dict]:
    if not os.path.isfile(db_path):
        return []
    start_ts = f"{start.isoformat()}T00:00:00+05:30"
    end_ts = f"{end.isoformat()}T23:59:59+05:30"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT ts_ist, open, high, low, close, volume
                 FROM candles
                WHERE symbol = ? AND exchange = ? AND interval = 'day'
                  AND ts_ist BETWEEN ? AND ?
                ORDER BY ts_ist ASC""",
            (symbol, exchange, start_ts, end_ts),
        ).fetchall()
    return [_row_to_candle(r, "ts_ist") for r in rows]


def _load_day_legacy_cache(
    symbol: str,
    exchange: str,
    start: datetime.date,
    end: datetime.date,
    db_path: str,
) -> list[dict]:
    if not os.path.isfile(db_path):
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT candle_date, open, high, low, close, volume
                 FROM candle_cache
                WHERE symbol = ? AND exchange = ? AND interval = 'day'
                  AND candle_date BETWEEN ? AND ?
                ORDER BY candle_date ASC""",
            (symbol, exchange, str(start), str(end) + " 23:59:59"),
        ).fetchall()
    return [_row_to_candle(r, "candle_date") for r in rows]


def _row_to_candle(row: sqlite3.Row, ts_field: str) -> dict:
    try:
        ts = datetime.datetime.fromisoformat(row[ts_field])
    except ValueError:
        ts = datetime.datetime.fromisoformat(str(row[ts_field]).replace("Z", "+00:00"))
    return {
        "ts": ts,
        "open": float(row["open"]), "high": float(row["high"]),
        "low": float(row["low"]),   "close": float(row["close"]),
        "volume": int(row["volume"] or 0),
    }


def _scanner_candles(candles: list[dict]) -> list[dict]:
    return [
        {
            "date": c["ts"],
            "open": c["open"], "high": c["high"],
            "low": c["low"],   "close": c["close"],
            "volume": c.get("volume", 0),
        }
        for c in candles
    ]


def _daily_window_for_as_of(daily: list[dict], as_of: datetime.datetime) -> list[dict]:
    as_of_date = as_of.date()
    return [d for d in daily if d["ts"].date() < as_of_date][-60:]


def score_bar_scanner(symbol: str, candles_15m: list[dict], candles_day: list[dict], as_of: datetime.datetime) -> dict | None:
    daily_window = _daily_window_for_as_of(candles_day, as_of)
    return analyse_candle_snapshot(
        symbol=symbol,
        exchange="NSE",
        candles_15m=_scanner_candles(candles_15m),
        candles_day=_scanner_candles(daily_window),
        config=Config,
        as_of=as_of,
        log=None,
    )


def _list_symbols(db_path: str, source_kind: str) -> list[str]:
    if not os.path.isfile(db_path):
        return []
    table = "candles" if source_kind == "backtest_data" else "candle_cache"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT DISTINCT symbol FROM {table} "
            "WHERE interval = '15minute' AND exchange = 'NSE' "
            "ORDER BY symbol"
        ).fetchall()
    return [r[0] for r in rows]


# ────────────────────────────────────────────────────────────────
# Trade simulator
# ────────────────────────────────────────────────────────────────
def simulate_trade(entry_idx: int, candles: list[dict], side: str,
                   atr_value: float, cfg) -> dict:
    """
    Simulate one trade from entry_idx forward. Returns dict with
    exit info or None if data ran out before any exit.
    """
    entry = candles[entry_idx]
    entry_price = entry["close"]
    if atr_value <= 0:
        atr_value = entry_price * 0.005   # 0.5% fallback
    sl_dist = atr_value * cfg.ATR_MULTIPLIER
    target_dist = sl_dist * cfg.RR_TARGET_RATIO
    if side == "BUY":
        sl_price = entry_price - sl_dist
        tgt_price = entry_price + target_dist
    else:
        sl_price = entry_price + sl_dist
        tgt_price = entry_price - target_dist

    sq_h = int(getattr(cfg, "SQUARE_OFF_HOUR", 15))
    sq_m = int(getattr(cfg, "SQUARE_OFF_MINUTE", 10))
    entry_date = entry["ts"].date()
    sq_dt = datetime.datetime.combine(
        entry_date, datetime.time(sq_h, sq_m)
    )
    # Match tz-awareness of the candle timestamps so comparisons work.
    if entry["ts"].tzinfo is not None and sq_dt.tzinfo is None:
        sq_dt = sq_dt.replace(tzinfo=entry["ts"].tzinfo)

    for i in range(entry_idx + 1, len(candles)):
        bar = candles[i]
        # Don't carry overnight — square off if we cross dates.
        if bar["ts"].date() != entry_date:
            return _close(entry, candles[i - 1], side, "EOD_SQUARE_OFF",
                          entry_price, sl_price, tgt_price)
        if bar["ts"] >= sq_dt:
            return _close(entry, bar, side, "EOD_SQUARE_OFF",
                          entry_price, sl_price, tgt_price)
        if side == "BUY":
            # Conservative: SL hit before target if both touched in same bar
            if bar["low"] <= sl_price:
                return _close(entry, bar, side, "STOP_LOSS",
                              entry_price, sl_price, tgt_price,
                              exit_price=sl_price)
            if bar["high"] >= tgt_price:
                return _close(entry, bar, side, "TARGET_HIT",
                              entry_price, sl_price, tgt_price,
                              exit_price=tgt_price)
        else:
            if bar["high"] >= sl_price:
                return _close(entry, bar, side, "STOP_LOSS",
                              entry_price, sl_price, tgt_price,
                              exit_price=sl_price)
            if bar["low"] <= tgt_price:
                return _close(entry, bar, side, "TARGET_HIT",
                              entry_price, sl_price, tgt_price,
                              exit_price=tgt_price)
    # Ran out of candles
    return _close(entry, candles[-1], side, "DATA_END",
                  entry_price, sl_price, tgt_price)


def _close(entry, exit_bar, side, reason, entry_price, sl, tgt,
           exit_price: float | None = None) -> dict:
    if exit_price is None:
        exit_price = exit_bar["close"]
    if side == "BUY":
        pnl_pct = (exit_price - entry_price) / entry_price * 100
    else:
        pnl_pct = (entry_price - exit_price) / entry_price * 100
    return {
        "entry_ts":   entry["ts"].isoformat(),
        "exit_ts":    exit_bar["ts"].isoformat(),
        "side":       side,
        "entry":      round(entry_price, 2),
        "exit":       round(exit_price, 2),
        "sl":         round(sl, 2),
        "target":     round(tgt, 2),
        "pnl_pct":    round(pnl_pct, 3),
        "reason":     reason,
    }


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested_float_or_none(mapping: dict, path: tuple[str, ...]) -> float | None:
    current = mapping
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _float_or_none(current)


def _candidate_row(
    *,
    symbol: str,
    bar: dict,
    score: float,
    score_mode: str,
    config_version: str,
    config_hash: str,
    scanner_snapshot: dict | None = None,
) -> dict:
    side = "BUY" if score > 0 else "SELL"
    row = {
        "date": bar["ts"].date().isoformat(),
        "scan_time": bar["ts"].isoformat(),
        "symbol": symbol,
        "exchange": "NSE",
        "side": side,
        "combined_score": round(float(score), 3),
        "score_mode": score_mode,
        "config_version": config_version,
        "config_hash": config_hash,
        "status": "SCORED",
        "rejected_gate": None,
        "entry_price": None,
        "entry_time": None,
        "exit_price": None,
        "exit_time": None,
        "exit_reason": None,
        "pnl_pct": None,
    }
    if scanner_snapshot:
        technical = scanner_snapshot.get("technical", {}) or {}
        pattern_summary = scanner_snapshot.get("pattern_summary", {}) or {}
        row.update({
            "pattern_score": _float_or_none(pattern_summary.get("score")),
            "tech_score": _float_or_none(technical.get("score")),
            "rsi": _nested_float_or_none(technical, ("rsi", "rsi")),
            "adx": _nested_float_or_none(technical, ("adx", "adx")),
            "rvol": _float_or_none(scanner_snapshot.get("rvol")),
            "vwap": _float_or_none(scanner_snapshot.get("vwap")),
            "ltp": _float_or_none(scanner_snapshot.get("current_price")),
            "technical_signal": technical.get("signal"),
            "patterns": (pattern_summary.get("patterns") or [])[:5],
        })
    return row


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key) or "<none>"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _slug(value) -> str:
    text = str(value).strip()
    cleaned = "".join(char if char.isalnum() else "-" for char in text)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or "ALL"


def _default_trade_value(cfg) -> float:
    budget = float(getattr(cfg, "MAX_BUDGET_INR", 0) or 0)
    pct = float(getattr(cfg, "MAX_POSITION_PCT", 0) or 0)
    if budget > 0 and pct > 0:
        return round(budget * pct / 100, 2)
    return 20_000.0


def _qty_for_trade_value(entry_price: float, trade_value: float) -> int:
    if entry_price <= 0 or trade_value <= 0:
        return 0
    return int(trade_value // entry_price)


def _timestamp_hour(iso_ts: str) -> int:
    try:
        return datetime.datetime.fromisoformat(iso_ts).hour
    except ValueError:
        return 0


def _adjusted_replay_slippage(base_pct: float, iso_ts: str, cfg) -> float:
    hour = _timestamp_hour(iso_ts)
    if hour == int(getattr(cfg, "MARKET_OPEN_HOUR", 9)):
        return base_pct * 2.0
    if hour >= int(getattr(cfg, "SQUARE_OFF_HOUR", 15)) - 1:
        return base_pct * 1.5
    return base_pct


def _adverse_fill(price: float, transaction: str, cost_pct: float) -> float:
    if transaction == "BUY":
        return round(price * (1 + cost_pct / 100), 2)
    return round(price * (1 - cost_pct / 100), 2)


def _costed_trade(trade: dict, qty: int, cfg, *, trade_value: float,
                  slippage_pct: float, spread_pct: float) -> dict:
    side = trade["side"]
    raw_entry = float(trade["entry"])
    raw_exit = float(trade["exit"])
    entry_slip_pct = _adjusted_replay_slippage(slippage_pct, trade["entry_ts"], cfg)
    exit_slip_pct = _adjusted_replay_slippage(slippage_pct, trade["exit_ts"], cfg)
    half_spread_pct = max(0.0, spread_pct) / 2
    entry_cost_pct = entry_slip_pct + half_spread_pct
    exit_cost_pct = exit_slip_pct + half_spread_pct

    if side == "BUY":
        fill_entry = _adverse_fill(raw_entry, "BUY", entry_cost_pct)
        fill_exit = _adverse_fill(raw_exit, "SELL", exit_cost_pct)
        raw_pnl = (raw_exit - raw_entry) * qty
        gross_pnl = (fill_exit - fill_entry) * qty
        buy_turnover = fill_entry * qty
        sell_turnover = fill_exit * qty
    else:
        fill_entry = _adverse_fill(raw_entry, "SELL", entry_cost_pct)
        fill_exit = _adverse_fill(raw_exit, "BUY", exit_cost_pct)
        raw_pnl = (raw_entry - raw_exit) * qty
        gross_pnl = (fill_entry - fill_exit) * qty
        sell_turnover = fill_entry * qty
        buy_turnover = fill_exit * qty

    charges = Config.calculate_charges(buy_turnover, sell_turnover, 2)
    charges_inr = float(charges["total_tax_and_charges"])
    net_pnl = gross_pnl - charges_inr
    entry_value = raw_entry * qty
    trade.update({
        "qty": qty,
        "trade_value_inr": round(trade_value, 2),
        "fill_entry": fill_entry,
        "fill_exit": fill_exit,
        "raw_pnl_inr": round(raw_pnl, 2),
        "gross_pnl_inr": round(gross_pnl, 2),
        "charges_inr": round(charges_inr, 2),
        "net_pnl_inr": round(net_pnl, 2),
        "net_pnl_pct": round((net_pnl / entry_value * 100) if entry_value else 0, 3),
        "execution_drag_inr": round(raw_pnl - gross_pnl, 2),
        "cost_drag_inr": round(raw_pnl - net_pnl, 2),
        "entry_slippage_pct": round(entry_slip_pct, 4),
        "exit_slippage_pct": round(exit_slip_pct, 4),
        "spread_pct": round(spread_pct, 4),
        "charges": charges,
    })
    return trade


def _profit_factor(values: list[float]) -> float | None:
    wins = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v <= 0)
    if losses <= 0:
        return float("inf") if wins > 0 else None
    return wins / losses


def _max_drawdown(values: list[float]) -> float:
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in values:
        cum += value
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return max_dd


# ────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Offline replay of Trade strategy on cached candles (#24)."
    )
    parser.add_argument("--from", dest="dt_from", required=True,
                        help="Start date (YYYY-MM-DD).")
    parser.add_argument("--to", dest="dt_to", required=True,
                        help="End date inclusive (YYYY-MM-DD).")
    parser.add_argument("--symbol", default=None,
                        help="Single symbol (default: every symbol in cache).")
    parser.add_argument("--min-score", type=int,
                        default=getattr(Config, "MIN_SCORE", 5),
                        help="Score threshold for opening a synthetic trade.")
    parser.add_argument("--max-trades-per-day", type=int, default=10,
                        help="Cap trades per session (mirrors live cap).")
    parser.add_argument("--trade-value", type=float, default=_default_trade_value(Config),
                        help="Synthetic rupees allocated to each replay trade (default: config budget x max-position pct).")
    parser.add_argument("--slippage-pct", type=float,
                        default=float(getattr(Config, "SLIPPAGE_PCT", 0.0)),
                        help="Base adverse slippage percent per fill; opening/late hours use the live dry-run multipliers.")
    parser.add_argument("--spread-pct", type=float, default=DEFAULT_REPLAY_SPREAD_PCT,
                        help="Assumed bid/ask spread percent; half is charged adversely on each fill because candle data has no L1 book.")
    parser.add_argument("--data-root", default=None,
                        help="Stage 1 backtest-data root (default: BACKTEST_DATA_PATH or ../ai-portfolio-backtest-data).")
    parser.add_argument("--score-mode", choices=("simple", "scanner"), default="simple",
                        help="simple keeps the legacy replay score; scanner uses the live scanner-style candle score.")
    parser.add_argument("--out", default=None,
                        help="Output JSON path override.")
    args = parser.parse_args()

    start = datetime.date.fromisoformat(args.dt_from)
    end = datetime.date.fromisoformat(args.dt_to)
    if end < start:
        print("  ! --to must be >= --from"); sys.exit(2)
    source_kind, candle_db = _resolve_candle_source(args.data_root)
    daily_db = _resolve_daily_source(candle_db, source_kind)
    if not os.path.isfile(candle_db):
        print(f"  ! Candle source not found at {candle_db}."); sys.exit(1)

    cfg = Config()
    version, cfg_hash = Config.snapshot_hash()
    print(f"  Config: {version} / {cfg_hash}")
    print(f"  Window: {start} .. {end}")
    print(f"  Min score: {args.min_score}")
    print(f"  Score mode: {args.score_mode}")
    print(
        f"  Cost model: Rs.{args.trade_value:,.0f}/trade, "
        f"slippage {args.slippage_pct:.3f}%, spread {args.spread_pct:.3f}%"
    )
    print(f"  Candle source: {source_kind} ({os.path.relpath(candle_db, PROJECT_ROOT)})")
    if args.score_mode == "scanner":
        print(f"  Daily source : {os.path.relpath(daily_db, PROJECT_ROOT)}")

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = _list_symbols(candle_db, source_kind)
    print(f"  Symbols: {len(symbols)} from candle source")

    trades: list[dict] = []
    candidates: list[dict] = []
    per_day_count: dict[str, int] = {}

    for sym in symbols:
        candles = _load_15m(sym, "NSE", start, end, candle_db, source_kind)
        if len(candles) < 30:
            continue
        daily_candles = []
        if args.score_mode == "scanner":
            daily_candles = _load_day(
                sym, "NSE", start - datetime.timedelta(days=90), end,
                daily_db, source_kind,
            )
        for i in range(20, len(candles) - 1):
            bar = candles[i]
            # Restrict entries to 10:00 .. 14:30 IST (mirrors live).
            if not (datetime.time(10, 0) <= bar["ts"].time() <= datetime.time(14, 30)):
                continue
            day_key = f"{sym}_{bar['ts'].date().isoformat()}"
            lookback = 80 if args.score_mode == "scanner" else 30
            window = candles[max(0, i - lookback): i + 1]
            scanner_snapshot = None
            if args.score_mode == "scanner":
                scanner_snapshot = score_bar_scanner(sym, window, daily_candles, bar["ts"])
                if not scanner_snapshot:
                    continue
                score = scanner_snapshot["combined_score"]
            else:
                score = score_bar(window)
            if score == 0:
                continue

            candidate = _candidate_row(
                symbol=sym,
                bar=bar,
                score=score,
                score_mode=args.score_mode,
                config_version=version,
                config_hash=cfg_hash,
                scanner_snapshot=scanner_snapshot,
            )

            if abs(score) < args.min_score:
                candidate["status"] = "REJECTED"
                candidate["rejected_gate"] = "SCORE_FLOOR"
                candidates.append(candidate)
                continue

            if per_day_count.get(day_key, 0) >= args.max_trades_per_day:
                candidate["status"] = "REJECTED"
                candidate["rejected_gate"] = "REPLAY_TRADE_CAP"
                candidates.append(candidate)
                continue
            side = "BUY" if score > 0 else "SELL"
            planned_qty = _qty_for_trade_value(bar["close"], args.trade_value)
            if planned_qty <= 0:
                candidate["status"] = "REJECTED"
                candidate["rejected_gate"] = "REPLAY_TRADE_VALUE"
                candidates.append(candidate)
                continue
            atr_val = _atr(window, 14)
            tr = simulate_trade(i, candles, side, atr_val, cfg)
            tr = _costed_trade(
                tr, planned_qty, cfg,
                trade_value=args.trade_value,
                slippage_pct=max(0.0, args.slippage_pct),
                spread_pct=max(0.0, args.spread_pct),
            )
            tr["symbol"] = sym
            tr["score"]  = score
            tr["score_mode"] = args.score_mode
            if scanner_snapshot:
                tr["technical_signal"] = scanner_snapshot["technical"].get("signal")
                tr["patterns"] = scanner_snapshot["pattern_summary"].get("patterns", [])[:5]
            tr["config_version"] = version
            tr["config_hash"]    = cfg_hash
            candidate["status"] = "ENTERED"
            candidate["entry_price"] = tr.get("entry")
            candidate["entry_time"] = tr.get("entry_ts")
            candidate["exit_price"] = tr.get("exit")
            candidate["exit_time"] = tr.get("exit_ts")
            candidate["exit_reason"] = tr.get("reason")
            candidate["pnl_pct"] = tr.get("pnl_pct")
            candidate["qty"] = tr.get("qty")
            candidate["gross_pnl_inr"] = tr.get("gross_pnl_inr")
            candidate["charges_inr"] = tr.get("charges_inr")
            candidate["net_pnl_inr"] = tr.get("net_pnl_inr")
            candidate["net_pnl_pct"] = tr.get("net_pnl_pct")
            candidates.append(candidate)
            trades.append(tr)
            per_day_count[day_key] = per_day_count.get(day_key, 0) + 1

    # ── summary stats ───────────────────────────────────────────
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    ordered_trades = sorted(trades, key=lambda x: x["entry_ts"])
    raw_pnl_values = [float(t.get("raw_pnl_inr", 0) or 0) for t in ordered_trades]
    gross_pnl_values = [float(t.get("gross_pnl_inr", 0) or 0) for t in ordered_trades]
    net_pnl_values = [float(t.get("net_pnl_inr", 0) or 0) for t in ordered_trades]
    net_wins = [v for v in net_pnl_values if v > 0]
    net_losses = [v for v in net_pnl_values if v <= 0]
    if trades:
        total_win_pct = sum(t["pnl_pct"] for t in wins)
        total_loss_pct = -sum(t["pnl_pct"] for t in losses)  # positive number
        pf = (total_win_pct / total_loss_pct) if total_loss_pct > 0 else float("inf")
        expectancy_pct = statistics.fmean(t["pnl_pct"] for t in trades)
        wr = len(wins) / len(trades) * 100
        net_pf = _profit_factor(net_pnl_values)
        net_expectancy = statistics.fmean(net_pnl_values)
        net_wr = len(net_wins) / len(net_pnl_values) * 100
    else:
        pf = None
        expectancy_pct = None
        wr = None
        net_pf = None
        net_expectancy = None
        net_wr = None

    raw_pnl_total = round(sum(raw_pnl_values), 2)
    gross_pnl_total = round(sum(gross_pnl_values), 2)
    net_pnl_total = round(sum(net_pnl_values), 2)
    charges_total = round(sum(float(t.get("charges_inr", 0) or 0) for t in trades), 2)
    execution_drag_total = round(sum(float(t.get("execution_drag_inr", 0) or 0) for t in trades), 2)
    cost_drag_total = round(sum(float(t.get("cost_drag_inr", 0) or 0) for t in trades), 2)
    turnover_total = round(
        sum(float((t.get("charges") or {}).get("total_turnover", 0) or 0) for t in trades), 2
    )
    net_max_dd = round(_max_drawdown(net_pnl_values), 2)

    # Equity curve & max-DD on cumulative-pct basis
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in ordered_trades:
        cum += t["pnl_pct"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    print(f"\n  ── Summary ────────────────────────────────────────")
    status_counts = _count_by(candidates, "status")
    rejection_counts = _count_by(
        [row for row in candidates if row.get("status") == "REJECTED"],
        "rejected_gate",
    )
    print(f"  Candidates        : {len(candidates)} {status_counts}")
    if rejection_counts:
        print(f"  Rejections        : {rejection_counts}")
    print(f"  Trades            : {len(trades)}")
    print(f"  Wins / Losses     : {len(wins)} / {len(losses)}")
    if trades:
        print(f"  Win rate          : {wr:.2f}%")
        print(f"  Avg P&L per trade : {expectancy_pct:+.3f}%")
        print(f"  Profit factor     : {pf:.2f}")
        print(f"  Raw P&L           : Rs.{raw_pnl_total:+,.2f} (before fills/charges)")
        print(f"  Gross P&L         : Rs.{gross_pnl_total:+,.2f} (after slippage/spread)")
        print(f"  Charges           : Rs.{charges_total:,.2f}")
        print(f"  Net P&L           : Rs.{net_pnl_total:+,.2f}")
        print(f"  Net expectancy    : Rs.{net_expectancy:+,.2f}/trade")
        if net_pf == float("inf"):
            print("  Net profit factor : inf")
        else:
            print(f"  Net profit factor : {net_pf:.2f}" if net_pf is not None else "  Net profit factor : n/a")
        print(f"  Net win rate      : {net_wr:.2f}%")
    else:
        print("  Win rate          : n/a")
        print("  Avg P&L per trade : n/a")
        print("  Profit factor     : n/a")
    print(f"  Max drawdown      : {max_dd:.2f}% (cum-pct basis)")
    if trades:
        print(f"  Net max drawdown  : Rs.{net_max_dd:,.2f}")
    by_reason = {}
    for t in trades:
        by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1
    print(f"  Exit reasons      : {by_reason}")
    if not trades:
        print("\n  No synthetic trades generated for this window.")

    # ── write per-trade JSON ────────────────────────────────────
    os.makedirs(OUT_DIR, exist_ok=True)
    symbol_tag = _slug(args.symbol.upper() if args.symbol else "ALL")
    score_tag = _slug(f"{args.score_mode}_min{args.min_score}")
    cost_tag = _slug(
        f"tv{args.trade_value:.0f}_slip{args.slippage_pct:.3f}_spr{args.spread_pct:.3f}"
    )
    out_path = args.out or os.path.join(
        OUT_DIR, f"{start}_to_{end}_{symbol_tag}_{score_tag}_{cost_tag}_{cfg_hash}.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "config_version": version,
            "config_hash":    cfg_hash,
            "from":           str(start),
            "to":             str(end),
            "min_score":      args.min_score,
            "score_mode":     args.score_mode,
            "candle_source":  source_kind,
            "cost_model": {
                "trade_value_inr": round(args.trade_value, 2),
                "base_slippage_pct": round(max(0.0, args.slippage_pct), 4),
                "spread_pct": round(max(0.0, args.spread_pct), 4),
                "spread_model": "half-spread adverse on entry and exit fills",
                "slippage_model": "base pct with live dry-run time multipliers: open x2, last hour x1.5",
                "charges_model": "Config.calculate_charges per synthetic round trip, num_orders=2",
                "square_off_model": "EOD_SQUARE_OFF exits use the configured square-off timestamp and the same adverse exit fill model",
            },
            "summary": {
                "candidates": len(candidates),
                "candidate_status": status_counts,
                "rejections": rejection_counts,
                "trades": len(trades),
                "wins":   len(wins),
                "losses": len(losses),
                "wr_pct": round(wr, 3) if wr is not None else None,
                "expectancy_pct": round(expectancy_pct, 4) if expectancy_pct is not None else None,
                "profit_factor":  round(pf, 4) if pf not in (None, float("inf")) else None,
                "max_dd_pct":     round(max_dd, 3),
                "raw_pnl_inr": round(raw_pnl_total, 2),
                "gross_pnl_inr": round(gross_pnl_total, 2),
                "charges_inr": round(charges_total, 2),
                "net_pnl_inr": round(net_pnl_total, 2),
                "execution_drag_inr": round(execution_drag_total, 2),
                "cost_drag_inr": round(cost_drag_total, 2),
                "turnover_inr": round(turnover_total, 2),
                "net_wins": len(net_wins),
                "net_losses": len(net_losses),
                "net_wr_pct": round(net_wr, 3) if net_wr is not None else None,
                "net_expectancy_inr": round(net_expectancy, 2) if net_expectancy is not None else None,
                "net_profit_factor": round(net_pf, 4) if net_pf not in (None, float("inf")) else None,
                "net_max_dd_inr": round(net_max_dd, 2),
                "exit_reasons":   by_reason,
            },
            "candidates": candidates,
            "trades": trades,
        }, f, indent=2)
    print(f"\n  Replay detail → {os.path.relpath(out_path, PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
