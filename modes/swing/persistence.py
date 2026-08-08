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
from dataclasses import dataclass
from typing import Iterator

from config import now_ist
from modes.swing.types import (
    SwingAction, SwingCandidate, SwingPosition, SwingRunResult,
    STATUS_PENDING,
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
            notes           TEXT,
            is_snapshot     INTEGER NOT NULL DEFAULT 0
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

        CREATE TABLE IF NOT EXISTS swing_watchlist (
            watchlist_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT NOT NULL,
            exchange        TEXT NOT NULL DEFAULT 'NSE',
            added_at        TEXT NOT NULL,
            added_price     REAL NOT NULL,
            setup_type      TEXT,
            action_id       INTEGER,
            notes           TEXT,
            status          TEXT NOT NULL DEFAULT 'WATCHING',
            removed_at      TEXT
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

    # Migration (S29): older `data/swing.db` files were created
    # before `swing_runs.is_snapshot` existed. SQLite has no
    # `ADD COLUMN IF NOT EXISTS`, so we probe via PRAGMA and only
    # ALTER when the column is missing. Default value 0 means every
    # legacy row is treated as a "real" (non-snapshot) run, matching
    # behaviour from before S29 shipped.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(swing_runs)")}
    if "is_snapshot" not in cols:
        conn.execute(
            "ALTER TABLE swing_runs ADD COLUMN is_snapshot "
            "INTEGER NOT NULL DEFAULT 0"
        )


def init_db(path: str = DB_PATH) -> None:
    """Create DB + schema. Idempotent."""
    with _connect(path) as conn:
        _ensure_schema(conn)


# ── Write: runs ─────────────────────────────────────────────────

def save_run(result: SwingRunResult, path: str = DB_PATH,
             is_snapshot: bool = False) -> int:
    """Persist a complete swing run: run row + candidates + actions.
    Returns the run_id.

    `is_snapshot=True` (S29) marks the row as a pre-AI checkpoint
    written by `SwingManager.run()` BEFORE the AI overlay loop —
    snapshots are filtered out by `latest_run()` so the dashboard
    always shows the post-AI row when one exists. Snapshots remain
    queryable for audit (full candidate + action lists are still
    persisted).
    """
    with _connect(path) as conn:
        _ensure_schema(conn)

        cur = conn.execute("""
            INSERT INTO swing_runs (
                started_at, finished_at, mode, universe, market_regime,
                run_for_date, trigger_source, user_requested_ai,
                candidates_seen, candidates_kept, blocked_reason,
                notes, is_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            1 if is_snapshot else 0,
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

        def _price_same(left: float, right: float) -> bool:
            return abs(float(left or 0.0) - float(right or 0.0)) <= 0.01

        def _link_candidate_id(a: SwingAction) -> int:
            matches = [c for c in result.candidates
                       if c.symbol == a.symbol and c._id]
            for c in matches:
                if (c.status in ("ACCEPTED", "PLANNED")
                        and int(c.suggested_qty or 0) == int(a.suggested_qty or 0)
                        and _price_same(c.entry_price, a.suggested_price)
                        and _price_same(c.stop_price, a.suggested_stop)
                        and _price_same(c.target_price, a.suggested_target)):
                    return int(c._id)
            for c in matches:
                if c.status in ("ACCEPTED", "PLANNED"):
                    return int(c._id)
            return int(matches[0]._id) if matches else 0

        # Actions
        for a in result.actions:
            a.run_id = run_id
            a.candidate_id = _link_candidate_id(a)
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
            # Re-entrancy guard (S42 hardening, 2026-05-14): two
            # concurrent Done clicks both saw `status='PENDING'` (the
            # check above is racey on default-isolation SQLite) and
            # both INSERTed a fresh position, producing duplicate
            # entries for the same action. Single-user dashboard so
            # the race is rare, but a slow network + impatient
            # double-click reproduces it. Guard: if any position
            # already references this action_id, skip the INSERT and
            # return the existing one — Done is now safe to spam-click.
            existing = conn.execute(
                "SELECT position_id FROM swing_positions "
                "WHERE linked_action_id = ?",
                (action_id,),
            ).fetchone()
            if existing:
                return _load_position(conn, int(existing["position_id"]))

            return _merge_or_insert_entry_position(
                conn,
                symbol=action.symbol,
                exchange=action.exchange,
                executed_qty=executed_qty,
                executed_price=executed_price,
                stop_price=confirmed_stop or action.suggested_stop,
                target_price=action.suggested_target,
                source=source,
                linked_run_id=action.run_id,
                linked_action_id=action_id,
                notes=notes,
                reason="Confirmed via dashboard/CLI",
            )

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
    """Mark a PENDING action as SKIPPED.

    Idempotent: returns True when the action is now in the SKIPPED
    state, regardless of whether this call was the one that flipped
    it. Pre-S42 (2026-05-14) a double-click on Skip surfaced a JS
    error toast because the second update found `status != 'PENDING'`
    and returned False; the action was already skipped, so the
    "error" was confusing UX cruft.
    """
    with _connect(path) as conn:
        _ensure_schema(conn)
        cur = conn.execute("""
            UPDATE swing_actions
            SET status = 'SKIPPED', notes = ?
            WHERE action_id = ? AND status = 'PENDING'
        """, (reason, action_id))
        if (cur.rowcount or 0) > 0:
            return True
        # Already skipped (or never existed) — idempotent success
        # iff the row exists and is now in SKIPPED state.
        row = conn.execute(
            "SELECT status FROM swing_actions WHERE action_id = ?",
            (action_id,),
        ).fetchone()
        return bool(row) and row["status"] == "SKIPPED"


# ── Read: runs ──────────────────────────────────────────────────

def latest_run(path: str = DB_PATH) -> dict | None:
    """Most recent swing run regardless of date.

    Filters out:
      * pre-AI snapshot rows (S29 — they're recoverable checkpoints,
        not user-visible runs).
      * SEARCH_BOX trigger_source rows (S43 hardening, 2026-05-14 —
        single-stock analyse-one runs are by design a 1-candidate
        slice and should NOT hijack the dashboard's main
        recommendations list when the user happens to navigate
        back after using the search box).

    Snapshots are still queryable explicitly via
    `latest_snapshot_run()` for audit; SEARCH_BOX runs are still
    queryable via `candidate_by_symbol()` so the per-stock detail
    page still finds them.
    """
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """SELECT * FROM swing_runs
               WHERE COALESCE(is_snapshot, 0) = 0
                 AND COALESCE(trigger_source, '') != 'SEARCH_BOX'
               ORDER BY run_id DESC LIMIT 1"""
        ).fetchone()
        return dict(row) if row else None


def latest_run_for_date(trade_date: str,
                        path: str = DB_PATH) -> dict | None:
    """Latest non-snapshot full-scan run row for a given trading date."""
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """SELECT * FROM swing_runs
               WHERE run_for_date = ?
                 AND COALESCE(is_snapshot, 0) = 0
                 AND COALESCE(trigger_source, '') != 'SEARCH_BOX'
               ORDER BY run_id DESC LIMIT 1""",
            (trade_date,),
        ).fetchone()
        return dict(row) if row else None


def latest_run_for_date_and_mode(trade_date: str, mode: str,
                                 path: str = DB_PATH) -> dict | None:
    """Latest non-snapshot full-scan run row for a given date + mode
    (NOAI / AI). SEARCH_BOX rows are filtered out per S43."""
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """SELECT * FROM swing_runs
               WHERE run_for_date = ? AND mode = ?
                 AND COALESCE(is_snapshot, 0) = 0
                 AND COALESCE(trigger_source, '') != 'SEARCH_BOX'
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
    """Best candidate record for the symbol's latest run.

    Resolution order (most-preferred first):
      1. Use the latest run_id that contains this symbol, including
         SEARCH_BOX rows. A fresh rejected one-stock search must not be
         hidden behind yesterday's accepted full-scan row.
      2. Within that same run, prefer a real technical setup over a
         dip-buy row. Dip-buy rows remain available through
         `dip_candidate_by_symbol`, but technical rows carry the richer
         setup/reason context the detail page and AI prompt need.
      3. If the technical scanner only emitted NONE, prefer an ACCEPTED
         row (usually 52W_DIP) over a no-setup diagnostic row.

    AI overlay carry-forward (S48, 2026-05-14): if the picked
    candidate's `ai_overlay_json` is empty (e.g. a fresh SEARCH_BOX
    run that didn't request AI but the symbol has a successful
    overlay from yesterday's full-scan AI run), graft the cached
    overlay onto the returned candidate. Without this graft the
    detail page silently dropped the AI section every time the
    user re-searched a symbol. The actual stored row is unchanged
    — this is a read-time enrichment.

    Treats both legacy `ATH_DIP` and current `52W_DIP` as dip-buy
    rows.
    """
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """WITH latest AS (
                   SELECT MAX(run_id) AS run_id
                   FROM swing_candidates
                   WHERE symbol = ?
               )
               SELECT * FROM swing_candidates
               WHERE symbol = ?
                 AND run_id = (SELECT run_id FROM latest)
                             ORDER BY
                                 CASE
                                     WHEN setup_type NOT IN ('ATH_DIP', '52W_DIP', 'NONE')
                                     THEN 0
                                     ELSE 1
                                 END,
                 CASE status WHEN 'ACCEPTED' THEN 0 ELSE 1 END,
                 CASE WHEN setup_type NOT IN ('ATH_DIP', '52W_DIP')
                      THEN 0 ELSE 1 END,
                 id DESC
               LIMIT 1""",
            (symbol.upper(), symbol.upper()),
        ).fetchone()
        cand = _row_to_candidate(row) if row else None
        if cand is None:
            return None
        # AI overlay carry-forward (S48). Reads the most recent good
        # overlay from any prior run when the picked candidate's
        # `ai_overlay_json` is empty. `latest_ai_overlay_for_symbol`
        # already filters out error-only payloads (S43 fix), so we
        # never carry forward a stale `{"error":"..."}`.
        if not (cand.ai_overlay_json or "").strip():
            try:
                # Inline the lookup so we don't have to forward-declare;
                # it shares the same connection-open helper.
                cached = latest_ai_overlay_for_symbol(symbol, path=path)
                if cached:
                    cand.ai_overlay_json = cached[0]
            except Exception:
                pass
        return cand


def dip_candidate_by_symbol(symbol: str, path: str = DB_PATH) -> SwingCandidate | None:
    """Most recent dip-buy candidate (legacy ATH_DIP or current 52W_DIP)."""
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """SELECT * FROM swing_candidates
               WHERE symbol = ?
                 AND setup_type IN ('ATH_DIP', '52W_DIP')
               ORDER BY run_id DESC, id DESC
               LIMIT 1""",
            (symbol.upper(),),
        ).fetchone()
        return _row_to_candidate(row) if row else None


# Legacy alias — pre-2026-05-14 callers (and any external scripts /
# copilot skills) used `ath_candidate_by_symbol`. The function now
# returns the most recent dip-buy candidate regardless of which
# setup_type the row was tagged with.
ath_candidate_by_symbol = dip_candidate_by_symbol


def latest_full_scan_rank_by_symbol(
    symbol: str, path: str = DB_PATH,
) -> tuple[int, int] | None:
    """Return `(rank, total_accepted)` for `symbol` in the latest
    full-scan run — i.e. the most recent run that is NOT a snapshot
    AND NOT a SEARCH_BOX trigger.

    Origin: 2026-05-14 user reported "(rank #1 today)" was printed
    on every detail page. Root cause: `candidate_by_symbol` (and the
    detail page that depends on it) reads the most recent ACCEPTED
    row regardless of run, so a single-stock SEARCH_BOX scan (which
    always assigns priority_rank=1 to the only candidate) shadowed
    the rank from the latest full universe scan. The detail page
    must look up the rank against the full-scan run only — that's
    the rank operators actually compare against.

    Returns:
      * `(rank, total)` — `rank` is 1-based; `total` is the count
        of ACCEPTED candidates in that run (so the detail page can
        render "rank #N of M today").
      * `None` — symbol not present in the latest full scan, or no
        full scan exists in the DB.
    """
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        run = conn.execute(
            """SELECT run_id FROM swing_runs
               WHERE COALESCE(is_snapshot, 0) = 0
                 AND COALESCE(trigger_source, '') != 'SEARCH_BOX'
               ORDER BY run_id DESC LIMIT 1"""
        ).fetchone()
        if not run:
            return None
        run_id = int(run["run_id"])
        total_row = conn.execute(
            "SELECT COUNT(*) AS n FROM swing_candidates "
            "WHERE run_id = ? AND status = 'ACCEPTED'",
            (run_id,),
        ).fetchone()
        total = int(total_row["n"] or 0)
        # When a symbol has multiple ACCEPTED rows in the same run
        # (e.g. accepted by BOTH technical and dip-buy scanners),
        # take the BEST (lowest, hence numerically smallest) rank.
        # Pre-fix this query had no ORDER BY / LIMIT so SQLite
        # returned whichever row it picked first — non-deterministic
        # and produced bugs like "two stocks both showing rank #1"
        # in the compare table (DRREDDY + APOLLOHOSP, 2026-05-14).
        row = conn.execute(
            """SELECT MIN(priority_rank) AS r
                 FROM swing_candidates
                WHERE run_id = ? AND symbol = ?
                  AND status = 'ACCEPTED'
                  AND priority_rank > 0""",
            (run_id, symbol.upper()),
        ).fetchone()
        if not row or row["r"] is None:
            return None
        rank = int(row["r"])
        if rank <= 0:
            return None
        return (rank, total)


def diff_latest_vs_prior_day(
    path: str = DB_PATH,
    *,
    rank_move_threshold: int = 3,
    history_limit: int = 30,
) -> dict | None:
    """Compare the latest full-scan run with the immediately prior
    full-scan run (by run_id, not by date), and walk further back
    if the diff is empty so the user always gets a meaningful "last
    big change" report.

    This compares consecutive scans regardless of their `run_for_date`,
    because a scan on day N might have `run_for_date = N-1` (pre-close
    scans use yesterday's completed candle). The user's mental model
    is "what changed from my last scan to this one", not "what changed
    between two different calendar dates".

    Filtering — both ends of the diff are limited to runs that pass
    the same gates as `latest_run()`: `is_snapshot=0` AND
    `trigger_source != 'SEARCH_BOX'`.

    Returns `None` when no full-scan history exists at all.

    Returns a dict shaped like:
      {
        "current_run_id": int,
        "current_run_date": str (YYYY-MM-DD),
        "current_run_finished_at": str | None,
        "prior_run_id": int | None,
        "prior_run_date": str | None,
        "prior_run_finished_at": str | None,
        "compared_to_latest": bool,    # False when we walked back
        "skipped_runs": int,           # how many identical runs we skipped
        "new_entries":   [{"symbol", "rank", "score", "setup_type"}, ...],
        "dropped":       [{"symbol", "prior_rank", "prior_score",
                            "prior_setup_type", "now_status"}, ...],
        "rank_movers":   [{"symbol", "prior_rank", "new_rank",
                            "delta", "score_delta"}, ...],
        "summary": str (one-line plain-English headline),
      }
    """
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """SELECT run_id, run_for_date, finished_at, started_at
                 FROM swing_runs
                WHERE COALESCE(is_snapshot, 0) = 0
                  AND COALESCE(trigger_source, '') != 'SEARCH_BOX'
                ORDER BY run_id DESC
                LIMIT ?""",
            (max(2, int(history_limit)),),
        ).fetchall()
        if not rows:
            return None
        latest = rows[0]
        latest_id = int(latest["run_id"])
        latest_date = latest["run_for_date"] or ""
        latest_fin = latest["finished_at"] or latest["started_at"]

        def _accepted_map(run_id: int) -> dict[str, dict]:
            crows = conn.execute(
                """SELECT symbol, priority_rank, score, setup_type, status
                     FROM swing_candidates
                    WHERE run_id = ? AND status = 'ACCEPTED'""",
                (run_id,),
            ).fetchall()
            return {
                r["symbol"]: {
                    "symbol": r["symbol"],
                    "rank": int(r["priority_rank"] or 0),
                    "score": float(r["score"] or 0.0),
                    "setup_type": r["setup_type"] or "",
                }
                for r in crows
            }

        def _all_status_map(run_id: int) -> dict[str, str]:
            crows = conn.execute(
                "SELECT symbol, status FROM swing_candidates WHERE run_id = ?",
                (run_id,),
            ).fetchall()
            return {r["symbol"]: (r["status"] or "") for r in crows}

        latest_map = _accepted_map(latest_id)
        latest_all = _all_status_map(latest_id)

        # Walk back through prior runs by run_id order (not date).
        # Always return the diff against the immediately prior run.
        # If that diff is empty, also note the last meaningful change.
        skipped = 0
        immediate_diff = None
        for prior in rows[1:]:
            prior_id = int(prior["run_id"])
            prior_date = prior["run_for_date"] or ""
            prior_map = _accepted_map(prior_id)
            new_entries = sorted(
                [
                    {**v, "rank": v["rank"]}
                    for s, v in latest_map.items() if s not in prior_map
                ],
                key=lambda d: d["rank"] or 9_999,
            )
            dropped = sorted(
                [
                    {
                        "symbol": s,
                        "prior_rank": v["rank"],
                        "prior_score": v["score"],
                        "prior_setup_type": v["setup_type"],
                        "now_status": latest_all.get(s, "MISSING"),
                    }
                    for s, v in prior_map.items() if s not in latest_map
                ],
                key=lambda d: d["prior_rank"] or 9_999,
            )
            rank_movers: list[dict] = []
            for s, v in latest_map.items():
                if s not in prior_map:
                    continue
                pr = prior_map[s]["rank"]
                nr = v["rank"]
                if pr <= 0 or nr <= 0:
                    continue
                delta = pr - nr  # +ve = moved up (smaller rank number)
                if abs(delta) >= int(rank_move_threshold):
                    rank_movers.append({
                        "symbol": s,
                        "prior_rank": pr,
                        "new_rank": nr,
                        "delta": delta,
                        "score_delta": round(
                            v["score"] - prior_map[s]["score"], 2),
                    })
            rank_movers.sort(key=lambda d: -abs(d["delta"]))

            no_change = (not new_entries
                         and not dropped and not rank_movers)

            # Build the diff result
            n_in = len(new_entries); n_out = len(dropped)
            n_mov = len(rank_movers)
            bits = []
            if n_in:  bits.append(f"{n_in} new")
            if n_out: bits.append(f"{n_out} dropped")
            if n_mov: bits.append(f"{n_mov} rank mover" + ("s" if n_mov != 1 else ""))
            headline = " · ".join(bits) if bits else "no notable changes"

            this_diff = {
                "current_run_id": latest_id,
                "current_run_date": latest_date,
                "current_run_finished_at": latest_fin,
                "prior_run_id": prior_id,
                "prior_run_date": prior_date,
                "prior_run_finished_at": prior["finished_at"]
                                        or prior["started_at"],
                "compared_to_latest": (skipped == 0),
                "skipped_runs": skipped,
                "new_entries": new_entries,
                "dropped": dropped,
                "rank_movers": rank_movers,
                "summary": headline,
            }

            # Always capture the immediate prior diff (first iteration)
            if immediate_diff is None:
                immediate_diff = this_diff
                if not no_change:
                    # Immediate prior has changes — return it
                    return this_diff
                # Immediate prior has no changes — continue walking
                # to find the last meaningful change, but we'll still
                # return the immediate diff (with "no changes" note)
                skipped += 1
                continue

            if no_change:
                skipped += 1
                continue

            # Found a meaningful change further back — annotate the
            # immediate diff with this info and return the immediate
            immediate_diff["last_meaningful_change"] = {
                "prior_run_id": prior_id,
                "prior_run_date": prior_date,
                "prior_run_finished_at": prior["finished_at"]
                                        or prior["started_at"],
                "skipped_runs": skipped,
                "new_entries": new_entries,
                "dropped": dropped,
                "rank_movers": rank_movers,
                "summary": headline,
            }
            return immediate_diff

        # Walked through all history — return whatever we have
        if immediate_diff:
            return immediate_diff

        # No prior run on a different date — return an empty diff
        # struct so the dashboard can render "first scan ever, no
        # prior data to compare against".
        return {
            "current_run_id": latest_id,
            "current_run_date": latest_date,
            "current_run_finished_at": latest_fin,
            "prior_run_id": None,
            "prior_run_date": None,
            "prior_run_finished_at": None,
            "compared_to_latest": True,
            "skipped_runs": 0,
            "new_entries": [],
            "dropped": [],
            "rank_movers": [],
            "summary": "no prior trading-day scan to compare against",
        }


