# ================================================================
# modes/swing/persistence.py
# ================================================================
# SQLite store for swing trading runs, candidates, actions, and
# positions. Lives at data/swing.db. Pattern mirrors
# modes/analyze/persistence.py.
#
# Schema: SWING_ROADMAP S4 (2026-05-13).
# ================================================================

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from config import now_ist
from modes.swing.types import (
    SwingAction, SwingCandidate, SwingPosition, SwingRunResult,
    STATUS_PENDING, POS_OPEN, POS_CLOSED,
)


DB_PATH = os.path.join("data", "swing.db")


# ── Connection helpers ───────────────────────────────────────────

def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def _connect(path: str = DB_PATH) -> Iterator[sqlite3.Connection]:
    _ensure_dir()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Schema ──────────────────────────────────────────────────────

def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS swing_runs (
            run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            mode            TEXT NOT NULL DEFAULT 'NOAI',
            universe        TEXT,
            market_regime   TEXT,
            run_for_date    TEXT,
            trigger_source  TEXT,
            user_requested_ai INTEGER NOT NULL DEFAULT 0,
            rerun_of_run_id INTEGER,
            rerun_reason    TEXT,
            candidates_seen INTEGER,
            candidates_kept INTEGER,
            blocked_reason  TEXT,
            notes           TEXT
        );

        CREATE TABLE IF NOT EXISTS swing_candidates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER NOT NULL REFERENCES swing_runs(run_id)
                                ON DELETE CASCADE,
            symbol          TEXT NOT NULL,
            exchange        TEXT NOT NULL DEFAULT 'NSE',
            setup_type      TEXT NOT NULL,
            score           REAL,
            priority_rank   INTEGER,
            priority_score  REAL,
            close_price     REAL,
            entry_price     REAL,
            stop_price      REAL,
            target_price    REAL,
            risk_rupees     REAL,
            reward_rupees   REAL,
            rr_ratio        REAL,
            suggested_qty   INTEGER,
            status          TEXT NOT NULL DEFAULT 'SCORED',
            rejected_reason TEXT,
            broker_instruction_json TEXT,
            ai_overlay_json TEXT,
            snapshot_json   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS swing_actions (
            action_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER NOT NULL REFERENCES swing_runs(run_id)
                                ON DELETE CASCADE,
            candidate_id    INTEGER REFERENCES swing_candidates(id),
            position_id     INTEGER REFERENCES swing_positions(position_id),
            symbol          TEXT NOT NULL,
            exchange        TEXT NOT NULL DEFAULT 'NSE',
            action_type     TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'PENDING',
            suggested_qty   INTEGER,
            suggested_price REAL,
            suggested_stop  REAL,
            suggested_target REAL,
            priority_rank   INTEGER,
            live_price      REAL,
            broker_instruction_json TEXT,
            created_at      TEXT NOT NULL,
            expires_at      TEXT,
            confirmed_at    TEXT,
            executed_qty    INTEGER,
            executed_price  REAL,
            confirmed_stop  REAL,
            confirmation_source TEXT,
            notes           TEXT
        );

        CREATE TABLE IF NOT EXISTS swing_positions (
            position_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            exchange        TEXT NOT NULL DEFAULT 'NSE',
            side            TEXT NOT NULL DEFAULT 'BUY',
            managed_qty     INTEGER NOT NULL,
            entry_price     REAL NOT NULL,
            entry_date      TEXT NOT NULL,
            stop_price      REAL NOT NULL,
            target_price    REAL,
            trailing_stop   REAL,
            status          TEXT NOT NULL DEFAULT 'OPEN',
            source          TEXT NOT NULL,
            linked_run_id   INTEGER,
            linked_action_id INTEGER,
            exit_date       TEXT,
            exit_price      REAL,
            exit_qty        INTEGER,
            gross_pnl       REAL,
            charges         REAL,
            net_pnl         REAL,
            charge_breakdown_json TEXT,
            closed_action_id INTEGER,
            notes           TEXT
        );

        CREATE TABLE IF NOT EXISTS swing_events (
            event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id     INTEGER REFERENCES swing_positions(position_id),
            event_time      TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            old_value       TEXT,
            new_value       TEXT,
            reason          TEXT,
            event_json      TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_swing_candidates_run
            ON swing_candidates(run_id);
        CREATE INDEX IF NOT EXISTS idx_swing_actions_run
            ON swing_actions(run_id);
        CREATE INDEX IF NOT EXISTS idx_swing_actions_status
            ON swing_actions(status);
        CREATE INDEX IF NOT EXISTS idx_swing_positions_status
            ON swing_positions(status);
        CREATE INDEX IF NOT EXISTS idx_swing_runs_date
            ON swing_runs(run_for_date DESC);
    """)


def init_db(path: str = DB_PATH) -> None:
    """Create DB + schema. Idempotent."""
    with _connect(path) as conn:
        _ensure_schema(conn)


# ── Write: runs ─────────────────────────────────────────────────

def save_run(result: SwingRunResult, path: str = DB_PATH) -> int:
    """Persist a complete swing run: run row + candidates + actions.
    Returns the run_id."""
    with _connect(path) as conn:
        _ensure_schema(conn)

        cur = conn.execute("""
            INSERT INTO swing_runs (
                started_at, finished_at, mode, universe, market_regime,
                run_for_date, trigger_source, user_requested_ai,
                candidates_seen, candidates_kept, blocked_reason, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            result.started_at,
            result.finished_at,
            result.mode,
            result.universe,
            result.market_regime,
            result.run_for_date,
            result.trigger_source,
            1 if result.mode == "AI" else 0,
            len(result.candidates),
            sum(1 for c in result.candidates if c.status in ("ACCEPTED", "PLANNED")),
            result.blocked_reason,
            result.notes,
        ))
        run_id = int(cur.lastrowid or 0)

        # Candidates
        for c in result.candidates:
            cur2 = conn.execute("""
                INSERT INTO swing_candidates (
                    run_id, symbol, exchange, setup_type, score,
                    priority_rank, priority_score, close_price,
                    entry_price, stop_price, target_price,
                    risk_rupees, reward_rupees, rr_ratio,
                    suggested_qty, status, rejected_reason,
                    broker_instruction_json, ai_overlay_json,
                    snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, c.symbol, c.exchange, c.setup_type, c.score,
                c.priority_rank, c.priority_score, c.close_price,
                c.entry_price, c.stop_price, c.target_price,
                c.risk_rupees, c.reward_rupees, c.rr_ratio,
                c.suggested_qty, c.status, c.rejected_reason,
                c.broker_instruction_json, c.ai_overlay_json,
                json.dumps(c.snapshot_dict(), default=str),
            ))
            c._id = int(cur2.lastrowid or 0)
            c._run_id = run_id

        # Actions
        for a in result.actions:
            a.run_id = run_id
            # Link candidate_id if symbol matches
            for c in result.candidates:
                if c.symbol == a.symbol and c._id:
                    a.candidate_id = c._id
                    break
            cur3 = conn.execute("""
                INSERT INTO swing_actions (
                    run_id, candidate_id, position_id, symbol, exchange,
                    action_type, status, suggested_qty, suggested_price,
                    suggested_stop, suggested_target, priority_rank,
                    live_price, broker_instruction_json, created_at,
                    expires_at, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, a.candidate_id or None, a.position_id or None,
                a.symbol, a.exchange, a.action_type, a.status,
                a.suggested_qty, a.suggested_price, a.suggested_stop,
                a.suggested_target, a.priority_rank, a.live_price,
                a.broker_instruction_json, a.created_at,
                a.expires_at, a.notes,
            ))
            a.action_id = int(cur3.lastrowid or 0)

        return run_id


