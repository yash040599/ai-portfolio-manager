"""
Export the local candle cache into the Stage 1 replay-data repository.

This is the first bridge from the existing tool data to the new private
`backtest_data/` repo. It performs no broker/API calls. It reads the local
`data/candle_cache.db`, writes normalized SQLite candle stores, refreshes CSV
metadata, and updates `backtest_data/manifest.json` with row counts and
checksums.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from shared.nifty_universe import get_universe  # noqa: E402


SOURCE_DB = PROJECT_ROOT / "data" / "candle_cache.db"
DEFAULT_BACKTEST_DATA_ROOT = PROJECT_ROOT.parent / "ai-portfolio-backtest-data"
BACKTEST_DATA_ROOT = Path(os.getenv("BACKTEST_DATA_PATH", "").strip() or str(DEFAULT_BACKTEST_DATA_ROOT))
if not BACKTEST_DATA_ROOT.is_absolute():
    BACKTEST_DATA_ROOT = (PROJECT_ROOT / BACKTEST_DATA_ROOT).resolve()
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
UNIVERSE_EFFECTIVE_FROM = "2026-05-14"


def _now_ist() -> str:
    return dt.datetime.now(IST).isoformat(timespec="seconds")


def _project_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_ts_ist(value: str) -> str:
    timestamp = dt.datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=IST)
    else:
        timestamp = timestamp.astimezone(IST)
    return timestamp.isoformat(timespec="seconds")


def _require_paths(source_db: Path, data_root: Path) -> None:
    if not source_db.is_file():
        raise SystemExit(f"Candle cache not found: {source_db}")
    if not (data_root / ".git").is_dir():
        raise SystemExit(
            f"Backtest data repo not found at {data_root}. Run "
            "scripts/shared/sync_backtest_data.py first."
        )
    schema_path = data_root / "schemas" / "candles.sql"
    if not schema_path.is_file():
        raise SystemExit(f"Missing candle schema: {schema_path}")


def _summarize_source(source_db: Path) -> dict[str, Any]:
    with sqlite3.connect(source_db) as connection:
        connection.row_factory = sqlite3.Row
        interval_rows = connection.execute(
            """
            SELECT interval,
                   COUNT(*) AS rows,
                   COUNT(DISTINCT symbol) AS symbols,
                   MIN(candle_date) AS first_ts,
                   MAX(candle_date) AS last_ts
              FROM candle_cache
             GROUP BY interval
             ORDER BY interval
            """
        ).fetchall()
        symbols = connection.execute(
            """
            SELECT symbol, exchange
              FROM candle_cache
             GROUP BY symbol, exchange
             ORDER BY symbol, exchange
            """
        ).fetchall()

    return {
        "intervals": [dict(row) for row in interval_rows],
        "symbols": [dict(row) for row in symbols],
    }


def _fetch_rows(
    source_db: Path,
    interval: str,
    source_tag: str,
    created_at: str,
) -> list[tuple[Any, ...]]:
    with sqlite3.connect(source_db) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT symbol, exchange, interval, candle_date, open, high, low, close, volume
              FROM candle_cache
             WHERE interval = ?
             ORDER BY symbol, exchange, candle_date
            """,
            (interval,),
        ).fetchall()

    exported: list[tuple[Any, ...]] = []
    for row in rows:
        exported.append((
            row["symbol"],
            None,
            row["exchange"],
            _normalize_ts_ist(row["candle_date"]),
            row["interval"],
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            int(row["volume"] or 0),
            source_tag,
            created_at,
        ))
    return exported


