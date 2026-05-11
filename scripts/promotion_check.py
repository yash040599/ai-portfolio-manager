"""
scripts/promotion_check.py
================================================================
Codified dry-run / live promotion criteria (industry-standard
graduation gate). PASS/FAIL on objective metrics computed from the
last N trading sessions in `data/trades.db`.

Why
---
Every reckless capital scale-up the bot has done in past tuning
cycles followed the same failure mode: a string of "good vibes"
sessions, no formal metric check, then a deeper drawdown the
following week. This script encodes the gate in code so every
capital scale (or relax of a major risk threshold) MUST be preceded
by a passing run of this script.

Usage
-----
    python scripts/promotion_check.py                 # last 20 sessions
    python scripts/promotion_check.py --window 30
    python scripts/promotion_check.py --min-trades 50
    python scripts/promotion_check.py --json          # machine-readable

Exit codes:
    0 = PASS (safe to promote)
    1 = FAIL (one or more criteria did not meet threshold)
    2 = INSUFFICIENT_DATA (not enough trades in window)

Promotion thresholds (defaults — tune in this file ONLY after a
deliberate review, never inline)
--------------------------------
  Profit factor          : >= 1.15
  Expectancy per trade   : >= +Rs.10 (after charges; charges are
                            already netted into pnl by the live
                            ledger)
  Day-level win rate     : >= 55%
  Max drawdown (cum)     : <= 3% of average daily capital used
  Trade-level win rate   : >= 40%      (sanity floor — strategy is
                            asymmetric so day-WR is the harder gate)
  Min trades in window   : >= 30
================================================================
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB_PATH = os.path.join(PROJECT_ROOT, "data", "trades.db")

# ── thresholds ──────────────────────────────────────────────────
THRESHOLDS = {
    "profit_factor":     1.15,
    "expectancy_inr":    10.0,
    "day_win_rate_pct":  55.0,
    "trade_win_rate_pct": 40.0,
    "max_dd_pct_of_capital": 3.0,
    "min_trades":        30,
}


def _load_recent_trades(window_days: int) -> list[dict]:
    if not os.path.isfile(DB_PATH):
        return []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        # Get the last N distinct session dates (calendar days with trades)
        days = conn.execute(
            "SELECT DISTINCT date FROM trades ORDER BY date DESC LIMIT ?",
            (window_days,),
        ).fetchall()
        if not days:
            return []
        date_set = tuple(d["date"] for d in days)
        placeholder = ",".join(["?"] * len(date_set))
        rows = conn.execute(
            f"SELECT * FROM trades WHERE date IN ({placeholder}) "
            "ORDER BY date ASC, entry_time ASC",
            date_set,
        ).fetchall()
    return [dict(r) for r in rows]


def _per_day_pnl(trades: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in trades:
        d = t["date"]
        out[d] = out.get(d, 0.0) + (t["pnl"] or 0.0)
    return out


def _max_drawdown_inr(trades: list[dict]) -> float:
    """Max peak-to-trough drop in cumulative P&L over the window."""
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: (x["date"], x["entry_time"] or "")):
        cum += t["pnl"] or 0.0
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return max_dd


def _avg_daily_capital_used(trades: list[dict]) -> float:
    """Approximate: per-day sum of (entry_price × qty) on the entry side."""
    by_day: dict[str, float] = {}
    for t in trades:
        if (t.get("side") or "").upper() not in ("BUY", "LONG", "SELL", "SHORT"):
            continue
        notional = (t.get("entry_price") or 0.0) * (t.get("qty") or 0)
        by_day[t["date"]] = by_day.get(t["date"], 0.0) + notional
    if not by_day:
        return 0.0
    return sum(by_day.values()) / len(by_day)


def _evaluate(trades: list[dict]) -> dict:
    if not trades:
        return {"status": "INSUFFICIENT_DATA", "trades": 0}

    wins = [t for t in trades if (t["pnl"] or 0) > 0]
    losses = [t for t in trades if (t["pnl"] or 0) <= 0]
    gross_win = sum(t["pnl"] for t in wins) if wins else 0.0
    gross_loss = -sum(t["pnl"] for t in losses) if losses else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    expectancy = sum(t["pnl"] for t in trades) / len(trades)
    trade_wr = len(wins) / len(trades) * 100.0

    day_pnl = _per_day_pnl(trades)
    profitable_days = [d for d, v in day_pnl.items() if v > 0]
    day_wr = (len(profitable_days) / len(day_pnl) * 100.0) if day_pnl else 0.0

    avg_cap = _avg_daily_capital_used(trades)
    max_dd_inr = _max_drawdown_inr(trades)
    max_dd_pct = (max_dd_inr / avg_cap * 100.0) if avg_cap > 0 else 0.0

    metrics = {
        "trades":               len(trades),
        "sessions":             len(day_pnl),
        "wins":                 len(wins),
        "losses":               len(losses),
        "gross_win_inr":        round(gross_win, 2),
        "gross_loss_inr":       round(gross_loss, 2),
        "net_pnl_inr":          round(gross_win - gross_loss, 2),
        "profit_factor":        round(pf, 4) if pf != float("inf") else None,
        "expectancy_inr":       round(expectancy, 2),
        "trade_win_rate_pct":   round(trade_wr, 2),
        "day_win_rate_pct":     round(day_wr, 2),
        "max_dd_inr":           round(max_dd_inr, 2),
        "avg_daily_capital_inr": round(avg_cap, 2),
        "max_dd_pct_of_capital": round(max_dd_pct, 3),
    }

    checks = []
    def add(name, actual, op, threshold):
        if op == ">=":
            ok = actual is not None and actual >= threshold
        elif op == "<=":
            ok = actual is not None and actual <= threshold
        else:
            ok = False
        checks.append({
            "name": name, "actual": actual, "op": op,
            "threshold": threshold, "pass": ok,
        })

    add("min_trades",          metrics["trades"],            ">=", THRESHOLDS["min_trades"])
    add("profit_factor",       metrics["profit_factor"],     ">=", THRESHOLDS["profit_factor"])
    add("expectancy_inr",      metrics["expectancy_inr"],    ">=", THRESHOLDS["expectancy_inr"])
    add("day_win_rate_pct",    metrics["day_win_rate_pct"],  ">=", THRESHOLDS["day_win_rate_pct"])
    add("trade_win_rate_pct",  metrics["trade_win_rate_pct"], ">=", THRESHOLDS["trade_win_rate_pct"])
    add("max_dd_pct_of_capital", metrics["max_dd_pct_of_capital"], "<=", THRESHOLDS["max_dd_pct_of_capital"])

    if metrics["trades"] < THRESHOLDS["min_trades"]:
        status = "INSUFFICIENT_DATA"
    elif all(c["pass"] for c in checks):
        status = "PASS"
    else:
        status = "FAIL"
    return {"status": status, "metrics": metrics, "checks": checks}


def main():
    parser = argparse.ArgumentParser(
        description="Promotion-criteria check for live capital scale-ups."
    )
    parser.add_argument("--window", type=int, default=20,
                        help="Most-recent N trading sessions to consider.")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON only.")
    args = parser.parse_args()

    trades = _load_recent_trades(args.window)
    result = _evaluate(trades)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"  Promotion check (window={args.window} sessions)")
        print(f"  DB: {os.path.relpath(DB_PATH, PROJECT_ROOT)}")
        if "metrics" not in result:
            print(f"\n  STATUS: {result['status']}  trades={result.get('trades', 0)}")
        else:
            m = result["metrics"]
            print(f"\n  Trades / Sessions  : {m['trades']} / {m['sessions']}")
            print(f"  Win rate (trade)   : {m['trade_win_rate_pct']}%")
            print(f"  Win rate (session) : {m['day_win_rate_pct']}%")
            print(f"  Profit factor      : {m['profit_factor']}")
            print(f"  Expectancy         : Rs.{m['expectancy_inr']}/trade")
            print(f"  Net P&L            : Rs.{m['net_pnl_inr']}")
            print(f"  Max drawdown       : Rs.{m['max_dd_inr']}  ({m['max_dd_pct_of_capital']}% of avg cap)")
            print(f"\n  Checks:")
            for c in result["checks"]:
                tag = "PASS" if c["pass"] else "FAIL"
                print(f"    [{tag}] {c['name']:<25} actual={c['actual']} {c['op']} {c['threshold']}")
            print(f"\n  STATUS: {result['status']}")

    if result["status"] == "PASS":
        sys.exit(0)
    if result["status"] == "FAIL":
        sys.exit(1)
    sys.exit(2)


if __name__ == "__main__":
    main()