# ── Write: action confirmation ──────────────────────────────────

def confirm_action(
    action_id: int,
    executed_qty: int,
    executed_price: float,
    source: str = "DASHBOARD",
    confirmed_stop: float = 0.0,
    notes: str = "",
    path: str = DB_PATH,
) -> SwingPosition | None:
    """Confirm a PENDING action. Creates a position for ENTRY actions,
    or updates an existing position for stop/exit actions.
    Returns the affected SwingPosition or None."""
    with _connect(path) as conn:
        _ensure_schema(conn)

        row = conn.execute(
            "SELECT * FROM swing_actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        if not row:
            return None

        action = _row_to_action(row)
        if action.status != STATUS_PENDING:
            return None

        ts = now_ist().isoformat()

        conn.execute("""
            UPDATE swing_actions
            SET status = 'CONFIRMED', confirmed_at = ?,
                executed_qty = ?, executed_price = ?,
                confirmed_stop = ?, confirmation_source = ?, notes = ?
            WHERE action_id = ?
        """, (ts, executed_qty, executed_price, confirmed_stop,
              source, notes, action_id))

        if action.action_type == "ENTRY":
            cur = conn.execute("""
                INSERT INTO swing_positions (
                    symbol, exchange, side, managed_qty, entry_price,
                    entry_date, stop_price, target_price, status,
                    source, linked_run_id, linked_action_id, notes
                ) VALUES (?, ?, 'BUY', ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)
            """, (
                action.symbol, action.exchange, executed_qty,
                executed_price, ts[:10],
                confirmed_stop or action.suggested_stop,
                action.suggested_target, source,
                action.run_id, action_id, notes,
            ))
            pos_id = int(cur.lastrowid or 0)

            conn.execute("""
                INSERT INTO swing_events (position_id, event_time,
                    event_type, new_value, reason)
                VALUES (?, ?, 'ENTRY', ?, 'Confirmed via dashboard/CLI')
            """, (pos_id, ts, json.dumps({
                "qty": executed_qty, "price": executed_price,
            })))

            return _load_position(conn, pos_id)

        elif action.action_type in ("FULL_EXIT", "PARTIAL_EXIT"):
            pos_id = action.position_id
            if not pos_id:
                return None
            pos = _load_position(conn, pos_id)
            if not pos:
                return None

            gross = (executed_price - pos.entry_price) * executed_qty
            charges = _estimate_delivery_charges(
                pos.entry_price, executed_price, executed_qty)
            net = gross - charges

            if action.action_type == "FULL_EXIT" or executed_qty >= pos.managed_qty:
                conn.execute("""
                    UPDATE swing_positions
                    SET status = 'CLOSED', exit_date = ?, exit_price = ?,
                        exit_qty = ?, gross_pnl = ?, charges = ?,
                        net_pnl = ?, closed_action_id = ?
                    WHERE position_id = ?
                """, (ts[:10], executed_price, executed_qty,
                      gross, charges, net, action_id, pos_id))
            else:
                new_qty = pos.managed_qty - executed_qty
                conn.execute("""
                    UPDATE swing_positions
                    SET managed_qty = ?
                    WHERE position_id = ?
                """, (new_qty, pos_id))

            conn.execute("""
                INSERT INTO swing_events (position_id, event_time,
                    event_type, old_value, new_value, reason)
                VALUES (?, ?, 'EXIT', ?, ?, ?)
            """, (pos_id, ts,
                  json.dumps({"qty": pos.managed_qty}),
                  json.dumps({"qty": executed_qty, "price": executed_price,
                              "gross": gross, "charges": charges, "net": net}),
                  f"Exit confirmed via {source}"))

            return _load_position(conn, pos_id)

        elif action.action_type == "TIGHTEN_STOP":
            pos_id = action.position_id
            if not pos_id:
                return None
            pos = _load_position(conn, pos_id)
            if not pos:
                return None

            old_stop = pos.stop_price
            conn.execute("""
                UPDATE swing_positions SET stop_price = ?
                WHERE position_id = ?
            """, (confirmed_stop, pos_id))

            conn.execute("""
                INSERT INTO swing_events (position_id, event_time,
                    event_type, old_value, new_value, reason)
                VALUES (?, ?, 'STOP_MOVE', ?, ?, ?)
            """, (pos_id, ts, str(old_stop), str(confirmed_stop),
                  f"Stop tightened via {source}"))

            return _load_position(conn, pos_id)

        return None


def skip_action(action_id: int, reason: str = "",
                path: str = DB_PATH) -> bool:
    """Mark a PENDING action as SKIPPED."""
    with _connect(path) as conn:
        _ensure_schema(conn)
        cur = conn.execute("""
            UPDATE swing_actions
            SET status = 'SKIPPED', notes = ?
            WHERE action_id = ? AND status = 'PENDING'
        """, (reason, action_id))
        return (cur.rowcount or 0) > 0


# ── Read: runs ──────────────────────────────────────────────────

def latest_run(path: str = DB_PATH) -> dict | None:
    """Most recent swing run regardless of date."""
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM swing_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def latest_run_for_date(trade_date: str,
                        path: str = DB_PATH) -> dict | None:
    """Latest run row for a given trading date."""
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """SELECT * FROM swing_runs
               WHERE run_for_date = ?
               ORDER BY run_id DESC LIMIT 1""",
            (trade_date,),
        ).fetchone()
        return dict(row) if row else None


