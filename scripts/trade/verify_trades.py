"""
Verify today's trades against Zerodha's actual API data.

Uses kite.trades() (trade fills) and kite.positions() (day positions)
to cross-reference internal tracking. Corrects prices, P&L, and charges
in both the trading JSON/TXT reports and the intraday tax ledger DB.

Run this after market close (or after square-off) on the same day.
Requires a valid Zerodha session token (bot must have logged in today).

Usage
─────
    python scripts/trade/verify_trades.py              # verify today
    python scripts/trade/verify_trades.py 2026-04-07   # verify specific date (same-day only)
    python scripts/trade/verify_trades.py --status      # show verification status for all dates
"""

import argparse
import datetime
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from config import Config, now_ist
from core.logger import Logger
from core.zerodha_client import ZerodhaClient
from shared.tax_db import get_db

REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports", "trading")


# ── Helpers ───────────────────────────────────────────────────────

def _trading_file_paths(d: datetime.date) -> tuple[str, str]:
    base = os.path.join(REPORTS_DIR, str(d.year), f"{d.month:02d}")
    json_path = os.path.join(base, f"trading_data_{d.day:02d}.json")
    txt_path  = os.path.join(base, f"trading_report_{d.day:02d}.txt")
    return json_path, txt_path


def _show_status():
    """Show verification status for all trading dates."""
    import glob
    # Reconfigure stdout to handle unicode glyphs (✓ ✗) on Windows cp1252.
    # Use reconfigure() (Python 3.7+) to preserve the original stream object.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass
    pattern = os.path.join(REPORTS_DIR, "**", "trading_data_*.json")
    files = sorted(glob.glob(pattern, recursive=True))
    if not files:
        print("\n  No trading data found.\n")
        return

    print(f"\n  {'Date':<14} {'Mode':<10} {'Verified':<12} {'Trades':<8} {'Net P&L':>10}")
    print(f"  {'─'*14} {'─'*10} {'─'*12} {'─'*8} {'─'*10}")

    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            date_str = data.get("date", "?")
            mode = data.get("mode", "?")
            verified = "✓ Yes" if data.get("verified") else "✗ No"
            positions = data.get("positions", [])
            closed = [p for p in positions if p.get("status") == "CLOSED"]
            net = data.get("pnl", {}).get("net_profit", 0)
            print(f"  {date_str:<14} {mode:<10} {verified:<12} {len(closed):<8} Rs.{net:>+9.2f}")
        except Exception:
            pass

    print()


# ── Core verification ─────────────────────────────────────────────

