# ================================================================
# services/performance_tracker.py
# ================================================================
# Persistent SQLite database for tracking trade performance across
# days. Enables the bot to learn from past results by feeding
# recent performance context into Claude's stock selection prompt.
#
# Database: data/trades.db
#
# Usage:
#   tracker = PerformanceTracker(config, logger)
#   tracker.record_trades(positions, market_condition)
#   context = tracker.get_claude_prompt_context()
# ================================================================

import os
import sqlite3
import datetime

from config      import Config
from core.logger import Logger


class PerformanceTracker:

    DB_PATH = os.path.join("data", "trades.db")

    def __init__(self, config: type[Config], log: Logger):
        self.cfg = config
        self.log = log
        self._ensure_db()

    # ================================================================
    # DATABASE SETUP
    # ================================================================

    def _ensure_db(self):
        """Creates the database and tables if they don't exist."""
        os.makedirs(os.path.dirname(self.DB_PATH), exist_ok=True)
        with self._connect() as conn:
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
                    next_steps      TEXT
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    # ================================================================
    # RECORD TRADES
    # ================================================================

    def record_trades(
        self,
        positions: list[dict],
        market_condition: str = "",
    ):
        """
        Stores all closed positions from today's session into the DB.
        Skips positions that are still open.
        """
        today = str(datetime.date.today())
        closed = [p for p in positions if p.get("status") == "CLOSED"]

        if not closed:
            self.log.info("No closed trades to record")
            return

        with self._connect() as conn:
            for p in closed:
                conn.execute(
                    """INSERT INTO trades
                       (date, symbol, side, entry_price, exit_price, qty,
                        pnl, exit_reason, claude_confidence, market_condition)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        today,
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

        self.log.success(f"Recorded {len(closed)} trades to performance database")

    # ================================================================
    # QUERIES
    # ================================================================

    def get_yesterday_summary(self) -> dict | None:
        """
        Returns a summary of the previous trading day's results.
        Returns None if no data found.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT DISTINCT date FROM trades ORDER BY date DESC LIMIT 1"
            ).fetchone()

            if not row:
                return None

            last_date = row["date"]
            rows = conn.execute(
                "SELECT * FROM trades WHERE date = ?", (last_date,)
            ).fetchall()

        if not rows:
            return None

        total   = len(rows)
        winners = [r for r in rows if r["pnl"] > 0]
        losers  = [r for r in rows if r["pnl"] < 0]
        net_pnl = sum(r["pnl"] for r in rows)

        # Top performer and worst performer
        best  = max(rows, key=lambda r: r["pnl"])
        worst = min(rows, key=lambda r: r["pnl"])

        return {
            "date":         last_date,
            "total_trades": total,
            "winners":      len(winners),
            "losers":       len(losers),
            "win_rate":     len(winners) / total * 100 if total > 0 else 0,
            "net_pnl":      round(net_pnl, 2),
            "best_stock":   best["symbol"],
            "best_pnl":     round(best["pnl"], 2),
            "worst_stock":  worst["symbol"],
            "worst_pnl":    round(worst["pnl"], 2),
        }

    def get_stock_history(self, symbol: str) -> dict | None:
        """
        Returns historical win rate and avg P&L for a specific stock.
        Returns None if no trades found for the stock.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pnl FROM trades WHERE symbol = ?", (symbol,)
            ).fetchall()

        if not rows:
            return None

        total    = len(rows)
        winners  = sum(1 for r in rows if r["pnl"] > 0)
        total_pnl = sum(r["pnl"] for r in rows)

        return {
            "symbol":    symbol,
            "total":     total,
            "win_rate":  round(winners / total * 100, 1) if total > 0 else 0,
            "avg_pnl":   round(total_pnl / total, 2) if total > 0 else 0,
            "total_pnl": round(total_pnl, 2),
        }

    def get_claude_prompt_context(self) -> str:
        """
        Formats the last 5 trading days of performance into a string
        suitable for prepending to Claude's stock selection prompt.

        Returns empty string if no historical data exists.
        """
        with self._connect() as conn:
            dates = conn.execute(
                "SELECT DISTINCT date FROM trades ORDER BY date DESC LIMIT 5"
            ).fetchall()

        if not dates:
            return ""

        lines = ["\nRECENT TRADING PERFORMANCE (last 5 days):"]

        with self._connect() as conn:
            for d in dates:
                date = d["date"]
                rows = conn.execute(
                    "SELECT symbol, pnl, exit_reason FROM trades WHERE date = ?",
                    (date,),
                ).fetchall()

                total    = len(rows)
                net_pnl  = sum(r["pnl"] for r in rows)
                winners  = sum(1 for r in rows if r["pnl"] > 0)
                win_rate = winners / total * 100 if total > 0 else 0

                sl_count = sum(1 for r in rows if r["exit_reason"] == "STOP_LOSS")

                lines.append(
                    f"  {date}: {total} trades, {win_rate:.0f}% win rate, "
                    f"Net P&L: ₹{net_pnl:+,.2f}, SL exits: {sl_count}"
                )

                # Flag consistently losing stocks
                losers = [r["symbol"] for r in rows if r["pnl"] < 0]
                if losers:
                    lines.append(f"    Losing stocks: {', '.join(losers)}")

        lines.append(
            "  Use this data to AVOID stocks that have been consistently losing "
            "and FAVOUR patterns that have been working.\n"
        )

        return "\n".join(lines)

    # ================================================================
    # PHASE 1 — PORTFOLIO ANALYSIS RECORDING
    # ================================================================

    def record_portfolio_analyses(
        self,
        portfolio: list[dict],
        analyses: list[dict],
    ):
        """
        Stores Phase 1 portfolio analysis results into the DB.
        Called by PortfolioAnalyser after the report is saved.

        Each analysis entry is a dict with 'symbol', 'stock' (enriched data),
        and 'parsed' (Claude's structured fields).
        """
        today = str(datetime.date.today())

        if not analyses:
            self.log.info("No analyses to record")
            return

        # Build stock lookup for portfolio data
        stock_by_symbol = {s["symbol"]: s for s in portfolio}

        with self._connect() as conn:
            for a in analyses:
                symbol = a.get("symbol", "")
                parsed = a.get("parsed", {})
                stock  = stock_by_symbol.get(symbol, a.get("stock", {}))

                conn.execute(
                    """INSERT INTO portfolio_analyses
                       (date, symbol, action, conviction, reasoning, horizon,
                        target_price, current_price, invested_value, current_value,
                        stock_pnl, stock_pnl_pct, action_detail, num_stocks,
                        trigger_price, trigger_action, risks, watch, next_steps)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        today,
                        symbol,
                        parsed.get("ACTION", ""),
                        parsed.get("CONVICTION", ""),
                        parsed.get("REASONING", ""),
                        parsed.get("HORIZON", ""),
                        parsed.get("TARGET_PRICE", ""),
                        stock.get("last_price", 0),
                        stock.get("invested_value", 0),
                        stock.get("current_value", 0),
                        stock.get("current_value", 0) - stock.get("invested_value", 0),
                        (
                            (stock.get("current_value", 0) - stock.get("invested_value", 0))
                            / stock.get("invested_value", 1) * 100
                            if stock.get("invested_value", 0) > 0 else 0
                        ),
                        parsed.get("ACTION_DETAIL", ""),
                        int(parsed.get("NUM_STOCKS", 0) or 0),
                        parsed.get("TRIGGER_PRICE", ""),
                        parsed.get("TRIGGER_ACTION", ""),
                        parsed.get("RISKS", ""),
                        parsed.get("WATCH", ""),
                        parsed.get("NEXT_STEPS", ""),
                    ),
                )

        self.log.success(f"Recorded {len(analyses)} portfolio analyses to database")

    def get_latest_portfolio_analysis(self) -> dict | None:
        """
        Returns the most recent portfolio analysis data from the DB,
        structured to match what find_latest_portfolio_data() returns
        from JSON files. This allows callers to use the DB as the
        source of truth instead of scanning files.

        Returns None if no data exists.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT DISTINCT date FROM portfolio_analyses ORDER BY date DESC LIMIT 1"
            ).fetchone()

            if not row:
                return None

            last_date = row["date"]
            rows = conn.execute(
                "SELECT * FROM portfolio_analyses WHERE date = ?", (last_date,)
            ).fetchall()

        if not rows:
            return None

        # Reconstruct the structure that AnalysisQueue.load() expects:
        # { "date": ..., "portfolio": [...], "analyses": [...] }
        portfolio = []
        analyses  = []

        for r in rows:
            stock_data = {
                "symbol":         r["symbol"],
                "last_price":     r["current_price"],
                "invested_value": r["invested_value"],
                "current_value":  r["current_value"],
            }
            portfolio.append(stock_data)

            analyses.append({
                "symbol": r["symbol"],
                "parsed": {
                    "ACTION":            r["action"],
                    "CONVICTION":        r["conviction"],
                    "REASONING":         r["reasoning"],
                    "HORIZON":           r["horizon"],
                    "TARGET_PRICE":      r["target_price"],
                    "ACTION_DETAIL":     r["action_detail"],
                    "NUM_STOCKS":        str(r["num_stocks"]),
                    "TRIGGER_PRICE":     r["trigger_price"],
                    "TRIGGER_ACTION":    r["trigger_action"],
                    "RISKS":             r["risks"],
                    "WATCH":             r["watch"],
                    "NEXT_STEPS":        r["next_steps"],
                },
            })

        return {
            "date":      last_date,
            "portfolio": portfolio,
            "analyses":  analyses,
        }

    def get_portfolio_history(self, symbol: str) -> list[dict]:
        """
        Returns the analysis history for a specific stock across all
        Phase 1 runs. Useful for tracking how Claude's recommendation
        has evolved over time.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT date, action, conviction, target_price,
                          current_price, stock_pnl_pct, reasoning
                   FROM portfolio_analyses
                   WHERE symbol = ?
                   ORDER BY date DESC
                   LIMIT 10""",
                (symbol,),
            ).fetchall()

        return [
            {
                "date":         r["date"],
                "action":       r["action"],
                "conviction":   r["conviction"],
                "target_price": r["target_price"],
                "price":        r["current_price"],
                "pnl_pct":      round(r["stock_pnl_pct"], 2),
                "reasoning":    r["reasoning"],
            }
            for r in rows
        ]