def latest_run_for_date_and_mode(trade_date: str, mode: str,
                                 path: str = DB_PATH) -> dict | None:
    """Latest run row for a given date + mode (NOAI / AI)."""
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """SELECT * FROM swing_runs
               WHERE run_for_date = ? AND mode = ?
               ORDER BY run_id DESC LIMIT 1""",
            (trade_date, mode),
        ).fetchone()
        return dict(row) if row else None


# ── Read: actions ───────────────────────────────────────────────

def pending_actions(path: str = DB_PATH) -> list[SwingAction]:
    """All PENDING actions, newest first."""
    if not os.path.exists(path):
        return []
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """SELECT * FROM swing_actions
               WHERE status = 'PENDING'
               ORDER BY priority_rank ASC, action_id DESC"""
        ).fetchall()
        return [_row_to_action(r) for r in rows]


def candidates_for_run(run_id: int, path: str = DB_PATH) -> list[SwingCandidate]:
    """All candidates for a given run, accepted first, by priority."""
    if not os.path.exists(path):
        return []
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """SELECT * FROM swing_candidates
               WHERE run_id = ?
               ORDER BY
                 CASE status WHEN 'ACCEPTED' THEN 0 ELSE 1 END,
                 priority_rank ASC, id ASC""",
            (run_id,),
        ).fetchall()
        return [_row_to_candidate(r) for r in rows]