def verify_today(date_str: str | None = None, force: bool = False) -> dict:
    """
    Verify trades for the given date (default: today) using Zerodha API.
    Returns stats dict: {verified, corrected, skipped, errors}.
    """
    target_date = datetime.date.fromisoformat(date_str) if date_str else now_ist().date()
    target_str  = target_date.isoformat()

    json_path, txt_path = _trading_file_paths(target_date)
    if not os.path.exists(json_path):
        print(f"\n  ❌ No trading data for {target_str}: {json_path}")
        return {"errors": ["No trading data file"]}

    # Load internal data
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("verified") and not force:
        # JSON already verified — but ledger rows may have been inserted
        # after verification (e.g. fill_intraday_ledger ran later).
        # Stamp any unverified ledger rows that match verified positions.
        _stamp_verified_ledger_rows(data, target_str)
        print(f"\n  ✓ Already verified on {data.get('verified_on', '?')}")
        return {"verified": len([p for p in data.get("positions", []) if p.get("status") == "CLOSED"])}

    if data.get("mode") != "live":
        print(f"\n  ⊘ Skipping — mode is '{data.get('mode')}' (only live trades need verification)")
        return {"skipped": "not live mode"}

    positions = data.get("positions", [])
    closed = [p for p in positions if p.get("status") == "CLOSED"]
    if not closed:
        print(f"\n  ⊘ No closed positions to verify")
        return {"skipped": "no closed positions"}

    # ── Connect to Zerodha ────────────────────────────────────
    log = Logger("verify")
    zerodha = ZerodhaClient(Config, log)

    print(f"\n  Connecting to Zerodha...")
    try:
        zerodha.login(interactive=False)
    except Exception as e:
        print(f"\n  ❌ Cannot login to Zerodha: {e}")
        print(f"     The bot must have logged in today for the token to be valid.")
        return {"errors": [f"Login failed: {e}"]}

    # ── Fetch Zerodha data ────────────────────────────────────
    print(f"  Fetching trades and positions from Zerodha...")

    z_trades = zerodha.get_todays_trades()
    z_positions = zerodha.get_todays_positions()

    if not z_trades and not z_positions:
        print(f"\n  ⚠ No trade data from Zerodha API (token may be expired)")
        return {"errors": ["No data from Zerodha"]}

    # ── Build lookups ─────────────────────────────────────────

    # Group Zerodha trades by order_id (for entry fill prices)
    z_by_order: dict[str, list[dict]] = {}
    for t in z_trades:
        if t.get("product") != "MIS":
            continue
        oid = str(t.get("order_id", ""))
        if oid:
            z_by_order.setdefault(oid, []).append(t)

    # Zerodha positions by symbol (for aggregate P&L verification)
    z_pos_by_sym: dict[str, dict] = {}
    for zp in z_positions:
        if zp.get("product") != "MIS":
            continue
        sym = zp.get("tradingsymbol", "")
        if sym:
            z_pos_by_sym[sym] = zp

    # ── Phase 1: correct entry prices from order fills ────────
    stats = {"verified": 0, "corrected": 0, "no_match": 0}

    for pos in closed:
        order_id = str(pos.get("order_id", ""))
        if order_id and order_id in z_by_order:
            fills = z_by_order[order_id]
            total_qty = sum(f.get("quantity", 0) for f in fills)
            if total_qty > 0:
                wavg = sum(f.get("average_price", 0) * f.get("quantity", 0)
                           for f in fills) / total_qty
                wavg = round(wavg, 2)
                if abs(wavg - pos["entry_price"]) > 0.01:
                    print(f"    ✎ {pos['symbol']}: entry Rs.{pos['entry_price']:.2f}→Rs.{wavg:.2f}")
                    pos["entry_price"] = wavg

    # ── Phase 2: correct exit prices using Zerodha aggregate P&L ─
    # Group closed positions by symbol
    from collections import defaultdict
    sym_positions: dict[str, list[dict]] = defaultdict(list)
    for pos in closed:
        sym_positions[pos["symbol"]].append(pos)

    for symbol, pos_list in sym_positions.items():
        zp = z_pos_by_sym.get(symbol)
        if not zp:
            for pos in pos_list:
                stats["no_match"] += 1
                print(f"    ? {symbol}: no Zerodha position data")
            continue

        z_pnl = round(zp.get("pnl", 0), 2)

        if len(pos_list) == 1:
            # Single trade: use position-level exit price directly
            pos = pos_list[0]
            side = pos.get("side", "BUY")
            old_exit = pos.get("exit_price", 0)

            z_buy_price  = zp.get("buy_price", 0)
            z_sell_price = zp.get("sell_price", 0)
            z_exit = z_sell_price if side == "BUY" else z_buy_price

            changes = []
            if z_exit > 0 and abs(z_exit - old_exit) > 0.01:
                if not pos.get("_partial_qty", 0):
                    changes.append(f"exit Rs.{old_exit:.2f}→Rs.{z_exit:.2f}")
                    pos["exit_price"] = round(z_exit, 2)

            # Recalculate P&L
            qty = pos.get("qty", 0)
            if side == "BUY":
                new_pnl = round((pos["exit_price"] - pos["entry_price"]) * qty, 2)
            else:
                new_pnl = round((pos["entry_price"] - pos["exit_price"]) * qty, 2)

            if abs(new_pnl - pos.get("pnl", 0)) > 0.01:
                changes.append(f"P&L Rs.{pos['pnl']:+,.2f}→Rs.{new_pnl:+,.2f}")
                pos["pnl"] = new_pnl

            if changes:
                stats["corrected"] += 1
                print(f"    ✎ {symbol}: {' | '.join(changes)}")
            else:
                stats["verified"] += 1
                print(f"    ✓ {symbol}: matches Zerodha")

        else:
            # Multiple trades for same symbol — use aggregate P&L
            # to distribute corrections.
            # First, recalculate each trade's P&L from current entry/exit
            for pos in pos_list:
                qty = pos.get("qty", 0)
                if pos["side"] == "BUY":
                    pos["pnl"] = round((pos["exit_price"] - pos["entry_price"]) * qty, 2)
                else:
                    pos["pnl"] = round((pos["entry_price"] - pos["exit_price"]) * qty, 2)

            internal_total = round(sum(p["pnl"] for p in pos_list), 2)
            diff = round(z_pnl - internal_total, 2)

            if abs(diff) <= 0.10:
                # Close enough — consider verified
                for pos in pos_list:
                    stats["verified"] += 1
                    print(f"    ✓ {symbol} ({pos.get('entry_time','?')}): matches Zerodha")
            else:
                # Distribute P&L difference to the last trade's exit price.
                # The last trade is most likely where the discrepancy is
                # (aggregated position prices lose per-trade granularity).
                last_pos = pos_list[-1]
                old_pnl = last_pos["pnl"]
                correction_per_share = diff / last_pos.get("qty", 1)

                if last_pos["side"] == "BUY":
                    old_exit = last_pos["exit_price"]
                    last_pos["exit_price"] = round(old_exit + correction_per_share, 2)
                else:
                    old_exit = last_pos["exit_price"]
                    last_pos["exit_price"] = round(old_exit - correction_per_share, 2)

                # Recalculate last trade's P&L
                qty = last_pos.get("qty", 0)
                if last_pos["side"] == "BUY":
                    last_pos["pnl"] = round((last_pos["exit_price"] - last_pos["entry_price"]) * qty, 2)
                else:
                    last_pos["pnl"] = round((last_pos["entry_price"] - last_pos["exit_price"]) * qty, 2)

                for pos in pos_list[:-1]:
                    stats["verified"] += 1
                    print(f"    ✓ {symbol} ({pos.get('entry_time','?')}): matches Zerodha")

                stats["corrected"] += 1
                print(f"    ✎ {symbol} ({last_pos.get('entry_time','?')}): "
                      f"exit Rs.{old_exit:.2f}→Rs.{last_pos['exit_price']:.2f} | "
                      f"P&L Rs.{old_pnl:+,.2f}→Rs.{last_pos['pnl']:+,.2f} "
                      f"(from Zerodha aggregate {z_pnl:+.2f})")

    # ── Recalculate charges from Zerodha positions ────────────
    # Use Zerodha's actual turnover data for accurate charge calculation
    total_buy = 0.0
    total_sell = 0.0
    for sym, zp in z_pos_by_sym.items():
        b_qty = zp.get("buy_quantity", 0)
        s_qty = zp.get("sell_quantity", 0)
        b_price = zp.get("buy_price", 0)
        s_price = zp.get("sell_price", 0)
        total_buy += b_qty * b_price
        total_sell += s_qty * s_price

    if total_buy > 0 or total_sell > 0:
        total_turnover = round(total_buy + total_sell, 2)
        charges = _compute_charges(total_buy, total_sell, total_turnover, data, z_trades)
        data["pnl"]["charges"] = charges

    # ── Recalculate P&L totals ────────────────────────────────
    gross_pnl = round(sum(p.get("pnl", 0) for p in positions if p.get("status") == "CLOSED"), 2)

    # Cross-check: aggregate Zerodha P&L vs our corrected total
    z_total_pnl = round(sum(zp.get("pnl", 0) for zp in z_pos_by_sym.values()), 2)
    if abs(gross_pnl - z_total_pnl) > 0.50:
        print(f"\n    ⚠ P&L mismatch: internal Rs.{gross_pnl:+.2f} vs Zerodha Rs.{z_total_pnl:+.2f} "
              f"(diff Rs.{gross_pnl - z_total_pnl:+.2f})")
    else:
        print(f"\n    ✓ Gross P&L confirmed: Rs.{gross_pnl:+.2f} (Zerodha: Rs.{z_total_pnl:+.2f})")

    total_costs = data["pnl"]["charges"]["total_costs"]
    net_profit = round(gross_pnl - total_costs, 2)

    tax_rate = Config.TAX_RATE_PCT * (1 + Config.TAX_CESS_PCT / 100) / 100
    tax_rate_pct = round(Config.TAX_RATE_PCT * (1 + Config.TAX_CESS_PCT / 100), 2)
    estimated_tax = round(net_profit * tax_rate, 2) if net_profit > 0 else 0.0

    data["pnl"]["gross_pnl"]        = gross_pnl
    data["pnl"]["net_profit"]       = net_profit
    data["pnl"]["is_profitable"]    = net_profit > 0
    data["pnl"]["tax_rate_pct"]     = tax_rate_pct
    data["pnl"]["estimated_tax"]    = estimated_tax
    data["pnl"]["profit_after_tax"] = round(net_profit - estimated_tax, 2)

    # ── Mark as verified and save ─────────────────────────────
    now_str = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    data["verified"]    = True
    data["verified_on"] = now_str

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    _write_verified_txt(txt_path, data, now_str)

    # ── Update intraday tax ledger ────────────────────────────
    _update_tax_ledger(data, target_str, z_trades)

    # ── Update trades table (performance_tracker) ─────────────
    # The trades table stores entry/exit prices for Claude learning
    # context. When verification corrects prices, sync them here too.
    _update_trades_table(data, target_str)

    print(f"\n  ✅ Verification complete: {stats['verified']} matched, "
          f"{stats['corrected']} corrected")
    print(f"     Reports saved: {json_path}")
    print(f"                    {txt_path}")

    return stats


