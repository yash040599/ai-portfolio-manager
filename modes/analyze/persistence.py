# ================================================================
# modes/analyze/persistence.py
# ================================================================
# SQLite store for `--mode analyze` runs.
#
# Schema (ANALYZE_ROADMAP P2, 2026-05-12):
#
#   portfolio_runs(run_id, started_at, finished_at, mode,
#                  holdings_count, portfolio_value, portfolio_pnl,
#                  metrics_json, gaps_json, notes)
#   stock_analyses(run_id, symbol, exchange, action, conviction,
#                  horizon, target_price, current_value, pnl,
#                  pnl_pct, most_stale_at, analysis_json,
#                  PRIMARY KEY(run_id, symbol))
#   INDEX idx_stock_analyses_symbol_run ON stock_analyses(symbol, run_id DESC)
#
# This DB is the contract the dashboard `/portfolio` page (D25-D29)
# reads from. Reads are microseconds; writes happen once per
# `--mode analyze` invocation. Pattern mirrors `shared/tax_db.py`.
# ================================================================

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from config import now_ist
from modes.analyze.types import (
    GapAnalysis,
    GapFlag,
    PortfolioMetrics,
    PortfolioSnapshot,
    SectorWeight,
    StockAnalysis,
    Field,
)


DB_PATH = os.path.join("data", "portfolio_analyses.db")


# ── Connection helpers ───────────────────────────────────────────