def latest_candidate_row_id_by_symbol(symbol: str,
                                      path: str = DB_PATH) -> int | None:
    """Return the `swing_candidates.id` matching `candidate_by_symbol`.

    Used by the per-stock AI analyse endpoints so the Claude response
    is persisted back to the row the detail page is actually showing.
    The ordering intentionally mirrors `candidate_by_symbol`: latest
    run for this symbol first, then real technical setup rows over
    dip-buy rows, with ACCEPTED as the tie-breaker.
    """
    if not os.path.exists(path):
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """WITH latest AS (
                   SELECT MAX(run_id) AS run_id
                   FROM swing_candidates
                   WHERE symbol = ?
               )
               SELECT id FROM swing_candidates
               WHERE symbol = ?
                 AND run_id = (SELECT run_id FROM latest)
                             ORDER BY
                                 CASE
                                     WHEN setup_type NOT IN ('ATH_DIP', '52W_DIP', 'NONE')
                                     THEN 0
                                     ELSE 1
                                 END,
                 CASE status WHEN 'ACCEPTED' THEN 0 ELSE 1 END,
                 CASE WHEN setup_type NOT IN ('ATH_DIP', '52W_DIP')
                      THEN 0 ELSE 1 END,
                 id DESC
               LIMIT 1""",
            (symbol.upper(), symbol.upper()),
        ).fetchone()
        if row:
            return int(row["id"])
        return None


