"""
Tax ledger for intraday trading — stores per-trade data in SQLite.

Only LIVE trades (actual Zerodha executions) are recorded.
Dry-run/simulated trades are excluded since they have no tax implications.

Modes
─────
  fill    — Populate DB from live trading JSONs (skips already-inserted rows),
            then print the FY summary.
  summary — Print FY summary from DB only (no data changes).

Usage
─────
    python scripts/generate_tax_ledger.py fill                # fill current FY + summary
    python scripts/generate_tax_ledger.py fill --fy 2025      # fill FY 2025-26 + summary
    python scripts/generate_tax_ledger.py fill --all          # fill all FYs + summaries
    python scripts/generate_tax_ledger.py summary             # summary for current FY
    python scripts/generate_tax_ledger.py summary --fy 2025   # summary for FY 2025-26
    python scripts/generate_tax_ledger.py summary --list      # list FYs with data in DB
"""

import argparse
import datetime
import glob
import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import Config

REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports", "trading")
DB_PATH     = os.path.join(PROJECT_ROOT, "data", "trades.db")


# ── Database ──────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tax_ledger (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            symbol          TEXT    NOT NULL,
            exchange        TEXT    NOT NULL DEFAULT 'NSE',
            side            TEXT    NOT NULL,
            qty             INTEGER NOT NULL,
            entry_price     REAL    NOT NULL,
            exit_price      REAL    NOT NULL,
            entry_time      TEXT,
            exit_time       TEXT,
            exit_reason     TEXT,
            gross_pnl       REAL    NOT NULL,
            buy_value       REAL    NOT NULL,
            sell_value      REAL    NOT NULL,
            turnover        REAL    NOT NULL,
            brokerage       REAL    NOT NULL DEFAULT 0,
            stt             REAL    NOT NULL DEFAULT 0,
            exchange_txn    REAL    NOT NULL DEFAULT 0,
            gst             REAL    NOT NULL DEFAULT 0,
            sebi_charges    REAL    NOT NULL DEFAULT 0,
            stamp_duty      REAL    NOT NULL DEFAULT 0,
            total_charges   REAL    NOT NULL DEFAULT 0,
            net_pnl         REAL    NOT NULL,
            order_id        TEXT    NOT NULL,
            UNIQUE(date, order_id)
        )
    """)
    conn.commit()
    return conn


# ── Helpers ───────────────────────────────────────────────────────

def indian_fy(date_str: str) -> int:
    """FY start year.  2026-03-25 → 2025 (FY 2025-26)."""
    d = datetime.date.fromisoformat(date_str)
    return d.year if d.month >= 4 else d.year - 1


def fy_label(fy_start: int) -> str:
    return f"FY {fy_start}-{str(fy_start + 1)[-2:]}"


def fy_date_range(fy_start: int) -> tuple[str, str]:
    return f"{fy_start}-04-01", f"{fy_start + 1}-03-31"


def current_fy() -> int:
    today = datetime.date.today()
    return today.year if today.month >= 4 else today.year - 1


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


# ── Fill Mode ─────────────────────────────────────────────────────

def fill_fy(fy_start: int) -> int:
    """
    Read live-mode trading JSONs, insert new trades into tax_ledger.
    Returns count of newly inserted rows.
    """
    all_jsons = find_all_trading_jsons()
    if not all_jsons:
        print(f"  No trading data found under {REPORTS_DIR}")
        return 0

    conn = _get_db()
    inserted = 0

    for jpath in all_jsons:
        data = load_json(jpath)
        if not data:
            continue

        date_str = data.get("date", "")
        if not date_str or indian_fy(date_str) != fy_start:
            continue

        # ── Only live trades ──────────────────────────────────────
        if data.get("mode") != "live":
            continue

        positions   = data.get("positions", [])
        day_charges = data.get("pnl", {}).get("charges", {})

        for pos in positions:
            if pos.get("status") != "CLOSED":
                continue

            order_id = pos.get("order_id", "")
            # Extra safety: skip anything that looks like a dry-run ID
            if not order_id or order_id.startswith("DRY_RUN"):
                continue

            # Skip if already in DB
            existing = conn.execute(
                "SELECT 1 FROM tax_ledger WHERE date = ? AND order_id = ?",
                (date_str, order_id),
            ).fetchone()
            if existing:
                continue

            tc = per_trade_charges(pos, day_charges)
            gross_pnl = round(pos.get("pnl", 0), 2)
            net_pnl   = round(gross_pnl - tc["total_charges"], 2)

            conn.execute(
                """INSERT INTO tax_ledger
                   (date, symbol, exchange, side, qty,
                    entry_price, exit_price, entry_time, exit_time,
                    exit_reason, gross_pnl,
                    buy_value, sell_value, turnover,
                    brokerage, stt, exchange_txn, gst,
                    sebi_charges, stamp_duty, total_charges,
                    net_pnl, order_id)
                   VALUES (?,?,?,?,?, ?,?,?,?, ?,?, ?,?,?, ?,?,?,?, ?,?,?, ?,?)""",
                (
                    date_str,
                    pos.get("symbol", ""),
                    pos.get("exchange", "NSE"),
                    pos.get("side", ""),
                    pos.get("qty", 0),
                    pos.get("entry_price", 0),
                    pos.get("exit_price", 0),
                    pos.get("entry_time", ""),
                    pos.get("exit_time", ""),
                    pos.get("exit_reason", ""),
                    gross_pnl,
                    tc["buy_value"], tc["sell_value"], tc["turnover"],
                    tc["brokerage"], tc["stt"], tc["exchange_txn"], tc["gst"],
                    tc["sebi_charges"], tc["stamp_duty"], tc["total_charges"],
                    net_pnl,
                    order_id,
                ),
            )
            inserted += 1

    conn.commit()
    conn.close()
    return inserted


# ── Summary ───────────────────────────────────────────────────────

def print_summary(fy_start: int) -> bool:
    """Print a full FY summary from the DB.  Returns False if no data."""
    fy_from, fy_to = fy_date_range(fy_start)
    label = fy_label(fy_start)

    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM tax_ledger WHERE date >= ? AND date <= ? ORDER BY date, id",
        (fy_from, fy_to),
    ).fetchall()

    if not rows:
        print(f"\n  No trades in DB for {label} ({fy_from} to {fy_to})")
        conn.close()
        return False

    # ── Aggregate ─────────────────────────────────────────────────
    total_gross     = sum(r["gross_pnl"]      for r in rows)
    total_charges   = sum(r["total_charges"]  for r in rows)
    total_brokerage = sum(r["brokerage"]      for r in rows)
    total_stt       = sum(r["stt"]            for r in rows)
    total_exch      = sum(r["exchange_txn"]   for r in rows)
    total_gst       = sum(r["gst"]            for r in rows)
    total_sebi      = sum(r["sebi_charges"]   for r in rows)
    total_stamp     = sum(r["stamp_duty"]     for r in rows)
    total_net       = round(total_gross - total_charges, 2)

    # Claude API costs from JSON (day-level, not per-trade)
    total_claude = _claude_costs_for_fy(fy_start)
    net_after_claude = round(total_net - total_claude, 2)

    effective_rate = Config.TAX_RATE_PCT * (1 + Config.TAX_CESS_PCT / 100)
    estimated_tax  = round(net_after_claude * effective_rate / 100, 2) if net_after_claude > 0 else 0.0
    profit_after_tax = round(net_after_claude - estimated_tax, 2)

    speculative_turnover = sum(abs(r["gross_pnl"]) for r in rows)

    # Zerodha subscription
    months_traded = len({r["date"][:7] for r in rows})
    zerodha_sub   = Config.ZERODHA_MONTHLY_COST * months_traded

    # Day-wise aggregation
    day_map: dict[str, dict] = {}
    for r in rows:
        d = r["date"]
        if d not in day_map:
            day_map[d] = {"trades": 0, "gross": 0.0, "charges": 0.0, "net": 0.0}
        day_map[d]["trades"]  += 1
        day_map[d]["gross"]   += r["gross_pnl"]
        day_map[d]["charges"] += r["total_charges"]
        day_map[d]["net"]     += r["net_pnl"]

    conn.close()

    # ── Print ─────────────────────────────────────────────────────
    w = 60
    print(f"\n{'=' * w}")
    print(f"  {label} — INTRADAY TAX SUMMARY  (live trades only)")
    print(f"  Period : {fy_from}  to  {fy_to}")
    print(f"  Income : Speculative Business Income (Section 43(5))")
    print(f"  ITR    : ITR-3 → Schedule BP → Speculative Income")
    print(f"{'=' * w}")

    print(f"\n  Trading days              : {len(day_map)}")
    print(f"  Total trades              : {len(rows)}")

    print(f"\n  {'─' * (w - 4)}")
    print(f"  PROFIT & LOSS")
    print(f"  {'─' * (w - 4)}")
    print(f"  Gross P&L (all trades)    : ₹{total_gross:>+12,.2f}")
    print(f"  Regulatory charges        : ₹{total_charges:>12,.2f}")
    print(f"  Claude API costs          : ₹{total_claude:>12,.2f}")
    print(f"  Net Profit (before tax)   : ₹{net_after_claude:>+12,.2f}")

    print(f"\n  {'─' * (w - 4)}")
    print(f"  SPECULATIVE TURNOVER")
    print(f"  {'─' * (w - 4)}")
    print(f"  Turnover (for ITR)        : ₹{speculative_turnover:>12,.2f}")
    print(f"    (absolute sum of per-trade P&L)")

    print(f"\n  {'─' * (w - 4)}")
    print(f"  ESTIMATED INCOME TAX")
    print(f"  {'─' * (w - 4)}")
    print(f"  Tax slab rate             : {Config.TAX_RATE_PCT}%")
    print(f"  Health & education cess   : {Config.TAX_CESS_PCT}%")
    print(f"  Effective rate            : {effective_rate:.2f}%")
    if net_after_claude > 0:
        print(f"  Estimated tax             : ₹{estimated_tax:>12,.2f}")
        print(f"  {'─' * (w - 4)}")
        print(f"  PROFIT AFTER TAX          : ₹{profit_after_tax:>+12,.2f}")
    else:
        print(f"  Estimated tax             : ₹        0.00  (loss — no tax)")
        print(f"  Loss carry-forward        : ₹{abs(net_after_claude):>12,.2f}")
        print(f"    (speculative losses offset speculative income only, up to 4 years)")
        print(f"  {'─' * (w - 4)}")
        print(f"  NET LOSS                  : ₹{net_after_claude:>+12,.2f}")

    print(f"\n  {'─' * (w - 4)}")
    print(f"  DEDUCTIBLE EXPENSES (claim in Schedule BP)")
    print(f"  {'─' * (w - 4)}")
    print(f"  Brokerage                 : ₹{total_brokerage:>12,.2f}")
    print(f"  STT                       : ₹{total_stt:>12,.2f}")
    print(f"  Exchange txn charges      : ₹{total_exch:>12,.2f}")
    print(f"  GST on trading charges    : ₹{total_gst:>12,.2f}")
    print(f"  SEBI charges              : ₹{total_sebi:>12,.4f}")
    print(f"  Stamp duty                : ₹{total_stamp:>12,.2f}")
    print(f"  Claude AI API costs       : ₹{total_claude:>12,.2f}")
    print(f"  Zerodha subscription      : ₹{zerodha_sub:>12,.2f}  ({months_traded} month(s) × ₹{Config.ZERODHA_MONTHLY_COST:,.0f})")
    total_deductible = total_charges + total_claude + zerodha_sub
    print(f"  Total deductible          : ₹{total_deductible:>12,.2f}")

    print(f"\n  {'─' * (w - 4)}")
    print(f"  DAY-WISE BREAKDOWN")
    print(f"  {'─' * (w - 4)}")
    print(f"  {'Date':<12} {'Trades':>6} {'Gross P&L':>12} {'Charges':>10} {'Net P&L':>12}")
    for date in sorted(day_map):
        d = day_map[date]
        print(
            f"  {date:<12} {d['trades']:>6} "
            f"₹{d['gross']:>+10,.2f} ₹{d['charges']:>8,.2f} ₹{d['net']:>+10,.2f}"
        )

    print(f"\n{'=' * w}\n")
    return True


def _claude_costs_for_fy(fy_start: int) -> float:
    """Sum Claude API costs from JSON files for live trading days in this FY."""
    total = 0.0
    for jpath in find_all_trading_jsons():
        data = load_json(jpath)
        if not data:
            continue
        date_str = data.get("date", "")
        if not date_str or indian_fy(date_str) != fy_start:
            continue
        if data.get("mode") != "live":
            continue
        total += data.get("pnl", {}).get("charges", {}).get("claude_api_cost", 0)
    return total


def get_db_fys() -> list[int]:
    """Returns FY start years that have data in the tax_ledger table."""
    conn = _get_db()
    rows = conn.execute("SELECT DISTINCT date FROM tax_ledger ORDER BY date").fetchall()
    conn.close()
    return sorted({indian_fy(r["date"]) for r in rows})


def get_json_fys() -> list[int]:
    """Returns FY start years that have live-mode trading JSONs."""
    fys = set()
    for jpath in find_all_trading_jsons():
        data = load_json(jpath)
        if data and data.get("date") and data.get("mode") == "live":
            fys.add(indian_fy(data["date"]))
    return sorted(fys)


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Intraday trading tax ledger — fill DB or view summary.",
    )
    parser.add_argument(
        "mode", choices=["fill", "summary"],
        help="fill = populate DB from live JSONs + print summary.  summary = print summary only.",
    )
    parser.add_argument(
        "--fy", type=int, default=None,
        help="Financial year start (e.g. 2025 for FY 2025-26). Default: current FY.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Process all available financial years.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List financial years with data.",
    )

    args = parser.parse_args()

    # ── List mode ─────────────────────────────────────────────────
    if args.list:
        if args.mode == "fill":
            fys = get_json_fys()
            label = "live trading JSONs"
        else:
            fys = get_db_fys()
            label = "tax_ledger DB"
        if not fys:
            print(f"\n  No data found in {label}.")
            return
        print(f"\n  Financial years with data ({label}):")
        for fy in fys:
            print(f"    {fy_label(fy)}")
        return

    # ── All FYs ───────────────────────────────────────────────────
    if args.all:
        fys = get_json_fys() if args.mode == "fill" else get_db_fys()
        if not fys:
            print("\n  No data found.")
            return
        for fy in fys:
            _run_single_fy(args.mode, fy)
        return

    # ── Single FY ─────────────────────────────────────────────────
    fy = args.fy if args.fy else current_fy()
    _run_single_fy(args.mode, fy)


def _run_single_fy(mode: str, fy: int):
    label = fy_label(fy)
    if mode == "fill":
        print(f"\n  Filling {label} from live trading data …")
        inserted = fill_fy(fy)
        if inserted:
            print(f"  ✓ Inserted {inserted} new trade(s) into tax_ledger.")
        else:
            print(f"  · No new trades to insert (already up-to-date or no live data).")
        print_summary(fy)
    else:
        print_summary(fy)


if __name__ == "__main__":
    main()
