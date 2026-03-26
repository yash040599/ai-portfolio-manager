"""
Tax summary for a financial year — intraday, capital gains, or both.

Usage
─────
    python scripts/tax_summary.py                       # both (current FY)
    python scripts/tax_summary.py --fy 2025             # both for FY 2025-26
    python scripts/tax_summary.py --intraday            # intraday only
    python scripts/tax_summary.py --capital-gains       # capital gains only
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
W = 60


# ── Intraday summary ─────────────────────────────────────────────

def _intraday_summary(fy_start: int) -> bool:
    fy_from, fy_to = fy_date_range(fy_start)
    label = fy_label(fy_start)
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM intraday_tax_ledger WHERE date>=? AND date<=? ORDER BY date, id",
        (fy_from, fy_to),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"\n  No intraday trades for {label}")
        return False

    total_gross     = sum(r["gross_pnl"]     for r in rows)
    total_charges   = sum(r["total_charges"] for r in rows)
    total_brokerage = sum(r["brokerage"]     for r in rows)
    total_stt       = sum(r["stt"]           for r in rows)
    total_exch      = sum(r["exchange_txn"]  for r in rows)
    total_gst       = sum(r["gst"]           for r in rows)
    total_sebi      = sum(r["sebi_charges"]  for r in rows)
    total_stamp     = sum(r["stamp_duty"]    for r in rows)
    total_net       = round(total_gross - total_charges, 2)

    total_claude = _claude_costs(fy_start)
    net_after_claude = round(total_net - total_claude, 2)

    eff = Config.TAX_RATE_PCT * (1 + Config.TAX_CESS_PCT / 100)
    tax = round(net_after_claude * eff / 100, 2) if net_after_claude > 0 else 0.0
    pat = round(net_after_claude - tax, 2)
    spec_turnover = sum(abs(r["gross_pnl"]) for r in rows)

    months = len({r["date"][:7] for r in rows})
    zerodha_sub = Config.ZERODHA_MONTHLY_COST * months

    day_map: dict[str, dict] = {}
    for r in rows:
        d = r["date"]
        if d not in day_map:
            day_map[d] = {"trades": 0, "gross": 0.0, "charges": 0.0, "net": 0.0}
        day_map[d]["trades"]  += 1
        day_map[d]["gross"]   += r["gross_pnl"]
        day_map[d]["charges"] += r["total_charges"]
        day_map[d]["net"]     += r["net_pnl"]

    verified = sum(1 for r in rows if r["verified"] == "verified")

    # ── Print ─────────────────────────────────────────────────
    print(f"\n{'=' * W}")
    print(f"  INTRADAY / SPECULATIVE INCOME  —  {label}")
    print(f"  Section 43(5)  |  ITR-3 → Schedule BP")
    print(f"{'=' * W}")

    print(f"\n  Trading days              : {len(day_map)}")
    print(f"  Total trades              : {len(rows)}  "
          f"({verified} verified, {len(rows) - verified} unverified)")

    _section("PROFIT & LOSS")
    print(f"  Gross P&L                 : ₹{total_gross:>+12,.2f}")
    print(f"  Regulatory charges        : ₹{total_charges:>12,.2f}")
    print(f"  Claude API costs          : ₹{total_claude:>12,.2f}")
    print(f"  Net Profit (before tax)   : ₹{net_after_claude:>+12,.2f}")

    _section("SPECULATIVE TURNOVER")
    print(f"  Turnover (for ITR)        : ₹{spec_turnover:>12,.2f}")
    print(f"    (absolute sum of per-trade P&L)")

    _section("ESTIMATED TAX")
    print(f"  Rate: {Config.TAX_RATE_PCT}% + {Config.TAX_CESS_PCT}% cess = {eff:.2f}%")
    if net_after_claude > 0:
        print(f"  Estimated tax             : ₹{tax:>12,.2f}")
        _divider()
        print(f"  PROFIT AFTER TAX          : ₹{pat:>+12,.2f}")
    else:
        print(f"  Estimated tax             : ₹        0.00  (loss)")
        print(f"  Carry-forward loss        : ₹{abs(net_after_claude):>12,.2f}  (4 yr, speculative only)")
        _divider()
        print(f"  NET LOSS                  : ₹{net_after_claude:>+12,.2f}")

    _section("DEDUCTIBLE EXPENSES")
    print(f"  Brokerage                 : ₹{total_brokerage:>12,.2f}")
    print(f"  STT                       : ₹{total_stt:>12,.2f}")
    print(f"  Exchange txn              : ₹{total_exch:>12,.2f}")
    print(f"  GST                       : ₹{total_gst:>12,.2f}")
    print(f"  SEBI charges              : ₹{total_sebi:>12,.4f}")
    print(f"  Stamp duty                : ₹{total_stamp:>12,.2f}")
    print(f"  Claude AI costs           : ₹{total_claude:>12,.2f}")
    print(f"  Zerodha subscription      : ₹{zerodha_sub:>12,.2f}  ({months} mo × ₹{Config.ZERODHA_MONTHLY_COST:,.0f})")
    print(f"  Total deductible          : ₹{total_charges + total_claude + zerodha_sub:>12,.2f}")

    _section("DAY-WISE BREAKDOWN")
    print(f"  {'Date':<12} {'Trades':>6} {'Gross P&L':>12} {'Charges':>10} {'Net P&L':>12}")
    for date in sorted(day_map):
        d = day_map[date]
        print(
            f"  {date:<12} {d['trades']:>6} "
            f"₹{d['gross']:>+10,.2f} ₹{d['charges']:>8,.2f} ₹{d['net']:>+10,.2f}"
        )

    return True


# ── Capital gains summary ─────────────────────────────────────────

def _capital_gains_summary(fy_start: int) -> bool:
    fy_from, fy_to = fy_date_range(fy_start)
    label = fy_label(fy_start)
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM capital_gains_ledger "
        "WHERE exit_date>=? AND exit_date<=? ORDER BY trade_type, exit_date",
        (fy_from, fy_to),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"\n  No capital-gains trades for {label}")
        return False

    st = [r for r in rows if r["trade_type"] == "short_term"]
    lt = [r for r in rows if r["trade_type"] == "long_term"]

    st_profit  = sum(r["taxable_profit"] for r in st)
    st_charges = sum(r["total_charges"]  for r in st)
    lt_profit  = sum(r["taxable_profit"] for r in lt)
    lt_charges = sum(r["total_charges"]  for r in lt)

    # Tax rates
    stcg_rate = Config.STCG_TAX_RATE_PCT * (1 + Config.TAX_CESS_PCT / 100)
    ltcg_rate = Config.LTCG_TAX_RATE_PCT * (1 + Config.TAX_CESS_PCT / 100)
    ltcg_exempt = Config.LTCG_EXEMPTION_LIMIT
    ltcg_taxable = max(0, lt_profit - ltcg_exempt) if lt_profit > 0 else 0

    stcg_tax = round(st_profit * stcg_rate / 100, 2) if st_profit > 0 else 0
    ltcg_tax = round(ltcg_taxable * ltcg_rate / 100, 2)

    print(f"\n{'=' * W}")
    print(f"  CAPITAL GAINS  —  {label}")
    print(f"  ITR-3 → Schedule CG")
    print(f"{'=' * W}")

    if st:
        _section("SHORT-TERM CAPITAL GAINS (STCG)")
        print(f"  Trades                    : {len(st)}")
        print(f"  Total profit              : ₹{st_profit:>+12,.2f}")
        print(f"  Total charges             : ₹{st_charges:>12,.2f}")
        print(f"  Tax rate                  : {Config.STCG_TAX_RATE_PCT}% + {Config.TAX_CESS_PCT}% cess = {stcg_rate:.2f}%")
        if st_profit > 0:
            print(f"  Estimated STCG tax        : ₹{stcg_tax:>12,.2f}")
        else:
            print(f"  Estimated STCG tax        : ₹        0.00  (loss)")
        # Per-symbol breakdown
        _section("STCG BY SYMBOL")
        syms: dict[str, float] = {}
        for r in st:
            syms[r["symbol"]] = syms.get(r["symbol"], 0) + r["taxable_profit"]
        for s in sorted(syms, key=lambda x: syms[x]):
            print(f"    {s:<16} ₹{syms[s]:>+12,.2f}")

    if lt:
        _section("LONG-TERM CAPITAL GAINS (LTCG)")
        print(f"  Trades                    : {len(lt)}")
        print(f"  Total profit              : ₹{lt_profit:>+12,.2f}")
        print(f"  Total charges             : ₹{lt_charges:>12,.2f}")
        print(f"  Exemption (sec 112A)      : ₹{ltcg_exempt:>12,.2f}")
        print(f"  Taxable LTCG              : ₹{ltcg_taxable:>12,.2f}")
        print(f"  Tax rate                  : {Config.LTCG_TAX_RATE_PCT}% + {Config.TAX_CESS_PCT}% cess = {ltcg_rate:.2f}%")
        print(f"  Estimated LTCG tax        : ₹{ltcg_tax:>12,.2f}")
        _section("LTCG BY SYMBOL")
        syms2: dict[str, float] = {}
        for r in lt:
            syms2[r["symbol"]] = syms2.get(r["symbol"], 0) + r["taxable_profit"]
        for s in sorted(syms2, key=lambda x: syms2[x]):
            print(f"    {s:<16} ₹{syms2[s]:>+12,.2f}")

    _section("CAPITAL GAINS TAX TOTAL")
    total_cg_tax = stcg_tax + ltcg_tax
    print(f"  STCG tax                  : ₹{stcg_tax:>12,.2f}")
    print(f"  LTCG tax                  : ₹{ltcg_tax:>12,.2f}")
    _divider()
    print(f"  Total CG tax              : ₹{total_cg_tax:>12,.2f}")

    return True


# ── Combined ──────────────────────────────────────────────────────

def combined_summary(fy_start: int, show_intraday: bool, show_cg: bool):
    label = fy_label(fy_start)
    has_intraday = has_cg = False

    if show_intraday:
        has_intraday = _intraday_summary(fy_start)
    if show_cg:
        has_cg = _capital_gains_summary(fy_start)

    if show_intraday and show_cg and (has_intraday or has_cg):
        # Grand total
        print(f"\n{'=' * W}")
        print(f"  GRAND TAX SUMMARY  —  {label}")
        print(f"{'=' * W}")

        conn = get_db()
        fy_from, fy_to = fy_date_range(fy_start)

        # Intraday
        intra_rows = conn.execute(
            "SELECT gross_pnl, total_charges FROM intraday_tax_ledger "
            "WHERE date>=? AND date<=?", (fy_from, fy_to),
        ).fetchall()
        intra_gross = sum(r["gross_pnl"] for r in intra_rows)
        intra_charges = sum(r["total_charges"] for r in intra_rows)
        intra_net = round(intra_gross - intra_charges, 2)
        claude = _claude_costs(fy_start)
        intra_final = round(intra_net - claude, 2)

        eff = Config.TAX_RATE_PCT * (1 + Config.TAX_CESS_PCT / 100)
        intra_tax = round(intra_final * eff / 100, 2) if intra_final > 0 else 0

        # Capital gains
        cg_rows = conn.execute(
            "SELECT trade_type, taxable_profit FROM capital_gains_ledger "
            "WHERE exit_date>=? AND exit_date<=?", (fy_from, fy_to),
        ).fetchall()
        conn.close()

        st_profit = sum(r["taxable_profit"] for r in cg_rows if r["trade_type"] == "short_term")
        lt_profit = sum(r["taxable_profit"] for r in cg_rows if r["trade_type"] == "long_term")

        stcg_rate = Config.STCG_TAX_RATE_PCT * (1 + Config.TAX_CESS_PCT / 100)
        ltcg_rate = Config.LTCG_TAX_RATE_PCT * (1 + Config.TAX_CESS_PCT / 100)
        ltcg_taxable = max(0, lt_profit - Config.LTCG_EXEMPTION_LIMIT) if lt_profit > 0 else 0

        stcg_tax = round(st_profit * stcg_rate / 100, 2) if st_profit > 0 else 0
        ltcg_tax = round(ltcg_taxable * ltcg_rate / 100, 2)

        total_tax = intra_tax + stcg_tax + ltcg_tax

        print(f"\n  Speculative (intraday)    : ₹{intra_final:>+12,.2f}  →  tax ₹{intra_tax:>10,.2f}")
        print(f"  Short-term CG             : ₹{st_profit:>+12,.2f}  →  tax ₹{stcg_tax:>10,.2f}")
        print(f"  Long-term CG              : ₹{lt_profit:>+12,.2f}  →  tax ₹{ltcg_tax:>10,.2f}")
        _divider()
        print(f"  TOTAL ESTIMATED TAX       : ₹{total_tax:>12,.2f}")
        print()

    if not has_intraday and not has_cg:
        print(f"\n  No data for {label}. Run fill / import scripts first.\n")


# ── Helpers ───────────────────────────────────────────────────────

def _section(title: str):
    print(f"\n  {'─' * (W - 4)}")
    print(f"  {title}")
    print(f"  {'─' * (W - 4)}")


def _divider():
    print(f"  {'─' * (W - 4)}")


def _claude_costs(fy_start: int) -> float:
    total = 0.0
    pattern = os.path.join(REPORTS_DIR, "**", "trading_data_*.json")
    for jpath in sorted(glob.glob(pattern, recursive=True)):
        try:
            with open(jpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue
        ds = data.get("date", "")
        if not ds or indian_fy(ds) != fy_start or data.get("mode") != "live":
            continue
        total += data.get("pnl", {}).get("charges", {}).get("claude_api_cost", 0)
    return total


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Tax summary for a financial year.")
    parser.add_argument("--fy", type=int, default=None)
    parser.add_argument("--intraday", action="store_true",
                        help="Show intraday/speculative summary only.")
    parser.add_argument("--capital-gains", action="store_true",
                        help="Show capital-gains summary only.")
    args = parser.parse_args()

    show_i = True
    show_c = True
    if args.intraday and not args.capital_gains:
        show_c = False
    elif args.capital_gains and not args.intraday:
        show_i = False

    combined_summary(args.fy or current_fy(), show_i, show_c)


if __name__ == "__main__":
    main()