def update_candidate_ai_overlay(candidate_id: int, overlay_json: str,
                                path: str = DB_PATH) -> bool:
    """Patch `ai_overlay_json` on a single candidate row. Used by the
    per-stock AI analyse endpoint (S37). Returns True when the row
    existed and the update wrote one row.
    """
    with _connect(path) as conn:
        _ensure_schema(conn)
        cur = conn.execute(
            "UPDATE swing_candidates SET ai_overlay_json = ? WHERE id = ?",
            (overlay_json, candidate_id),
        )
        return (cur.rowcount or 0) > 0


def latest_ai_overlay_for_symbol(
    symbol: str,
    *,
    max_age_days: int = 7,
    path: str = DB_PATH,
) -> tuple[str, str] | None:
    """Return `(ai_overlay_json, finished_at_iso)` for the most recent
    swing_candidates row that has a non-empty ai_overlay_json AND is
    NOT older than `max_age_days`. Used by:

    * `SwingManager.run()` — carry an existing AI overlay forward to a
      fresh scan's candidate row, so a stock the user paid Claude for
      yesterday still shows the AI analysis on today's recommendation
      table without being charged again.
    * The dashboard detail page — render the analysis-date badge
      ("Analysed 3 days ago") so the user can tell stale vs fresh.

    Returns None when no row qualifies. The freshness gate is keyed on
    the parent `swing_runs.finished_at` so a "good" overlay from a
    one-stock S38 search-box run carries forward identically to one
    from a full universe scan.
    """
    if not os.path.exists(path):
        return None
    import datetime as _dt
    # Cutoff is IST-naive to match the timestamp shape stored in
    # `swing_runs.finished_at` (which is `now_ist().isoformat()` —
    # IST-naive). Comparing UTC-naive to IST-naive lets in runs
    # ~5h30m older than `max_age_days` because IST > UTC by 5h30m,
    # so the IST timestamp string sorts as "later" than a UTC
    # cutoff string. Trivial drift on a 7-day window but semantically
    # wrong; fix is one line. (S42 hardening pass, 2026-05-14.)
    cutoff = (now_ist().replace(tzinfo=None)
              - _dt.timedelta(days=max(0, max_age_days))).isoformat()
    with _connect(path) as conn:
        _ensure_schema(conn)
        # JOIN to runs so we can apply the freshness gate. ORDER by
        # run_id DESC so we always take the most recent valid overlay.
        # Skip rows whose overlay is JUST an error payload — pre-S43
        # the carry-forward path (manager.py) and the detail page both
        # could pick a 57-byte `{"error":"..."}` payload (from a prior
        # failed Claude call) over a 2161-byte successful response on
        # an older run. Heuristic: if the JSON parses and has BOTH no
        # "raw_response" AND an "error" key, skip it.
        rows = conn.execute(
            """SELECT c.ai_overlay_json AS overlay,
                      COALESCE(r.finished_at, r.started_at) AS ts
                 FROM swing_candidates c
                 JOIN swing_runs r ON c.run_id = r.run_id
                WHERE c.symbol = ?
                  AND c.ai_overlay_json IS NOT NULL
                  AND c.ai_overlay_json != ''
                  AND COALESCE(r.finished_at, r.started_at) >= ?
                ORDER BY r.run_id DESC, c.id DESC
                LIMIT 8""",
            (symbol.upper(), cutoff),
        ).fetchall()
        for row in rows:
            overlay = row["overlay"] or ""
            if not overlay:
                continue
            try:
                payload = json.loads(overlay)
            except (json.JSONDecodeError, TypeError):
                # Not JSON — treat as opaque text and accept it.
                return overlay, row["ts"] or ""
            # Skip error-only payloads.
            if isinstance(payload, dict):
                if payload.get("error") and not payload.get("raw_response"):
                    continue
            return overlay, row["ts"] or ""
        return None


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

