# ================================================================
# modes/options/performance_tracker.py
# ================================================================
# Persistent SQLite database for tracking options trade performance.
# Separate DB from equity (data/options.db).
#
# Schema mirrors equity's trades.db but adds option-specific columns
# (strike, expiry, option_type, premium, greeks snapshot).
# ================================================================

import os
import sqlite3
import datetime

from config      import Config, now_ist
from core.logger import Logger


class OptionsPerformanceTracker:

    DB_PATH = os.path.join("data", "options.db")

    def __init__(self, config: type[Config], log: Logger):
        self.cfg = config
        self.log = log
        self._ensure_db()

    # ================================================================
    # DATABASE SETUP
    # ================================================================

    def _ensure_db(self):
        os.makedirs(os.path.dirname(self.DB_PATH), exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS option_trades (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    date             TEXT    NOT NULL,
                    symbol           TEXT    NOT NULL,
                    option_type      TEXT    NOT NULL,
                    strike           INTEGER NOT NULL,
                    expiry           TEXT    NOT NULL,
                    side             TEXT    NOT NULL,
                    lots             INTEGER NOT NULL DEFAULT 1,
                    qty              INTEGER NOT NULL,
                    entry_premium    REAL    NOT NULL,
                    exit_premium     REAL,
                    cost             REAL,
                    pnl              REAL    DEFAULT 0,
                    exit_reason      TEXT,
                    nifty_price      REAL,
                    nifty_trend      TEXT,
                    india_vix        REAL,
                    regime           TEXT,
                    entry_time       TEXT,
                    exit_time        TEXT,
                    rationale        TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS option_candidates (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    date             TEXT    NOT NULL,
                    scan_time        TEXT,
                    symbol           TEXT,
                    option_type      TEXT,
                    strike           INTEGER,
                    expiry           TEXT,
                    premium          REAL,
                    nifty_price      REAL,
                    nifty_trend      TEXT,
                    india_vix        REAL,
                    regime           TEXT,
                    accepted         INTEGER DEFAULT 0,
                    reject_reason    TEXT,
                    rationale        TEXT
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.DB_PATH)

    # ================================================================
    # RECORD TRADES
    # ================================================================

    def record_trades(self, positions: list[dict]):
        """Persist closed option trades to the database."""
        today = now_ist().strftime("%Y-%m-%d")
        closed = [p for p in positions if p.get("status") == "CLOSED"]
        if not closed:
            return

        with self._connect() as conn:
            for p in closed:
                expiry_str = ""
                if isinstance(p.get("expiry"), datetime.date):
                    expiry_str = p["expiry"].isoformat()
                elif isinstance(p.get("expiry"), str):
                    expiry_str = p["expiry"]

                conn.execute("""
                    INSERT INTO option_trades
                        (date, symbol, option_type, strike, expiry, side,
                         lots, qty, entry_premium, exit_premium, cost, pnl,
                         exit_reason, nifty_price, nifty_trend, india_vix,
                         regime, entry_time, exit_time, rationale)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    today,
                    p.get("symbol", ""),
                    p.get("option_type", ""),
                    p.get("strike", 0),
                    expiry_str,
                    p.get("side", "BUY"),
                    p.get("lots", 1),
                    p.get("qty", 0),
                    p.get("entry_premium", 0),
                    p.get("exit_premium"),
                    p.get("cost", 0),
                    p.get("pnl", 0),
                    p.get("exit_reason"),
                    p.get("nifty_price"),
                    p.get("nifty_trend"),
                    p.get("india_vix"),
                    p.get("regime"),
                    p.get("entry_time"),
                    p.get("exit_time"),
                    p.get("rationale"),
                ))

        self.log.info(f"Persisted {len(closed)} option trade(s) to {self.DB_PATH}")

    # ================================================================
    # RECORD CANDIDATES (audit trail)
    # ================================================================

    def record_candidate(self, candidate: dict | None, accepted: bool, reject_reason: str = ""):
        """Log a scan candidate (accepted or rejected) for audit."""
        today = now_ist().strftime("%Y-%m-%d")
        scan_time = now_ist().strftime("%H:%M:%S")

        with self._connect() as conn:
            if candidate:
                expiry_str = ""
                if isinstance(candidate.get("expiry"), datetime.date):
                    expiry_str = candidate["expiry"].isoformat()

                conn.execute("""
                    INSERT INTO option_candidates
                        (date, scan_time, symbol, option_type, strike, expiry,
                         premium, nifty_price, nifty_trend, india_vix, regime,
                         accepted, reject_reason, rationale)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    today, scan_time,
                    candidate.get("symbol"),
                    candidate.get("option_type"),
                    candidate.get("strike"),
                    expiry_str,
                    candidate.get("premium"),
                    candidate.get("nifty_price"),
                    candidate.get("nifty_trend"),
                    candidate.get("india_vix"),
                    candidate.get("regime"),
                    1 if accepted else 0,
                    reject_reason,
                    candidate.get("rationale"),
                ))
            else:
                # No candidate generated (regime/VIX filter etc.)
                conn.execute("""
                    INSERT INTO option_candidates
                        (date, scan_time, accepted, reject_reason)
                    VALUES (?, ?, 0, ?)
                """, (today, scan_time, reject_reason or "no_candidate"))

    # ================================================================
    # QUERIES
    # ================================================================

    def get_today_trades(self) -> list[dict]:
        """Get all option trades for today."""
        today = now_ist().strftime("%Y-%m-%d")
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM option_trades WHERE date = ? ORDER BY id",
                (today,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_summary(self, days: int = 30) -> dict:
        """Get aggregate stats for the last N days."""
        cutoff = (now_ist() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM option_trades WHERE date >= ? ORDER BY date, id",
                (cutoff,),
            ).fetchall()

        trades = [dict(r) for r in rows]
        if not trades:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "total_pnl": 0, "profit_factor": 0,
                "avg_win": 0, "avg_loss": 0,
            }

        wins = [t for t in trades if (t.get("pnl") or 0) > 0]
        losses = [t for t in trades if (t.get("pnl") or 0) < 0]
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        gross_profit = sum(t["pnl"] for t in wins) if wins else 0
        gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 0

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
            "total_pnl": round(total_pnl, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0,
            "avg_win": round(gross_profit / len(wins), 2) if wins else 0,
            "avg_loss": round(gross_loss / len(losses), 2) if losses else 0,
        }
