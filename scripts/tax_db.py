"""
Shared helpers for all tax-ledger scripts.

Handles DB connection, table creation / migration, and FY utilities.
"""

import datetime
import os
import sqlite3

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH      = os.path.join(PROJECT_ROOT, "data", "trades.db")


# ── Financial Year helpers ────────────────────────────────────────

def indian_fy(date_str: str) -> int:
    """FY start year.  2026-03-25 → 2025 (FY 2025-26)."""
    d = datetime.date.fromisoformat(date_str)
    return d.year if d.month >= 4 else d.year - 1


def fy_label(fy_start: int) -> str:
    return f"FY {fy_start}-{str(fy_start + 1)[-2:]}"


def fy_date_range(fy_start: int) -> tuple[str, str]:
    return f"{fy_start}-04-01", f"{fy_start + 1}-03-31"


def current_fy() -> int:
    today = datetime.date.today()
    return today.year if today.month >= 4 else today.year - 1


# ── Database ──────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    """Return a connection with row_factory set and all tables ensured."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection):
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}

    # ── Rename old table ──────────────────────────────────────
    if "tax_ledger" in tables and "intraday_tax_ledger" not in tables:
        conn.execute("ALTER TABLE tax_ledger RENAME TO intraday_tax_ledger")
        conn.commit()
        tables.add("intraday_tax_ledger")

    # ── Intraday tax ledger ───────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS intraday_tax_ledger (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            symbol          TEXT    NOT NULL,
            exchange        TEXT    NOT NULL DEFAULT 'NSE',
            side            TEXT    NOT NULL,
            qty             INTEGER NOT NULL,
            entry_price     REAL    NOT NULL,
            exit_price      REAL    NOT NULL,
            entry_time      TEXT,
            exit_time       TEXT,
            exit_reason     TEXT,
            gross_pnl       REAL    NOT NULL,
            buy_value       REAL    NOT NULL,
            sell_value      REAL    NOT NULL,
            turnover        REAL    NOT NULL,
            brokerage       REAL    NOT NULL DEFAULT 0,
            stt             REAL    NOT NULL DEFAULT 0,
            exchange_txn    REAL    NOT NULL DEFAULT 0,
            gst             REAL    NOT NULL DEFAULT 0,
            sebi_charges    REAL    NOT NULL DEFAULT 0,
            stamp_duty      REAL    NOT NULL DEFAULT 0,
            total_charges   REAL    NOT NULL DEFAULT 0,
            net_pnl         REAL    NOT NULL,
            order_id        TEXT    NOT NULL,
            verified        TEXT    NOT NULL DEFAULT 'unverified',
            UNIQUE(date, order_id)
        )
    """)

    # Add verified column to existing rows from old schema
    if "intraday_tax_ledger" in tables:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(intraday_tax_ledger)"
        )}
        if "verified" not in cols:
            conn.execute(
                "ALTER TABLE intraday_tax_ledger "
                "ADD COLUMN verified TEXT NOT NULL DEFAULT 'unverified'"
            )

    # ── Capital gains ledger ──────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capital_gains_ledger (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_type          TEXT    NOT NULL,
            symbol              TEXT    NOT NULL,
            isin                TEXT,
            entry_date          TEXT    NOT NULL,
            exit_date           TEXT    NOT NULL,
            qty                 REAL    NOT NULL,
            buy_value           REAL    NOT NULL,
            sell_value          REAL    NOT NULL,
            profit              REAL    NOT NULL,
            period_of_holding   INTEGER,
            fair_market_value   REAL    DEFAULT 0,
            taxable_profit      REAL    NOT NULL,
            turnover            REAL    NOT NULL DEFAULT 0,
            brokerage           REAL    NOT NULL DEFAULT 0,
            exchange_txn        REAL    NOT NULL DEFAULT 0,
            sebi_charges        REAL    NOT NULL DEFAULT 0,
            gst                 REAL    NOT NULL DEFAULT 0,
            stamp_duty          REAL    NOT NULL DEFAULT 0,
            stt                 REAL    NOT NULL DEFAULT 0,
            total_charges       REAL    NOT NULL DEFAULT 0,
            verified            TEXT    NOT NULL DEFAULT 'verified',
            UNIQUE(entry_date, exit_date, symbol, qty, trade_type)
        )
    """)

    conn.commit()