def open_positions(path: str = DB_PATH,
                   exchange: str | None = None) -> list[SwingPosition]:
    """All OPEN swing positions."""
    if not os.path.exists(path):
        return []
    with _connect(path) as conn:
        _ensure_schema(conn)
        if exchange:
            rows = conn.execute(
                """SELECT * FROM swing_positions
                   WHERE status = 'OPEN' AND exchange = ?
                   ORDER BY entry_date ASC""",
                (exchange.upper(),),
            ).fetchall()
            return [_row_to_position(r) for r in rows]
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


def realised_pnl_summary(path: str = DB_PATH,
                         exchange: str | None = None) -> dict:
    """Aggregate realised P&L from closed positions."""
    if not os.path.exists(path):
        return {"gross_pnl": 0.0, "charges": 0.0, "net_pnl": 0.0, "count": 0}
    with _connect(path) as conn:
        _ensure_schema(conn)
        if exchange:
            row = conn.execute("""
                SELECT COALESCE(SUM(gross_pnl), 0) AS gross,
                       COALESCE(SUM(charges), 0)   AS charges,
                       COALESCE(SUM(net_pnl), 0)   AS net,
                       COUNT(*)                    AS cnt
                FROM swing_positions
                WHERE status = 'CLOSED' AND exchange = ?
            """, (exchange.upper(),)).fetchone()
            return {
                "gross_pnl": float(row["gross"]),
                "charges":   float(row["charges"]),
                "net_pnl":   float(row["net"]),
                "count":     int(row["cnt"]),
            }
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
    # Reconstruct from snapshot_json if present (has the full data
    # captured at scan time — including all indicator fields). But
    # `snapshot_json` is FROZEN at scan time and never updated.
    # Columns that mutate post-scan (specifically `ai_overlay_json`,
    # patched by S37/S38 + the manager carry-forward pass) live in
    # the column proper. We must overlay the live column over the
    # snapshot value otherwise a candidate that received an AI
    # analyse via the detail-page button would still surface as
    # "no AI" because the snapshot was empty when the row was first
    # written. Same for `priority_rank` if it was rebanked after the
    # initial save. Origin: 2026-05-14 user reported "AI review is
    # not coming up when I go back and enter again the details page"
    # — root cause was this stale-snapshot bug.
    snap = d.get("snapshot_json")
    if snap:
        try:
            full = json.loads(snap)
            cand = SwingCandidate.from_dict(full)
            cand._id = int(d.get("id") or 0)
            cand._run_id = int(d.get("run_id") or 0)
            # Overlay live mutable columns on top of the snapshot.
            live_ai = d.get("ai_overlay_json") or ""
            if live_ai:
                cand.ai_overlay_json = live_ai
            return cand
        except (json.JSONDecodeError, TypeError):
            pass
    cand = SwingCandidate.from_dict(d)
    cand._id = int(d.get("id") or 0)
    cand._run_id = int(d.get("run_id") or 0)
    return cand


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