def _compute_charges(total_buy: float, total_sell: float,
                     total_turnover: float, data: dict,
                     z_trades: list[dict]) -> dict:
    """Compute Zerodha charges from actual turnover values."""
    old_charges = data.get("pnl", {}).get("charges", {})

    api_order_ids = {
        str(t.get("order_id"))
        for t in z_trades
        if t.get("product") == "MIS" and t.get("order_id")
    }
    num_orders = len(api_order_ids) or int(old_charges.get("num_orders", 0) or 0)

    c = Config.calculate_charges(
        total_buy_turnover=total_buy,
        total_sell_turnover=total_sell,
        num_orders=num_orders,
        claude_calls=0,
    )

    claude_api_cost = old_charges.get("claude_api_cost", 0.0)
    total_costs = round(c["total_tax_and_charges"] + claude_api_cost, 2)

    return {
        "total_turnover":        total_turnover,
        "buy_turnover":          round(total_buy, 2),
        "sell_turnover":         round(total_sell, 2),
        "num_orders":            num_orders,
        "brokerage":             c["brokerage"],
        "stt":                   c["stt"],
        "exchange_txn":          c["exchange_txn"],
        "gst":                   c["gst"],
        "sebi_charges":          c["sebi_charges"],
        "stamp_duty":            c["stamp_duty"],
        "total_tax_and_charges": c["total_tax_and_charges"],
        "claude_api_cost":       claude_api_cost,
        "total_costs":           total_costs,
        "zerodha_monthly_fyi":   old_charges.get("zerodha_monthly_fyi", 500.0),
    }


