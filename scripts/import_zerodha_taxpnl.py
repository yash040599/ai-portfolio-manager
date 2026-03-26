"""
Import and verify trade data from a Zerodha Tax P&L xlsx report.

• Intraday section  → verifies / corrects existing rows in intraday_tax_ledger,
                      inserts missing trades, marks everything as 'verified'.
• Short-term / Long-term sections → inserts into capital_gains_ledger.

Usage
─────
    python scripts/import_zerodha_taxpnl.py                              # latest xlsx in data/ZerodhaTaxPL/
    python scripts/import_zerodha_taxpnl.py data/ZerodhaTaxPL/file.xlsx  # specific file
"""

import argparse
import datetime
import glob
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from scripts.tax_db import get_db, indian_fy, fy_label

ZERODHA_DIR = os.path.join(PROJECT_ROOT, "data", "ZerodhaTaxPL")


# ── xlsx parsing ──────────────────────────────────────────────────

def _to_date_str(val) -> str:
    if isinstance(val, datetime.datetime):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, datetime.date):
        return val.isoformat()
    return str(val).strip() if val else ""


def _f(val, default=0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def parse_xlsx(path: str):
    """
    Parse the 'Tradewise Exits' sheet.
    Returns (intraday_trades, short_term_trades, long_term_trades).
    Each trade is a dict with Zerodha fields.
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)

    # First sheet is always Tradewise Exits
    ws = wb.worksheets[0]

    intraday, short_term, long_term = [], [], []
    section = None

    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, values_only=True):
        vals = list(row)
        cell1 = vals[1] if len(vals) > 1 else None

        # Section headers
        if cell1 == "Equity - Intraday":
            section = "intraday"; continue
        if cell1 == "Equity - Short Term":
            section = "short_term"; continue
        if cell1 == "Equity - Long Term":
            section = "long_term"; continue
        if cell1 in ("Equity - Buyback", "Non Equity", "Mutual Funds",
                      "F&O", "Currency", "Commodity"):
            section = None; continue
        if cell1 == "Symbol":      # header row — skip
            continue

        if section is None or cell1 is None or not str(cell1).strip():
            continue

        t = {
            "symbol":       str(cell1).strip(),
            "isin":         str(vals[2]).strip() if vals[2] else "",
            "entry_date":   _to_date_str(vals[3]),
            "exit_date":    _to_date_str(vals[4]),
            "qty":          _f(vals[5]),
            "buy_value":    _f(vals[6]),
            "sell_value":   _f(vals[7]),
            "profit":       _f(vals[8]),
            "holding_days": int(_f(vals[9])),
            "fmv":          _f(vals[10]),
            "taxable_profit": _f(vals[11]),
            "turnover":     _f(vals[12]),
            "brokerage":    _f(vals[13]),
            "exchange_txn": _f(vals[14]) + _f(vals[15]),   # exch + IPFT
            "sebi_charges": _f(vals[16]),
            "gst":          _f(vals[17]) + _f(vals[18]) + _f(vals[19]),  # CGST+SGST+IGST
            "stamp_duty":   _f(vals[20]),
            "stt":          _f(vals[21]),
        }
        t["total_charges"] = round(
            t["brokerage"] + t["exchange_txn"] + t["sebi_charges"]
            + t["gst"] + t["stamp_duty"] + t["stt"], 4
        )

        if section == "intraday":
            intraday.append(t)
        elif section == "short_term":
            short_term.append(t)
        elif section == "long_term":
            long_term.append(t)

    wb.close()
    return intraday, short_term, long_term


# ── Intraday verification ────────────────────────────────────────

def _verify_intraday(conn, zerodha_trades: list[dict]) -> dict:
    """
    Verify / correct intraday_tax_ledger against Zerodha data.
    Groups by (date, symbol) and compares aggregate P&L.

    Returns {"verified": n, "corrected": n, "inserted": n}.
    """
    # Group Zerodha by (exit_date, symbol)
    z_groups: dict[tuple, list] = {}
    for t in zerodha_trades:
        key = (t["exit_date"], t["symbol"])
        z_groups.setdefault(key, []).append(t)

    # Group DB by (date, symbol)
    db_rows = conn.execute(
        "SELECT * FROM intraday_tax_ledger"
    ).fetchall()
    db_groups: dict[tuple, list] = {}
    for r in db_rows:
        key = (r["date"], r["symbol"])
        db_groups.setdefault(key, []).append(r)

    stats = {"verified": 0, "corrected": 0, "inserted": 0}

    for key, z_trades in z_groups.items():
        date, symbol = key
        z_total_pnl = sum(t["profit"] for t in z_trades)
        z_total_qty = sum(t["qty"] for t in z_trades)

        if key in db_groups:
            db_rows_g = db_groups[key]
            db_total_pnl = sum(r["gross_pnl"] for r in db_rows_g)
            db_total_qty = sum(r["qty"] for r in db_rows_g)

            if (abs(db_total_pnl - z_total_pnl) < 0.10
                    and abs(db_total_qty - z_total_qty) < 0.01):
                # Match — mark existing rows verified
                for r in db_rows_g:
                    conn.execute(
                        "UPDATE intraday_tax_ledger SET verified='verified' WHERE id=?",
                        (r["id"],),
                    )
                stats["verified"] += len(db_rows_g)
            else:
                # Mismatch — replace with Zerodha data
                for r in db_rows_g:
                    conn.execute(
                        "DELETE FROM intraday_tax_ledger WHERE id=?",
                        (r["id"],),
                    )
                for i, t in enumerate(z_trades):
                    _insert_zerodha_intraday(conn, t, i)
                stats["corrected"] += len(z_trades)
                print(f"    ✎ Corrected {symbol} on {date}: "
                      f"P&L {db_total_pnl:+.2f} → {z_total_pnl:+.2f}")
        else:
            # Only in Zerodha — insert
            for i, t in enumerate(z_trades):
                _insert_zerodha_intraday(conn, t, i)
            stats["inserted"] += len(z_trades)

    conn.commit()
    return stats


def _insert_zerodha_intraday(conn, t: dict, idx: int):
    """Insert a single Zerodha intraday trade into the DB."""
    date = t["exit_date"]
    qty = int(t["qty"])
    entry_price = round(t["buy_value"] / qty, 2) if qty else 0
    exit_price  = round(t["sell_value"] / qty, 2) if qty else 0
    order_id = f"ZV_{date}_{t['symbol']}_{idx}"

    conn.execute(
        """INSERT OR REPLACE INTO intraday_tax_ledger
           (date, symbol, exchange, side, qty,
            entry_price, exit_price, entry_time, exit_time,
            exit_reason, gross_pnl,
            buy_value, sell_value, turnover,
            brokerage, stt, exchange_txn, gst,
            sebi_charges, stamp_duty, total_charges,
            net_pnl, order_id, verified)
           VALUES (?,?,?,?,?, ?,?,?,?, ?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?,?)""",
        (
            date, t["symbol"], "NSE", "BUY", qty,
            entry_price, exit_price, "", "",
            "", round(t["profit"], 2),
            round(t["buy_value"], 2), round(t["sell_value"], 2),
            round(t["turnover"], 2),
            round(t["brokerage"], 4), round(t["stt"], 4),
            round(t["exchange_txn"], 4), round(t["gst"], 4),
            round(t["sebi_charges"], 4), round(t["stamp_duty"], 4),
            round(t["total_charges"], 4),
            round(t["profit"] - t["total_charges"], 2),
            order_id, "verified",
        ),
    )


# ── Capital gains import ─────────────────────────────────────────

def _import_capital_gains(conn, trades: list[dict], trade_type: str) -> int:
    """Insert capital-gains trades. Skips duplicates. Returns insert count."""
    inserted = 0
    for t in trades:
        qty = t["qty"]
        try:
            conn.execute(
                """INSERT INTO capital_gains_ledger
                   (trade_type, symbol, isin, entry_date, exit_date,
                    qty, buy_value, sell_value, profit,
                    period_of_holding, fair_market_value, taxable_profit,
                    turnover, brokerage, exchange_txn, sebi_charges,
                    gst, stamp_duty, stt, total_charges, verified)
                   VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?,?, ?,?,?,?,?)""",
                (
                    trade_type, t["symbol"], t["isin"],
                    t["entry_date"], t["exit_date"],
                    qty, round(t["buy_value"], 2), round(t["sell_value"], 2),
                    round(t["profit"], 2),
                    t["holding_days"], round(t["fmv"], 2),
                    round(t["taxable_profit"], 2),
                    round(t["turnover"], 2),
                    round(t["brokerage"], 4), round(t["exchange_txn"], 4),
                    round(t["sebi_charges"], 4), round(t["gst"], 4),
                    round(t["stamp_duty"], 4), round(t["stt"], 4),
                    round(t["total_charges"], 4), "verified",
                ),
            )
            inserted += 1
        except Exception:
            pass  # duplicate — silently skip
    conn.commit()
    return inserted


# ── CLI ───────────────────────────────────────────────────────────

def _find_latest_xlsx() -> str | None:
    pattern = os.path.join(ZERODHA_DIR, "taxpnl-*.xlsx")
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def main():
    parser = argparse.ArgumentParser(
        description="Import Zerodha Tax P&L xlsx — verify intraday & import capital gains.",
    )
    parser.add_argument(
        "xlsx", nargs="?", default=None,
        help="Path to Zerodha xlsx. Default: latest file in data/ZerodhaTaxPL/.",
    )
    args = parser.parse_args()

    xlsx_path = args.xlsx or _find_latest_xlsx()
    if not xlsx_path or not os.path.exists(xlsx_path):
        print(f"\n  No Zerodha xlsx found. Place it in data/ZerodhaTaxPL/")
        return

    print(f"\n  Reading: {os.path.basename(xlsx_path)}")
    intraday, short_term, long_term = parse_xlsx(xlsx_path)
    print(f"  Parsed: {len(intraday)} intraday, "
          f"{len(short_term)} short-term, {len(long_term)} long-term")

    conn = get_db()

    # ── Intraday verification ─────────────────────────────────
    if intraday:
        print(f"\n  Verifying intraday trades …")
        stats = _verify_intraday(conn, intraday)
        print(f"  ✓ Verified: {stats['verified']}  |  "
              f"Corrected: {stats['corrected']}  |  "
              f"Inserted: {stats['inserted']}")

    # ── Capital gains import ──────────────────────────────────
    if short_term:
        n = _import_capital_gains(conn, short_term, "short_term")
        print(f"  ✓ Short-term: {n} trade(s) imported ({len(short_term) - n} already existed)")
    if long_term:
        n = _import_capital_gains(conn, long_term, "long_term")
        print(f"  ✓ Long-term:  {n} trade(s) imported ({len(long_term) - n} already existed)")

    conn.close()
    print(f"\n  Done.\n")


if __name__ == "__main__":
    main()