def _merge_or_insert_entry_position(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    exchange: str,
    executed_qty: int,
    executed_price: float,
    stop_price: float,
    target_price: float,
    source: str,
    linked_run_id: int = 0,
    linked_action_id: int = 0,
    notes: str = "",
    reason: str = "Entry confirmed",
) -> SwingPosition | None:
    ts = now_ist().isoformat()
    symbol = symbol.upper()
    existing = conn.execute(
        """SELECT * FROM swing_positions
           WHERE symbol = ? AND exchange = ? AND side = 'BUY'
             AND status = 'OPEN'
           ORDER BY position_id DESC
           LIMIT 1""",
        (symbol, exchange),
    ).fetchone()

    if existing:
        pos = _row_to_position(existing)
        # Use float so fractional US shares (the dashboard sends
        # qty as float for NASDAQ/NYSE) survive a merge.  NSE callers
        # always pass whole numbers, so this is a no-op for Indian
        # swing.
        new_qty = float(pos.managed_qty) + float(executed_qty)
        if new_qty <= 0:
            return None
        avg_price = round(
            ((pos.entry_price * pos.managed_qty)
             + (executed_price * executed_qty)) / new_qty,
            2,
        )
        new_stop = stop_price if stop_price > 0 else pos.stop_price
        new_target = target_price if target_price > 0 else pos.target_price
        new_linked_run_id = linked_run_id if linked_run_id > 0 else pos.linked_run_id
        new_linked_action_id = (
            linked_action_id if linked_action_id > 0 else pos.linked_action_id
        )
        merged_notes = notes or pos.notes

        conn.execute(
            """UPDATE swing_positions
               SET managed_qty = ?, entry_price = ?, stop_price = ?,
                   target_price = ?, linked_run_id = ?, linked_action_id = ?,
                   notes = ?
               WHERE position_id = ?""",
            (new_qty, avg_price, new_stop, new_target,
             new_linked_run_id, new_linked_action_id, merged_notes,
             pos.position_id),
        )
        conn.execute(
            """INSERT INTO swing_events (position_id, event_time,
                   event_type, old_value, new_value, reason)
               VALUES (?, ?, 'ENTRY', ?, ?, ?)""",
            (pos.position_id, ts,
             json.dumps({"qty": pos.managed_qty, "avg_price": pos.entry_price}),
             json.dumps({
                 "added_qty": executed_qty,
                 "added_price": executed_price,
                 "qty": new_qty,
                 "avg_price": avg_price,
                 "linked_action_id": linked_action_id,
             }),
             reason),
        )
        return _load_position(conn, pos.position_id)

    cur = conn.execute("""
        INSERT INTO swing_positions (
            symbol, exchange, side, managed_qty, entry_price,
            entry_date, stop_price, target_price, status,
            source, linked_run_id, linked_action_id, notes
        ) VALUES (?, ?, 'BUY', ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)
    """, (
        symbol, exchange, executed_qty, executed_price, ts[:10],
        stop_price, target_price, source, linked_run_id,
        linked_action_id, notes,
    ))
    pos_id = int(cur.lastrowid or 0)

    conn.execute("""
        INSERT INTO swing_events (position_id, event_time,
            event_type, new_value, reason)
        VALUES (?, ?, 'ENTRY', ?, ?)
    """, (pos_id, ts, json.dumps({
        "qty": executed_qty,
        "price": executed_price,
        "linked_action_id": linked_action_id,
    }), reason))

    return _load_position(conn, pos_id)


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


