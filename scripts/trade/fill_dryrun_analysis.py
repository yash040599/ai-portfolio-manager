"""
Fill the dry-run analysis ledger from dry-run trading JSON reports.

This script never writes to data/trades.db and never writes to the live
intraday tax ledger. It stores simulated dry-run outcomes in
data/trade_analysis.db for research/replay comparison only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from config import Config  # noqa: E402
from modes.trade.candidate_telemetry import CandidateTelemetry  # noqa: E402
from scripts.trade.fill_intraday_ledger import per_trade_charges  # noqa: E402


REPORTS_DIR = PROJECT_ROOT / "reports" / "trading"
DEFAULT_DB = PROJECT_ROOT / Config.TRADE_ANALYSIS_DB_PATH


def _relpath(path: str | Path) -> str:
    try:
        return os.path.relpath(path, PROJECT_ROOT)
    except ValueError:
        return str(path)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  skipped {_relpath(path)}: {exc}")
        return None


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_db(conn)
    return conn


def _ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dryrun_trade_ledger (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            source_report   TEXT    NOT NULL,
            sessions        INTEGER,
            config_version  TEXT,
            config_hash     TEXT,
            git_sha         TEXT,
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
            brokerage       REAL    NOT NULL,
            stt             REAL    NOT NULL,
            exchange_txn    REAL    NOT NULL,
            gst             REAL    NOT NULL,
            sebi_charges    REAL    NOT NULL,
            stamp_duty      REAL    NOT NULL,
            total_charges   REAL    NOT NULL,
            net_pnl         REAL    NOT NULL,
            order_id        TEXT    NOT NULL,
            verified        TEXT    NOT NULL DEFAULT 'simulated',
            sheet_verified  TEXT    NOT NULL DEFAULT 'analysis',
            notes           TEXT,
            created_at      TEXT    NOT NULL,
            UNIQUE(date, order_id, symbol, side, entry_time)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dryrun_trade_ledger_date "
        "ON dryrun_trade_ledger (date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dryrun_trade_ledger_symbol_date "
        "ON dryrun_trade_ledger (symbol, date)"
    )


def _report_paths(reports_dir: Path) -> list[Path]:
    pattern = str(reports_dir / "**" / "trading_data_*.json")
    return [Path(path) for path in sorted(glob.glob(pattern, recursive=True))]


def _in_scope(date_str: str, start: str | None, end: str | None, symbol: str | None) -> bool:
    if start and date_str < start:
        return False
    if end and date_str > end:
        return False
    return True


def _closed_dryrun_positions(data: dict[str, Any], symbol: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position in data.get("positions", []):
        if not isinstance(position, dict):
            continue
        if position.get("status") != "CLOSED":
            continue
        order_id = str(position.get("order_id") or "")
        if not order_id.startswith("DRY_RUN"):
            continue
        if symbol and str(position.get("symbol") or "").upper() != symbol.upper():
            continue
        rows.append(position)
    return rows


def _strategy_config(data: dict[str, Any]) -> tuple[str, str, str, str | None]:
    config = data.get("config", {}) if isinstance(data.get("config"), dict) else {}
    version = config.get("strategy_config_version")
    hash_value = config.get("strategy_config_hash")
    stage_name = config.get("trade_stage_name") or getattr(Config, "TRADE_STAGE_NAME", "")
    note_parts: list[str] = []
    if stage_name:
        note_parts.append(f"stage={stage_name}")
    if not version or not hash_value:
        version, hash_value = Config.snapshot_hash()
        note_parts.append("config hash inferred at import time; source report predates config-hash stamping")
    note = "; ".join(note_parts) if note_parts else None
    return str(version), str(hash_value), str(config.get("git_sha") or ""), note


def _trade_values(position: dict[str, Any]) -> tuple[int, float, float, float, float]:
    qty = int(position.get("qty", 0) or 0)
    partial_qty = int(position.get("_partial_qty", 0) or 0)
    total_qty = qty + partial_qty
    gross_pnl = round(float(position.get("pnl", 0) or 0) + float(position.get("_partial_pnl", 0) or 0), 2)
    entry_price = float(position.get("entry_price", 0) or 0)
    exit_price = float(position.get("exit_price", 0) or 0)
    return total_qty, gross_pnl, entry_price, exit_price, float(partial_qty)


def _insert_trade(
    conn: sqlite3.Connection,
    *,
    date_str: str,
    source_report: str,
    sessions: int,
    config_version: str,
    config_hash: str,
    git_sha: str,
    note: str | None,
    position: dict[str, Any],
) -> bool:
    total_qty, gross_pnl, entry_price, exit_price, _partial_qty = _trade_values(position)
    if total_qty <= 0 or entry_price <= 0 or exit_price <= 0:
        return False

    charges = per_trade_charges(position)
    net_pnl = round(gross_pnl - charges["total_charges"], 2)
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO dryrun_trade_ledger
           (date, source_report, sessions, config_version, config_hash, git_sha,
            symbol, exchange, side, qty, entry_price, exit_price, entry_time,
            exit_time, exit_reason, gross_pnl, buy_value, sell_value, turnover,
            brokerage, stt, exchange_txn, gst, sebi_charges, stamp_duty,
            total_charges, net_pnl, order_id, verified, sheet_verified, notes,
            created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'simulated', 'analysis', ?, ?)
        """,
        (
            date_str,
            source_report,
            sessions,
            config_version,
            config_hash,
            git_sha,
            str(position.get("symbol") or ""),
            str(position.get("exchange") or "NSE"),
            str(position.get("side") or ""),
            total_qty,
            round(entry_price, 2),
            round(exit_price, 2),
            str(position.get("entry_time") or position.get("_entry_time") or ""),
            str(position.get("exit_time") or position.get("_exit_time") or ""),
            str(position.get("exit_reason") or ""),
            gross_pnl,
            charges["buy_value"],
            charges["sell_value"],
            charges["turnover"],
            charges["brokerage"],
            charges["stt"],
            charges["exchange_txn"],
            charges["gst"],
            charges["sebi_charges"],
            charges["stamp_duty"],
            charges["total_charges"],
            net_pnl,
            str(position.get("order_id") or ""),
            note,
            dt.datetime.now(dt.timezone.utc).isoformat(),
        ),
    )
    return conn.total_changes > before


