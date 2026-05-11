"""
services/volume_baseline.py
================================================================
Per-symbol intraday volume baselines (Roadmap #260).

Why this exists
---------------
NSE intraday volume is U-shaped: ~10-15% of full-day volume in the
first hour, the curve flattens through midday, then accelerates back
into the close. The current scanner approximates the live RVol with a
*linear* pro-rating:
    prorated_today = today_volume_so_far * (25 / candles_so_far)
…and then RVOL_FLOOR_BY_HOUR softens the resulting floor by an hour
multiplier. That softening still uses the wrong denominator: by the
end of the lunch lull a stock has done less than half its full-day
volume, so the linear pro-rate over-projects the day's total.

This module replaces the denominator with a per-symbol per-hour
historical baseline:
    baseline_share[hour] = mean(today_cum_vol_at_hour / today_total_vol)
                                    over the last N completed days

`live_rvol = today_cum_vol_so_far / (avg_daily_vol * baseline_share[hour])`

Default OFF until the baseline file is built. Turn on by:
  1. python scripts/build_volume_baseline.py
  2. set Config.INTRADAY_VOLUME_BASELINE_ENABLED = True

Storage
-------
A separate SQLite DB at `data/volume_baseline.db`, one row per
`(symbol, exchange, hour_bucket)` with `mean_share`, `samples`,
`last_built`. Separate from `trades.db` so the nightly rebuild
doesn't churn the trades-DB WAL or affect the trades backup-sync
diff.
================================================================
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional

DB_PATH = os.path.join("data", "volume_baseline.db")

# Hour buckets (24-hour clock, IST). NSE session: 9:15 - 15:30, so
# we model 9..15. A baseline at hour H represents the cumulative
# volume share through the END of hour H.
SESSION_HOURS = (9, 10, 11, 12, 13, 14, 15)


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS volume_baseline (
                symbol      TEXT    NOT NULL,
                exchange    TEXT    NOT NULL DEFAULT 'NSE',
                hour_bucket INTEGER NOT NULL,
                mean_share  REAL    NOT NULL,
                stdev_share REAL    NOT NULL DEFAULT 0,
                samples     INTEGER NOT NULL,
                last_built  TEXT    NOT NULL,
                PRIMARY KEY (symbol, exchange, hour_bucket)
            )
            """
        )


def upsert_baseline(symbol: str, exchange: str, hour_bucket: int,
                    mean_share: float, stdev_share: float,
                    samples: int, last_built: str):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO volume_baseline
                 (symbol, exchange, hour_bucket, mean_share, stdev_share,
                  samples, last_built)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, exchange, hour_bucket) DO UPDATE
                 SET mean_share=excluded.mean_share,
                     stdev_share=excluded.stdev_share,
                     samples=excluded.samples,
                     last_built=excluded.last_built""",
            (symbol, exchange, hour_bucket, mean_share, stdev_share,
             samples, last_built),
        )


def get_baseline_share(symbol: str, exchange: str, hour: int,
                       min_samples: int = 10) -> Optional[float]:
    """
    Returns the mean cumulative-volume share at end of `hour` for the
    given symbol, or None if the baseline is missing / under-sampled
    (caller should fall back to the linear pro-rating).
    """
    if not os.path.isfile(DB_PATH):
        return None
    with _connect() as conn:
        row = conn.execute(
            """SELECT mean_share, samples
                 FROM volume_baseline
                WHERE symbol = ? AND exchange = ? AND hour_bucket = ?""",
            (symbol, exchange, hour),
        ).fetchone()
    if not row or row["samples"] < min_samples:
        return None
    share = row["mean_share"]
    if share is None or share <= 0:
        return None
    return float(share)


def get_baseline_summary() -> dict:
    """Quick counts for the operator (used by the build script + audit)."""
    if not os.path.isfile(DB_PATH):
        return {"db": DB_PATH, "exists": False, "rows": 0, "symbols": 0}
    with _connect() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM volume_baseline").fetchone()[0]
        syms = conn.execute(
            "SELECT COUNT(DISTINCT symbol) FROM volume_baseline"
        ).fetchone()[0]
    return {"db": DB_PATH, "exists": True, "rows": rows, "symbols": syms}