# ── Watchlist ───────────────────────────────────────────────────

@dataclass
class WatchlistItem:
    watchlist_id: int = 0
    symbol: str = ""
    exchange: str = "NSE"
    added_at: str = ""
    added_price: float = 0.0
    setup_type: str = ""
    action_id: int = 0
    notes: str = ""
    status: str = "WATCHING"
    removed_at: str = ""
    # Live overlay (not persisted)
    live_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0


def add_to_watchlist(
    symbol: str,
    price: float,
    setup_type: str = "",
    action_id: int = 0,
    notes: str = "",
    exchange: str = "NSE",
    path: str = DB_PATH,
) -> int:
    """Add a stock to the watchlist. Returns the watchlist_id."""
    with _connect(path) as conn:
        _ensure_schema(conn)
        ts = now_ist().isoformat()
        cur = conn.execute("""
            INSERT INTO swing_watchlist
                (symbol, exchange, added_at, added_price, setup_type,
                 action_id, notes, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'WATCHING')
        """, (symbol.upper(), exchange, ts, price, setup_type,
              action_id or None, notes))
        return int(cur.lastrowid or 0)


def remove_from_watchlist(watchlist_id: int, path: str = DB_PATH) -> bool:
    """Remove a stock from the watchlist (marks it removed)."""
    with _connect(path) as conn:
        _ensure_schema(conn)
        ts = now_ist().isoformat()
        cur = conn.execute("""
            UPDATE swing_watchlist
            SET status = 'REMOVED', removed_at = ?
            WHERE watchlist_id = ? AND status = 'WATCHING'
        """, (ts, watchlist_id))
        return (cur.rowcount or 0) > 0


