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


def per_trade_charges(position: dict, day_charges: dict | None = None) -> dict:
    """Calculate charges for a single trade using its own buy/sell values.

    Uses Config.calculate_charges() per-trade (num_orders=2 for the
    entry + exit pair) rather than apportioning the day-level total.
    This gives the correct per-component breakdown and remains accurate
    even when EXTERNAL positions inflate or deflate the day-level total.

    If day_charges (the day-level charge totals from the JSON report) is
    provided, it is not used for computation but is available for
    cross-checking at the caller level.
    """
    remaining_qty = position.get("qty", 0)
    partial_qty   = position.get("_partial_qty", 0)
    total_qty     = remaining_qty + partial_qty
    entry  = position.get("entry_price", 0)
    exit_p = position.get("exit_price", 0)
    partial_exit = position.get("_partial_exit_price", entry)

    if position.get("side") == "BUY":
        buy_val  = entry * total_qty
        sell_val = exit_p * remaining_qty + partial_exit * partial_qty
    else:
        sell_val = entry * total_qty
        buy_val  = exit_p * remaining_qty + partial_exit * partial_qty

    c = Config.calculate_charges(
        total_buy_turnover=buy_val,
        total_sell_turnover=sell_val,
        num_orders=2,
    )
    return {
        "buy_value":     round(buy_val, 2),
        "sell_value":    round(sell_val, 2),
        "turnover":      round(buy_val + sell_val, 2),
        "brokerage":     c["brokerage"],
        "stt":           c["stt"],
        "exchange_txn":  c["exchange_txn"],
        "gst":           c["gst"],
        "sebi_charges":  c["sebi_charges"],
        "stamp_duty":    c["stamp_duty"],
        "total_charges": c["total_tax_and_charges"],
    }


# ── Cross-check ───────────────────────────────────────────────────

CHARGE_TOLERANCE_RS = 5.0  # Rs – allow small rounding/cap differences


def _cross_check_day_charges(date_str: str, positions: list[dict],
                             day_charges: dict) -> None:
    """Compare sum of per-trade charges against day-level total.

    Day-level charges are computed from aggregated turnover, while
    per-trade charges are computed independently per position.  They
    won't match exactly (brokerage cap, rounding) but a large gap
    flags data issues or missing positions.
    """
    if not day_charges:
        return
    day_total = day_charges.get("total_tax_and_charges", 0)
    if not day_total:
        return

    per_trade_sum = 0.0
    for pos in positions:
        if pos.get("status") != "CLOSED":
            continue
        oid = pos.get("order_id", "")
        if not oid or oid.startswith("DRY_RUN"):
            continue
        tc = per_trade_charges(pos)
        per_trade_sum += tc["total_charges"]

    diff = abs(per_trade_sum - day_total)
    if diff > CHARGE_TOLERANCE_RS:
        print(f"  ⚠ {date_str}: charge cross-check mismatch — "
              f"per-trade sum Rs {per_trade_sum:.2f} vs "
              f"day-level Rs {day_total:.2f} (diff Rs {diff:.2f})")


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
        _cross_check_day_charges(date_str, positions, day_charges)
        external_counter: dict[str, int] = {}

        # Skip this date entirely if Zerodha sheet-verified rows (ZV_)
        # already exist. The sheet is the authoritative source — re-inserting
        # bot rows alongside ZV_ rows creates duplicates with wrong aggregates.
        has_sheet_rows = conn.execute(
            "SELECT 1 FROM intraday_tax_ledger "
            "WHERE date=? AND order_id LIKE 'ZV_%' LIMIT 1",
            (date_str,),
        ).fetchone()
        if has_sheet_rows:
            continue

        for pos in positions:
            if pos.get("status") != "CLOSED":
                continue
            order_id = pos.get("order_id", "")
            if not order_id or order_id.startswith("DRY_RUN"):
                continue

            # EXTERNAL positions all share order_id="EXTERNAL".  Generating a
            # deterministic unique ID per (date, symbol, side, entry_price)
            # makes the dedup check reliable across multiple runs.
            if order_id == "EXTERNAL":
                key = f"{date_str}_{pos.get('symbol')}_{pos.get('side')}"
                external_counter[key] = external_counter.get(key, 0) + 1
                order_id = (
                    f"EXT_{date_str}_{pos.get('symbol')}"
                    f"_{pos.get('side')}_{external_counter[key]}"
                )

            if conn.execute(
                "SELECT 1 FROM intraday_tax_ledger WHERE date=? AND order_id=?",
                (date_str, order_id),
            ).fetchone():
                continue

            tc = per_trade_charges(pos)
            gross_pnl = round(pos.get("pnl", 0) + pos.get("_partial_pnl", 0), 2)
            net_pnl   = round(gross_pnl - tc["total_charges"], 2)
            total_qty = pos.get("qty", 0) + pos.get("_partial_qty", 0)

            conn.execute(
                """INSERT INTO intraday_tax_ledger
                   (date, symbol, exchange, side, qty,
                    entry_price, exit_price, entry_time, exit_time,
                    exit_reason, gross_pnl,
                    buy_value, sell_value, turnover,
                    brokerage, stt, exchange_txn, gst,
                    sebi_charges, stamp_duty, total_charges,
                    net_pnl, order_id, verified,
                    sheet_verified, sheet_verified_on)
                   VALUES (?,?,?,?,?, ?,?,?,?, ?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?)""",
                (
                    date_str, pos.get("symbol", ""), pos.get("exchange", "NSE"),
                    pos.get("side", ""), total_qty,
                    pos.get("entry_price", 0), pos.get("exit_price", 0),
                    pos.get("entry_time", ""), pos.get("exit_time", ""),
                    pos.get("exit_reason", ""), gross_pnl,
                    tc["buy_value"], tc["sell_value"], tc["turnover"],
                    tc["brokerage"], tc["stt"], tc["exchange_txn"], tc["gst"],
                    tc["sebi_charges"], tc["stamp_duty"], tc["total_charges"],
                    net_pnl, order_id, "unverified",
                    "pending", None,
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
