# ================================================================
# import_reports_to_db.py
# ================================================================
# One-time script to import existing JSON report files into the
# SQLite database (data/trades.db).
#
# Imports:
#   - Portfolio analysis JSONs → portfolio_analyses table
#   - Trading data JSONs       → trades table
#
# Safe to run multiple times — skips records that already exist
# for a given (date, symbol) combination.
#
# Usage:
#   python scripts/import_reports_to_db.py
# ================================================================

import os
import re
import json
import glob
import sqlite3

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "trades.db")


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(conn):
    """Create tables if they don't exist (mirrors performance_tracker.py)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            date             TEXT    NOT NULL,
            symbol           TEXT    NOT NULL,
            side             TEXT    NOT NULL,
            entry_price      REAL    NOT NULL,
            exit_price       REAL,
            qty              INTEGER NOT NULL,
            pnl              REAL    DEFAULT 0,
            exit_reason      TEXT,
            claude_confidence TEXT,
            market_condition TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_analyses (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            symbol          TEXT    NOT NULL,
            action          TEXT,
            conviction      TEXT,
            reasoning       TEXT,
            horizon         TEXT,
            target_price    TEXT,
            current_price   REAL    DEFAULT 0,
            invested_value  REAL    DEFAULT 0,
            current_value   REAL    DEFAULT 0,
            stock_pnl       REAL    DEFAULT 0,
            stock_pnl_pct   REAL    DEFAULT 0,
            action_detail   TEXT,
            num_stocks      INTEGER DEFAULT 0,
            trigger_price   TEXT,
            trigger_action  TEXT,
            risks           TEXT,
            watch           TEXT,
            next_steps      TEXT,
            action_taken    TEXT    DEFAULT 'PENDING'
        )
    """)
    try:
        conn.execute(
            "ALTER TABLE portfolio_analyses ADD COLUMN action_taken TEXT DEFAULT 'PENDING'"
        )
    except sqlite3.OperationalError:
        pass
    conn.commit()


def existing_dates(conn, table: str) -> set:
    """Returns set of dates already in the table."""
    rows = conn.execute(f"SELECT DISTINCT date FROM {table}").fetchall()
    return {r["date"] for r in rows}


