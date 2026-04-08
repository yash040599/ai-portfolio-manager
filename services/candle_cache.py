# ================================================================
# services/candle_cache.py
# ================================================================
# SQLite-backed cache for historical candle data from Zerodha.
#
# Caches candle data that doesn't change during a trading session:
#   - Daily candles (previous days only — today's daily candle updates)
#   - Previous day's 15-min candles (finalized once that day ends)
#
# Today's intraday candles are NEVER cached — they update every
# 15 minutes as new candles form during the live session.
#
# Cache is stored in data/candle_cache.db (separate DB for git transferability).
# One row per candle: (symbol, interval, date, OHLCV).
# Lookup key: (symbol, interval) → all candles for that combo.
#
# This saves ~100 Zerodha API calls per scan on a 100-stock universe
# when daily candles are already cached, and ~100 more when previous
# day's intraday candles are cached.
# ================================================================

import datetime
import os
import sqlite3

from config import now_ist

DB_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "candle_cache.db")


class CandleCache:
    """
    Read-through cache for Zerodha historical candle data.
    Only caches data from completed trading days (not today).
    """

    def __init__(self):
        os.makedirs(DB_DIR, exist_ok=True)
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candle_cache (
                    symbol      TEXT    NOT NULL,
                    exchange    TEXT    NOT NULL DEFAULT 'NSE',
                    interval    TEXT    NOT NULL,
                    candle_date TEXT    NOT NULL,
                    open        REAL    NOT NULL,
                    high        REAL    NOT NULL,
                    low         REAL    NOT NULL,
                    close       REAL    NOT NULL,
                    volume      INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (symbol, exchange, interval, candle_date)
                )
            """)

    # ────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ────────────────────────────────────────────────────────────────

    def get_cached_candles(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        from_date: datetime.date,
        to_date: datetime.date,
    ) -> list[dict]:
        """
        Returns cached candles for the given symbol/interval/date range.
        Only returns candles from BEFORE today (today's data is never cached).
        Returns empty list if nothing is cached.
        """
        today = now_ist().date()
        # Never return cached data for today — it changes intraday
        cache_end = min(to_date, today - datetime.timedelta(days=1))
        if cache_end < from_date:
            return []

        # candle_date is stored as full datetime string ("2026-04-07 09:15:00")
        # so the upper bound needs a time component to include all candles on that date
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT candle_date, open, high, low, close, volume
                   FROM candle_cache
                   WHERE symbol = ? AND exchange = ? AND interval = ?
                     AND candle_date >= ? AND candle_date <= ?
                   ORDER BY candle_date ASC""",
                (symbol, exchange, interval,
                 str(from_date), str(cache_end) + " 23:59:59"),
            ).fetchall()

        return [
            {
                "date":   datetime.datetime.fromisoformat(r["candle_date"]),
                "open":   r["open"],
                "high":   r["high"],
                "low":    r["low"],
                "close":  r["close"],
                "volume": r["volume"],
            }
            for r in rows
        ]

    def store_candles(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        candles: list[dict],
    ):
        """
        Stores candles in the cache. Only stores candles from BEFORE today.
        Today's candles are silently skipped (they change intraday).
        Uses INSERT OR IGNORE to avoid duplicates.
        """
        today = now_ist().date()
        rows = []

        for c in candles:
            dt = c.get("date")
            if dt is None:
                continue
            cdate = dt.date() if hasattr(dt, "date") else dt
            if cdate >= today:
                continue  # skip today's candles
            rows.append((
                symbol, exchange, interval, str(dt),
                c["open"], c["high"], c["low"], c["close"],
                c.get("volume", 0),
            ))

        if not rows:
            return

        with self._connect() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO candle_cache
                   (symbol, exchange, interval, candle_date,
                    open, high, low, close, volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )

    def has_cached_data(
        self,
        symbol: str,
        exchange: str,
        interval: str,
        target_date: datetime.date,
    ) -> bool:
        """Checks if we have any cached candles for a given date."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT 1 FROM candle_cache
                   WHERE symbol = ? AND exchange = ? AND interval = ?
                     AND candle_date LIKE ?
                   LIMIT 1""",
                (symbol, exchange, interval, f"{target_date}%"),
            ).fetchone()
        return row is not None

    def invalidate_symbol(self, symbol: str, exchange: str = "NSE"):
        """
        Removes ALL cached candles for a symbol.
        Call this when a corporate action (split, bonus) is detected.
        Next fetch will re-cache with adjusted prices from Zerodha.
        """
        with self._connect() as conn:
            deleted = conn.execute(
                "DELETE FROM candle_cache WHERE symbol = ? AND exchange = ?",
                (symbol, exchange),
            ).rowcount
        return deleted

    def get_last_cached_close(self, symbol: str, exchange: str, interval: str) -> float | None:
        """
        Returns the most recent cached close price for a symbol.
        Used to detect price discontinuities (corporate actions).
        """
        with self._connect() as conn:
            row = conn.execute(
                """SELECT close FROM candle_cache
                   WHERE symbol = ? AND exchange = ? AND interval = ?
                   ORDER BY candle_date DESC LIMIT 1""",
                (symbol, exchange, interval),
            ).fetchone()
        return row["close"] if row else None

    def cleanup_old(self, keep_days: int = 45):
        """Removes cached candles older than keep_days (default 45 days)."""
        cutoff = str(now_ist().date() - datetime.timedelta(days=keep_days))
        with self._connect() as conn:
            deleted = conn.execute(
                "DELETE FROM candle_cache WHERE candle_date < ?", (cutoff,)
            ).rowcount
        return deleted