def _stamp_verified_ledger_rows(data: dict, date_str: str):
    """Mark unverified ledger rows as 'verified' when the JSON is already verified.

    This handles the case where ledger rows were inserted *after* the JSON
    was verified (e.g. fill_intraday_ledger ran later).  We match by
    (date, order_id) — the same key used by _update_tax_ledger.
    """
    conn = get_db()
    unverified = conn.execute(
        "SELECT id, order_id FROM intraday_tax_ledger "
        "WHERE date=? AND verified != 'verified'",
        (date_str,),
    ).fetchall()
    if not unverified:
        return

    # Build set of order_ids from verified positions
    position_oids = set()
    for pos in data.get("positions", []):
        if pos.get("status") != "CLOSED":
            continue
        oid = pos.get("order_id", "")
        if oid and oid != "EXTERNAL" and not oid.startswith("DRY_RUN"):
            position_oids.add(oid)

    stamped = 0
    for row in unverified:
        if row["order_id"] in position_oids:
            conn.execute(
                "UPDATE intraday_tax_ledger SET verified='verified' WHERE id=?",
                (row["id"],),
            )
            stamped += 1

    if stamped:
        conn.commit()
        print(f"    ✓ Stamped {stamped} ledger row(s) as verified (post-fill catch-up)")
    conn.close()


