"""
View capital gains trades stored in the capital_gains_ledger table.

Usage
─────
    python scripts/view_capital_gains_ledger.py                     # current FY, all types
    python scripts/view_capital_gains_ledger.py --fy 2025           # FY 2025-26
    python scripts/view_capital_gains_ledger.py --type short_term   # short-term only
    python scripts/view_capital_gains_ledger.py --type long_term    # long-term only
    python scripts/view_capital_gains_ledger.py --list              # list FYs
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
        "SELECT DISTINCT exit_date FROM capital_gains_ledger ORDER BY exit_date"
    ).fetchall()
    conn.close()
    fys = sorted({indian_fy(r["exit_date"]) for r in rows})
    if not fys:
        print("\n  No data in capital_gains_ledger.")
        print("  Run 'python scripts/import_zerodha_taxpnl.py' first.")
        return
    print("\n  FYs in capital_gains_ledger:")
    for fy in fys:
        fy_from, fy_to = fy_date_range(fy)
        conn2 = get_db()
        st = conn2.execute(
            "SELECT COUNT(*) FROM capital_gains_ledger "
            "WHERE exit_date>=? AND exit_date<=? AND trade_type='short_term'",
            (fy_from, fy_to),
        ).fetchone()[0]
        lt = conn2.execute(
            "SELECT COUNT(*) FROM capital_gains_ledger "
            "WHERE exit_date>=? AND exit_date<=? AND trade_type='long_term'",
            (fy_from, fy_to),
        ).fetchone()[0]
        conn2.close()
        print(f"    {fy_label(fy)}  —  {st} short-term, {lt} long-term")


def view_fy(fy_start: int, trade_type: str | None = None):
    fy_from, fy_to = fy_date_range(fy_start)
    label = fy_label(fy_start)

    conn = get_db()
    if trade_type:
        rows = conn.execute(
            "SELECT * FROM capital_gains_ledger "
            "WHERE exit_date>=? AND exit_date<=? AND trade_type=? "
            "ORDER BY exit_date, id",
            (fy_from, fy_to, trade_type),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM capital_gains_ledger "
            "WHERE exit_date>=? AND exit_date<=? ORDER BY trade_type, exit_date, id",
            (fy_from, fy_to),
        ).fetchall()
    conn.close()

    if not rows:
        print(f"\n  No capital-gains trades for {label}.")
        return

    type_label = trade_type.replace("_", " ").upper() if trade_type else "ALL"
    w = 130
    print(f"\n{'=' * w}")
    print(f"  {label} — CAPITAL GAINS LEDGER  ({type_label})")
    print(f"{'=' * w}")

    hdr = (
        f"  {'#':>3}  {'Type':<6}  {'Symbol':<12}  {'Entry':<10}  {'Exit':<10}"
        f"  {'Qty':>6}  {'Buy Val':>12}  {'Sell Val':>12}  {'Profit':>12}"
        f"  {'Holding':>7}  {'Charges':>8}  {'Taxable':>12}"
    )
    print(f"\n{hdr}")
    print(f"  {'─' * (w - 4)}")

    total_profit = total_charges = total_taxable = 0.0
    prev_type = None

    for i, r in enumerate(rows, 1):
        if prev_type and r["trade_type"] != prev_type:
            print(f"  {'─' * (w - 4)}")
        prev_type = r["trade_type"]

        tt = "ST" if r["trade_type"] == "short_term" else "LT"
        p, c, tp = r["profit"], r["total_charges"], r["taxable_profit"]
        total_profit += p; total_charges += c; total_taxable += tp

        print(
            f"  {i:>3}  {tt:<6}  {r['symbol']:<12}  {r['entry_date']:<10}  {r['exit_date']:<10}"
            f"  {r['qty']:>6.0f}  ₹{r['buy_value']:>11,.2f}  ₹{r['sell_value']:>11,.2f}"
            f"  ₹{p:>+11,.2f}  {r['period_of_holding']:>5}d  ₹{c:>7,.2f}  ₹{tp:>+11,.2f}"
        )

    print(f"  {'─' * (w - 4)}")
    st_count = sum(1 for r in rows if r["trade_type"] == "short_term")
    lt_count = sum(1 for r in rows if r["trade_type"] == "long_term")
    print(f"\n  {len(rows)} trade(s)  |  {st_count} short-term, {lt_count} long-term")
    print(f"  Total profit : ₹{total_profit:+,.2f}  |  Total charges: ₹{total_charges:,.2f}")
    print(f"{'=' * w}\n")


def main():
    parser = argparse.ArgumentParser(description="View capital gains ledger.")
    parser.add_argument("--fy", type=int, default=None)
    parser.add_argument("--type", choices=["short_term", "long_term"], default=None)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        list_fys(); return
    view_fy(args.fy or current_fy(), args.type)


if __name__ == "__main__":
    main()
