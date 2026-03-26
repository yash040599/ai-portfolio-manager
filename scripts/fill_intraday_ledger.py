"""
Fill the intraday tax ledger from live trading JSON reports.

Only LIVE trades (actual Zerodha executions) are recorded.
Dry-run/simulated trades are excluded — they have no tax implications.
Rows are inserted with verified='unverified' until confirmed via Zerodha sheet.

Usage
─────
    python scripts/fill_intraday_ledger.py              # current FY
    python scripts/fill_intraday_ledger.py --fy 2025    # FY 2025-26
    python scripts/fill_intraday_ledger.py --all        # all FYs
    python scripts/fill_intraday_ledger.py --list       # list FYs with live data
"""

import argparse
import glob
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import Config
from scripts.tax_db import get_db, indian_fy, fy_label, fy_date_range, current_fy

REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports", "trading")


# ── Helpers ───────────────────────────────────────────────────────

def find_all_trading_jsons() -> list[str]:
    pattern = os.path.join(REPORTS_DIR, "**", "trading_data_*.json")
    return sorted(glob.glob(pattern, recursive=True))


def load_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  ⚠ Skipped {path}: {e}")
        return None


def per_trade_charges(position: dict, day_charges: dict) -> dict:
    """Apportion day-level charges to a single trade by turnover share."""
    qty    = position.get("qty", 0)
    entry  = position.get("entry_price", 0)
    exit_p = position.get("exit_price", 0)

    if position.get("side") == "BUY":
        buy_val, sell_val = entry * qty, exit_p * qty
    else:
        sell_val, buy_val = entry * qty, exit_p * qty

    trade_turnover = buy_val + sell_val
    total_turnover = day_charges.get("total_turnover", 0)
    share = trade_turnover / total_turnover if total_turnover > 0 else 0

    return {
        "buy_value":     round(buy_val, 2),
        "sell_value":    round(sell_val, 2),
        "turnover":      round(trade_turnover, 2),
        "brokerage":     round(day_charges.get("brokerage", 0) * share, 2),
        "stt":           round(day_charges.get("stt", 0) * share, 2),
        "exchange_txn":  round(day_charges.get("exchange_txn", 0) * share, 2),
        "gst":           round(day_charges.get("gst", 0) * share, 2),
        "sebi_charges":  round(day_charges.get("sebi_charges", 0) * share, 4),
        "stamp_duty":    round(day_charges.get("stamp_duty", 0) * share, 2),
        "total_charges": round(day_charges.get("total_tax_and_charges", 0) * share, 2),
    }


# ── Fill ──────────────────────────────────────────────────────────

def fill_fy(fy_start: int) -> int:
    """Insert new live trades into intraday_tax_ledger. Returns insert count."""
    all_jsons = find_all_trading_jsons()
    if not all_jsons:
        print(f"  No trading data found under {REPORTS_DIR}")
        return 0

    conn = get_db()
    inserted = 0

    for jpath in all_jsons:
        data = load_json(jpath)
        if not data:
            continue
        date_str = data.get("date", "")
        if not date_str or indian_fy(date_str) != fy_start:
            continue
        if data.get("mode") != "live":
            continue

        positions   = data.get("positions", [])
        day_charges = data.get("pnl", {}).get("charges", {})

        for pos in positions:
            if pos.get("status") != "CLOSED":
                continue
            order_id = pos.get("order_id", "")
            if not order_id or order_id.startswith("DRY_RUN"):
                continue

            if conn.execute(
                "SELECT 1 FROM intraday_tax_ledger WHERE date=? AND order_id=?",
                (date_str, order_id),
            ).fetchone():
                continue

            tc = per_trade_charges(pos, day_charges)
            gross_pnl = round(pos.get("pnl", 0), 2)
            net_pnl   = round(gross_pnl - tc["total_charges"], 2)

            conn.execute(
                """INSERT INTO intraday_tax_ledger
                   (date, symbol, exchange, side, qty,
                    entry_price, exit_price, entry_time, exit_time,
                    exit_reason, gross_pnl,
                    buy_value, sell_value, turnover,
                    brokerage, stt, exchange_txn, gst,
                    sebi_charges, stamp_duty, total_charges,
                    net_pnl, order_id, verified)
                   VALUES (?,?,?,?,?, ?,?,?,?, ?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?,?)""",
                (
                    date_str, pos.get("symbol", ""), pos.get("exchange", "NSE"),
                    pos.get("side", ""), pos.get("qty", 0),
                    pos.get("entry_price", 0), pos.get("exit_price", 0),
                    pos.get("entry_time", ""), pos.get("exit_time", ""),
                    pos.get("exit_reason", ""), gross_pnl,
                    tc["buy_value"], tc["sell_value"], tc["turnover"],
                    tc["brokerage"], tc["stt"], tc["exchange_txn"], tc["gst"],
                    tc["sebi_charges"], tc["stamp_duty"], tc["total_charges"],
                    net_pnl, order_id, "unverified",
                ),
            )
            inserted += 1

    conn.commit()
    conn.close()
    return inserted


def get_json_fys() -> list[int]:
    """FY start years that have live-mode trading JSONs."""
    fys = set()
    for jpath in find_all_trading_jsons():
        data = load_json(jpath)
        if data and data.get("date") and data.get("mode") == "live":
            fys.add(indian_fy(data["date"]))
    return sorted(fys)


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fill intraday tax ledger from live trading JSONs.",
    )
    parser.add_argument(
        "--fy", type=int, default=None,
        help="FY start year (e.g. 2025 for FY 2025-26). Default: current FY.",
    )
    parser.add_argument("--all", action="store_true", help="Fill all FYs.")
    parser.add_argument("--list", action="store_true", help="List FYs with live data.")
    args = parser.parse_args()

    if args.list:
        fys = get_json_fys()
        if not fys:
            print("\n  No live trading data found.")
            return
        print("\n  FYs with live trading JSONs:")
        for fy in fys:
            print(f"    {fy_label(fy)}")
        return

    targets = get_json_fys() if args.all else [args.fy or current_fy()]
    for fy in targets:
        print(f"\n  Filling {fy_label(fy)} …")
        n = fill_fy(fy)
        if n:
            print(f"  ✓ Inserted {n} trade(s).")
        else:
            print(f"  · Already up-to-date (or no live data).")


if __name__ == "__main__":
    main()
