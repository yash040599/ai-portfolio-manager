"""
scripts/trade/fetch_backtest_candles.py
================================================================
Bulk-fetch 15-minute intraday candles from Zerodha for backtesting.

Fetches as far back as Zerodha allows (~2 years for 15-min) for
all NIFTY 50/100 stocks and stores them in the backtest-data repo's
SQLite format (candles/intraday_15m.sqlite).

Usage:
    python scripts/trade/fetch_backtest_candles.py
    python scripts/trade/fetch_backtest_candles.py --universe NIFTY50
    python scripts/trade/fetch_backtest_candles.py --symbol RELIANCE
    python scripts/trade/fetch_backtest_candles.py --from 2024-06-01 --dry-run

Requires a valid Zerodha session (run login first if expired).
Rate-limited to ~3 req/sec to respect Zerodha API limits.
================================================================
"""

from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
except ImportError:
    pass

from config import Config, now_ist  # noqa: E402
from core.logger import Logger  # noqa: E402
from core.zerodha_client import ZerodhaClient  # noqa: E402
from shared.nifty_universe import get_universe  # noqa: E402

# Output database — same format as the backtest-data repo
DEFAULT_OUT_DIR = os.path.join(
    os.path.dirname(PROJECT_ROOT), "ai-portfolio-backtest-data", "candles"
)
OUT_DB_15M = "intraday_15m.sqlite"

# Zerodha allows ~2 years of 15-min candles.
# We chunk in 55-day windows (their per-request limit).
CHUNK_DAYS = 55
MAX_HISTORY_DAYS = 730  # ~2 years

log = Logger("FetchCandles")


def ensure_db(db_path: str) -> None:
    """Create the SQLite database and table if needed.
    Matches the schema of the existing backtest-data repo."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                symbol           TEXT NOT NULL,
                instrument_token TEXT,
                exchange         TEXT NOT NULL DEFAULT 'NSE',
                ts_ist           TEXT NOT NULL,
                interval         TEXT NOT NULL,
                open             REAL NOT NULL,
                high             REAL NOT NULL,
                low              REAL NOT NULL,
                close            REAL NOT NULL,
                volume           INTEGER,
                source           TEXT NOT NULL,
                created_at_ist   TEXT NOT NULL,
                PRIMARY KEY (symbol, interval, ts_ist, source)
            )
        """)


def existing_range(db_path: str, symbol: str) -> tuple[str | None, str | None]:
    """Return (min_ts, max_ts) for a symbol's 15-min candles, or (None, None)."""
    if not os.path.isfile(db_path):
        return None, None
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT MIN(ts_ist), MAX(ts_ist) FROM candles "
            "WHERE symbol=? AND exchange='NSE' AND interval='15minute'",
            (symbol,)
        ).fetchone()
    if row and row[0]:
        return row[0], row[1]
    return None, None


def fetch_symbol(
    zerodha: ZerodhaClient,
    symbol: str,
    from_date: datetime.date,
    to_date: datetime.date,
    db_path: str,
    dry_run: bool = False,
) -> int:
    """Fetch 15-min candles for one symbol and insert into DB.
    Returns number of new rows inserted."""

    total_inserted = 0
    chunk_start = from_date

    while chunk_start <= to_date:
        chunk_end = min(chunk_start + datetime.timedelta(days=CHUNK_DAYS - 1), to_date)

        if dry_run:
            print(f"  [DRY RUN] Would fetch {symbol} {chunk_start} -> {chunk_end}")
            chunk_start = chunk_end + datetime.timedelta(days=1)
            continue

        try:
            candles = zerodha.get_historical(
                symbol=symbol,
                exchange="NSE",
                from_date=chunk_start,
                to_date=chunk_end,
                interval="15minute",
            )
        except Exception as exc:
            log.warning(f"  {symbol} {chunk_start}->{chunk_end}: {exc}")
            chunk_start = chunk_end + datetime.timedelta(days=1)
            continue

        if candles:
            now_str = datetime.datetime.now().isoformat()
            rows = []
            for c in candles:
                ts = c.get("date")
                if ts is None:
                    continue
                ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                rows.append((
                    symbol, None, "NSE", ts_str, "15minute",
                    float(c["open"]), float(c["high"]),
                    float(c["low"]), float(c["close"]),
                    int(c.get("volume", 0)),
                    "zerodha_backfill", now_str,
                ))

            with sqlite3.connect(db_path) as conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO candles "
                    "(symbol, instrument_token, exchange, ts_ist, interval, "
                    "open, high, low, close, volume, source, created_at_ist) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            total_inserted += len(rows)
            log.info(f"  {symbol} {chunk_start}->{chunk_end}: {len(candles)} candles")
        else:
            log.info(f"  {symbol} {chunk_start}->{chunk_end}: no data")

        chunk_start = chunk_end + datetime.timedelta(days=1)
        time.sleep(0.4)  # Rate limit

    return total_inserted


