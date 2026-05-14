"""
scripts/trade/backtest.py
================================================================
Offline replay harness for the NoAI strategy (Roadmap #24).

What it does
------------
For each (symbol × date × 15-min bar) in the requested window, this
script:
  1. Pulls cached 15-min and daily candles from `data/candle_cache.db`
  2. Computes a *simplified* score (EMA-cross + RSI + momentum + ATR)
     mirroring the directional intent of the live scanner — see the
     "Scoring fidelity" note below
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
Per-trade JSON to `reports/backtest/<from>_to_<to>_<hash>.json` and a
summary table (WR, PF, expectancy, max-DD) printed to stdout.

Scoring fidelity (read this!)
-----------------------------
The live scanner score uses 12+ indicators and consults
`now_ist().date()` for VWAP, ORB, gap, hourly-EMA, etc. — many of
those use clock-relative state that would mis-fire in replay. This
backtest deliberately uses a **simplified, replay-safe scoring**
(EMA-cross + RSI + 1h momentum) — the *direction-of-effect* should
match the live system but absolute P&L will differ. Use this for:
  - Comparing two configs (gate-on vs gate-off)
  - Sanity-checking the magnitude of a tuning move
  - Spotting regressions in scoring stability
DO NOT use the absolute numbers as a forecast of live P&L.

Usage
-----
    python scripts/trade/backtest.py --from 2026-04-01 --to 2026-05-09
    python scripts/trade/backtest.py --from 2026-04-01 --to 2026-05-09 \
        --symbol RELIANCE --min-score 6
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

CANDLE_DB = os.path.join(PROJECT_ROOT, "data", "candle_cache.db")
DEFAULT_BACKTEST_DATA_ROOT = os.path.join(os.path.dirname(PROJECT_ROOT), "ai-portfolio-backtest-data")
LEGACY_BACKTEST_DATA_ROOT = os.path.join(PROJECT_ROOT, "backtest_data")
CONFIGURED_BACKTEST_DATA_ROOT = os.getenv("BACKTEST_DATA_PATH", "").strip()
BACKTEST_DATA_ROOT = os.path.abspath(
    os.path.join(PROJECT_ROOT, CONFIGURED_BACKTEST_DATA_ROOT or DEFAULT_BACKTEST_DATA_ROOT)
)
OUT_DIR = os.path.join(PROJECT_ROOT, "reports", "backtest")


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
    parser.add_argument("--data-root", default=None,
                        help="Stage 1 backtest-data root (default: BACKTEST_DATA_PATH or ../ai-portfolio-backtest-data).")
    parser.add_argument("--out", default=None,
                        help="Output JSON path override.")
    args = parser.parse_args()

    start = datetime.date.fromisoformat(args.dt_from)
    end = datetime.date.fromisoformat(args.dt_to)
    if end < start:
        print("  ! --to must be >= --from"); sys.exit(2)
    source_kind, candle_db = _resolve_candle_source(args.data_root)
    if not os.path.isfile(candle_db):
        print(f"  ! Candle source not found at {candle_db}."); sys.exit(1)

    cfg = Config()
    version, cfg_hash = Config.snapshot_hash()
    print(f"  Config: {version} / {cfg_hash}")
    print(f"  Window: {start} .. {end}")
    print(f"  Min score: {args.min_score}")
    print(f"  Candle source: {source_kind} ({os.path.relpath(candle_db, PROJECT_ROOT)})")

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = _list_symbols(candle_db, source_kind)
    print(f"  Symbols: {len(symbols)} from candle source")

    trades: list[dict] = []
    per_day_count: dict[str, int] = {}

    for sym in symbols:
        candles = _load_15m(sym, "NSE", start, end, candle_db, source_kind)
        if len(candles) < 30:
            continue
        for i in range(20, len(candles) - 1):
            bar = candles[i]
            # Restrict entries to 10:00 .. 14:30 IST (mirrors live).
            if not (datetime.time(10, 0) <= bar["ts"].time() <= datetime.time(14, 30)):
                continue
            day_key = f"{sym}_{bar['ts'].date().isoformat()}"
            if per_day_count.get(day_key, 0) >= args.max_trades_per_day:
                continue
            window = candles[max(0, i - 30): i + 1]
            score = score_bar(window)
            if abs(score) < args.min_score:
                continue
            side = "BUY" if score > 0 else "SELL"
            atr_val = _atr(window, 14)
            tr = simulate_trade(i, candles, side, atr_val, cfg)
            tr["symbol"] = sym
            tr["score"]  = score
            tr["config_version"] = version
            tr["config_hash"]    = cfg_hash
            trades.append(tr)
            per_day_count[day_key] = per_day_count.get(day_key, 0) + 1

    if not trades:
        print("\n  No synthetic trades generated for this window.")
        sys.exit(0)

    # ── summary stats ───────────────────────────────────────────
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    total_win_pct = sum(t["pnl_pct"] for t in wins)
    total_loss_pct = -sum(t["pnl_pct"] for t in losses)  # positive number
    pf = (total_win_pct / total_loss_pct) if total_loss_pct > 0 else float("inf")
    expectancy_pct = statistics.fmean(t["pnl_pct"] for t in trades)
    wr = len(wins) / len(trades) * 100

    # Equity curve & max-DD on cumulative-pct basis
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        cum += t["pnl_pct"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    print(f"\n  ── Summary ────────────────────────────────────────")
    print(f"  Trades            : {len(trades)}")
    print(f"  Wins / Losses     : {len(wins)} / {len(losses)}")
    print(f"  Win rate          : {wr:.2f}%")
    print(f"  Avg P&L per trade : {expectancy_pct:+.3f}%")
    print(f"  Profit factor     : {pf:.2f}")
    print(f"  Max drawdown      : {max_dd:.2f}% (cum-pct basis)")
    by_reason = {}
    for t in trades:
        by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1
    print(f"  Exit reasons      : {by_reason}")

    # ── write per-trade JSON ────────────────────────────────────
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = args.out or os.path.join(
        OUT_DIR, f"{start}_to_{end}_{cfg_hash}.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "config_version": version,
            "config_hash":    cfg_hash,
            "from":           str(start),
            "to":             str(end),
            "min_score":      args.min_score,
            "summary": {
                "trades": len(trades),
                "wins":   len(wins),
                "losses": len(losses),
                "wr_pct": round(wr, 3),
                "expectancy_pct": round(expectancy_pct, 4),
                "profit_factor":  round(pf, 4) if pf != float("inf") else None,
                "max_dd_pct":     round(max_dd, 3),
                "exit_reasons":   by_reason,
            },
            "trades": trades,
        }, f, indent=2)
    print(f"\n  Per-trade detail → {os.path.relpath(out_path, PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
