# ================================================================
# modes/trade/candidate_telemetry.py
# ================================================================
# Per-candidate telemetry store (Roadmap #259).
#
# Why this exists
# ---------------
# The `trades` table only sees stocks that ENTERED. That is heavily
# selection-biased: every entry-gate rejection is invisible. Without a
# rejected-candidate stream you cannot:
#   - measure missed-profit when a fail-closed gate (e.g. NO_RESCUE_ZONE)
#     becomes more aggressive;
#   - run an honest backtest against live ledger (the backtest sees
#     the universe, the ledger sees only the survivors);
#   - decide whether a score-bucket inversion is real or an artifact
#     of which buckets the gates admitted.
#
# This module owns one SQLite table — `intraday_candidates`. Live runs
# write to `data/trades.db`; dry-run/research runs write to
# `data/trade_analysis.db` so simulated evidence never pollutes actual
# P&L, tax, or dashboard ledgers.
#
# Three writers (write-only API; readers go through scripts/view_*):
#   1. `record_scored(candidate)` — called by the Scanner for every
#      candidate that passes the MIN_SCORE filter. Status = SCORED.
#   2. `mark_attempted(symbol, side, scan_time, status, ...)` — called
#      by `OrderEngine.enter_trade` (or its caller) once the entry
#      attempt resolves. Updates status to ENTERED or REJECTED, fills
#      `rejected_gate` when known, fills `entry_price` on success.
#   3. `attach_outcome(date, symbol, side, entry_time, ...)` — called
#      by `PerformanceTracker.record_trades` for every closed trade,
#      backfills `exit_price`, `exit_reason`, `pnl`.
#
# All writes are best-effort: the bot must NEVER fail a trade because
# telemetry insertion failed. Every public method swallows exceptions
# and logs a warning instead.
# ================================================================

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from typing import Any

from config import Config, now_ist


DB_PATH = os.path.join("data", "trades.db")
ANALYSIS_DB_PATH = Config.TRADE_ANALYSIS_DB_PATH


