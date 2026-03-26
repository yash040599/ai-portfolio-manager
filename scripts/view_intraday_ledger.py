"""
View intraday trades stored in the intraday_tax_ledger table.

Usage
─────
    python scripts/view_intraday_ledger.py              # current FY
    python scripts/view_intraday_ledger.py --fy 2025    # FY 2025-26
    python scripts/view_intraday_ledger.py --list       # list FYs with data
"""

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.tax_db import get_db, indian_fy, fy_label, fy_date_range, current_fy


def list_fys():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT date FROM intraday_tax_ledger ORDER BY date"
    ).fetchall()
    conn.close()
    fys = sorted({indian_fy(r["date"]) for r in rows})
    if not fys:
        print("\n  No data in intraday_tax_ledger.")
        print("  Run 'python scripts/fill_intraday_ledger.py' first.")
        return
    print("\n  FYs in intraday_tax_ledger:")
    for fy in fys:
        fy_from, fy_to = fy_date_range(fy)
        conn2 = get_db()
        count = conn2.execute(
            "SELECT COUNT(*) FROM intraday_tax_ledger WHERE date>=? AND date<=?",
            (fy_from, fy_to),
        ).fetchone()[0]
        conn2.close()
        print(f"    {fy_label(fy)}  —  {count} trade(s)")


def view_fy(fy_start: int):
    fy_from, fy_to = fy_date_range(fy_start)
    label = fy_label(fy_start)

    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM intraday_tax_ledger WHERE date>=? AND date<=? ORDER BY date, id",
        (fy_from, fy_to),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"\n  No trades for {label}.")
        return

    w = 130
    print(f"\n{'=' * w}")
    print(f"  {label} — INTRADAY TAX LEDGER  (live trades only)")
    print(f"{'=' * w}")

    hdr = (
        f"  {'#':>3}  {'Date':<10}  {'Symbol':<12}  {'Side':<4}  {'Qty':>4}"
        f"  {'Entry':>10}  {'Exit':>10}  {'Gross P&L':>10}"
        f"  {'Charges':>8}  {'Net P&L':>10}  {'Exit Reason':<12}  {'Verified':<10}"
    )
    print(f"\n{hdr}")
    print(f"  {'─' * (w - 4)}")

    total_gross = total_charges = total_net = 0.0
    prev_date = None

    for i, r in enumerate(rows, 1):
        if prev_date and r["date"] != prev_date:
            print(f"  {'─' * (w - 4)}")
        prev_date = r["date"]

        g, c, n = r["gross_pnl"], r["total_charges"], r["net_pnl"]
        total_gross += g; total_charges += c; total_net += n
        v = "✓" if r["verified"] == "verified" else "—"

        print(
            f"  {i:>3}  {r['date']:<10}  {r['symbol']:<12}  {r['side']:<4}  {r['qty']:>4}"
            f"  ₹{r['entry_price']:>9,.2f}  ₹{r['exit_price']:>9,.2f}  ₹{g:>+9,.2f}"
            f"  ₹{c:>7,.2f}  ₹{n:>+9,.2f}  {r['exit_reason'] or '':<12}  {v:<10}"
        )

    print(f"  {'─' * (w - 4)}")
    print(
        f"  {'':>3}  {'TOTAL':<10}  {'':12}  {'':4}  {'':4}"
        f"  {'':10}  {'':10}  ₹{total_gross:>+9,.2f}"
        f"  ₹{total_charges:>7,.2f}  ₹{total_net:>+9,.2f}"
    )
    print(f"\n  {len(rows)} trade(s)  |  {len({r['date'] for r in rows})} day(s)")
    verified_count = sum(1 for r in rows if r["verified"] == "verified")
    if verified_count < len(rows):
        print(f"  ⚠ {len(rows) - verified_count} trade(s) still unverified — "
              f"run import_zerodha_taxpnl.py to verify")
    print(f"{'=' * w}\n")


def main():
    parser = argparse.ArgumentParser(description="View intraday tax ledger.")
    parser.add_argument("--fy", type=int, default=None)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        list_fys(); return
    view_fy(args.fy or current_fy())


if __name__ == "__main__":
    main()