def _write_candle_store(db_path: Path, schema_sql: str, rows: list[tuple[Any, ...]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as connection:
        connection.executescript(schema_sql)
        connection.executemany(
            """
            INSERT INTO candles
            (symbol, instrument_token, exchange, ts_ist, interval,
             open, high, low, close, volume, source, created_at_ist)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _interval_stats(db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT interval,
                   COUNT(*) AS rows,
                   COUNT(DISTINCT symbol) AS symbols,
                   MIN(ts_ist) AS first_ts,
                   MAX(ts_ist) AS last_ts
              FROM candles
             GROUP BY interval
            """
        ).fetchone()
    return dict(row) if row else {}


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_symbol_files(data_root: Path, source_symbols: list[dict[str, str]]) -> list[Path]:
    written: list[Path] = []
    instruments_path = data_root / "symbols" / "instruments_nse.csv"
    instrument_fields = [
        "symbol", "exchange", "instrument_token", "name", "segment",
        "lot_size", "tick_size", "source", "effective_from", "effective_to",
    ]
    instrument_rows = []
    for symbol_row in source_symbols:
        instrument_rows.append({
            "symbol": symbol_row["symbol"],
            "exchange": symbol_row["exchange"],
            "instrument_token": "",
            "name": "",
            "segment": "EQ",
            "lot_size": "1",
            "tick_size": "",
            "source": "data/candle_cache.db",
            "effective_from": "",
            "effective_to": "",
        })
    _write_csv(instruments_path, instrument_fields, instrument_rows)
    written.append(instruments_path)

    universe_fields = ["symbol", "exchange", "effective_from", "effective_to", "source", "notes"]
    for universe_name in ("NIFTY50", "NIFTY100"):
        universe_path = data_root / "symbols" / f"universe_{universe_name.lower()}.csv"
        universe_rows = [
            {
                "symbol": symbol,
                "exchange": "NSE",
                "effective_from": UNIVERSE_EFFECTIVE_FROM,
                "effective_to": "",
                "source": "shared.nifty_universe",
                "notes": "current membership; not historical membership",
            }
            for symbol in get_universe(universe_name)
        ]
        _write_csv(universe_path, universe_fields, universe_rows)
        written.append(universe_path)

    actions_path = data_root / "corporate_actions" / "actions.csv"
    _write_csv(
        actions_path,
        ["symbol", "exchange", "action_type", "ex_date", "value", "source", "notes"],
        [],
    )
    written.append(actions_path)
    return written


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _build_manifest(
    *,
    data_root: Path,
    source_db: Path,
    source_summary: dict[str, Any],
    interval_files: dict[str, Path],
    interval_stats: dict[str, dict[str, Any]],
    metadata_files: list[Path],
    created_at: str,
    source_tag: str,
) -> dict[str, Any]:
    all_stats = [stats for stats in interval_stats.values() if stats]
    first_values = [stats["first_ts"] for stats in all_stats if stats.get("first_ts")]
    last_values = [stats["last_ts"] for stats in all_stats if stats.get("last_ts")]
    source_symbols = source_summary["symbols"]
    checksummed = list(interval_files.values()) + metadata_files + [data_root / "schemas" / "candles.sql"]

    return {
        "dataset_id": "nse_intraday_replay_v1",
        "dataset_version": f"candle-cache-{created_at[:10]}",
        "status": "seeded_from_candle_cache",
        "created_at_ist": created_at,
        "source": [
            {
                "name": "data/candle_cache.db",
                "tag": source_tag,
                "sha256": _sha256(source_db),
                "size_bytes": source_db.stat().st_size,
                "summary": source_summary["intervals"],
            }
        ],
        "universe": {
            "name": "candle_cache_symbols_plus_current_nifty_metadata",
            "symbols": len({row["symbol"] for row in source_symbols}),
            "policy": (
                "candle cache symbols for available candles; current NIFTY50/NIFTY100 "
                "metadata files are not historical membership."
            ),
        },
        "intervals": [
            {
                "name": interval,
                "file": _relative(interval_files[interval], data_root),
                "rows": interval_stats[interval].get("rows", 0),
                "symbols": interval_stats[interval].get("symbols", 0),
                "date_range": {
                    "start": interval_stats[interval].get("first_ts"),
                    "end": interval_stats[interval].get("last_ts"),
                },
            }
            for interval in sorted(interval_files)
        ],
        "date_range": {
            "start": min(first_values) if first_values else None,
            "end": max(last_values) if last_values else None,
        },
        "adjustment_policy": "source_as_cached; no independent corporate-action adjustment applied by exporter",
        "market_timezone": "Asia/Kolkata",
        "generated_by": {
            "repo": "ai-portfolio-manager",
            "commit": _project_commit(),
            "script": "scripts/trade/export_backtest_data.py",
        },
        "checksums": {
            _relative(path, data_root): _sha256(path)
            for path in checksummed
            if path.is_file()
        },
        "notes": [
            "Initial seed from the existing candle cache; no Zerodha/API calls were made.",
            "15-minute coverage is short and suitable only for replay plumbing, not strategy promotion.",
            "Daily history is longer but current universe metadata is survivor-biased until historical membership is added.",
        ],
    }


def _write_manifest(data_root: Path, manifest: dict[str, Any]) -> None:
    with (data_root / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export data/candle_cache.db into the private backtest_data repo."
    )
    parser.add_argument("--source-db", default=str(SOURCE_DB), help="Source candle_cache.db path.")
    parser.add_argument("--data-root", default=str(BACKTEST_DATA_ROOT), help="Backtest data repo path.")
    parser.add_argument("--source-tag", default="candle_cache", help="Source label stamped on exported rows.")
    parser.add_argument("--dry-run", action="store_true", help="Show counts without writing files.")
    args = parser.parse_args()

    source_db = Path(args.source_db).resolve()
    data_root = Path(args.data_root).resolve()
    _require_paths(source_db, data_root)

    source_summary = _summarize_source(source_db)
    print(f"Source: {source_db}")
    for interval_row in source_summary["intervals"]:
        print(
            f"  {interval_row['interval']:<9} rows={interval_row['rows']:<7} "
            f"symbols={interval_row['symbols']:<4} "
            f"range={interval_row['first_ts']}..{interval_row['last_ts']}"
        )

    if args.dry_run:
        print("Dry run: no files written.")
        return 0

    created_at = _now_ist()
    schema_sql = (data_root / "schemas" / "candles.sql").read_text(encoding="utf-8")
    interval_files = {
        "15minute": data_root / "candles" / "intraday_15m.sqlite",
        "day": data_root / "candles" / "daily.sqlite",
    }
    interval_stats: dict[str, dict[str, Any]] = {}

    for interval, target_path in interval_files.items():
        rows = _fetch_rows(source_db, interval, args.source_tag, created_at)
        _write_candle_store(target_path, schema_sql, rows)
        interval_stats[interval] = _interval_stats(target_path)
        print(f"Wrote {_relative(target_path, data_root)} rows={len(rows)}")

    metadata_files = _write_symbol_files(data_root, source_summary["symbols"])
    manifest = _build_manifest(
        data_root=data_root,
        source_db=source_db,
        source_summary=source_summary,
        interval_files=interval_files,
        interval_stats=interval_stats,
        metadata_files=metadata_files,
        created_at=created_at,
        source_tag=args.source_tag,
    )
    _write_manifest(data_root, manifest)
    print(f"Wrote {_relative(data_root / 'manifest.json', data_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())