def _update_tax_ledger(data: dict, date_str: str, z_trades: list[dict]):
    """Update intraday_tax_ledger rows with corrected prices + charges.

    Matching order:
    1) Exact (date, order_id)

    We intentionally avoid fallback matching by symbol/qty because that can
    overwrite manually reconciled rows that use synthetic IDs.
    """
    conn = get_db()

    positions = data.get("positions", [])
    api_order_ids = {
        str(t.get("order_id"))
        for t in z_trades
        if t.get("product") == "MIS" and t.get("order_id")
    }
    used_ids: set[int] = set()
    updated = 0
    missing = 0

    for pos in positions:
        if pos.get("status") != "CLOSED":
            continue
        order_id = pos.get("order_id", "")
        if not order_id or order_id.startswith("DRY_RUN"):
            continue

        # Runtime-adopted external rows are for live management and may not map
        # 1:1 to independent tax-ledger trades.
        if order_id == "EXTERNAL" or pos.get("_external"):
            continue

        # Do not overwrite ledger rows unless this order_id exists in Zerodha API
        # trade fills for the day. This protects manually reconciled rows that
        # intentionally use synthetic IDs (e.g., cleanup-corrected entries).
        if order_id not in api_order_ids:
            continue

        remaining_qty = pos.get("qty", 0)
        partial_qty   = pos.get("_partial_qty", 0)
        qty = remaining_qty + partial_qty
        if qty <= 0:
            continue

        entry = float(pos.get("entry_price", 0) or 0)
        exit_p = float(pos.get("exit_price", 0) or 0)
        partial_exit = float(pos.get("_partial_exit_price", entry) or entry)
        side = pos.get("side", "BUY")

        if side == "BUY":
            buy_val  = entry * qty
            sell_val = exit_p * remaining_qty + partial_exit * partial_qty
        else:
            sell_val = entry * qty
            buy_val  = exit_p * remaining_qty + partial_exit * partial_qty

        c = Config.calculate_charges(
            total_buy_turnover=buy_val,
            total_sell_turnover=sell_val,
            num_orders=2,
            claude_calls=0,
        )
        gross_pnl = round(pos.get("pnl", 0) + pos.get("_partial_pnl", 0), 2)
        net_pnl = round(gross_pnl - c["total_tax_and_charges"], 2)

        existing = None
        if order_id != "EXTERNAL":
            existing = conn.execute(
                "SELECT id FROM intraday_tax_ledger WHERE date=? AND order_id=?",
                (date_str, order_id),
            ).fetchone()

        if existing:
            row_id = existing["id"]
            conn.execute(
                """UPDATE intraday_tax_ledger
                   SET entry_price=?, exit_price=?, gross_pnl=?,
                       buy_value=?, sell_value=?, turnover=?,
                       brokerage=?, stt=?, exchange_txn=?, gst=?,
                       sebi_charges=?, stamp_duty=?, total_charges=?,
                       net_pnl=?, verified='verified'
                   WHERE id=?""",
                (
                    round(entry, 2), round(exit_p, 2), gross_pnl,
                    round(buy_val, 2), round(sell_val, 2), round(buy_val + sell_val, 2),
                    c["brokerage"], c["stt"], c["exchange_txn"], c["gst"],
                    c["sebi_charges"], c["stamp_duty"], c["total_tax_and_charges"],
                    net_pnl, row_id,
                ),
            )
            used_ids.add(row_id)
            updated += 1
        else:
            # Exact row absent: leave ledger untouched to avoid accidental remapping.
            missing += 1

    conn.commit()
    conn.close()
    print(f"    ✓ Tax ledger sync: {updated} updated, {missing} missing")


