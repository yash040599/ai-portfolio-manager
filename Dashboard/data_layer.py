"""Read-only DB access for the dashboard (Roadmap D1).

CRITICAL contract (see DASHBOARD_ROADMAP.md → "Data Finality"):
final P&L numbers shown to the user MUST come ONLY from rows where
`sheet_verified = 'verified'`. Provisional (API-day-of) rows can be
included for analytics, but only when the caller explicitly opts in
via `include_provisional=True`, and the UI must visually mark them.

This module imports nothing from the trading bot's runtime modules
(`portfolio.manager*`, `services.order_engine`, etc.). It only reads.
"""

from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass
from typing import Iterable

# Reuse the project's single source of truth for the DB path so we
# don't drift if it ever moves. tax_db.py also handles schema migration.
from scripts.tax_db import DB_PATH, get_db


@dataclass(frozen=True)
class TradeRow:
    """One closed intraday trade with charges resolved.

    Mirrors the columns we actually consume from `intraday_tax_ledger`.
    Keeping a typed wrapper (vs raw sqlite3.Row dicts) makes downstream
    metrics code easier to read and unit-test.
    """
    date:           str          # ISO YYYY-MM-DD
    symbol:         str
    side:           str          # BUY / SELL
    qty:            int
    entry_price:    float
    exit_price:     float
    gross_pnl:      float
    total_charges:  float
    net_pnl:        float
    sheet_verified: str          # 'verified' | 'pending' | other


# ── Window helpers ────────────────────────────────────────────────

def current_fy_window(today: datetime.date | None = None) -> tuple[str, str]:
    """Indian FY window containing `today`. April 1 → March 31."""
    today = today or datetime.date.today()
    fy_start_year = today.year if today.month >= 4 else today.year - 1
    return (
        f"{fy_start_year}-04-01",
        f"{fy_start_year + 1}-03-31",
    )


def resolve_window(
    days: int | None       = None,
    date_from: str | None  = None,
    date_to: str | None    = None,
    today: datetime.date | None = None,
) -> tuple[str, str]:
    """Resolve the analysis window to an inclusive (from, to) date pair.

    Precedence: explicit `date_from`/`date_to` > `--days` > default
    current Indian FY (Apr 1 → Mar 31). Future `date_to` is clamped
    to `today` (warns at the CLI layer).
    """
    today = today or datetime.date.today()
    if date_from and date_to:
        d_from = datetime.date.fromisoformat(date_from)
        d_to   = datetime.date.fromisoformat(date_to)
    elif days and days > 0:
        d_to   = today
        d_from = today - datetime.timedelta(days=days - 1)
    else:
        f_from, f_to = current_fy_window(today)
        d_from = datetime.date.fromisoformat(f_from)
        d_to   = datetime.date.fromisoformat(f_to)

    if d_to > today:
        d_to = today
    if d_from > d_to:
        d_from = d_to

    return d_from.isoformat(), d_to.isoformat()


# ── Reads ─────────────────────────────────────────────────────────

def fetch_trades(
    date_from: str,
    date_to: str,
    *,
    include_provisional: bool = False,
    conn: sqlite3.Connection | None = None,
) -> list[TradeRow]:
    """Return closed intraday trades in the inclusive window.

    By default ONLY sheet-verified rows are returned (data-finality
    contract). Pass `include_provisional=True` to include pending
    rows as well (for analytics — never for tax outputs).
    """
    own_conn = conn is None
    conn = conn or get_db()
    try:
        sql  = (
            "SELECT date, symbol, side, qty, entry_price, exit_price, "
            "       gross_pnl, total_charges, net_pnl, sheet_verified "
            "FROM intraday_tax_ledger "
            "WHERE date >= ? AND date <= ?"
        )
        args: list = [date_from, date_to]
        if not include_provisional:
            sql += " AND sheet_verified = 'verified'"
        sql += " ORDER BY date, id"
        rows = conn.execute(sql, args).fetchall()
        return [_row_to_trade(r) for r in rows]
    finally:
        if own_conn:
            conn.close()


def pending_verification_dates(
    date_from: str,
    date_to: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """List trading dates in window that have rows but ZERO sheet-verified rows.

    Used to surface the "still provisional" banner in any render mode.
    """
    own_conn = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT date, "
            "       SUM(CASE WHEN sheet_verified='verified' THEN 1 ELSE 0 END) AS v_count, "
            "       COUNT(*) AS total "
            "FROM intraday_tax_ledger "
            "WHERE date >= ? AND date <= ? "
            "GROUP BY date "
            "ORDER BY date",
            (date_from, date_to),
        ).fetchall()
        return [r["date"] for r in rows if r["total"] > 0 and r["v_count"] == 0]
    finally:
        if own_conn:
            conn.close()


def verified_dates(
    date_from: str,
    date_to: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """Return distinct dates with at least one sheet-verified row."""
    own_conn = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM intraday_tax_ledger "
            "WHERE date >= ? AND date <= ? AND sheet_verified='verified' "
            "ORDER BY date",
            (date_from, date_to),
        ).fetchall()
        return [r["date"] for r in rows]
    finally:
        if own_conn:
            conn.close()


# ── Internal helpers ──────────────────────────────────────────────

def _row_to_trade(r: sqlite3.Row) -> TradeRow:
    return TradeRow(
        date           = r["date"],
        symbol         = r["symbol"],
        side           = r["side"],
        qty            = int(r["qty"]),
        entry_price    = float(r["entry_price"]),
        exit_price     = float(r["exit_price"]),
        gross_pnl      = float(r["gross_pnl"]),
        total_charges  = float(r["total_charges"]),
        net_pnl        = float(r["net_pnl"]),
        sheet_verified = r["sheet_verified"] or "pending",
    )


__all__ = [
    "TradeRow",
    "current_fy_window",
    "resolve_window",
    "fetch_trades",
    "pending_verification_dates",
    "verified_dates",
]
