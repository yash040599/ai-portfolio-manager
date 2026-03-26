"""
View tax ledger trades stored in the database for a financial year.

Displays every trade with entry/exit prices (Zerodha-verified),
charges breakdown, and net P&L — then a quick FY summary.

Usage
─────
    python scripts/view_tax_ledger.py              # current FY
    python scripts/view_tax_ledger.py --fy 2025    # FY 2025-26
    python scripts/view_tax_ledger.py --list       # list FYs with data
"""

import argparse
import datetime
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

DB_PATH = os.path.join(PROJECT_ROOT, "data", "trades.db")


def _get_db() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        print(f"  Database not found: {DB_PATH}")
        print(f"  Run 'python scripts/generate_tax_ledger.py fill' first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def indian_fy(date_str: str) -> int:
    d = datetime.date.fromisoformat(date_str)
    return d.year if d.month >= 4 else d.year - 1


def fy_label(fy_start: int) -> str:
    return f"FY {fy_start}-{str(fy_start + 1)[-2:]}"


def current_fy() -> int:
    today = datetime.date.today()
    return today.year if today.month >= 4 else today.year - 1


def list_fys():
    conn = _get_db()
    rows = conn.execute("SELECT DISTINCT date FROM tax_ledger ORDER BY date").fetchall()
    conn.close()
    fys = sorted({indian_fy(r["date"]) for r in rows})
    if not fys:
        print("\n  No data in tax_ledger table.")
        print("  Run 'python scripts/generate_tax_ledger.py fill' first.")
        return
    print("\n  Financial years in tax_ledger:")
    for fy in fys:
        conn2 = _get_db()
        fy_from = f"{fy}-04-01"
        fy_to   = f"{fy + 1}-03-31"
        count = conn2.execute(
            "SELECT COUNT(*) FROM tax_ledger WHERE date >= ? AND date <= ?",
            (fy_from, fy_to),
        ).fetchone()[0]
        conn2.close()
        print(f"    {fy_label(fy)}  —  {count} trade(s)")


def view_fy(fy_start: int):
    fy_from = f"{fy_start}-04-01"
    fy_to   = f"{fy_start + 1}-03-31"
    label   = fy_label(fy_start)

    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM tax_ledger WHERE date >= ? AND date <= ? ORDER BY date, id",
        (fy_from, fy_to),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"\n  No trades found for {label}.")
        print(f"  Run 'python scripts/generate_tax_ledger.py fill --fy {fy_start}' first.")
        return

    w = 120
    print(f"\n{'=' * w}")
    print(f"  {label} — TAX LEDGER  (live trades only)")
    print(f"  Period : {fy_from}  to  {fy_to}")
    print(f"{'=' * w}")

    # ── Table header ──────────────────────────────────────────────
    hdr = (
        f"  {'#':>3}  {'Date':<10}  {'Symbol':<12}  {'Side':<4}  {'Qty':>4}"
        f"  {'Entry':>10}  {'Exit':>10}  {'Gross P&L':>10}"
        f"  {'Charges':>8}  {'Net P&L':>10}  {'Exit Reason':<12}  {'Order ID':<22}"
    )
    print(f"\n{hdr}")
    print(f"  {'─' * (w - 4)}")

    total_gross   = 0.0
    total_charges = 0.0
    total_net     = 0.0
    prev_date     = None

    for i, r in enumerate(rows, 1):
        # Separator between trading days
        if prev_date and r["date"] != prev_date:
            print(f"  {'─' * (w - 4)}")
        prev_date = r["date"]

        gross   = r["gross_pnl"]
        charges = r["total_charges"]
        net     = r["net_pnl"]
        total_gross   += gross
        total_charges += charges
        total_net     += net

        line = (
            f"  {i:>3}  {r['date']:<10}  {r['symbol']:<12}  {r['side']:<4}  {r['qty']:>4}"
            f"  ₹{r['entry_price']:>9,.2f}  ₹{r['exit_price']:>9,.2f}  ₹{gross:>+9,.2f}"
            f"  ₹{charges:>7,.2f}  ₹{net:>+9,.2f}  {r['exit_reason'] or '':<12}  {r['order_id']:<22}"
        )
        print(line)

    print(f"  {'─' * (w - 4)}")
    print(
        f"  {'':>3}  {'TOTAL':<10}  {'':12}  {'':4}  {'':4}"
        f"  {'':10}  {'':10}  ₹{total_gross:>+9,.2f}"
        f"  ₹{total_charges:>7,.2f}  ₹{total_net:>+9,.2f}"
    )
    print(f"\n  {len(rows)} trade(s) across {len({r['date'] for r in rows})} trading day(s)")
    print(f"{'=' * w}\n")


def main():
    parser = argparse.ArgumentParser(
        description="View tax ledger trades from the database.",
    )
    parser.add_argument(
        "--fy", type=int, default=None,
        help="Financial year start (e.g. 2025 for FY 2025-26). Default: current FY.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List financial years with data.",
    )

    args = parser.parse_args()

    if args.list:
        list_fys()
        return

    fy = args.fy if args.fy else current_fy()
    view_fy(fy)


if __name__ == "__main__":
    main()