def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def _connect(path: str = DB_PATH) -> Iterator[sqlite3.Connection]:
    _ensure_dir()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_runs (
            run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at       TEXT NOT NULL,
            finished_at      TEXT,
            mode             TEXT NOT NULL,
            holdings_count   INTEGER,
            portfolio_value  REAL,
            portfolio_pnl    REAL,
            metrics_json     TEXT,
            gaps_json        TEXT,
            notes            TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_analyses (
            run_id           INTEGER NOT NULL REFERENCES portfolio_runs(run_id)
                                ON DELETE CASCADE,
            symbol           TEXT    NOT NULL,
            exchange         TEXT    NOT NULL,
            action           TEXT,
            conviction       TEXT,
            horizon          TEXT,
            target_price     TEXT,
            current_value    REAL,
            pnl              REAL,
            pnl_pct          REAL,
            most_stale_at    TEXT NOT NULL,
            analysis_json    TEXT NOT NULL,
            PRIMARY KEY (run_id, symbol)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_stock_analyses_symbol_run
            ON stock_analyses(symbol, run_id DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_portfolio_runs_started_at
            ON portfolio_runs(started_at DESC)
    """)


def init_db(path: str = DB_PATH) -> None:
    """Create the DB and schema if they don't exist. Idempotent."""
    with _connect(path) as conn:
        _ensure_schema(conn)


# ── Write path ──────────────────────────────────────────────────

def save_snapshot(snapshot: PortfolioSnapshot, path: str = DB_PATH) -> int:
    """Persist a complete `--mode analyze` run.

    Returns the new `run_id`. Writes one `portfolio_runs` row plus
    one `stock_analyses` row per holding in a single transaction.
    """
    with _connect(path) as conn:
        _ensure_schema(conn)

        finished_at = now_ist().isoformat()
        cur = conn.execute(
            """
            INSERT INTO portfolio_runs (
                started_at, finished_at, mode, holdings_count,
                portfolio_value, portfolio_pnl, metrics_json,
                gaps_json, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.timestamp.isoformat(),
                finished_at,
                snapshot.mode,
                len(snapshot.holdings),
                _field_value(snapshot.metrics.total_current_value),
                _field_value(snapshot.metrics.total_pnl),
                json.dumps(snapshot.metrics.to_dict(), default=str),
                json.dumps(snapshot.gaps.to_dict(), default=str),
                snapshot.notes or "",
            ),
        )
        run_id = int(cur.lastrowid or 0)

        rows = []
        for h in snapshot.holdings:
            rows.append((
                run_id,
                h.symbol,
                h.exchange,
                h.effective_action(),
                _field_value(h.rule_conviction),
                _field_value(h.rule_horizon),
                _field_value(h.rule_target_price),
                _field_value(h.current_value),
                _field_value(h.pnl),
                _field_value(h.pnl_pct),
                h.most_stale_at().isoformat(),
                json.dumps(h.to_dict(), default=str),
            ))
        conn.executemany(
            """
            INSERT INTO stock_analyses (
                run_id, symbol, exchange, action, conviction, horizon,
                target_price, current_value, pnl, pnl_pct,
                most_stale_at, analysis_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

        return run_id


def _field_value(f):
    """Pull `Field.value` if present, else None. Tolerates plain
    Python values too so callers can pass either."""
    if f is None:
        return None
    if isinstance(f, Field):
        return f.value
    return f


# ── Read path ───────────────────────────────────────────────────

def latest_run(path: str = DB_PATH) -> dict | None:
    """Returns the most recent `portfolio_runs` row as a dict, or
    None when the DB is empty / missing."""
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM portfolio_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def runs_between(d_from: str, d_to: str,
                 path: str = DB_PATH) -> list[dict]:
    """Returns runs whose `started_at` ISO date falls inside
    [d_from, d_to] inclusive (lexical compare on ISO strings)."""
    if not os.path.exists(path):
        return []
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT * FROM portfolio_runs
            WHERE substr(started_at, 1, 10) BETWEEN ? AND ?
            ORDER BY run_id DESC
            """,
            (d_from, d_to),
        ).fetchall()
        return [dict(r) for r in rows]


def latest_for_symbol(symbol: str,
                      path: str = DB_PATH) -> StockAnalysis | None:
    """Returns the most recent `StockAnalysis` for a symbol, or
    None when the symbol has never been analysed."""
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT analysis_json FROM stock_analyses
            WHERE symbol = ?
            ORDER BY run_id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        if not row:
            return None
        return _decode_stock(row["analysis_json"])


def history_for_symbol(symbol: str, limit: int = 5,
                       path: str = DB_PATH) -> list[StockAnalysis]:
    """Returns the last N analyses for a symbol, newest first.
    Used by the dashboard drill-down strip (D29)."""
    if not os.path.exists(path):
        return []
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT analysis_json FROM stock_analyses
            WHERE symbol = ?
            ORDER BY run_id DESC
            LIMIT ?
            """,
            (symbol, max(1, int(limit))),
        ).fetchall()
        return [_decode_stock(r["analysis_json"]) for r in rows]


def stocks_for_run(run_id: int,
                   path: str = DB_PATH) -> list[StockAnalysis]:
    """Returns every `StockAnalysis` for one run, ordered by symbol."""
    if not os.path.exists(path):
        return []
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT analysis_json FROM stock_analyses
            WHERE run_id = ?
            ORDER BY symbol ASC
            """,
            (int(run_id),),
        ).fetchall()
        return [_decode_stock(r["analysis_json"]) for r in rows]


def latest_snapshot(path: str = DB_PATH) -> PortfolioSnapshot | None:
    """Reconstructs the full `PortfolioSnapshot` for the most recent
    run. Used by the dashboard `/portfolio` page (D25)."""
    run = latest_run(path)
    if not run:
        return None
    holdings = stocks_for_run(int(run["run_id"]), path)
    metrics = _decode_metrics(run.get("metrics_json"))
    gaps    = _decode_gaps(run.get("gaps_json"))
    import datetime as _dt
    try:
        ts = _dt.datetime.fromisoformat(run["started_at"])
    except (TypeError, ValueError):
        ts = now_ist()
    return PortfolioSnapshot(
        timestamp = ts,
        mode      = str(run["mode"]),
        holdings  = holdings,
        metrics   = metrics,
        gaps      = gaps,
        notes     = run.get("notes") or "",
    )


# ── JSON decoders ───────────────────────────────────────────────

def _decode_stock(blob: str) -> StockAnalysis:
    return StockAnalysis.from_dict(json.loads(blob))


def _decode_metrics(blob: str | None) -> PortfolioMetrics:
    if not blob:
        return _empty_metrics()
    d = json.loads(blob)
    sector_weights = [
        SectorWeight(**sw) for sw in d.get("sector_weights", []) or []
    ]
    return PortfolioMetrics(
        sector_weights          = sector_weights,
        hhi_concentration       = Field.from_dict(d.get("hhi_concentration"))      or Field.missing(),
        top_5_concentration_pct = Field.from_dict(d.get("top_5_concentration_pct")) or Field.missing(),
        single_name_max_pct     = Field.from_dict(d.get("single_name_max_pct"))    or Field.missing(),
        single_name_max_symbol  = Field.from_dict(d.get("single_name_max_symbol")) or Field.missing(),
        group_concentration     = Field.from_dict(d.get("group_concentration"))    or Field.missing(),
        weighted_pe             = Field.from_dict(d.get("weighted_pe"))            or Field.missing(),
        weighted_dividend_yield = Field.from_dict(d.get("weighted_dividend_yield")) or Field.missing(),
        portfolio_beta_vs_nifty = Field.from_dict(d.get("portfolio_beta_vs_nifty")) or Field.missing(),
        total_invested          = Field.from_dict(d.get("total_invested"))         or Field.missing(),
        total_current_value     = Field.from_dict(d.get("total_current_value"))    or Field.missing(),
        total_pnl               = Field.from_dict(d.get("total_pnl"))              or Field.missing(),
        total_pnl_pct           = Field.from_dict(d.get("total_pnl_pct"))          or Field.missing(),
        # Optional risk/return + cash slots — populated only when the
        # writer had data; round-trip nulls back to None so the renderer
        # knows to skip the section instead of showing zeros.
        volatility_30d_pct       = Field.from_dict(d.get("volatility_30d_pct")),
        sharpe_ratio             = Field.from_dict(d.get("sharpe_ratio")),
        max_drawdown_pct         = Field.from_dict(d.get("max_drawdown_pct")),
        xirr_pct                 = Field.from_dict(d.get("xirr_pct")),
        annual_dividend_estimate = Field.from_dict(d.get("annual_dividend_estimate")),
        cash_balance             = Field.from_dict(d.get("cash_balance")),
        cash_drag_pct            = Field.from_dict(d.get("cash_drag_pct")),
        cap_tier_weights         = Field.from_dict(d.get("cap_tier_weights")),
    )


def _decode_gaps(blob: str | None) -> GapAnalysis:
    if not blob:
        return GapAnalysis(flags=[], benchmark_label="")
    d = json.loads(blob)
    flags = [GapFlag(**f) for f in d.get("flags", []) or []]
    return GapAnalysis(flags=flags, benchmark_label=d.get("benchmark_label", ""))


def _empty_metrics() -> PortfolioMetrics:
    miss = Field.missing()
    return PortfolioMetrics(
        sector_weights          = [],
        hhi_concentration       = miss,
        top_5_concentration_pct = miss,
        single_name_max_pct     = miss,
        single_name_max_symbol  = miss,
        group_concentration     = miss,
        weighted_pe             = miss,
        weighted_dividend_yield = miss,
        portfolio_beta_vs_nifty = miss,
        total_invested          = miss,
        total_current_value     = miss,
        total_pnl               = miss,
        total_pnl_pct           = miss,
    )