def candidate_by_symbol(symbol: str, path: str = DB_PATH) -> SwingCandidate | None:
    """Most recent candidate record for a symbol (any status).
    Prefers non-ATH_DIP candidates when multiple exist for the same run."""
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        # First try: most recent non-ATH candidate (has richer data)
        row = conn.execute(
            """SELECT * FROM swing_candidates
               WHERE symbol = ? AND setup_type != 'ATH_DIP'
               ORDER BY run_id DESC, id DESC
               LIMIT 1""",
            (symbol.upper(),),
        ).fetchone()
        if row:
            return _row_to_candidate(row)
        # Fallback: any candidate including ATH
        row = conn.execute(
            """SELECT * FROM swing_candidates
               WHERE symbol = ?
               ORDER BY run_id DESC, id DESC
               LIMIT 1""",
            (symbol.upper(),),
        ).fetchone()
        return _row_to_candidate(row) if row else None


def ath_candidate_by_symbol(symbol: str, path: str = DB_PATH) -> SwingCandidate | None:
    """Most recent ATH_DIP candidate for a symbol."""
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """SELECT * FROM swing_candidates
               WHERE symbol = ? AND setup_type = 'ATH_DIP'
               ORDER BY run_id DESC, id DESC
               LIMIT 1""",
            (symbol.upper(),),
        ).fetchone()
        return _row_to_candidate(row) if row else None