class CandidateTelemetry:
    """Per-candidate feature/outcome store. Best-effort writes only."""

    def __init__(self, log=None, db_path: str | None = None):
        self.log = log
        self.db_path = db_path or self._default_db_path()
        # Health flag — when False, no writes will succeed and the
        # operator should see this in startup logs. Doesn't break trade
        # flow either way (every write method is wrapped in try/except).
        self.healthy = False
        try:
            self._ensure_db()
            self.healthy = True
        except Exception as e:
            # ERROR (not WARNING): if telemetry init fails the full
            # SCORED → ENTERED/REJECTED → OUTCOME audit trail for the
            # day is lost. Trade flow is unaffected (every write is
            # try/except'd) but the operator MUST see this in the
            # startup log scan.
            if self.log:
                self.log.error(
                    f"CandidateTelemetry init failed — telemetry rows will "
                    f"NOT be written this session (audit trail lost): {e}"
                )

    # ── DB plumbing ──────────────────────────────────────────────

    @staticmethod
    def _default_db_path() -> str:
        return ANALYSIS_DB_PATH if Config.DRY_RUN else DB_PATH

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # 30s timeout (was 10s). WAL mode makes reader/writer contention
        # rare, but three writers (scanner SCORED, manager mark_attempted,
        # performance_tracker attach_outcome) plus the engine's writes to
        # `trades` and `intraday_tax_ledger` on the same DB file can hit
        # 10-30s lock windows on slow VMs. Best-effort writes never block
        # a trade either way (every public method is try/except'd), but
        # the longer timeout reduces dropped telemetry rows under load.
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_db(self):
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS intraday_candidates (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    date            TEXT    NOT NULL,
                    scan_time       TEXT    NOT NULL,
                    symbol          TEXT    NOT NULL,
                    exchange        TEXT    NOT NULL DEFAULT 'NSE',
                    side            TEXT    NOT NULL,
                    combined_score  REAL,
                    pattern_score   REAL,
                    tech_score      REAL,
                    rsi             REAL,
                    adx             REAL,
                    rvol            REAL,
                    vwap            REAL,
                    ltp             REAL,
                    pattern_summary TEXT,
                    technical_json  TEXT,
                    nifty_trend     TEXT,
                    vix             REAL,
                    tape            TEXT,
                    sector          TEXT,
                    config_version  TEXT,
                    config_hash     TEXT,
                    status          TEXT    NOT NULL DEFAULT 'SCORED',
                    rejected_gate   TEXT,
                    entry_price     REAL,
                    entry_time      TEXT,
                    exit_price      REAL,
                    exit_time       TEXT,
                    exit_reason     TEXT,
                    pnl             REAL,
                    notes           TEXT,
                    UNIQUE(date, symbol, side, scan_time)
                )
                """
            )
            # Helpful covering indexes for the common audit queries.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_date_status "
                "ON intraday_candidates (date, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_candidates_symbol_date "
                "ON intraday_candidates (symbol, date)"
            )
            conn.commit()

    # ── Writer 1: SCORED row ─────────────────────────────────────

    def record_scored(self, candidate: dict, *, scan_time: str | None = None,
                      nifty_trend: str = "", vix: float | None = None,
                      tape: str = "", sector: str = "") -> None:
        """
        Write one row per V2_MIN-passing candidate from the scanner.

        Required candidate keys (subset of the dict returned by
        `services/stock_scanner_v2._analyse_stock`):
          symbol, exchange, combined_score, pattern_summary, technical,
          vwap, current_price, rvol.
        """
        try:
            symbol   = candidate.get("symbol", "")
            if not symbol:
                return
            exchange = candidate.get("exchange", "NSE")
            score    = float(candidate.get("combined_score", 0) or 0)
            # Mirror the live scanner's directional rule (Roadmap #169):
            # score==0 has no directional bias — skip the telemetry row
            # rather than silently mis-tag it as BUY. The MIN_SCORE
            # prefilter normally blocks zeros, but be defensive against
            # config tweaks that lower the floor.
            if score > 0:
                side = "BUY"
            elif score < 0:
                side = "SELL"
            else:
                return
            now      = now_ist()
            scan_ts  = scan_time or now.strftime("%Y-%m-%d %H:%M:%S")
            today    = str(now.date())

            tech = candidate.get("technical", {}) or {}
            psum = candidate.get("pattern_summary", {}) or {}

            tech_score    = _safe_float(tech.get("score"))
            pattern_score = _safe_float(psum.get("score"))
            rsi_val       = _nested_float(tech, ("rsi", "rsi"))
            adx_val       = _nested_float(tech, ("adx", "adx")) or _nested_float(tech, ("adx_di", "adx"))

            try:
                technical_json = json.dumps(_jsonable(tech))
            except Exception:
                technical_json = ""
            try:
                pattern_json = json.dumps(_jsonable(psum))
            except Exception:
                pattern_json = ""

            cfg_version, cfg_hash = Config.snapshot_hash()

            with closing(self._connect()) as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO intraday_candidates
                       (date, scan_time, symbol, exchange, side,
                        combined_score, pattern_score, tech_score,
                        rsi, adx, rvol, vwap, ltp,
                        pattern_summary, technical_json,
                        nifty_trend, vix, tape, sector,
                        config_version, config_hash, status)
                       VALUES (?, ?, ?, ?, ?,
                               ?, ?, ?,
                               ?, ?, ?, ?, ?,
                               ?, ?,
                               ?, ?, ?, ?,
                               ?, ?, 'SCORED')""",
                    (
                        today, scan_ts, symbol, exchange, side,
                        score, pattern_score, tech_score,
                        rsi_val, adx_val,
                        _safe_float(candidate.get("rvol")),
                        _safe_float(candidate.get("vwap")),
                        _safe_float(candidate.get("current_price")),
                        pattern_json, technical_json,
                        nifty_trend, _safe_float(vix), tape, sector,
                        cfg_version, cfg_hash,
                    ),
                )
                conn.commit()
        except Exception as e:
            if self.log:
                self.log.warning(f"CandidateTelemetry.record_scored({candidate.get('symbol')}): {e}")

    # ── Writer 2: ENTERED / REJECTED ────────────────────────────

    def mark_attempted(self, *, symbol: str, side: str,
                       scan_time: str | None = None,
                       status: str = "REJECTED",
                       rejected_gate: str | None = None,
                       entry_price: float | None = None,
                       entry_time: str | None = None,
                       notes: str | None = None) -> None:
        """
        Update the most recent SCORED row for (symbol, side) on today
        to ENTERED or REJECTED.

        scan_time is matched if provided; otherwise we update the
        latest SCORED row by id. This mirrors the reality that the
        engine sees the trade dict the scanner produced, not the exact
        scan timestamp.
        """
        try:
            today = str(now_ist().date())
            with closing(self._connect()) as conn:
                if scan_time:
                    row = conn.execute(
                        """SELECT id FROM intraday_candidates
                           WHERE date = ? AND symbol = ? AND side = ?
                             AND scan_time = ?
                           ORDER BY id DESC LIMIT 1""",
                        (today, symbol, side, scan_time),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """SELECT id FROM intraday_candidates
                           WHERE date = ? AND symbol = ? AND side = ?
                             AND status = 'SCORED'
                           ORDER BY id DESC LIMIT 1""",
                        (today, symbol, side),
                    ).fetchone()
                if not row:
                    return
                conn.execute(
                    """UPDATE intraday_candidates
                       SET status = ?, rejected_gate = ?,
                           entry_price = COALESCE(?, entry_price),
                           entry_time  = COALESCE(?, entry_time),
                           notes       = COALESCE(?, notes)
                       WHERE id = ?""",
                    (status, rejected_gate, entry_price, entry_time,
                     notes, row[0]),
                )
                conn.commit()
        except Exception as e:
            if self.log:
                self.log.warning(
                    f"CandidateTelemetry.mark_attempted({symbol},{side}): {e}"
                )

    # ── Writer 3: outcome backfill ───────────────────────────────

    def attach_outcome(self, *, date: str, symbol: str, side: str,
                       entry_time: str | None,
                       exit_price: float | None,
                       exit_time: str | None,
                       exit_reason: str | None,
                       pnl: float | None) -> None:
        """
        Attach the realised exit details to the matching ENTERED row.
        Matched on (date, symbol, side, entry_time) when entry_time is
        present; otherwise on the latest ENTERED row that still has
        no exit_price.
        """
        try:
            with closing(self._connect()) as conn:
                if entry_time:
                    row = conn.execute(
                        """SELECT id FROM intraday_candidates
                           WHERE date = ? AND symbol = ? AND side = ?
                             AND entry_time = ?
                           ORDER BY id DESC LIMIT 1""",
                        (date, symbol, side, entry_time),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """SELECT id FROM intraday_candidates
                           WHERE date = ? AND symbol = ? AND side = ?
                             AND status = 'ENTERED'
                             AND exit_price IS NULL
                           ORDER BY id DESC LIMIT 1""",
                        (date, symbol, side),
                    ).fetchone()
                if not row:
                    return
                conn.execute(
                    """UPDATE intraday_candidates
                       SET exit_price = ?, exit_time = ?,
                           exit_reason = ?, pnl = ?
                       WHERE id = ?""",
                    (exit_price, exit_time, exit_reason, pnl, row[0]),
                )
                conn.commit()
        except Exception as e:
            if self.log:
                self.log.warning(
                    f"CandidateTelemetry.attach_outcome({date},{symbol},{side}): {e}"
                )


# ── helpers ─────────────────────────────────────────────────────

def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _nested_float(d: dict, path: tuple) -> float | None:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return _safe_float(cur)


def _jsonable(v: Any) -> Any:
    """Strip non-JSON-serialisable values to repr()."""
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return repr(v)