def main():
    parser = argparse.ArgumentParser(
        description="Bulk-fetch 15-min candles from Zerodha for backtesting."
    )
    parser.add_argument("--universe", default="NIFTY50",
                        help="NIFTY50, NIFTY100, NIFTY150, NIFTY200 (default: NIFTY50)")
    parser.add_argument("--symbol", default=None,
                        help="Fetch a single symbol instead of the full universe")
    parser.add_argument("--from", dest="from_date", default=None,
                        help="Start date YYYY-MM-DD (default: 2 years ago)")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="End date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                        help=f"Output directory (default: {DEFAULT_OUT_DIR})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be fetched without calling the API")
    args = parser.parse_args()

    # Dates
    today = now_ist().date()
    yesterday = today - datetime.timedelta(days=1)
    if args.from_date:
        start = datetime.date.fromisoformat(args.from_date)
    else:
        start = today - datetime.timedelta(days=MAX_HISTORY_DAYS)
    end = datetime.date.fromisoformat(args.to_date) if args.to_date else yesterday

    # Symbols
    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = get_universe(args.universe)
        if not symbols:
            print(f"No symbols found for universe {args.universe}")
            sys.exit(1)

    db_path = os.path.join(args.out_dir, OUT_DB_15M)

    print(f"\n  Fetch 15-min candles: {start} to {end}")
    print(f"  Universe: {args.universe} ({len(symbols)} symbols)")
    print(f"  Output: {db_path}")
    print(f"  Mode: {'DRY RUN' if args.dry_run else 'LIVE FETCH'}")
    print()

    if not args.dry_run:
        ensure_db(db_path)

        # Login to Zerodha
        zerodha = ZerodhaClient(Config, log)
        try:
            zerodha.login()
        except Exception as exc:
            print(f"  Zerodha login failed: {exc}")
            print("  Run: python main.py --mode login")
            sys.exit(1)

    grand_total = 0
    for i, sym in enumerate(symbols, 1):
        # Check what we already have
        min_ts, max_ts = existing_range(db_path, sym) if not args.dry_run else (None, None)

        # Build list of (fetch_start, fetch_end) ranges to fill gaps
        ranges_to_fetch = []
        if min_ts and max_ts:
            try:
                existing_start = datetime.datetime.fromisoformat(min_ts).date()
                existing_end = datetime.datetime.fromisoformat(max_ts).date()
                # Gap before existing data?
                if start < existing_start:
                    ranges_to_fetch.append((start, existing_start - datetime.timedelta(days=1)))
                # Gap after existing data?
                if existing_end < end:
                    ranges_to_fetch.append((existing_end + datetime.timedelta(days=1), end))
                if not ranges_to_fetch:
                    print(f"  [{i}/{len(symbols)}] {sym}: already {existing_start} to {existing_end}, skip")
                    continue
            except ValueError:
                ranges_to_fetch = [(start, end)]
        else:
            ranges_to_fetch = [(start, end)]

        for fetch_start, fetch_end in ranges_to_fetch:
            print(f"  [{i}/{len(symbols)}] {sym}: {fetch_start} -> {fetch_end}")
            if args.dry_run:
                fetch_symbol(None, sym, fetch_start, fetch_end, db_path, dry_run=True)
            else:
                n = fetch_symbol(zerodha, sym, fetch_start, fetch_end, db_path)
                grand_total += n

    print(f"\n  Done. Total new rows: {grand_total:,}")
    if not args.dry_run:
        # Print final stats
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT MIN(ts_ist), MAX(ts_ist), COUNT(*), COUNT(DISTINCT symbol) "
                "FROM candles WHERE interval='15minute'"
            ).fetchone()
            print(f"  DB range: {row[0]} -> {row[1]}")
            print(f"  Total rows: {row[2]:,}, symbols: {row[3]}")


if __name__ == "__main__":
    main()