def _update_trades_table(data: dict, date_str: str):
    """Sync corrected prices into the trades table (performance_tracker DB).

    The trades table feeds Claude's learning context — if entry/exit prices
    are wrong there, the bot learns from incorrect P&L data. This function
    applies the same corrections that _update_tax_ledger applies to the
    intraday_tax_ledger.
    """
    conn = get_db()
    positions = data.get("positions", [])
    updated = 0
    used_ids: set[int] = set()

    for pos in positions:
        if pos.get("status") != "CLOSED":
            continue
        symbol = pos.get("symbol", "")
        side   = pos.get("side", "")
        if not symbol:
            continue

        # Match by date + symbol + side + qty (no unique order_id in trades table)
        qty = pos.get("qty", 0) + pos.get("_partial_qty", 0)
        pnl = round(pos.get("pnl", 0) + pos.get("_partial_pnl", 0), 2)

        rows = conn.execute(
            "SELECT id, entry_price, exit_price, pnl FROM trades "
            "WHERE date=? AND symbol=? AND side=? AND qty=? "
            "ORDER BY ABS(entry_price - ?)",
            (date_str, symbol, side, qty, pos.get("entry_price", 0)),
        ).fetchall()
        row = None
        for cand in rows:
            if cand["id"] not in used_ids:
                row = cand
                break

        if row:
            needs_update = (
                abs(row["entry_price"] - pos["entry_price"]) > 0.01
                or abs((row["exit_price"] or 0) - (pos.get("exit_price") or 0)) > 0.01
                or abs(row["pnl"] - pnl) > 0.01
            )
            if needs_update:
                conn.execute(
                    "UPDATE trades SET entry_price=?, exit_price=?, pnl=? WHERE id=?",
                    (pos["entry_price"], pos.get("exit_price", 0), pnl, row["id"]),
                )
                updated += 1
            used_ids.add(row["id"])

    if updated:
        conn.commit()
        print(f"    ✎ Updated {updated} row(s) in trades table")
    conn.close()


