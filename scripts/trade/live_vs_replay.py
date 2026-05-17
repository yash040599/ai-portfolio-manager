"""
scripts/trade/live_vs_replay.py
================================================================
Stage 1 T1.4 live-vs-replay comparison report.

Read-only against `data/trades.db`. Compares one replay JSON from
`reports/backtest/` with live candidate telemetry and realised live
outcomes for the same date/symbol scope.

This is parity/evidence plumbing, not a promotion gate.
================================================================
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sqlite3
import statistics
import sys
from collections import Counter
from typing import Any


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from config import Config  # noqa: E402


DEFAULT_DB = os.path.join(PROJECT_ROOT, "data", "trades.db")
DEFAULT_ANALYSIS_DB = os.path.join(PROJECT_ROOT, Config.TRADE_ANALYSIS_DB_PATH)
OUT_DIR = os.path.join(PROJECT_ROOT, "reports", "backtest")


def _utf8_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _relpath(path: str) -> str:
    try:
        return os.path.relpath(path, PROJECT_ROOT)
    except ValueError:
        return path


def _slug(value: Any) -> str:
    text = str(value).strip()
    cleaned = "".join(char if char.isalnum() else "-" for char in text)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or "ALL"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _row_date(row: dict) -> str | None:
    for key in ("date", "scan_time", "entry_ts", "entry_time"):
        value = row.get(key)
        if value is not None and len(str(value)) >= 10:
            return str(value)[:10]
    return None


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts[str(row.get(key) or "<none>")] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _pct(part: int | float, total: int | float) -> float | None:
    if not total:
        return None
    return round(float(part) / float(total) * 100.0, 3)


def _profit_factor(values: list[float]) -> tuple[float | None, str | None]:
    wins = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value <= 0)
    if losses <= 0:
        return (None, "inf") if wins > 0 else (None, None)
    return wins / losses, None


def _max_drawdown(values: list[float]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    return max_drawdown


def _pnl_stats(values: list[float]) -> dict[str, Any]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    total = sum(values)
    profit_factor, profit_factor_label = _profit_factor(values)
    return {
        "count": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": _pct(len(wins), len(values)),
        "net_pnl_inr": round(total, 2),
        "expectancy_inr": round(statistics.fmean(values), 2) if values else None,
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "profit_factor_label": profit_factor_label,
        "max_drawdown_inr": round(_max_drawdown(values), 2),
    }


def _candidate_stats(rows: list[dict]) -> dict[str, Any]:
    entered = [row for row in rows if row.get("status") == "ENTERED"]
    rejected = [row for row in rows if row.get("status") == "REJECTED"]
    abs_scores = [abs(_float(row.get("combined_score"))) for row in rows]
    return {
        "count": len(rows),
        "entered": len(entered),
        "rejected": len(rejected),
        "entry_rate_pct": _pct(len(entered), len(rows)),
        "status": _count_by(rows, "status"),
        "side": _count_by(rows, "side"),
        "rejections": _count_by(rejected, "rejected_gate"),
        "avg_abs_score": round(statistics.fmean(abs_scores), 3) if abs_scores else None,
        "max_abs_score": round(max(abs_scores), 3) if abs_scores else None,
    }


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or "candidates" not in data or "trades" not in data:
        raise ValueError(f"Not a replay JSON: {path}")
    return data


def _latest_replay_path() -> str:
    paths = sorted(glob.glob(os.path.join(OUT_DIR, "*.json")), key=os.path.getmtime, reverse=True)
    for path in paths:
        if "live-vs-replay" in os.path.basename(path):
            continue
        try:
            _load_json(path)
            return path
        except Exception:
            continue
    raise FileNotFoundError(f"No replay JSON found in {OUT_DIR}")


def _infer_window(replay: dict, args: argparse.Namespace) -> tuple[str, str]:
    start = args.dt_from or replay.get("from")
    end = args.dt_to or replay.get("to")
    if not start or not end:
        rows = list(replay.get("candidates", [])) + list(replay.get("trades", []))
        dates = [_row_date(row) for row in rows if isinstance(row, dict)]
        dates = [date_value for date_value in dates if date_value]
        if dates:
            start = start or min(dates)
            end = end or max(dates)
    if not start or not end:
        raise ValueError("Date range missing; pass --from/--to or use a replay JSON with from/to.")
    dt.date.fromisoformat(str(start))
    dt.date.fromisoformat(str(end))
    if str(end) < str(start):
        raise ValueError("--to must be >= --from")
    return str(start), str(end)


def _infer_symbol(replay: dict, explicit_symbol: str | None) -> str | None:
    if explicit_symbol:
        return explicit_symbol.upper()
    symbols = {
        str(row.get("symbol") or "").upper()
        for group in (replay.get("candidates", []), replay.get("trades", []))
        for row in group
        if isinstance(row, dict) and row.get("symbol")
    }
    return next(iter(symbols)) if len(symbols) == 1 else None


def _in_scope(row: dict, start: str, end: str, symbol: str | None) -> bool:
    date_value = _row_date(row)
    if not date_value or not (start <= date_value <= end):
        return False
    if symbol and str(row.get("symbol") or "").upper() != symbol.upper():
        return False
    return True


def _filter_replay(replay: dict, start: str, end: str, symbol: str | None) -> tuple[list[dict], list[dict]]:
    candidates = [
        row for row in replay.get("candidates", [])
        if isinstance(row, dict) and _in_scope(row, start, end, symbol)
    ]
    trades = [
        row for row in replay.get("trades", [])
        if isinstance(row, dict) and _in_scope(row, start, end, symbol)
    ]
    return candidates, trades


def _replay_score_passing(replay: dict, candidates: list[dict]) -> list[dict]:
    min_score = _float(replay.get("min_score"))
    passing = []
    for row in candidates:
        if row.get("rejected_gate") == "SCORE_FLOOR":
            continue
        if min_score > 0 and abs(_float(row.get("combined_score"))) < min_score:
            continue
        passing.append(row)
    return passing


def _connect_ro(db_path: str) -> sqlite3.Connection:
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"DB not found: {db_path}")
    uri = "file:" + os.path.abspath(db_path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _fetch_rows(conn: sqlite3.Connection, table_name: str, columns: str,
                start: str, end: str, symbol: str | None) -> list[dict]:
    if not _table_exists(conn, table_name):
        return []
    params: list[Any] = [start, end]
    symbol_sql = ""
    if symbol:
        symbol_sql = " AND symbol = ?"
        params.append(symbol.upper())
    rows = conn.execute(
        f"SELECT {columns} FROM {table_name} "
        f"WHERE date >= ? AND date <= ?{symbol_sql} ORDER BY date ASC, id ASC",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _load_live_data(db_path: str, start: str, end: str, symbol: str | None,
                    config_hash: str | None, data_source: str) -> dict[str, list[dict]]:
    tax_table = "dryrun_trade_ledger" if data_source == "dryrun" else "intraday_tax_ledger"
    with _connect_ro(db_path) as conn:
        all_candidates = _fetch_rows(
            conn,
            "intraday_candidates",
            "date, scan_time, symbol, exchange, side, combined_score, pattern_score, "
            "tech_score, rsi, adx, rvol, vwap, ltp, config_version, config_hash, "
            "status, rejected_gate, entry_price, entry_time, exit_price, exit_time, "
            "exit_reason, pnl",
            start,
            end,
            symbol,
        )
        candidates = [row for row in all_candidates if row.get("config_hash") == config_hash] if config_hash else all_candidates
        if data_source == "dryrun":
            logical_trades = []
        else:
            logical_trades = _fetch_rows(
                conn,
                "trades",
                "date, symbol, side, entry_price, exit_price, qty, pnl, exit_reason, "
                "entry_score, entry_rsi, entry_time, exit_time, market_condition",
                start,
                end,
                symbol,
            )
        tax_trades = _fetch_rows(
            conn,
            tax_table,
            "date, symbol, exchange, side, qty, entry_price, exit_price, entry_time, "
            "exit_time, exit_reason, gross_pnl, buy_value, sell_value, turnover, "
            "total_charges, net_pnl, verified, sheet_verified",
            start,
            end,
            symbol,
        )
    return {
        "all_candidates": all_candidates,
        "candidates": candidates,
        "logical_trades": logical_trades,
        "tax_trades": tax_trades,
    }


def _tax_trade_stats(rows: list[dict]) -> dict[str, Any]:
    net_values = [_float(row.get("net_pnl")) for row in rows]
    charge_total = sum(_float(row.get("total_charges")) for row in rows)
    return {
        "net": _pnl_stats(net_values),
        "gross_pnl_inr": round(sum(_float(row.get("gross_pnl")) for row in rows), 2),
        "charges_inr": round(charge_total, 2),
        "turnover_inr": round(sum(_float(row.get("turnover")) for row in rows), 2),
        "avg_charge_inr": round(charge_total / len(rows), 2) if rows else None,
        "side": _count_by(rows, "side"),
        "exit_reasons": _count_by(rows, "exit_reason"),
        "verified": _count_by(rows, "verified"),
        "sheet_verified": _count_by(rows, "sheet_verified"),
    }


def _logical_trade_stats(rows: list[dict]) -> dict[str, Any]:
    return {
        "gross": _pnl_stats([_float(row.get("pnl")) for row in rows]),
        "side": _count_by(rows, "side"),
        "exit_reasons": _count_by(rows, "exit_reason"),
    }


def _replay_trade_stats(rows: list[dict]) -> dict[str, Any]:
    charge_total = sum(_float(row.get("charges_inr")) for row in rows)
    return {
        "net": _pnl_stats([_float(row.get("net_pnl_inr")) for row in rows]),
        "raw_pnl_inr": round(sum(_float(row.get("raw_pnl_inr")) for row in rows), 2),
        "gross_pnl_inr": round(sum(_float(row.get("gross_pnl_inr")) for row in rows), 2),
        "charges_inr": round(charge_total, 2),
        "avg_charge_inr": round(charge_total / len(rows), 2) if rows else None,
        "side": _count_by(rows, "side"),
        "exit_reasons": _count_by(rows, "reason"),
    }


def _candidate_keys(rows: list[dict], status: str | None = None) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for row in rows:
        if status and row.get("status") != status:
            continue
        date_value = _row_date(row)
        symbol = str(row.get("symbol") or "").upper()
        side = str(row.get("side") or "").upper()
        if date_value and symbol and side:
            keys.add((date_value, symbol, side))
    return keys


def _candidate_overlap(live_rows: list[dict], replay_rows: list[dict]) -> dict[str, Any]:
    live_keys = _candidate_keys(live_rows)
    replay_keys = _candidate_keys(replay_rows)
    live_entered = _candidate_keys(live_rows, "ENTERED")
    replay_entered = _candidate_keys(replay_rows, "ENTERED")
    overlap = live_keys & replay_keys
    entered_overlap = live_entered & replay_entered
    return {
        "key_shape": "date|symbol|side",
        "live_candidate_keys": len(live_keys),
        "replay_candidate_keys": len(replay_keys),
        "candidate_key_overlap": len(overlap),
        "candidate_key_overlap_pct_of_live": _pct(len(overlap), len(live_keys)),
        "candidate_key_overlap_pct_of_replay": _pct(len(overlap), len(replay_keys)),
        "live_entered_keys": len(live_entered),
        "replay_entered_keys": len(replay_entered),
        "entered_key_overlap": len(entered_overlap),
        "entered_key_overlap_pct_of_live": _pct(len(entered_overlap), len(live_entered)),
        "entered_key_overlap_pct_of_replay": _pct(len(entered_overlap), len(replay_entered)),
    }


def _delta(live_value: Any, replay_value: Any, places: int = 2) -> float | None:
    if live_value is None or replay_value is None:
        return None
    return round(float(live_value) - float(replay_value), places)


def _outcome_delta(live_tax_stats: dict, replay_stats: dict) -> dict[str, Any]:
    live_net = live_tax_stats["net"]
    replay_net = replay_stats["net"]
    return {
        "trade_count_live_minus_replay": _delta(live_net["count"], replay_net["count"], 0),
        "net_pnl_live_minus_replay_inr": _delta(live_net["net_pnl_inr"], replay_net["net_pnl_inr"]),
        "expectancy_live_minus_replay_inr": _delta(live_net["expectancy_inr"], replay_net["expectancy_inr"]),
        "win_rate_live_minus_replay_pct": _delta(live_net["win_rate_pct"], replay_net["win_rate_pct"], 3),
        "profit_factor_live_minus_replay": _delta(live_net["profit_factor"], replay_net["profit_factor"], 4),
    }


def _build_red_flags(report: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    live = report["live"]
    replay = report["replay"]
    replay_metrics = report["replay_metrics"]
    overlap = report["comparison"]["candidate_key_overlap_vs_score_passing"]
    source_label = "Dry-run" if report.get("data_source") == "dryrun" else "Live"

    if replay.get("score_mode") != "scanner":
        flags.append("Replay score_mode is not scanner; rerun with --score-mode scanner for live-path parity evidence.")
    if not replay.get("config_hash"):
        flags.append("Replay JSON has no config_hash.")
    if replay.get("config_hash") and replay["config_hash"] != replay.get("current_config_hash"):
        flags.append(f"Replay config hash {replay['config_hash']} differs from current config hash {replay['current_config_hash']}.")
    if not live["candidate_rows_any_hash"]:
        flags.append(f"No {source_label.lower()} candidate telemetry rows exist in this scope; candidate parity cannot be assessed yet.")
    elif not live["candidate_rows_compared"]:
        flags.append(f"{source_label} candidate rows exist, but none match the replay config_hash.")
    if not live["tax_trade_rows"]:
        flags.append(f"No {source_label.lower()} after-cost outcome rows in this scope; outcome comparison is unavailable.")
    if not replay_metrics["trades"]["net"]["count"]:
        flags.append("Replay has no entered trades in this scope; outcome comparison is unavailable.")
    if live["tax_trade_rows"] and replay_metrics["trades"]["net"]["count"] and live["tax_trade_rows"] != replay_metrics["trades"]["net"]["count"]:
        flags.append(f"{source_label} after-cost trade count differs from replay trade count; outcome deltas describe scope mismatch, not parity.")
    if report.get("data_source") == "live" and live["tax_trade_rows"] and live["logical_trade_rows"] and live["tax_trade_rows"] != live["logical_trade_rows"]:
        flags.append("Live logical trade count differs from intraday_tax_ledger count; inspect reconciliation before using deltas.")
    if live["candidate_rows_compared"] and replay_metrics["candidates_score_passing"]["count"] and not overlap["candidate_key_overlap"]:
        flags.append(f"{source_label} and replay candidate overlap is zero at date|symbol|side granularity.")
    return flags


def _default_out_path(start: str, end: str, symbol: str | None, config_hash: str | None,
                      data_source: str) -> str:
    comparison_slug = "dryrun-vs-replay" if data_source == "dryrun" else "live-vs-replay"
    return os.path.join(
        OUT_DIR,
        f"{start}_to_{end}_{_slug(symbol or 'ALL')}_{comparison_slug}_{_slug(config_hash or 'NOHASH')}.json",
    )


def _fmt_money(value: Any) -> str:
    if value is None:
        return "n/a"
    number = _float(value)
    sign = "+" if number >= 0 else "-"
    return f"{sign}Rs.{abs(number):,.2f}"


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    replay_path = os.path.abspath(args.replay or _latest_replay_path())
    replay = _load_json(replay_path)
    start, end = _infer_window(replay, args)
    symbol = _infer_symbol(replay, args.symbol)
    config_hash = args.config_hash or replay.get("config_hash")
    current_version, current_hash = Config.snapshot_hash()
    db_path = os.path.abspath(args.db or (DEFAULT_ANALYSIS_DB if args.data_source == "dryrun" else DEFAULT_DB))

    replay_candidates_all, replay_trades = _filter_replay(replay, start, end, symbol)
    replay_candidates_passing = _replay_score_passing(replay, replay_candidates_all)
    live = _load_live_data(db_path, start, end, symbol, None if args.allow_hash_mismatch else config_hash, args.data_source)

    live_candidates = live["candidates"]
    live_logical = live["logical_trades"]
    live_tax = live["tax_trades"]
    report: dict[str, Any] = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "PENDING",
        "data_source": args.data_source,
        "window": {"from": start, "to": end, "symbol": symbol},
        "db_path": _relpath(db_path),
        "replay": {
            "path": _relpath(replay_path),
            "config_version": replay.get("config_version"),
            "config_hash": replay.get("config_hash"),
            "current_config_version": current_version,
            "current_config_hash": current_hash,
            "score_mode": replay.get("score_mode"),
            "min_score": replay.get("min_score"),
            "cost_model": replay.get("cost_model", {}),
        },
        "live": {
            "candidate_rows_any_hash": len(live["all_candidates"]),
            "candidate_rows_compared": len(live_candidates),
            "candidate_hash_counts": _count_by(live["all_candidates"], "config_hash"),
            "logical_trade_rows": len(live_logical),
            "tax_trade_rows": len(live_tax),
        },
        "live_metrics": {
            "candidates": _candidate_stats(live_candidates),
            "logical_trades": _logical_trade_stats(live_logical),
            "tax_trades": _tax_trade_stats(live_tax),
        },
        "replay_metrics": {
            "candidates_all_nonzero": _candidate_stats(replay_candidates_all),
            "candidates_score_passing": _candidate_stats(replay_candidates_passing),
            "trades": _replay_trade_stats(replay_trades),
        },
        "comparison": {
            "candidate_key_overlap_vs_score_passing": _candidate_overlap(live_candidates, replay_candidates_passing),
        },
    }
    report["comparison"]["live_tax_minus_replay_net"] = _outcome_delta(
        report["live_metrics"]["tax_trades"],
        report["replay_metrics"]["trades"],
    )
    report["red_flags"] = _build_red_flags(report)
    if not report["red_flags"]:
        report["status"] = "COMPARE_READY"
    elif not live_candidates or not live_tax or not replay_trades:
        report["status"] = "DATA_GAP"
    else:
        report["status"] = "REVIEW_REQUIRED"
    return report


def main() -> None:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        description="Read-only live-vs-replay comparison report for Chan Stage 1 T1.4."
    )
    parser.add_argument("--replay", default=None, help="Replay JSON path. Default: newest replay JSON in reports/backtest.")
    parser.add_argument("--from", dest="dt_from", default=None, help="Start date YYYY-MM-DD. Default: replay JSON 'from'.")
    parser.add_argument("--to", dest="dt_to", default=None, help="End date YYYY-MM-DD. Default: replay JSON 'to'.")
    parser.add_argument("--symbol", default=None, help="Single-symbol scope. Default: infer if replay has exactly one symbol.")
    parser.add_argument("--config-hash", default=None, help="Live candidate config hash. Default: replay JSON config_hash.")
    parser.add_argument("--allow-hash-mismatch", action="store_true", help="Compare all live candidate rows instead of filtering to the replay hash.")
    parser.add_argument("--data-source", choices=("live", "dryrun"), default="live", help="Observed data source: live data/trades.db or dry-run data/trade_analysis.db.")
    parser.add_argument("--db", default=None, help="Observed DB path. Default depends on --data-source. Opened read-only.")
    parser.add_argument("--out", default=None, help="Output JSON path.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 when red flags are present.")
    args = parser.parse_args()

    report = build_report(args)
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.abspath(args.out or _default_out_path(
        report["window"]["from"],
        report["window"]["to"],
        report["window"]["symbol"],
        report["replay"].get("config_hash"),
        report["data_source"],
    ))
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)

    live_candidates = report["live_metrics"]["candidates"]
    replay_candidates = report["replay_metrics"]["candidates_score_passing"]
    live_net = report["live_metrics"]["tax_trades"]["net"]
    replay_net = report["replay_metrics"]["trades"]["net"]
    source_label = "Dry-run" if report["data_source"] == "dryrun" else "Live"
    print(f"\n  {source_label} vs replay comparison")
    print(f"  Status       : {report['status']}")
    print(f"  Window       : {report['window']['from']} .. {report['window']['to']}")
    print(f"  Symbol scope : {report['window']['symbol'] or 'ALL'}")
    print(f"  Config hash  : {report['replay'].get('config_hash') or 'n/a'}")
    print(f"  Candidates   : {source_label.lower()} {live_candidates['count']} vs replay {replay_candidates['count']}")
    print(f"  Entered      : {source_label.lower()} {live_candidates['entered']} vs replay {replay_candidates['entered']}")
    print(f"  Net P&L      : {source_label.lower()} {_fmt_money(live_net['net_pnl_inr'])} vs replay {_fmt_money(replay_net['net_pnl_inr'])}")
    if report["red_flags"]:
        print("  Red flags    :")
        for flag in report["red_flags"]:
            print(f"    - {flag}")
    print(f"\n  JSON report  : {_relpath(out_path)}")

    if args.strict and report["red_flags"]:
        sys.exit(1)


if __name__ == "__main__":
    main()