def import_portfolio_reports(conn):
    """Import portfolio_data_*.json files into portfolio_analyses table."""
    existing = existing_dates(conn, "portfolio_analyses")
    files = sorted(glob.glob(os.path.join(PROJECT_ROOT, "reports/portfolio/*/*/portfolio_data_*.json")))

    if not files:
        print("  No portfolio JSON files found")
        return

    imported = 0
    skipped = 0

    for path in files:
        path = path.replace("\\", "/")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  ✗ Could not read {path}: {e}")
            continue

        date = data.get("date", "")
        if not date:
            print(f"  ✗ No date in {path}")
            continue

        if date in existing:
            print(f"  ⏭ {path} — date {date} already in DB, skipping")
            skipped += 1
            continue

        # Build stock lookup from portfolio array
        portfolio = data.get("portfolio", [])
        stock_by_symbol = {s["symbol"]: s for s in portfolio}

        analyses = data.get("analyses", [])
        if not analyses:
            print(f"  ⏭ {path} — no analyses")
            continue

        for a in analyses:
            symbol = a.get("symbol", "")
            parsed = a.get("parsed", {})
            stock = stock_by_symbol.get(symbol, {})

            invested = stock.get("invested_value", 0)
            current = stock.get("current_value", 0)
            pnl = current - invested
            pnl_pct = (pnl / invested * 100) if invested > 0 else 0

            action = parsed.get("ACTION", "HOLD")
            action_taken = "N/A" if action == "HOLD" else "PENDING"

            conn.execute(
                """INSERT INTO portfolio_analyses
                   (date, symbol, action, conviction, reasoning, horizon,
                    target_price, current_price, invested_value, current_value,
                    stock_pnl, stock_pnl_pct, action_detail, num_stocks,
                    trigger_price, trigger_action, risks, watch, next_steps,
                    action_taken)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    date,
                    symbol,
                    action,
                    parsed.get("CONVICTION", ""),
                    parsed.get("REASONING", ""),
                    parsed.get("HORIZON", ""),
                    parsed.get("TARGET_PRICE", ""),
                    stock.get("current_price", stock.get("last_price", 0)),
                    invested,
                    current,
                    round(pnl, 2),
                    round(pnl_pct, 2),
                    parsed.get("ACTION_DETAIL", ""),
                    _parse_int(parsed.get("NUM_STOCKS", "0")),
                    parsed.get("TRIGGER_PRICE", ""),
                    parsed.get("TRIGGER_ACTION", ""),
                    parsed.get("RISKS", ""),
                    parsed.get("WATCH", ""),
                    parsed.get("NEXT_STEPS", ""),
                    action_taken,
                ),
            )

        conn.commit()
        imported += 1
        print(f"  ✓ {path} — imported {len(analyses)} analyses for {date}")

    print(f"\n  Portfolio: {imported} files imported, {skipped} skipped")


def import_trading_reports(conn):
    """Import trading_data_*.json files into trades table."""
    existing = existing_dates(conn, "trades")
    files = sorted(glob.glob(os.path.join(PROJECT_ROOT, "reports/trading/*/*/trading_data_*.json")))

    if not files:
        print("  No trading JSON files found")
        return

    imported = 0
    skipped = 0

    for path in files:
        path = path.replace("\\", "/")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  ✗ Could not read {path}: {e}")
            continue

        date = data.get("date", "")
        if not date:
            print(f"  ✗ No date in {path}")
            continue

        # For trading data, use filename to disambiguate same-day sessions
        # (e.g. trading_data_17.json and trading_data_17_1.json)
        # Check by date — if merged, all positions are in one file
        if date in existing:
            print(f"  ⏭ {path} — date {date} already in DB, skipping")
            skipped += 1
            continue

        positions = data.get("positions", [])
        market_condition = data.get("market_condition", "")
        closed = [p for p in positions if p.get("status") == "CLOSED"]

        if not closed:
            print(f"  ⏭ {path} — no closed positions")
            continue

        for p in closed:
            conn.execute(
                """INSERT INTO trades
                   (date, symbol, side, entry_price, exit_price, qty,
                    pnl, exit_reason, claude_confidence, market_condition)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    date,
                    p.get("symbol", ""),
                    p.get("side", ""),
                    p.get("entry_price", 0),
                    p.get("exit_price", 0),
                    p.get("qty", 0),
                    p.get("pnl", 0),
                    p.get("exit_reason", ""),
                    p.get("claude_confidence", ""),
                    market_condition,
                ),
            )

        conn.commit()
        imported += 1
        print(f"  ✓ {path} — imported {len(closed)} trades for {date}")

    print(f"\n  Trading: {imported} files imported, {skipped} skipped")


def _parse_int(value) -> int:
    """Safely extract integer from a string like '25 shares' or '0'."""
    if isinstance(value, int):
        return value
    nums = re.findall(r"\d+", str(value))
    return int(nums[0]) if nums else 0


def print_summary(conn):
    """Show what's in the DB after import."""
    pa_count = conn.execute("SELECT COUNT(*) as c FROM portfolio_analyses").fetchone()["c"]
    pa_dates = conn.execute("SELECT COUNT(DISTINCT date) as c FROM portfolio_analyses").fetchone()["c"]
    tr_count = conn.execute("SELECT COUNT(*) as c FROM trades").fetchone()["c"]
    tr_dates = conn.execute("SELECT COUNT(DISTINCT date) as c FROM trades").fetchone()["c"]

    print(f"\n{'='*50}")
    print("  DATABASE SUMMARY")
    print(f"{'='*50}")
    print(f"  Portfolio analyses : {pa_count} records across {pa_dates} dates")
    print(f"  Intraday trades   : {tr_count} records across {tr_dates} dates")
    print(f"  Database file      : {os.path.abspath(DB_PATH)}")
    print(f"{'='*50}\n")


def main():
    print(f"\n{'='*50}")
    print("  IMPORT REPORTS TO DATABASE")
    print(f"{'='*50}\n")

    conn = connect()
    ensure_tables(conn)

    print("Importing portfolio analyses...")
    import_portfolio_reports(conn)

    print("\nImporting trading data...")
    import_trading_reports(conn)

    print_summary(conn)
    conn.close()


if __name__ == "__main__":
    main()