def actions_for_run(run_id: int, path: str = DB_PATH) -> list[SwingAction]:
    """All actions for a given run."""
    if not os.path.exists(path):
        return []
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """SELECT * FROM swing_actions
               WHERE run_id = ?
               ORDER BY priority_rank ASC, action_id ASC""",
            (run_id,),
        ).fetchall()
        return [_row_to_action(r) for r in rows]


# ── Read: positions ─────────────────────────────────────────────

def open_positions(path: str = DB_PATH) -> list[SwingPosition]:
    """All OPEN swing positions."""
    if not os.path.exists(path):
        return []
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """SELECT * FROM swing_positions
               WHERE status = 'OPEN'
               ORDER BY entry_date ASC"""
        ).fetchall()
        return [_row_to_position(r) for r in rows]


def all_positions(path: str = DB_PATH) -> list[SwingPosition]:
    """All positions (open + closed)."""
    if not os.path.exists(path):
        return []
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM swing_positions ORDER BY position_id DESC"
        ).fetchall()
        return [_row_to_position(r) for r in rows]


def realised_pnl_summary(path: str = DB_PATH) -> dict:
    """Aggregate realised P&L from closed positions."""
    if not os.path.exists(path):
        return {"gross_pnl": 0.0, "charges": 0.0, "net_pnl": 0.0, "count": 0}
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute("""
            SELECT COALESCE(SUM(gross_pnl), 0) AS gross,
                   COALESCE(SUM(charges), 0)   AS charges,
                   COALESCE(SUM(net_pnl), 0)   AS net,
                   COUNT(*)                    AS cnt
            FROM swing_positions
            WHERE status = 'CLOSED'
        """).fetchone()
        return {
            "gross_pnl": float(row["gross"]),
            "charges":   float(row["charges"]),
            "net_pnl":   float(row["net"]),
            "count":     int(row["cnt"]),
        }


# ── Helpers ─────────────────────────────────────────────────────

def _row_to_action(row: sqlite3.Row) -> SwingAction:
    d = dict(row)
    return SwingAction.from_dict(d)


def _row_to_candidate(row: sqlite3.Row) -> SwingCandidate:
    d = dict(row)
    # Reconstruct from snapshot_json if present (has the full data)
    snap = d.get("snapshot_json")
    if snap:
        try:
            full = json.loads(snap)
            return SwingCandidate.from_dict(full)
        except (json.JSONDecodeError, TypeError):
            pass
    return SwingCandidate.from_dict(d)


def _row_to_position(row: sqlite3.Row) -> SwingPosition:
    d = dict(row)
    return SwingPosition.from_dict(d)


def _load_position(conn: sqlite3.Connection,
                   pos_id: int) -> SwingPosition | None:
    row = conn.execute(
        "SELECT * FROM swing_positions WHERE position_id = ?",
        (pos_id,),
    ).fetchone()
    return _row_to_position(row) if row else None


def _estimate_delivery_charges(entry_price: float, exit_price: float,
                               qty: int) -> float:
    """Estimate statutory delivery charges for a CNC round-trip.

    Zerodha delivery brokerage is zero. Statutory charges:
    - STT: 0.1% on both buy + sell value
    - Exchange txn: 0.00345% on turnover
    - GST: 18% on (brokerage + exchange txn)
    - SEBI: Rs.10 per crore of turnover
    - Stamp duty: 0.015% on buy-side value only

    This is an estimate; actual charges come from the Zerodha
    contract note. The estimate is close enough for P&L tracking.
    """
    buy_value = entry_price * qty
    sell_value = exit_price * qty
    turnover = buy_value + sell_value

    stt = turnover * 0.001          # 0.1% on both legs
    exchange_txn = turnover * 0.0000345
    brokerage = 0.0                 # Zerodha delivery = zero
    gst = (brokerage + exchange_txn) * 0.18
    sebi = turnover * 0.000001      # Rs.10 per crore
    stamp = buy_value * 0.00015     # 0.015% on buy side

    return round(stt + exchange_txn + gst + sebi + stamp, 2)
