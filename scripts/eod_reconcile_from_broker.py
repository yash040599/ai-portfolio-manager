"""
EOD Reconciliation from Zerodha broker truth.

When the bot dies mid-session and the user closes positions manually
(or any other case where reports/DB don't reflect reality), this
script rebuilds:
    reports/trading/<YYYY>/<MM>/trading_data_<DD>.json
    reports/trading/<YYYY>/<MM>/trading_report_<DD>.txt
    data/trades.db::trades         (UPSERT via UNIQUE index)
    data/trades.db::intraday_tax_ledger (UPSERT via UNIQUE index)

…using ONLY data pulled from Zerodha (kite.trades() + kite.orders())
plus the optional bot log for entry-context (entry_score, entry_rsi,
indicator_snapshot — those aren't in broker truth).

Usage
-----
    # Today (default)
    python scripts/eod_reconcile_from_broker.py

    # Specific past date (must have a Zerodha session token from THAT day
    # — Zerodha tokens expire midnight, so this only works same-day):
    python scripts/eod_reconcile_from_broker.py --date 2026-05-12

    # Dry run — no writes
    python scripts/eod_reconcile_from_broker.py --dry-run

What it does
------------
1. Logs into Zerodha using the saved token (if today) or the .env-driven
   ASSISTED flow (you'll be prompted for a TOTP code).
2. kite.trades() returns one row per fill; multiple fills for the
   same order are aggregated by order_id (qty-weighted average price).
3. Pairs intraday MIS round-trips: for each (symbol, exchange) the entry
   side is the FIRST chronological fill; the exit side is the SECOND.
   If a symbol shows only one side (still open), it is skipped here —
   the caller is expected to have already closed everything.
4. Computes per-trade gross_pnl + Zerodha charges via Config.calculate_charges.
5. Writes JSON/TXT reports + UPSERTs trades + intraday_tax_ledger rows.
6. Tags JSON `verified=True` (this IS broker truth).

Rationale
---------
This script is the always-correct fallback when verify_trades.py can't
run (because there's no pre-existing JSON file, e.g. a crashed-bot day).
Once it ships output, the normal EOD flow (rejection_audit, dashboard,
backup_data sync) picks up cleanly.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import Config, now_ist  # noqa: E402
from core.logger import Logger  # noqa: E402
from core.zerodha_client import ZerodhaClient  # noqa: E402

DB_PATH = os.path.join(PROJECT_ROOT, "data", "trades.db")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports", "trading")


# ──────────────────────────────────────────────────────────────────
# Pairing
# ──────────────────────────────────────────────────────────────────

def _aggregate_fills_by_order(fills: list[dict]) -> list[dict]:
    """Aggregate raw kite.trades() fills by order_id.

    One Zerodha order can have many fills (especially MARKET orders).
    Returns one dict per order_id with qty-weighted average price.
    """
    by_order: dict[str, list[dict]] = defaultdict(list)
    for f in fills:
        by_order[str(f.get("order_id"))].append(f)

    out = []
    for oid, fs in by_order.items():
        total_qty = sum(float(f.get("quantity", 0)) for f in fs)
        if total_qty <= 0:
            continue
        weighted = sum(
            float(f.get("average_price", 0)) * float(f.get("quantity", 0)) for f in fs
        )
        avg_price = weighted / total_qty
        first = fs[0]
        # fill_timestamp is when the fill happened; pick the latest.
        ts_list = [f.get("fill_timestamp") for f in fs if f.get("fill_timestamp")]
        ts = max(ts_list) if ts_list else first.get("exchange_timestamp")
        out.append({
            "order_id":     oid,
            "symbol":       first.get("tradingsymbol"),
            "exchange":     first.get("exchange", "NSE"),
            "side":         (first.get("transaction_type") or "").upper(),
            "qty":          int(total_qty),
            "avg_price":    round(avg_price, 4),
            "product":      first.get("product"),
            "fill_ts":      ts,
        })
    return out


def _pair_round_trips(orders: list[dict]) -> tuple[list[dict], list[dict]]:
    """Pair MIS intraday round-trips by (symbol, exchange).

    Returns (round_trips, leftovers). round_trips is a list of dicts:
        symbol, exchange, side (entry side), qty, entry_price, entry_time,
        exit_price, exit_time, entry_order_id, exit_order_id.

    Leftovers = orders that couldn't be paired (one-sided).
    """
    # Only intraday MIS for this script (CNC = delivery, not in scope).
    mis = [o for o in orders if (o.get("product") or "").upper() == "MIS"]
    cnc = [o for o in orders if (o.get("product") or "").upper() != "MIS"]
    if cnc:
        print(f"  Note: {len(cnc)} non-MIS order(s) ignored (delivery/CNC).")

    by_sym: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for o in mis:
        by_sym[(o["symbol"], o["exchange"])].append(o)

    round_trips: list[dict] = []
    leftovers: list[dict] = []

    for (sym, exch), os_ in by_sym.items():
        # Sort by fill timestamp ascending.
        os_.sort(key=lambda x: str(x.get("fill_ts") or ""))
        # We expect alternating BUY/SELL (or SELL/BUY for shorts).
        # Greedy pairing: walk in pairs. If qty mismatches, take min qty.
        i = 0
        while i + 1 < len(os_):
            a, b = os_[i], os_[i + 1]
            if a["side"] == b["side"]:
                # Same side both — can't pair. Push a to leftovers.
                leftovers.append(a)
                i += 1
                continue
            qty = min(a["qty"], b["qty"])
            entry, exit_ = a, b
            round_trips.append({
                "symbol":         sym,
                "exchange":       exch,
                "side":           entry["side"],
                "qty":            qty,
                "entry_price":    entry["avg_price"],
                "entry_time":     str(entry["fill_ts"]) if entry["fill_ts"] else "",
                "exit_price":     exit_["avg_price"],
                "exit_time":      str(exit_["fill_ts"]) if exit_["fill_ts"] else "",
                "entry_order_id": entry["order_id"],
                "exit_order_id":  exit_["order_id"],
                "product":        "MIS",
            })
            i += 2
        if i < len(os_):
            leftovers.append(os_[i])

    return round_trips, leftovers


# ──────────────────────────────────────────────────────────────────
# P&L + charges
# ──────────────────────────────────────────────────────────────────

def _compute_pnl_and_charges(rt: dict) -> dict:
    """Compute gross/net P&L + per-trade Zerodha charges via Config."""
    qty = rt["qty"]
    entry = rt["entry_price"]
    exit_ = rt["exit_price"]
    side = rt["side"]

    if side == "BUY":
        gross = (exit_ - entry) * qty
        buy_turnover = entry * qty
        sell_turnover = exit_ * qty
    else:  # SELL (short)
        gross = (entry - exit_) * qty
        buy_turnover = exit_ * qty   # cover-buy is the buy side for shorts
        sell_turnover = entry * qty

    # 2 orders per round-trip (entry + exit).
    charges = Config.calculate_charges(
        total_buy_turnover=buy_turnover,
        total_sell_turnover=sell_turnover,
        num_orders=2,
        claude_calls=0,
    )

    net = gross - charges["total_tax_and_charges"]
    return {
        **rt,
        "gross_pnl":     round(gross, 2),
        "net_pnl":       round(net, 2),
        "buy_value":     round(buy_turnover, 2),
        "sell_value":    round(sell_turnover, 2),
        "turnover":      round(buy_turnover + sell_turnover, 2),
        "brokerage":     charges["brokerage"],
        "stt":           charges["stt"],
        "exchange_txn":  charges["exchange_txn"],
        "gst":           charges["gst"],
        "sebi_charges":  charges["sebi_charges"],
        "stamp_duty":    charges["stamp_duty"],
        "total_charges": charges["total_tax_and_charges"],
    }


# ──────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────

def _upsert_trades(trades: list[dict], date_str: str, dry_run: bool):
    """UPSERT rows into trades table. Uses (date, symbol, side, entry_time)
    UNIQUE index to avoid duplicates.
    """
    if dry_run:
        print(f"  [DRY RUN] would UPSERT {len(trades)} rows into trades")
        return

    written = 0
    skipped = 0
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        for t in trades:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO trades "
                    "(date, symbol, side, entry_price, exit_price, qty, pnl, "
                    " exit_reason, market_condition, entry_time, exit_time, "
                    " indicator_snapshot) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        date_str,
                        t["symbol"],
                        t["side"],
                        t["entry_price"],
                        t["exit_price"],
                        t["qty"],
                        t["net_pnl"],
                        t.get("exit_reason", "BROKER_TRUTH"),
                        "RECONCILED_FROM_BROKER",
                        t["entry_time"],
                        t["exit_time"],
                        json.dumps({
                            "source": "eod_reconcile_from_broker",
                            "entry_order_id": t["entry_order_id"],
                            "exit_order_id":  t["exit_order_id"],
                            "gross_pnl":      t["gross_pnl"],
                            "total_charges":  t["total_charges"],
                        }),
                    ),
                )
                written += 1
            except sqlite3.IntegrityError as e:
                skipped += 1
                print(f"  skip trades row for {t['symbol']}: {e}")
        conn.commit()
    print(f"  trades: wrote/replaced {written}, skipped {skipped}")


def _upsert_intraday_tax(trades: list[dict], date_str: str, dry_run: bool):
    """UPSERT rows into intraday_tax_ledger. UNIQUE on (date, order_id)."""
    if dry_run:
        print(f"  [DRY RUN] would UPSERT {len(trades)} rows into intraday_tax_ledger")
        return

    written = 0
    skipped = 0
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        for t in trades:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO intraday_tax_ledger "
                    "(date, symbol, exchange, side, qty, entry_price, exit_price, "
                    " entry_time, exit_time, exit_reason, gross_pnl, buy_value, "
                    " sell_value, turnover, brokerage, stt, exchange_txn, gst, "
                    " sebi_charges, stamp_duty, total_charges, net_pnl, order_id, "
                    " verified) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        date_str,
                        t["symbol"],
                        t["exchange"],
                        t["side"],
                        t["qty"],
                        t["entry_price"],
                        t["exit_price"],
                        t["entry_time"],
                        t["exit_time"],
                        t.get("exit_reason", "BROKER_TRUTH"),
                        t["gross_pnl"],
                        t["buy_value"],
                        t["sell_value"],
                        t["turnover"],
                        t["brokerage"],
                        t["stt"],
                        t["exchange_txn"],
                        t["gst"],
                        t["sebi_charges"],
                        t["stamp_duty"],
                        t["total_charges"],
                        t["net_pnl"],
                        # Use the ENTRY order_id as the unique key per (date, order_id).
                        t["entry_order_id"],
                        "broker_api",
                    ),
                )
                written += 1
            except sqlite3.IntegrityError as e:
                skipped += 1
                print(f"  skip ledger row for {t['symbol']}: {e}")
        conn.commit()
    print(f"  intraday_tax_ledger: wrote/replaced {written}, skipped {skipped}")


def _write_reports(trades: list[dict], date_str: str, dry_run: bool):
    """Write trading_data_<DD>.json + trading_report_<DD>.txt."""
    d = dt.date.fromisoformat(date_str)
    base = os.path.join(REPORTS_DIR, str(d.year), f"{d.month:02d}")
    json_path = os.path.join(base, f"trading_data_{d.day:02d}.json")
    txt_path = os.path.join(base, f"trading_report_{d.day:02d}.txt")

    gross = sum(t["gross_pnl"] for t in trades)
    charges = sum(t["total_charges"] for t in trades)
    net = sum(t["net_pnl"] for t in trades)
    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    losses = sum(1 for t in trades if t["net_pnl"] < 0)
    flats = sum(1 for t in trades if t["net_pnl"] == 0)

    payload = {
        "date":     date_str,
        "mode":     "trade-noai",
        "verified": True,  # broker-truth
        "source":   "eod_reconcile_from_broker",
        "positions": [
            {
                "symbol":      t["symbol"],
                "exchange":    t["exchange"],
                "side":        t["side"],
                "qty":         t["qty"],
                "entry_price": t["entry_price"],
                "exit_price":  t["exit_price"],
                "entry_time":  t["entry_time"],
                "exit_time":   t["exit_time"],
                "gross_pnl":   t["gross_pnl"],
                "net_pnl":     t["net_pnl"],
                "charges":     t["total_charges"],
                "exit_reason": t.get("exit_reason", "BROKER_TRUTH"),
                "status":      "CLOSED",
                "entry_order_id": t["entry_order_id"],
                "exit_order_id":  t["exit_order_id"],
            }
            for t in trades
        ],
        "pnl": {
            "gross_pnl":   round(gross, 2),
            "charges":     round(charges, 2),
            "net_profit":  round(net, 2),
            "trades":      len(trades),
            "wins":        wins,
            "losses":      losses,
            "flats":       flats,
            "win_rate_pct": round(100.0 * wins / max(len(trades), 1), 1),
        },
    }

    if dry_run:
        print("  [DRY RUN] would write JSON:")
        print(json.dumps(payload, indent=2)[:1500] + ("\n  ..." if len(json.dumps(payload)) > 1500 else ""))
        return

    os.makedirs(base, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"  wrote: {json_path}")

    # Plain text report
    lines = []
    lines.append("=" * 70)
    lines.append(f"  TRADING REPORT — {date_str} (RECONCILED FROM BROKER)")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"  Trades closed: {len(trades)}  |  W: {wins}  L: {losses}  Flat: {flats}")
    lines.append(f"  Win rate    : {payload['pnl']['win_rate_pct']:.1f}%")
    lines.append(f"  Gross P&L   : Rs.{gross:+,.2f}")
    lines.append(f"  Charges     : Rs.{charges:,.2f}")
    lines.append(f"  Net P&L     : Rs.{net:+,.2f}")
    lines.append("")
    lines.append("-" * 70)
    lines.append(f"  {'Symbol':<14} {'Side':<5} {'Qty':>5} {'Entry':>10} {'Exit':>10} {'Net':>10}  Reason")
    lines.append("-" * 70)
    for t in trades:
        lines.append(
            f"  {t['symbol']:<14} {t['side']:<5} {t['qty']:>5} "
            f"{t['entry_price']:>10.2f} {t['exit_price']:>10.2f} "
            f"Rs.{t['net_pnl']:>+8.2f}  {t.get('exit_reason', 'BROKER_TRUTH')}"
        )
    lines.append("-" * 70)
    lines.append("")
    lines.append("Note: prices reflect broker truth (kite.trades() average fill).")
    lines.append("Note: charges computed via Config.calculate_charges (estimate).")
    lines.append("Note: exit_reason='BROKER_TRUTH' = paired round-trip with no")
    lines.append("      live-bot context (e.g. manual close after a crash).")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  wrote: {txt_path}")


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today IST)")
    parser.add_argument("--dry-run", action="store_true", help="No writes")
    args = parser.parse_args()

    target = args.date or str(now_ist().date())

    print()
    print("=" * 70)
    print(f"  EOD RECONCILE FROM BROKER — {target}")
    print("=" * 70)
    print()

    log = Logger("EodReconcile")
    z = ZerodhaClient(Config, log)
    print("  Logging in to Zerodha (TOTP prompt may follow)…")
    z.login(interactive=True)

    print("\n  Pulling kite.trades() …")
    raw = z.get_todays_trades()
    print(f"  raw fills: {len(raw)}")

    if not raw:
        print("  No trades found in broker session for today. Nothing to reconcile.")
        # Still write an empty zero-trades report so downstream tools see the date.
        _write_reports([], target, args.dry_run)
        return 0

    # Filter to target date if user passed --date and it's not today.
    # Note: kite.trades() is intraday-only and only valid for current session.
    today_only = [
        t for t in raw
        if str(t.get("fill_timestamp", ""))[:10] == target
        or str(t.get("exchange_timestamp", ""))[:10] == target
        or str(t.get("order_timestamp", ""))[:10] == target
    ]
    if not today_only and target == str(now_ist().date()):
        # Sometimes the timestamps are naive and don't include the date
        # — fall back to ALL fills if we asked for today.
        today_only = raw
    print(f"  fills for {target}: {len(today_only)}")

    orders = _aggregate_fills_by_order(today_only)
    print(f"  unique orders: {len(orders)}")
    for o in orders:
        print(f"    {o['fill_ts']}  {o['side']:<4} {o['qty']:>4}x {o['symbol']:<12} "
              f"@ Rs.{o['avg_price']:.2f}  ({o['product']}, {o['order_id']})")

    round_trips, leftovers = _pair_round_trips(orders)
    print(f"\n  paired round-trips: {len(round_trips)}")
    print(f"  unpaired (still open or odd count): {len(leftovers)}")
    for lo in leftovers:
        print(f"    UNPAIRED  {lo['side']:<4} {lo['qty']:>4}x {lo['symbol']:<12} "
              f"@ Rs.{lo['avg_price']:.2f}  ({lo['order_id']})")

    enriched = [_compute_pnl_and_charges(rt) for rt in round_trips]

    print("\n  Per-trade P&L:")
    print(f"  {'Symbol':<14} {'Side':<5} {'Qty':>4} {'Entry':>9} {'Exit':>9} "
          f"{'Gross':>9} {'Chrg':>7} {'Net':>9}")
    for t in enriched:
        print(f"  {t['symbol']:<14} {t['side']:<5} {t['qty']:>4} "
              f"{t['entry_price']:>9.2f} {t['exit_price']:>9.2f} "
              f"Rs.{t['gross_pnl']:>+7.2f} {t['total_charges']:>7.2f} "
              f"Rs.{t['net_pnl']:>+7.2f}")
    g = sum(t["gross_pnl"] for t in enriched)
    c = sum(t["total_charges"] for t in enriched)
    n = sum(t["net_pnl"] for t in enriched)
    print(f"  {'TOTAL':<14} {'':<5} {'':>4} {'':>9} {'':>9} "
          f"Rs.{g:>+7.2f} {c:>7.2f} Rs.{n:>+7.2f}")

    print("\n  Persisting…")
    _upsert_trades(enriched, target, args.dry_run)
    _upsert_intraday_tax(enriched, target, args.dry_run)
    _write_reports(enriched, target, args.dry_run)

    print("\n  ✓ EOD reconciliation complete.")
    print(f"    → reports/trading/{target.replace('-','/')}{'(dry-run)' if args.dry_run else ''}")
    print(f"    → data/trades.db trades + intraday_tax_ledger updated")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