def _attach_candidate_outcome(db_path: Path, date_str: str, position: dict[str, Any]) -> None:
    telemetry = CandidateTelemetry(db_path=str(db_path))
    gross_pnl = round(float(position.get("pnl", 0) or 0) + float(position.get("_partial_pnl", 0) or 0), 2)
    telemetry.attach_outcome(
        date=date_str,
        symbol=str(position.get("symbol") or ""),
        side=str(position.get("side") or ""),
        entry_time=str(position.get("entry_time") or position.get("_entry_time") or "") or None,
        exit_price=float(position.get("exit_price", 0) or 0),
        exit_time=str(position.get("exit_time") or position.get("_exit_time") or "") or None,
        exit_reason=str(position.get("exit_reason") or ""),
        pnl=gross_pnl,
    )


def fill_reports(
    *,
    db_path: str | Path = DEFAULT_DB,
    reports_dir: str | Path = REPORTS_DIR,
    date_from: str | None = None,
    date_to: str | None = None,
    symbol: str | None = None,
    check_only: bool = False,
) -> dict[str, int]:
    db_path = Path(db_path)
    reports_dir = Path(reports_dir)
    stats = {
        "reports_scanned": 0,
        "dryrun_reports": 0,
        "closed_positions": 0,
        "inserted": 0,
        "skipped": 0,
    }

    conn = _connect(db_path)
    try:
        for path in _report_paths(reports_dir):
            stats["reports_scanned"] += 1
            data = _load_json(path)
            if not data or data.get("mode") != "dry_run":
                continue
            date_str = str(data.get("date") or "")
            if not date_str or not _in_scope(date_str, date_from, date_to, symbol):
                continue
            stats["dryrun_reports"] += 1
            config_version, config_hash, git_sha, note = _strategy_config(data)
            positions = _closed_dryrun_positions(data, symbol)
            for position in positions:
                stats["closed_positions"] += 1
                if check_only:
                    continue
                inserted = _insert_trade(
                    conn,
                    date_str=date_str,
                    source_report=_relpath(path),
                    sessions=int(data.get("sessions", 1) or 1),
                    config_version=config_version,
                    config_hash=config_hash,
                    git_sha=git_sha,
                    note=note,
                    position=position,
                )
                if inserted:
                    stats["inserted"] += 1
                else:
                    stats["skipped"] += 1
                _attach_candidate_outcome(db_path, date_str, position)
        if check_only:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fill dry-run analysis DB from dry-run trading reports."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Analysis DB path. Default: data/trade_analysis.db")
    parser.add_argument("--reports-dir", default=str(REPORTS_DIR), help="Trading reports root. Default: reports/trading")
    parser.add_argument("--from", dest="date_from", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--symbol", default=None, help="Optional symbol filter")
    parser.add_argument("--check-only", action="store_true", help="Scan and count rows without inserting")
    args = parser.parse_args()

    stats = fill_reports(
        db_path=args.db,
        reports_dir=args.reports_dir,
        date_from=args.date_from,
        date_to=args.date_to,
        symbol=args.symbol,
        check_only=args.check_only,
    )
    print("\n  Dry-run analysis fill")
    print(f"  DB               : {_relpath(args.db)}")
    print(f"  Reports scanned  : {stats['reports_scanned']}")
    print(f"  Dry-run reports  : {stats['dryrun_reports']}")
    print(f"  Closed positions : {stats['closed_positions']}")
    if args.check_only:
        print("  Mode             : check-only")
    else:
        print(f"  Inserted         : {stats['inserted']}")
        print(f"  Skipped/dupes    : {stats['skipped']}")


if __name__ == "__main__":
    main()