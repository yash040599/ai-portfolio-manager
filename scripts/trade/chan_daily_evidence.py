"""
Write a daily Chan-framework evidence snapshot after trade runs.

This is the lightweight end-of-day automation layer. It updates the
appropriate analysis/live DB path, then writes JSON + Markdown evidence
under reports/trading/<YYYY>/<MM>/ without changing actual dashboard/tax
contracts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from config import Config, now_ist  # noqa: E402
from scripts.trade.fill_dryrun_analysis import fill_reports  # noqa: E402
from scripts.trade.fill_intraday_ledger import fill_fy  # noqa: E402
from shared.tax_db import indian_fy  # noqa: E402


LIVE_DB = PROJECT_ROOT / "data" / "trades.db"
DRYRUN_DB = PROJECT_ROOT / Config.TRADE_ANALYSIS_DB_PATH
REPORTS_DIR = PROJECT_ROOT / "reports" / "trading"


def _relpath(path: str | Path) -> str:
    try:
        return os.path.relpath(path, PROJECT_ROOT)
    except ValueError:
        return str(path)


def _today() -> str:
    return now_ist().date().isoformat()


def _date_obj(date_str: str) -> dt.date:
    return dt.date.fromisoformat(date_str)


def _report_dir(date_str: str) -> Path:
    d = _date_obj(date_str)
    return REPORTS_DIR / f"{d.year:04d}" / f"{d.month:02d}"


def _trading_json_path(date_str: str, data_source: str) -> Path:
    d = _date_obj(date_str)
    suffix = "_dry_run" if data_source == "dryrun" else ""
    return _report_dir(date_str) / f"trading_data_{d.day:02d}{suffix}.json"


def _evidence_paths(date_str: str, data_source: str) -> tuple[Path, Path]:
    d = _date_obj(date_str)
    prefix = _report_dir(date_str) / f"chan_evidence_{d.day:02d}_{data_source}"
    return prefix.with_suffix(".json"), prefix.with_suffix(".md")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _connect_existing(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _count_by(rows: list[sqlite3.Row], key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts[str(row[key] or "<none>")] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _sum(rows: list[sqlite3.Row], key: str) -> float:
    return round(sum(float(row[key] or 0.0) for row in rows), 2)


def _candidate_stats(conn: sqlite3.Connection | None, date_str: str, config_hash: str) -> dict[str, Any]:
    empty = {
        "rows_any_hash": 0,
        "rows_matching_hash": 0,
        "hash_counts": {},
        "status_counts": {},
        "side_counts": {},
        "entered": 0,
        "rejected": 0,
    }
    if conn is None or not _table_exists(conn, "intraday_candidates"):
        return empty
    all_rows = conn.execute(
        "SELECT config_hash, status, side FROM intraday_candidates WHERE date=?",
        (date_str,),
    ).fetchall()
    rows = [row for row in all_rows if row["config_hash"] == config_hash]
    return {
        "rows_any_hash": len(all_rows),
        "rows_matching_hash": len(rows),
        "hash_counts": _count_by(all_rows, "config_hash"),
        "status_counts": _count_by(rows, "status"),
        "side_counts": _count_by(rows, "side"),
        "entered": sum(1 for row in rows if row["status"] == "ENTERED"),
        "rejected": sum(1 for row in rows if row["status"] == "REJECTED"),
    }


def _outcome_stats(conn: sqlite3.Connection | None, date_str: str, table: str) -> dict[str, Any]:
    empty = {
        "table": table,
        "rows": 0,
        "gross_pnl_inr": 0.0,
        "charges_inr": 0.0,
        "net_pnl_inr": 0.0,
        "side_counts": {},
        "exit_reason_counts": {},
    }
    if conn is None or not _table_exists(conn, table):
        return empty
    rows = conn.execute(
        f"SELECT side, exit_reason, gross_pnl, total_charges, net_pnl FROM {table} WHERE date=?",
        (date_str,),
    ).fetchall()
    return {
        "table": table,
        "rows": len(rows),
        "gross_pnl_inr": _sum(rows, "gross_pnl"),
        "charges_inr": _sum(rows, "total_charges"),
        "net_pnl_inr": _sum(rows, "net_pnl"),
        "side_counts": _count_by(rows, "side"),
        "exit_reason_counts": _count_by(rows, "exit_reason"),
    }


def _live_logical_trade_count(conn: sqlite3.Connection | None, date_str: str) -> int | None:
    if conn is None or not _table_exists(conn, "trades"):
        return None
    row = conn.execute("SELECT COUNT(*) AS n FROM trades WHERE date=?", (date_str,)).fetchone()
    return int(row["n"] or 0) if row else 0


def _status_and_flags(data_source: str, candidates: dict[str, Any], outcomes: dict[str, Any],
                      logical_trade_rows: int | None) -> tuple[str, list[str]]:
    label = "dry-run" if data_source == "dryrun" else "live"
    flags: list[str] = []
    if candidates["rows_any_hash"] == 0:
        flags.append(f"No {label} candidate telemetry rows for this date.")
    elif candidates["rows_matching_hash"] == 0:
        flags.append(f"{label} candidate rows exist, but none match the current config hash.")
    if outcomes["rows"] == 0:
        flags.append(f"No {label} after-cost outcome rows for this date.")
    if data_source == "live" and logical_trade_rows is not None and outcomes["rows"] and logical_trade_rows != outcomes["rows"]:
        flags.append("Live logical trade count differs from intraday_tax_ledger count.")
    if not flags:
        return "READY", flags
    if candidates["rows_matching_hash"] == 0 or outcomes["rows"] == 0:
        return "DATA_GAP", flags
    return "REVIEW_REQUIRED", flags


def build_daily_evidence(
    date_str: str,
    data_source: str,
    *,
    update_dbs: bool = True,
    require_trading_report: bool = True,
) -> dict[str, Any]:
    if data_source not in {"dryrun", "live"}:
        raise ValueError("data_source must be 'dryrun' or 'live'")
    trading_json = _trading_json_path(date_str, data_source)
    if require_trading_report and not trading_json.exists():
        raise FileNotFoundError(
            f"Trading data report not found for {date_str} {data_source}: "
            f"{_relpath(trading_json)}. Run this after the trade report is generated."
        )
    if update_dbs:
        if data_source == "dryrun":
            fill_reports(date_from=date_str, date_to=date_str)
        else:
            fill_fy(indian_fy(date_str))

    config_version, config_hash = Config.snapshot_hash()
    report = _load_json(trading_json)
    report_config = report.get("config", {}) if isinstance(report.get("config"), dict) else {}
    config_version = str(report_config.get("strategy_config_version") or config_version)
    config_hash = str(report_config.get("strategy_config_hash") or config_hash)
    stage_name = str(
        report_config.get("trade_stage_name")
        or getattr(Config, "TRADE_STAGE_NAME", "")
    )

    db_path = DRYRUN_DB if data_source == "dryrun" else LIVE_DB
    conn = _connect_existing(db_path)
    try:
        candidates = _candidate_stats(conn, date_str, config_hash)
        table = "dryrun_trade_ledger" if data_source == "dryrun" else "intraday_tax_ledger"
        outcomes = _outcome_stats(conn, date_str, table)
        logical_rows = None if data_source == "dryrun" else _live_logical_trade_count(conn, date_str)
    finally:
        if conn is not None:
            conn.close()

    status, flags = _status_and_flags(data_source, candidates, outcomes, logical_rows)
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "date": date_str,
        "data_source": data_source,
        "status": status,
        "stage_name": stage_name,
        "config_version": config_version,
        "config_hash": config_hash,
        "db_path": _relpath(db_path),
        "trading_report_path": _relpath(trading_json),
        "candidates": candidates,
        "outcomes": outcomes,
        "logical_trade_rows": logical_rows,
        "red_flags": flags,
        "data_contract": {
            "dryrun": "analysis only; stored in data/trade_analysis.db",
            "live": "actual P&L/tax; stored in data/trades.db intraday_tax_ledger",
        },
    }


def _money(value: Any) -> str:
    number = float(value or 0.0)
    sign = "+" if number >= 0 else "-"
    return f"{sign}Rs.{abs(number):,.2f}"


def _markdown(snapshot: dict[str, Any]) -> str:
    title = "Dry-Run" if snapshot["data_source"] == "dryrun" else "Live"
    outcomes = snapshot["outcomes"]
    candidates = snapshot["candidates"]
    lines = [
        f"# Chan Daily Evidence - {snapshot['date']} ({title})",
        "",
        f"Status: `{snapshot['status']}`",
        f"Stage: `{snapshot.get('stage_name') or '-'}`",
        f"Config: `{snapshot['config_version']} / {snapshot['config_hash']}`",
        f"DB: `{snapshot['db_path']}`",
        f"Trading report: `{snapshot['trading_report_path']}`",
        "",
        "## Candidate Telemetry",
        "",
        f"- Rows any hash: {candidates['rows_any_hash']}",
        f"- Rows matching hash: {candidates['rows_matching_hash']}",
        f"- Entered: {candidates['entered']}",
        f"- Rejected: {candidates['rejected']}",
        "",
        "## After-Cost Outcomes",
        "",
        f"- Table: `{outcomes['table']}`",
        f"- Rows: {outcomes['rows']}",
        f"- Gross P&L: {_money(outcomes['gross_pnl_inr'])}",
        f"- Regulatory charges: {_money(outcomes['charges_inr'])}",
        f"- Net P&L: {_money(outcomes['net_pnl_inr'])}",
        "",
    ]
    if snapshot.get("logical_trade_rows") is not None:
        lines.extend([f"- Logical trade rows: {snapshot['logical_trade_rows']}", ""])
    if snapshot["red_flags"]:
        lines.extend(["## Red Flags", ""])
        lines.extend(f"- {flag}" for flag in snapshot["red_flags"])
        lines.append("")
    lines.extend([
        "## Data Boundary",
        "",
        "- Dry-run rows are analysis-only and do not feed dashboard/tax actual P&L.",
        "- Live dashboard/tax actuals remain sourced from `intraday_tax_ledger`.",
        "",
    ])
    return "\n".join(lines)


def write_daily_evidence(
    date_str: str | None = None,
    data_source: str = "dryrun",
    *,
    update_dbs: bool = True,
    require_trading_report: bool = True,
) -> dict[str, Any]:
    date_str = date_str or _today()
    snapshot = build_daily_evidence(
        date_str,
        data_source,
        update_dbs=update_dbs,
        require_trading_report=require_trading_report,
    )
    json_path, md_path = _evidence_paths(date_str, data_source)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(snapshot, indent=2, allow_nan=False), encoding="utf-8")
    md_path.write_text(_markdown(snapshot), encoding="utf-8")
    snapshot["evidence_json_path"] = _relpath(json_path)
    snapshot["evidence_markdown_path"] = _relpath(md_path)
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Write Chan daily evidence snapshot.")
    parser.add_argument("--date", default=_today(), help="Date YYYY-MM-DD. Default: today.")
    parser.add_argument("--data-source", choices=("dryrun", "live"), default="dryrun")
    parser.add_argument("--no-db-update", action="store_true", help="Only read current DB state; do not run ledger fill scripts.")
    parser.add_argument(
        "--allow-missing-report",
        action="store_true",
        help="Write a gap snapshot even when trading_data_DD*.json has not been generated.",
    )
    args = parser.parse_args()

    snapshot = write_daily_evidence(
        args.date,
        args.data_source,
        update_dbs=not args.no_db_update,
        require_trading_report=not args.allow_missing_report,
    )
    print("\n  Chan daily evidence")
    print(f"  Status      : {snapshot['status']}")
    print(f"  Source      : {snapshot['data_source']}")
    print(f"  Candidates  : {snapshot['candidates']['rows_matching_hash']}")
    print(f"  Outcomes    : {snapshot['outcomes']['rows']}")
    print(f"  Net P&L     : {_money(snapshot['outcomes']['net_pnl_inr'])}")
    if snapshot["red_flags"]:
        print("  Red flags   :")
        for flag in snapshot["red_flags"]:
            print(f"    - {flag}")
    print(f"  JSON        : {snapshot['evidence_json_path']}")
    print(f"  Markdown    : {snapshot['evidence_markdown_path']}")


if __name__ == "__main__":
    main()