def promote_watchlist_to_position(
    watchlist_id: int,
    executed_qty: int,
    executed_price: float,
    stop_price: float = 0.0,
    target_price: float = 0.0,
    path: str = DB_PATH,
) -> SwingPosition | None:
    """Move a watchlist item to the open swing book (I bought it)."""
    with _connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM swing_watchlist WHERE watchlist_id = ? AND status = 'WATCHING'",
            (watchlist_id,),
        ).fetchone()
        if not row:
            return None

        symbol = row["symbol"]
        exchange = row["exchange"]
        ts = now_ist().isoformat()

        # Mark watchlist item as promoted
        conn.execute("""
            UPDATE swing_watchlist
            SET status = 'PROMOTED', removed_at = ?
            WHERE watchlist_id = ?
        """, (ts, watchlist_id))

        if stop_price <= 0:
            stop_price = round(executed_price * 0.90, 2)
        if target_price <= 0:
            target_price = round(executed_price * 1.15, 2)
        return _merge_or_insert_entry_position(
            conn,
            symbol=symbol,
            exchange=exchange,
            executed_qty=executed_qty,
            executed_price=executed_price,
            stop_price=stop_price,
            target_price=target_price,
            source="WATCHLIST_PROMOTE",
            notes=f"Promoted from watchlist #{watchlist_id}",
            reason="Promoted from watchlist",
        )


