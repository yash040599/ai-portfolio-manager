"""
Generate a tax ledger for intraday trading — one file per Indian financial year.

Reads all trading_data_*.json reports and produces a clean TSV table with
every trade, charges breakdown, and estimated tax liability. Ready for ITR filing.

Usage:
    python scripts/generate_tax_ledger.py            # current FY
    python scripts/generate_tax_ledger.py --fy 2025  # FY 2025-26 (Apr 2025 - Mar 2026)
    python scripts/generate_tax_ledger.py --all       # all FYs found in data
    python scripts/generate_tax_ledger.py --list       # list available FYs
"""

import argparse
import datetime
import glob
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import Config

REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports", "trading")
OUTPUT_DIR  = os.path.join(PROJECT_ROOT, "reports", "tax")


# ── Helpers ───────────────────────────────────────────────────────

def indian_fy(date_str: str) -> int:
    """
    Returns the Indian financial year start year for a given date.
    FY runs April 1 to March 31.
    e.g. 2026-03-25 → FY 2025 (FY 2025-26)
         2026-04-01 → FY 2026 (FY 2026-27)
    """
    d = datetime.date.fromisoformat(date_str)
    return d.year if d.month >= 4 else d.year - 1


def fy_label(fy_start: int) -> str:
    """e.g. 2025 → 'FY 2025-26'"""
    return f"FY {fy_start}-{str(fy_start + 1)[-2:]}"


def find_all_trading_jsons() -> list[str]:
    """Finds all trading_data_*.json files under reports/trading/."""
    pattern = os.path.join(REPORTS_DIR, "**", "trading_data_*.json")
    return sorted(glob.glob(pattern, recursive=True))


