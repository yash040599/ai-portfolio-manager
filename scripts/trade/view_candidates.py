"""
Read-only viewer for the per-candidate telemetry table (Roadmap #259).

Surfaces the SCORED / ENTERED / REJECTED candidate rows written by
`modes/trade/candidate_telemetry.py`. Use this to:

  - Inspect today's full candidate set (selection bias removed).
  - Compare a single symbol's rejected vs entered scores over time.
  - Audit which gates are rejecting candidates most often (once
    `rejection_audit.py` backfills the `rejected_gate` column).
  - Confirm that a config tuning change actually moved decisions
    (the `config_hash` column flips on every meaningful constant change).

Usage
-----

    python scripts/trade/view_candidates.py                 # today's live rows
    python scripts/trade/view_candidates.py --data-source dryrun
    python scripts/trade/view_candidates.py --date 2026-05-12
    python scripts/trade/view_candidates.py --since 2026-05-01
    python scripts/trade/view_candidates.py --symbol RELIANCE
    python scripts/trade/view_candidates.py --status REJECTED
    python scripts/trade/view_candidates.py --summary       # cohort summary
    python scripts/trade/view_candidates.py --hash          # group by config_hash

All queries are read-only (SELECTs only). Safe to run during a live
session — the SQLite WAL mode lets the bot keep writing.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import sys


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIVE_DB_PATH = os.path.join(PROJECT_ROOT, "data", "trades.db")
ANALYSIS_DB_PATH = os.path.join(PROJECT_ROOT, "data", "trade_analysis.db")


def _utf8_stdout():
    """Force stdout to UTF-8 so hash / arrow chars survive Windows cp1252."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _connect(db_path: str) -> sqlite3.Connection:
    if not os.path.isfile(db_path):
        print(f"  ! DB not found: {db_path}")
        sys.exit(2)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='intraday_candidates'"
    ).fetchone()
    return row is not None


def _build_where(args) -> tuple[str, list]:
    where = []
    params: list = []
    if args.date:
        where.append("date = ?")
        params.append(args.date)
    elif args.since:
        where.append("date >= ?")
        params.append(args.since)
    else:
        # Default to today.
        where.append("date = ?")
        params.append(str(datetime.date.today()))
    if args.symbol:
        where.append("symbol = ?")
        params.append(args.symbol.upper())
    if args.status:
        where.append("status = ?")
        params.append(args.status.upper())
    if args.side:
        where.append("side = ?")
        params.append(args.side.upper())
    return (" WHERE " + " AND ".join(where)) if where else "", params


def cmd_list(args, conn: sqlite3.Connection):
    where_sql, params = _build_where(args)
    rows = conn.execute(
        f"""SELECT date, scan_time, symbol, side, combined_score,
                   rsi, adx, rvol, status, rejected_gate,
                   entry_price, exit_price, exit_reason, pnl,
                   config_hash
            FROM intraday_candidates
            {where_sql}
            ORDER BY scan_time ASC, symbol ASC""",
        params,
    ).fetchall()

    if not rows:
        print("  (no candidate rows match)")
        return

    print(f"  {'time':<19} {'symbol':<12} {'side':<4} "
          f"{'score':>6} {'rsi':>4} {'adx':>4} {'rvol':>4}  "
          f"{'status':<9} {'pnl':>8}  {'cfg':<16}")
    print("  " + "-" * 110)
    for r in rows:
        ts = (r["scan_time"] or "")[-19:]
        score = r["combined_score"] or 0
        rsi = r["rsi"] or 0
        adx = r["adx"] or 0
        rvol = r["rvol"] or 0
        pnl = r["pnl"]
        pnl_s = f"{pnl:+.2f}" if pnl is not None else "-"
        gate = r["rejected_gate"] or ""
        cfg = (r["config_hash"] or "")[:16]
        status_label = r["status"]
        if status_label == "REJECTED" and gate:
            status_label = f"REJ:{gate[:5]}"
        print(f"  {ts:<19} {r['symbol']:<12} {r['side']:<4} "
              f"{score:>+6.1f} {rsi:>4.0f} {adx:>4.0f} {rvol:>4.1f}  "
              f"{status_label:<9} {pnl_s:>8}  {cfg:<16}")
    print(f"\n  {len(rows)} row(s).")