def _write_verified_txt(txt_path: str, data: dict, verified_on: str):
    """Regenerate the .txt trading report from verified data."""
    SEP_MAJOR = "=" * 58
    SEP_MINOR = "─" * 58
    SEP_TABLE = "─" * 86

    date_str = data["date"]
    mode_label = "DRY RUN (simulated)" if data.get("mode") == "dry_run" else "LIVE TRADING"
    session_count = data.get("sessions", 1)
    config = data.get("config", {})
    budget = config.get("budget", 0)
    market_condition = data.get("market_condition", "")
    positions = data.get("positions", [])
    trade_log = data.get("trade_log", [])
    pnl = data.get("pnl", {})
    charges = pnl.get("charges", {})

    closed = [p for p in positions if p.get("status") == "CLOSED"]
    open_p = [p for p in positions if p.get("status") == "OPEN"]
    winners = [p for p in closed if p.get("pnl", 0) > 0]
    losers  = [p for p in closed if p.get("pnl", 0) < 0]

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"{SEP_MAJOR}\n")
        f.write(f"  ✓ VERIFIED — Data verified via Zerodha API (same-day)\n")
        f.write(f"  Updated on: {verified_on}\n")
        f.write(f"{SEP_MAJOR}\n\n")

        f.write(f"{SEP_MAJOR}\n")
        f.write(f"  INTRADAY TRADING REPORT — {date_str}\n")
        f.write(f"  Mode: {mode_label}\n")
        if session_count > 1:
            f.write(f"  Sessions: {session_count} (combined)\n")
        f.write(f"{SEP_MAJOR}\n\n")

        f.write("CONFIGURATION\n")
        f.write(f"{SEP_MINOR}\n")
        f.write(f"Claude plan     : {config.get('claude_plan', 'PRO').upper()}\n")
        f.write(f"Budget          : Rs.{budget:,.2f} (from Zerodha funds)\n")
        f.write(f"Universe        : {config.get('universe', 'NIFTY100')}\n")
        if market_condition:
            f.write(f"Market condition: {market_condition}\n")
        f.write(f"Max positions   : {config.get('max_positions', 5)}\n")
        f.write(f"Stop-loss       : {config.get('stop_loss_pct', 1.5)}%\n")
        f.write(f"Target          : {config.get('target_pct', 2.0)}%\n")
        f.write(f"Circuit breaker : {Config.MAX_LOSS_PER_DAY_PCT}%\n\n")

        f.write("TRADE SUMMARY\n")
        f.write(f"{SEP_MINOR}\n")
        f.write(f"Total trades    : {len(closed)}\n")
        f.write(f"Winners         : {len(winners)}\n")
        f.write(f"Losers          : {len(losers)}\n")
        f.write(f"Still open      : {len(open_p)}\n")
        if closed:
            win_rate = len(winners) / len(closed) * 100
            f.write(f"Win rate        : {win_rate:.1f}%\n")
        f.write("\n")

        f.write("TRADE DETAILS\n")
        f.write(f"{SEP_TABLE}\n")
        f.write(
            f"{'SYMBOL':<12} {'SIDE':<6} {'QTY':>5} "
            f"{'ENTRY':>10} {'EXIT':>10} {'P&L':>12} "
            f"{'REASON':<14} {'ENTRY_T':<10} {'EXIT_T':<10}\n"
        )
        f.write(f"{SEP_TABLE}\n")

        for p in positions:
            exit_p  = f"Rs.{p['exit_price']:.2f}" if p.get("exit_price") else "—"
            pnl_val = f"Rs.{p.get('pnl', 0):+,.2f}" if p.get("exit_price") else "—"
            f.write(
                f"{p['symbol']:<12} {p['side']:<6} {p['qty']:>5} "
                f"Rs.{p['entry_price']:>9.2f} {exit_p:>10} {pnl_val:>12} "
                f"{(p.get('exit_reason') or 'OPEN'):<14} "
                f"{(p.get('entry_time') or '—'):<10} "
                f"{(p.get('exit_time') or '—'):<10}\n"
            )
        f.write("\n")

        f.write("TRADE RATIONALES\n")
        f.write(f"{SEP_MINOR}\n")
        for p in positions:
            f.write(f"  {p['symbol']}: {p.get('rationale', '—')}\n")
        f.write("\n")

        f.write(f"{SEP_MAJOR}\n")
        f.write("P&L BREAKDOWN\n")
        f.write(f"{SEP_MAJOR}\n\n")

        f.write(f"Gross P&L               : Rs.{pnl['gross_pnl']:+,.2f}\n\n")

        f.write("CHARGES & TAXES:\n")
        f.write(f"  Brokerage             : Rs.{charges['brokerage']:,.2f}\n")
        f.write(f"  STT (sell side)       : Rs.{charges['stt']:,.2f}\n")
        f.write(f"  Exchange transaction  : Rs.{charges['exchange_txn']:,.2f}\n")
        f.write(f"  GST (18%)             : Rs.{charges['gst']:,.2f}\n")
        f.write(f"  SEBI charges          : Rs.{charges['sebi_charges']:,.4f}\n")
        f.write(f"  Stamp duty (buy side) : Rs.{charges['stamp_duty']:,.2f}\n")
        f.write(f"  {'─' * 40}\n")
        f.write(f"  Total tax & charges   : Rs.{charges['total_tax_and_charges']:,.2f}\n\n")

        f.write("CLAUDE API COST:\n")
        f.write(f"  Claude API usage      : Rs.{charges['claude_api_cost']:,.2f}  "
                f"(est. Rs.{Config.CLAUDE_COST_PER_CALL}/call × actual calls)\n")
        f.write(f"  {'─' * 40}\n")
        f.write(f"  Total all costs       : Rs.{charges['total_costs']:,.2f}\n\n")

        f.write(f"{'=' * 42}\n")
        f.write(f"  NET PROFIT AFTER ALL  : Rs.{pnl['net_profit']:+,.2f}\n")
        f.write(f"{'=' * 42}\n")
        profitable = "YES ✓" if pnl["is_profitable"] else "NO ✗"
        f.write(f"  Profitable?           : {profitable}\n")
        if budget > 0:
            returns_pct = pnl["net_profit"] / budget * 100
            f.write(f"  Day returns           : {returns_pct:+.2f}% on Rs.{budget:,.0f} budget\n")
        f.write("\n")

        tax_rate_pct = pnl.get("tax_rate_pct", 0)
        estimated_tax = pnl.get("estimated_tax", 0)
        f.write("ESTIMATED INCOME TAX (speculative business income)\n")
        f.write(f"{SEP_MINOR}\n")
        f.write(f"  Tax slab rate         : {Config.TAX_RATE_PCT}% + "
                f"{Config.TAX_CESS_PCT}% cess = {tax_rate_pct}% effective\n")
        if pnl["net_profit"] > 0:
            f.write(f"  Estimated tax         : Rs.{estimated_tax:,.2f}\n")
            f.write(f"  Profit after tax      : Rs.{pnl['profit_after_tax']:+,.2f}\n")
        else:
            f.write(f"  Estimated tax         : Rs.0.00 (no tax on losses)\n")
            f.write(f"  Loss can be carried forward for 4 years (speculative only)\n")
        f.write("\n")

        f.write(f"  FYI: Zerodha Kite Connect subscription is "
                f"Rs.{Config.ZERODHA_MONTHLY_COST:,.0f}/month (not deducted above).\n")
        f.write(f"  Track cumulative daily profits to ensure they cover "
                f"this monthly cost.\n\n")

        f.write("TURNOVER DETAILS\n")
        f.write(f"{SEP_MINOR}\n")
        f.write(f"  Buy turnover          : Rs.{charges['buy_turnover']:,.2f}\n")
        f.write(f"  Sell turnover         : Rs.{charges['sell_turnover']:,.2f}\n")
        f.write(f"  Total turnover        : Rs.{charges['total_turnover']:,.2f}\n")
        f.write(f"  Total orders          : {charges['num_orders']}\n\n")

        if trade_log:
            f.write("CHRONOLOGICAL TRADE LOG\n")
            f.write(f"{SEP_MINOR}\n")
            for entry in trade_log:
                f.write(
                    f"  [{entry['time']}] {entry['action']:<14} "
                    f"{entry['symbol']:<12} {entry['side']:<5} "
                    f"{entry['qty']:>5}  Rs.{entry['price']:>10}  "
                    f"{entry['detail']}\n"
                )
            f.write("\n")


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Verify trades against Zerodha API (same-day verification).",
    )
    parser.add_argument(
        "date", nargs="?", default=None,
        help="Date in YYYY-MM-DD (default: today). Must be same trading day.",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show verification status for all trading dates.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Run verification even if the day is already marked verified.",
    )
    args = parser.parse_args()

    if args.status:
        _show_status()
        return

    print(f"\n  🔍 Zerodha Trade Verification")
    print(f"  {'─' * 40}")

    stats = verify_today(args.date, force=args.force)

    if stats.get("errors"):
        print(f"\n  ❌ Verification failed: {stats['errors'][0]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