def load_trading_day(json_path: str) -> dict | None:
    """Load a single trading day's JSON data."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  ⚠ Skipped {json_path}: {e}")
        return None


def per_trade_charges(position: dict, day_charges: dict, day_positions: list[dict]) -> dict:
    """
    Apportion the day's charges to a single trade based on its share
    of total turnover. Returns a dict of per-trade charge components.
    """
    qty = position.get("qty", 0)
    entry = position.get("entry_price", 0)
    exit_p = position.get("exit_price", 0)

    if position.get("side") == "BUY":
        buy_val = entry * qty
        sell_val = exit_p * qty
    else:
        sell_val = entry * qty
        buy_val = exit_p * qty

    trade_turnover = buy_val + sell_val
    total_turnover = day_charges.get("total_turnover", 0)

    if total_turnover <= 0:
        share = 0
    else:
        share = trade_turnover / total_turnover

    return {
        "buy_value":       round(buy_val, 2),
        "sell_value":      round(sell_val, 2),
        "turnover":        round(trade_turnover, 2),
        "brokerage":       round(day_charges.get("brokerage", 0) * share, 2),
        "stt":             round(day_charges.get("stt", 0) * share, 2),
        "exchange_txn":    round(day_charges.get("exchange_txn", 0) * share, 2),
        "gst":             round(day_charges.get("gst", 0) * share, 2),
        "sebi_charges":    round(day_charges.get("sebi_charges", 0) * share, 4),
        "stamp_duty":      round(day_charges.get("stamp_duty", 0) * share, 2),
        "total_charges":   round(day_charges.get("total_tax_and_charges", 0) * share, 2),
    }


# ── Main Generation ───────────────────────────────────────────────

def generate_ledger(fy_start: int) -> str | None:
    """
    Generate the tax ledger TSV for one Indian financial year.
    Returns the output file path, or None if no data found.
    """
    fy_end = fy_start + 1
    fy_from = f"{fy_start}-04-01"
    fy_to   = f"{fy_end}-03-31"

    all_jsons = find_all_trading_jsons()
    if not all_jsons:
        print(f"  No trading data found under {REPORTS_DIR}")
        return None

    # Filter to this FY
    trades = []
    skipped_columns = []
    day_summaries = []

    for jpath in all_jsons:
        data = load_trading_day(jpath)
        if not data:
            continue

        date_str = data.get("date", "")
        if not date_str or indian_fy(date_str) != fy_start:
            continue

        mode = data.get("mode", "unknown")
        positions = data.get("positions", [])
        day_charges = data.get("pnl", {}).get("charges", {})
        day_pnl = data.get("pnl", {})

        if not positions:
            continue

        for pos in positions:
            if pos.get("status") != "CLOSED":
                continue

            tc = per_trade_charges(pos, day_charges, positions)

            gross_pnl = pos.get("pnl", 0)
            net_pnl = gross_pnl - tc["total_charges"]

            # Check for missing fields
            missing = []
            if not pos.get("exit_price"):
                missing.append("exit_price")
            if not pos.get("entry_time"):
                missing.append("entry_time")
            if not pos.get("exit_time"):
                missing.append("exit_time")
            if missing:
                skipped_columns.append((date_str, pos.get("symbol", "?"), missing))

            trades.append({
                "date":          date_str,
                "symbol":        pos.get("symbol", ""),
                "exchange":      pos.get("exchange", "NSE"),
                "side":          pos.get("side", ""),
                "qty":           pos.get("qty", 0),
                "entry_price":   pos.get("entry_price", 0),
                "exit_price":    pos.get("exit_price", ""),
                "entry_time":    pos.get("entry_time", ""),
                "exit_time":     pos.get("exit_time", ""),
                "exit_reason":   pos.get("exit_reason", ""),
                "gross_pnl":     round(gross_pnl, 2),
                "buy_value":     tc["buy_value"],
                "sell_value":    tc["sell_value"],
                "turnover":      tc["turnover"],
                "brokerage":     tc["brokerage"],
                "stt":           tc["stt"],
                "exchange_txn":  tc["exchange_txn"],
                "gst":           tc["gst"],
                "sebi_charges":  tc["sebi_charges"],
                "stamp_duty":    tc["stamp_duty"],
                "total_charges": tc["total_charges"],
                "net_pnl":       round(net_pnl, 2),
                "mode":          mode,
                "order_id":      pos.get("order_id", ""),
            })

        # Day-level summary
        day_summaries.append({
            "date":             date_str,
            "mode":             mode,
            "num_trades":       len([p for p in positions if p.get("status") == "CLOSED"]),
            "gross_pnl":        day_pnl.get("gross_pnl", 0),
            "total_charges":    day_charges.get("total_tax_and_charges", 0),
            "claude_api_cost":  day_charges.get("claude_api_cost", 0),
            "net_profit":       day_pnl.get("net_profit", 0),
        })

    if not trades:
        print(f"  No trades found for {fy_label(fy_start)} ({fy_from} to {fy_to})")
        return None

    # ── Print skipped columns warnings ────────────────────────────
    if skipped_columns:
        print(f"\n  ⚠ Some fields were empty (left blank in output):")
        for date, sym, cols in skipped_columns:
            print(f"    {date} {sym}: {', '.join(cols)}")

    # ── Calculate FY totals ───────────────────────────────────────
    total_gross    = sum(t["gross_pnl"] for t in trades)
    total_charges  = sum(t["total_charges"] for t in trades)
    total_claude   = sum(d["claude_api_cost"] for d in day_summaries)
    total_net      = total_gross - total_charges - total_claude

    effective_tax_rate = Config.TAX_RATE_PCT * (1 + Config.TAX_CESS_PCT / 100)
    estimated_tax = round(total_net * effective_tax_rate / 100, 2) if total_net > 0 else 0
    profit_after_tax = round(total_net - estimated_tax, 2)

    # Turnover for tax purposes (absolute sum of P&L per trade)
    speculative_turnover = sum(abs(t["gross_pnl"]) for t in trades)

    # ── Write TSV ─────────────────────────────────────────────────
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = f"tax_ledger_{fy_label(fy_start).replace(' ', '_')}.tsv"
    out_path = os.path.join(OUTPUT_DIR, filename)

    headers = [
        "Date", "Symbol", "Exchange", "Side", "Qty",
        "Entry Price", "Exit Price", "Entry Time", "Exit Time",
        "Exit Reason", "Gross P&L",
        "Buy Value", "Sell Value", "Turnover",
        "Brokerage", "STT", "Exchange Txn", "GST",
        "SEBI Charges", "Stamp Duty", "Total Charges",
        "Net P&L", "Mode", "Order ID",
    ]

    with open(out_path, "w", encoding="utf-8") as f:
        # Header comment block
        f.write(f"# {fy_label(fy_start)} — Intraday Trading Tax Ledger\n")
        f.write(f"# Period: {fy_from} to {fy_to}\n")
        f.write(f"# Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"# Income type: Speculative Business Income (Section 43(5))\n")
        f.write(f"# ITR Form: ITR-3 → Schedule BP → Speculative Business Income\n")
        f.write(f"#\n")

        # Column headers
        f.write("\t".join(headers) + "\n")

        # Trade rows
        for t in trades:
            row = [
                t["date"], t["symbol"], t["exchange"], t["side"], str(t["qty"]),
                f'{t["entry_price"]:.2f}', f'{t["exit_price"]:.2f}' if t["exit_price"] else "",
                t["entry_time"], t["exit_time"],
                t["exit_reason"], f'{t["gross_pnl"]:.2f}',
                f'{t["buy_value"]:.2f}', f'{t["sell_value"]:.2f}', f'{t["turnover"]:.2f}',
                f'{t["brokerage"]:.2f}', f'{t["stt"]:.2f}', f'{t["exchange_txn"]:.2f}',
                f'{t["gst"]:.2f}', f'{t["sebi_charges"]:.4f}', f'{t["stamp_duty"]:.2f}',
                f'{t["total_charges"]:.2f}', f'{t["net_pnl"]:.2f}',
                t["mode"], t["order_id"],
            ]
            f.write("\t".join(row) + "\n")

        # ── Summary section ───────────────────────────────────────
        f.write(f"\n")
        f.write(f"# {'='*60}\n")
        f.write(f"# {fy_label(fy_start)} SUMMARY\n")
        f.write(f"# {'='*60}\n")
        f.write(f"#\n")
        f.write(f"# Trading days              : {len(day_summaries)}\n")
        f.write(f"# Total trades              : {len(trades)}\n")

        live_trades = [t for t in trades if t["mode"] == "live"]
        dry_trades  = [t for t in trades if t["mode"] != "live"]
        if live_trades and dry_trades:
            f.write(f"#   Live trades             : {len(live_trades)}\n")
            f.write(f"#   Dry-run trades          : {len(dry_trades)}\n")

        f.write(f"#\n")
        f.write(f"# Gross P&L (all trades)    : ₹{total_gross:+,.2f}\n")
        f.write(f"# Total charges & taxes     : ₹{total_charges:,.2f}\n")
        f.write(f"# Claude API costs          : ₹{total_claude:,.2f}\n")
        f.write(f"# Net Profit (before tax)   : ₹{total_net:+,.2f}\n")
        f.write(f"#\n")

        f.write(f"# Speculative turnover      : ₹{speculative_turnover:,.2f}\n")
        f.write(f"#   (absolute sum of per-trade P&L — for ITR turnover calculation)\n")
        f.write(f"#\n")

        f.write(f"# ESTIMATED INCOME TAX\n")
        f.write(f"# Tax slab rate             : {Config.TAX_RATE_PCT}%\n")
        f.write(f"# Health & education cess   : {Config.TAX_CESS_PCT}%\n")
        f.write(f"# Effective rate            : {effective_tax_rate:.2f}%\n")
        if total_net > 0:
            f.write(f"# Estimated tax             : ₹{estimated_tax:,.2f}\n")
            f.write(f"# Profit after tax          : ₹{profit_after_tax:+,.2f}\n")
        else:
            f.write(f"# Estimated tax             : ₹0.00 (speculative loss — no tax)\n")
            f.write(f"# Loss carry forward        : ₹{abs(total_net):,.2f} (up to 4 years, speculative only)\n")
        f.write(f"#\n")

        # ── Deductible expenses summary ───────────────────────────
        total_brokerage = sum(t["brokerage"] for t in trades)
        total_stt      = sum(t["stt"] for t in trades)
        total_exch     = sum(t["exchange_txn"] for t in trades)
        total_gst      = sum(t["gst"] for t in trades)
        total_sebi     = sum(t["sebi_charges"] for t in trades)
        total_stamp    = sum(t["stamp_duty"] for t in trades)

        f.write(f"# DEDUCTIBLE EXPENSES (claim in Schedule BP)\n")
        f.write(f"# Brokerage                 : ₹{total_brokerage:,.2f}\n")
        f.write(f"# STT                       : ₹{total_stt:,.2f}\n")
        f.write(f"# Exchange txn charges      : ₹{total_exch:,.2f}\n")
        f.write(f"# GST on trading charges    : ₹{total_gst:,.2f}\n")
        f.write(f"# SEBI charges              : ₹{total_sebi:,.4f}\n")
        f.write(f"# Stamp duty                : ₹{total_stamp:,.2f}\n")
        f.write(f"# Claude AI API costs       : ₹{total_claude:,.2f}\n")
        zerodha_sub = Config.ZERODHA_MONTHLY_COST
        months_traded = len(set(d["date"][:7] for d in day_summaries))  # unique months
        f.write(f"# Zerodha subscription      : ₹{zerodha_sub * months_traded:,.2f} ({months_traded} month(s) × ₹{zerodha_sub:,.0f})\n")
        f.write(f"# Total deductible expenses : ₹{total_charges + total_claude + zerodha_sub * months_traded:,.2f}\n")
        f.write(f"#\n")

        # ── Day-wise summary ──────────────────────────────────────
        f.write(f"# DAY-WISE SUMMARY\n")
        f.write(f"# {'Date':<12} {'Mode':<8} {'Trades':>6} {'Gross P&L':>12} {'Charges':>10} {'Claude':>10} {'Net P&L':>12}\n")
        for d in day_summaries:
            f.write(
                f"# {d['date']:<12} {d['mode']:<8} {d['num_trades']:>6} "
                f"₹{d['gross_pnl']:>+10,.2f} ₹{d['total_charges']:>8,.2f} "
                f"₹{d['claude_api_cost']:>8,.2f} ₹{d['net_profit']:>+10,.2f}\n"
            )

    return out_path


def get_available_fys() -> list[int]:
    """Returns sorted list of FY start years that have trading data."""
    all_jsons = find_all_trading_jsons()
    fys = set()
    for jpath in all_jsons:
        data = load_trading_day(jpath)
        if data and data.get("date"):
            fys.add(indian_fy(data["date"]))
    return sorted(fys)


def current_fy() -> int:
    """Returns the current Indian FY start year."""
    today = datetime.date.today()
    return today.year if today.month >= 4 else today.year - 1


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate intraday trading tax ledger for ITR filing.",
        epilog="Output: reports/tax/tax_ledger_FY_YYYY-YY.tsv",
    )
    parser.add_argument(
        "--fy", type=int, default=None,
        help="Financial year start (e.g. 2025 for FY 2025-26). Default: current FY.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Generate ledgers for all available financial years.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available financial years with data.",
    )

    args = parser.parse_args()

    if args.list:
        fys = get_available_fys()
        if not fys:
            print("No trading data found.")
            return
        print("\nAvailable financial years with trading data:")
        for fy in fys:
            jsons = find_all_trading_jsons()
            count = sum(1 for j in jsons if (d := load_trading_day(j)) and d.get("date") and indian_fy(d["date"]) == fy)
            print(f"  {fy_label(fy)}  ({count} trading day(s))")
        return

    if args.all:
        fys = get_available_fys()
        if not fys:
            print("No trading data found.")
            return
        for fy in fys:
            print(f"\n{'='*60}")
            print(f"  Generating {fy_label(fy)}")
            print(f"{'='*60}")
            path = generate_ledger(fy)
            if path:
                print(f"\n  ✓ Saved: {path}")
        return

    # Single FY
    fy = args.fy if args.fy else current_fy()
    print(f"\n{'='*60}")
    print(f"  Generating {fy_label(fy)} Tax Ledger")
    print(f"{'='*60}")
    path = generate_ledger(fy)
    if path:
        print(f"\n  ✓ Saved: {path}")
        print(f"\n  Open in Excel/Google Sheets (TSV format) for a clean table.")
        print(f"  All charges are already broken down — ready for ITR-3 filing.")
    else:
        print(f"\n  No data found. Run the trading bot first, then try again.")


if __name__ == "__main__":
    main()