def cmd_summary(args, conn: sqlite3.Connection):
    where_sql, params = _build_where(args)

    totals = conn.execute(
        f"SELECT status, COUNT(*) AS n FROM intraday_candidates "
        f"{where_sql} GROUP BY status",
        params,
    ).fetchall()
    print("  Status totals:")
    for r in totals:
        print(f"    {r['status']:<9}  n={r['n']}")
    print()

    # Conversion rate
    n_scored = sum(r["n"] for r in totals)
    n_entered = sum(r["n"] for r in totals if r["status"] == "ENTERED")
    n_rejected = sum(r["n"] for r in totals if r["status"] == "REJECTED")
    if n_scored:
        print(f"  Scored: {n_scored}  Entered: {n_entered} "
              f"({n_entered/n_scored*100:.1f}%)  "
              f"Rejected: {n_rejected} ({n_rejected/n_scored*100:.1f}%)")

    # Rejection-by-gate (only if any gate is filled)
    gates = conn.execute(
        f"SELECT COALESCE(rejected_gate,'<unattributed>') AS g, COUNT(*) AS n "
        f"FROM intraday_candidates {where_sql} AND status='REJECTED' "
        f"GROUP BY g ORDER BY n DESC",
        params,
    ).fetchall()
    if gates:
        print("\n  Rejection by gate:")
        for r in gates:
            print(f"    {r['g']:<32} n={r['n']}")

    # Per-side outcome on ENTERED cohort
    sides = conn.execute(
        f"""SELECT side,
                   COUNT(*) AS n,
                   ROUND(AVG(pnl), 2) AS avg_pnl,
                   ROUND(SUM(pnl), 2) AS sum_pnl
            FROM intraday_candidates
            {where_sql} AND status='ENTERED' AND pnl IS NOT NULL
            GROUP BY side""",
        params,
    ).fetchall()
    if sides:
        print("\n  Realised outcomes (ENTERED + closed):")
        for r in sides:
            print(f"    {r['side']:<5}  n={r['n']:>3}  "
                  f"avg={r['avg_pnl']:+.2f}  sum={r['sum_pnl']:+.2f}")


def cmd_hash(args, conn: sqlite3.Connection):
    """Group entered/rejected counts by config_hash to verify a config
    change actually altered decisions."""
    where_sql, params = _build_where(args)
    rows = conn.execute(
        f"""SELECT config_version, config_hash, status, COUNT(*) AS n
            FROM intraday_candidates {where_sql}
            GROUP BY config_version, config_hash, status
            ORDER BY config_hash, status""",
        params,
    ).fetchall()
    if not rows:
        print("  (no rows)")
        return
    by_hash: dict[str, dict] = {}
    for r in rows:
        h = r["config_hash"] or "<none>"
        d = by_hash.setdefault(h, {"version": r["config_version"] or "<none>"})
        d[r["status"]] = r["n"]
    for h, d in by_hash.items():
        version = d.pop("version")
        counts = "  ".join(f"{k}={v}" for k, v in sorted(d.items()))
        print(f"  {h:<16}  {version:<24}  {counts}")


def main():
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        description="Read-only viewer for intraday_candidates telemetry "
                    "(Roadmap #259)."
    )
    parser.add_argument("--date", help="Single date YYYY-MM-DD (default: today).")
    parser.add_argument("--since", help="From date YYYY-MM-DD (overrides --date if --date omitted).")
    parser.add_argument("--symbol", help="Filter by symbol.")
    parser.add_argument("--side", choices=("BUY", "SELL"), help="Filter by side.")
    parser.add_argument("--status", help="Filter by status (SCORED / ENTERED / REJECTED).")
    parser.add_argument("--data-source", choices=("live", "dryrun"), default="live",
                        help="Read candidates from live trades.db or dry-run trade_analysis.db.")
    parser.add_argument("--db", help="Explicit SQLite DB path override.")
    parser.add_argument("--summary", action="store_true",
                        help="Show counts + per-gate / per-side rollups.")
    parser.add_argument("--hash", action="store_true",
                        help="Group counts by config_hash (verify a config "
                             "tune actually moved decisions).")
    args = parser.parse_args()

    db_path = args.db or (
        ANALYSIS_DB_PATH if args.data_source == "dryrun" else LIVE_DB_PATH
    )
    conn = _connect(db_path)
    try:
        if not _table_exists(conn):
            print("  ! intraday_candidates table not found. The bot writes the "
                  "table on first scanner run.")
            sys.exit(0)
        if args.summary:
            cmd_summary(args, conn)
        elif args.hash:
            cmd_hash(args, conn)
        else:
            cmd_list(args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