def add_manual_position(
    symbol: str,
    executed_qty: int,
    executed_price: float,
    stop_price: float = 0.0,
    target_price: float = 0.0,
    exchange: str = "NSE",
    source: str = "DASHBOARD_MANUAL_ADD",
    notes: str = "",
    path: str = DB_PATH,
) -> SwingPosition | None:
    """Add a manually bought swing position without a pending action."""
    if executed_qty <= 0 or executed_price <= 0:
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        if stop_price <= 0:
            stop_price = round(executed_price * 0.90, 2)
        if target_price <= 0:
            target_price = round(executed_price * 1.15, 2)
        return _merge_or_insert_entry_position(
            conn,
            symbol=symbol,
            exchange=exchange,
            executed_qty=executed_qty,
            executed_price=executed_price,
            stop_price=stop_price,
            target_price=target_price,
            source=source,
            notes=notes,
            reason="Manual dashboard Add+",
        )


def edit_position(
    position_id: int,
    managed_qty: int,
    entry_price: float,
    stop_price: float = 0.0,
    target_price: float = 0.0,
    notes: str = "Manual open-book edit",
    path: str = DB_PATH,
) -> SwingPosition | None:
    """Edit an open position's total shares and average cost."""
    if managed_qty <= 0 or entry_price <= 0:
        return None
    with _connect(path) as conn:
        _ensure_schema(conn)
        pos = _load_position(conn, position_id)
        if not pos or pos.status != "OPEN":
            return None
        new_stop = stop_price if stop_price > 0 else pos.stop_price
        new_target = target_price if target_price > 0 else pos.target_price
        ts = now_ist().isoformat()
        conn.execute(
            """UPDATE swing_positions
               SET managed_qty = ?, entry_price = ?, stop_price = ?,
                   target_price = ?, notes = ?
               WHERE position_id = ?""",
            (managed_qty, entry_price, new_stop, new_target,
             notes or pos.notes, position_id),
        )
        conn.execute(
            """INSERT INTO swing_events (position_id, event_time,
                   event_type, old_value, new_value, reason)
               VALUES (?, ?, 'EDIT', ?, ?, ?)""",
            (position_id, ts,
             json.dumps({"qty": pos.managed_qty, "avg_price": pos.entry_price}),
             json.dumps({
                 "qty": managed_qty,
                 "avg_price": entry_price,
                 "stop": new_stop,
                 "target": new_target,
             }),
             notes or "Manual open-book edit"),
        )
        return _load_position(conn, position_id)


def get_watchlist(path: str = DB_PATH,
                  exchange: str | None = None) -> list[WatchlistItem]:
    """All active watchlist items (WATCHING status)."""
    if not os.path.exists(path):
        return []
    with _connect(path) as conn:
        _ensure_schema(conn)
        if exchange:
            rows = conn.execute(
                """SELECT * FROM swing_watchlist
                   WHERE status = 'WATCHING' AND exchange = ?
                   ORDER BY added_at DESC""",
                (exchange.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM swing_watchlist
                   WHERE status = 'WATCHING'
                   ORDER BY added_at DESC"""
            ).fetchall()
        items = []
        for r in rows:
            items.append(WatchlistItem(
                watchlist_id=int(r["watchlist_id"]),
                symbol=r["symbol"],
                exchange=r["exchange"] or "NSE",
                added_at=r["added_at"] or "",
                added_price=float(r["added_price"] or 0),
                setup_type=r["setup_type"] or "",
                action_id=int(r["action_id"] or 0),
                notes=r["notes"] or "",
                status=r["status"] or "WATCHING",
            ))
        return items


# (end of watchlist helpers)
