"""
Correct the 2026-05-08 trade record from Zerodha authoritative truth.

Background
==========
On 2026-05-08 around 14:34 the user pressed Ctrl+C on the trade tool with
3 bot-tracked positions still open (TATASTEEL SHORT, TMPV SHORT,
BAJAJHLDNG LONG). User had ALSO manually closed those same 3 positions
seconds earlier on Zerodha web (14:34:25-14:34:34). The bot's polling
hadn't yet detected the manual exits, so its shutdown SQUARE_OFF placed
market orders against already-flat net positions — opening 3 NEW
positions in the OPPOSITE direction. User then manually closed those
"ghost" positions at 14:35:38-14:35:54.

Bug consequences captured in data/trades.db + intraday_tax_ledger:
  1. TATASTEEL/TMPV/BAJAJHLDNG rows show fictional weighted-avg exit
     prices (the bot's RECONCILE step averaged the user's exit fill
     with the bot's own SQUARE_OFF fill).
  2. The 3 ghost round-trips are missing entirely — no row exists.
  3. Net P&L under-reports by Rs.46.15 (Zerodha −491.15 vs DB −445.00).
  4. Regulatory charges under-counted (3 ghost round-trips = 6 extra
     fills not billed in the per-trade ledger).

This script:
  • Loads the authoritative Zerodha tape pulled via kite.trades() into
    data/zerodha_authoritative_2026-05-08.json
  • Walks each symbol's fills chronologically with FIFO matching to
    produce the correct round-trips (12 single-trip + 3 ghost = 15)
  • UPDATEs existing intraday_tax_ledger + trades rows
  • INSERTs 3 new ghost-trip rows
  • Recomputes per-trade Indian-intraday charges via Config.calculate_charges
  • Updates reports/trading/2026/05/trading_data_08.json
  • Appends a RECONCILIATION block to trading_report_08.txt

Idempotent: safe to re-run; checks for existing GHOST rows before insert.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import Config

DATE = "2026-05-08"
ZERODHA_JSON = os.path.join(PROJECT_ROOT, "data", f"zerodha_authoritative_{DATE}.json")
TRADES_DB = os.path.join(PROJECT_ROOT, "data", "trades.db")
TRADING_DATA_PATH = os.path.join(
    PROJECT_ROOT, "reports", "trading", "2026", "05", "trading_data_08.json"
)
TRADING_REPORT_PATH = os.path.join(
    PROJECT_ROOT, "reports", "trading", "2026", "05", "trading_report_08.txt"
)
RECONCILED_TAG = f"reconciled_zerodha_{DATE}"
GHOST_REASON = "BOT_SHUTDOWN_GHOST"


# ─────────────────────────────────────────────────────────────
# Round-trip extraction (FIFO over chronological fills)
# ─────────────────────────────────────────────────────────────

def _ts(s: str) -> str:
    """Extract HH:MM:SS from a Zerodha timestamp like 'YYYY-MM-DD HH:MM:SS'."""
    if not s:
        return ""
    normalised = str(s).replace("T", " ")
    if " " in normalised:
        return normalised.split(" ")[-1][:8]
    return normalised[:8]


def build_roundtrips(fills: list[dict]) -> list[dict]:
    """Walk chronologically, tracking signed running qty.

    Emits one round-trip dict each time the running qty returns to zero.
    Same-direction fills accumulate into the open leg; opposite fills
    accumulate into the close leg.
    """
    fills = sorted(
        fills,
        key=lambda f: (f["fill_timestamp"], f.get("trade_id", "")),
    )
    trips: list[dict] = []
    pos_qty = 0
    open_fills: list[dict] = []
    close_fills: list[dict] = []
    entry_side: str | None = None

    for f in fills:
        side = f["transaction_type"]
        qty = f["quantity"]
        signed = qty if side == "BUY" else -qty

        if pos_qty == 0:
            pos_qty = signed
            open_fills = [f]
            entry_side = side
        elif (pos_qty > 0 and signed > 0) or (pos_qty < 0 and signed < 0):
            pos_qty += signed
            open_fills.append(f)
        else:
            close_fills.append(f)
            pos_qty += signed
            if pos_qty == 0:
                tot_open = sum(o["quantity"] for o in open_fills)
                tot_close = sum(c["quantity"] for c in close_fills)
                if tot_open != tot_close:
                    raise RuntimeError(
                        f"qty mismatch: open={tot_open} close={tot_close} "
                        f"on {open_fills[0]['tradingsymbol']}"
                    )
                entry_px = (
                    sum(o["quantity"] * o["average_price"] for o in open_fills)
                    / tot_open
                )
                exit_px = (
                    sum(c["quantity"] * c["average_price"] for c in close_fills)
                    / tot_close
                )
                if entry_side == "BUY":
                    pnl = (exit_px - entry_px) * tot_open
                    buy_value = entry_px * tot_open
                    sell_value = exit_px * tot_open
                else:
                    pnl = (entry_px - exit_px) * tot_open
                    sell_value = entry_px * tot_open
                    buy_value = exit_px * tot_open
                trips.append(
                    {
                        "symbol": open_fills[0]["tradingsymbol"],
                        "side": entry_side,
                        "qty": tot_open,
                        "entry_price": round(entry_px, 4),
                        "exit_price": round(exit_px, 4),
                        "entry_time": _ts(open_fills[0]["fill_timestamp"]),
                        "exit_time": _ts(close_fills[-1]["fill_timestamp"]),
                        "gross_pnl": round(pnl, 2),
                        "buy_value": round(buy_value, 2),
                        "sell_value": round(sell_value, 2),
                        "open_order_id": open_fills[0]["order_id"],
                        "close_order_id": close_fills[-1]["order_id"],
                    }
                )
                open_fills = []
                close_fills = []
                entry_side = None

    if pos_qty != 0:
        raise RuntimeError(
            f"unclosed position: {open_fills[0]['tradingsymbol']} pos_qty={pos_qty}"
        )
    return trips


# ─────────────────────────────────────────────────────────────
# Charges (Indian intraday — same formula as live bot)
# ─────────────────────────────────────────────────────────────

def trip_charges(trip: dict) -> dict:
    c = Config.calculate_charges(
        total_buy_turnover=trip["buy_value"],
        total_sell_turnover=trip["sell_value"],
        num_orders=2,
    )
    return {
        "buy_value": round(trip["buy_value"], 2),
        "sell_value": round(trip["sell_value"], 2),
        "turnover": round(trip["buy_value"] + trip["sell_value"], 2),
        "brokerage": c["brokerage"],
        "stt": c["stt"],
        "exchange_txn": c["exchange_txn"],
        "gst": c["gst"],
        "sebi_charges": c["sebi_charges"],
        "stamp_duty": c["stamp_duty"],
        "total_charges": c["total_tax_and_charges"],
    }


# ─────────────────────────────────────────────────────────────
# DB updates
# ─────────────────────────────────────────────────────────────

# Symbols where the bot's record is the FIRST round-trip (chronologically)
# and a 2nd ghost trip needs INSERTing. The bot's existing row gets
# UPDATEd to the user's actual exit (not the bot's SQUARE_OFF average).
GHOST_SYMBOLS = {"TATASTEEL", "TMPV", "BAJAJHLDNG"}


def _next_trades_id(cur: sqlite3.Cursor) -> int:
    row = cur.execute("SELECT MAX(id) FROM trades").fetchone()
    return (row[0] or 0) + 1


def update_db(trips_by_symbol: dict[str, list[dict]]) -> dict[str, Any]:
    """Apply UPDATE/INSERT operations to trades and intraday_tax_ledger."""
    summary: dict[str, Any] = {
        "ledger_updated": 0,
        "ledger_inserted": 0,
        "trades_updated": 0,
        "trades_inserted": 0,
        "trips": [],
    }

    conn = sqlite3.connect(TRADES_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    next_trade_id = _next_trades_id(cur)

    for symbol, trips in trips_by_symbol.items():
        primary = trips[0]
        ghost = trips[1] if len(trips) > 1 else None
        ch_p = trip_charges(primary)
        net_p = round(primary["gross_pnl"] - ch_p["total_charges"], 2)

        # ── UPDATE intraday_tax_ledger primary row ──
        existing = cur.execute(
            "SELECT id, exit_reason FROM intraday_tax_ledger "
            "WHERE date=? AND symbol=? AND verified != 'ghost' "
            "ORDER BY id LIMIT 1",
            (DATE, symbol),
        ).fetchone()
        if existing is None:
            print(f"  ! No existing ledger row for {symbol} — skipping primary update")
        else:
            new_reason = primary.get("exit_reason") or existing["exit_reason"]
            if symbol in GHOST_SYMBOLS:
                # User manually closed these on Zerodha web; bot's
                # original SQUARE_OFF reason is misleading.
                new_reason = "EXTERNAL_CLOSE"
            cur.execute(
                """UPDATE intraday_tax_ledger SET
                       side=?, qty=?,
                       entry_price=?, exit_price=?,
                       entry_time=?, exit_time=?, exit_reason=?,
                       gross_pnl=?,
                       buy_value=?, sell_value=?, turnover=?,
                       brokerage=?, stt=?, exchange_txn=?, gst=?,
                       sebi_charges=?, stamp_duty=?, total_charges=?,
                       net_pnl=?, order_id=?, verified=?
                   WHERE id=?""",
                (
                    primary["side"], primary["qty"],
                    primary["entry_price"], primary["exit_price"],
                    primary["entry_time"], primary["exit_time"], new_reason,
                    primary["gross_pnl"],
                    ch_p["buy_value"], ch_p["sell_value"], ch_p["turnover"],
                    ch_p["brokerage"], ch_p["stt"], ch_p["exchange_txn"], ch_p["gst"],
                    ch_p["sebi_charges"], ch_p["stamp_duty"], ch_p["total_charges"],
                    net_p, primary["close_order_id"], RECONCILED_TAG,
                    existing["id"],
                ),
            )
            summary["ledger_updated"] += 1

        # ── UPDATE trades row (primary) ──
        # 'trades' rows for today have id=NULL; key on (date, symbol).
        trade_row = cur.execute(
            "SELECT rowid, id, exit_reason FROM trades "
            "WHERE date=? AND symbol=? AND (exit_reason != 'BOT_SHUTDOWN_GHOST' OR exit_reason IS NULL) "
            "ORDER BY rowid LIMIT 1",
            (DATE, symbol),
        ).fetchone()
        if trade_row is None:
            print(f"  ! No existing trades row for {symbol} — skipping primary update")
        else:
            new_reason = trade_row["exit_reason"]
            if symbol in GHOST_SYMBOLS:
                new_reason = "EXTERNAL_CLOSE"
            assigned_id = trade_row["id"] if trade_row["id"] is not None else next_trade_id
            if trade_row["id"] is None:
                next_trade_id += 1
            cur.execute(
                """UPDATE trades SET
                       id=?, side=?, entry_price=?, exit_price=?,
                       qty=?, pnl=?, exit_reason=?,
                       entry_time=?, exit_time=?
                   WHERE rowid=?""",
                (
                    assigned_id, primary["side"], primary["entry_price"],
                    primary["exit_price"], primary["qty"], primary["gross_pnl"],
                    new_reason,
                    primary["entry_time"], primary["exit_time"],
                    trade_row["rowid"],
                ),
            )
            summary["trades_updated"] += 1

        # ── INSERT ghost-trip rows ──
        if ghost is not None:
            ch_g = trip_charges(ghost)
            net_g = round(ghost["gross_pnl"] - ch_g["total_charges"], 2)

            # idempotency: skip if already present
            already = cur.execute(
                "SELECT 1 FROM intraday_tax_ledger "
                "WHERE date=? AND symbol=? AND exit_reason=?",
                (DATE, symbol, GHOST_REASON),
            ).fetchone()
            if not already:
                cur.execute(
                    """INSERT INTO intraday_tax_ledger
                       (date, symbol, exchange, side, qty,
                        entry_price, exit_price, entry_time, exit_time,
                        exit_reason, gross_pnl,
                        buy_value, sell_value, turnover,
                        brokerage, stt, exchange_txn, gst,
                        sebi_charges, stamp_duty, total_charges,
                        net_pnl, order_id, verified,
                        sheet_verified, sheet_verified_on)
                       VALUES (?,?,?,?,?, ?,?,?,?, ?,?, ?,?,?, ?,?,?,?,
                               ?,?,?, ?,?,?, ?,?)""",
                    (
                        DATE, symbol, "NSE",
                        ghost["side"], ghost["qty"],
                        ghost["entry_price"], ghost["exit_price"],
                        ghost["entry_time"], ghost["exit_time"],
                        GHOST_REASON, ghost["gross_pnl"],
                        ch_g["buy_value"], ch_g["sell_value"], ch_g["turnover"],
                        ch_g["brokerage"], ch_g["stt"], ch_g["exchange_txn"],
                        ch_g["gst"], ch_g["sebi_charges"], ch_g["stamp_duty"],
                        ch_g["total_charges"], net_g,
                        ghost["close_order_id"], RECONCILED_TAG,
                        "pending", None,
                    ),
                )
                summary["ledger_inserted"] += 1

            already_t = cur.execute(
                "SELECT 1 FROM trades WHERE date=? AND symbol=? AND exit_reason=?",
                (DATE, symbol, GHOST_REASON),
            ).fetchone()
            if not already_t:
                cur.execute(
                    """INSERT INTO trades
                       (id, date, symbol, side, entry_price, exit_price,
                        qty, pnl, exit_reason, claude_confidence,
                        market_condition, entry_score, entry_rsi,
                        entry_time, exit_time, indicator_snapshot)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        next_trade_id, DATE, symbol, ghost["side"],
                        ghost["entry_price"], ghost["exit_price"],
                        ghost["qty"], ghost["gross_pnl"], GHOST_REASON,
                        None, None, None, None,
                        ghost["entry_time"], ghost["exit_time"], None,
                    ),
                )
                next_trade_id += 1
                summary["trades_inserted"] += 1

        # collect for caller
        summary["trips"].append((primary, ghost))

    conn.commit()
    conn.close()
    return summary


# ─────────────────────────────────────────────────────────────
# JSON + report file updates
# ─────────────────────────────────────────────────────────────

def _trip_to_position(trip: dict, exit_reason: str) -> dict:
    return {
        "symbol": trip["symbol"],
        "side": trip["side"],
        "qty": trip["qty"],
        "entry_price": trip["entry_price"],
        "exit_price": trip["exit_price"],
        "entry_time": trip["entry_time"],
        "exit_time": trip["exit_time"],
        "exit_reason": exit_reason,
        "pnl": trip["gross_pnl"],
        "exchange": "NSE",
        "status": "CLOSED",
        "order_id": trip["close_order_id"],
    }


def update_trading_data_json(
    trips_by_symbol: dict[str, list[dict]],
) -> tuple[float, float, float, dict]:
    """Rewrite the positions list + pnl block. Returns (gross, charges, net)."""
    with open(TRADING_DATA_PATH) as f:
        data = json.load(f)

    # Preserve original analytic fields by indexing existing positions
    by_symbol_original = {p["symbol"]: p for p in data["positions"]}

    new_positions: list[dict] = []
    total_buy = 0.0
    total_sell = 0.0
    total_gross = 0.0
    total_charges = 0.0
    num_orders = 0

    for symbol, trips in trips_by_symbol.items():
        for idx, trip in enumerate(trips):
            ch = trip_charges(trip)
            total_buy += ch["buy_value"]
            total_sell += ch["sell_value"]
            total_gross += trip["gross_pnl"]
            total_charges += ch["total_charges"]
            num_orders += 2  # entry + exit

            if idx == 0 and symbol in by_symbol_original:
                base = dict(by_symbol_original[symbol])
                base.update(_trip_to_position(
                    trip,
                    "EXTERNAL_CLOSE" if symbol in GHOST_SYMBOLS
                    else base.get("exit_reason", "UNKNOWN"),
                ))
            else:
                base = _trip_to_position(trip, GHOST_REASON)
            new_positions.append(base)

    data["positions"] = new_positions
    aggregated = Config.calculate_charges(
        total_buy_turnover=total_buy,
        total_sell_turnover=total_sell,
        num_orders=num_orders,
    )
    net = round(total_gross - aggregated["total_tax_and_charges"], 2)
    data["pnl"]["gross_pnl"] = round(total_gross, 2)
    data["pnl"]["charges"] = aggregated
    data["pnl"]["net_profit"] = net
    data["pnl"]["is_profitable"] = net > 0
    data["pnl"]["estimated_tax"] = (
        round(net * data["pnl"].get("tax_rate_pct", 30) / 100, 2) if net > 0 else 0
    )
    data["pnl"]["profit_after_tax"] = (
        round(net - data["pnl"]["estimated_tax"], 2) if net > 0 else net
    )
    data["verified"] = "Reconciled with Zerodha tape (kite.trades())"
    data["verified_on"] = datetime.now().isoformat()

    with open(TRADING_DATA_PATH, "w") as f:
        json.dump(data, f, indent=2, default=str)

    return total_gross, aggregated["total_tax_and_charges"], net, aggregated


def append_reconciliation_block(
    gross: float, charges: float, net: float,
    aggregated: dict, summary: dict,
) -> None:
    block = []
    block.append("\n")
    block.append("=" * 79 + "\n")
    block.append("ZERODHA RECONCILIATION  —  applied " + datetime.now().isoformat() + "\n")
    block.append("=" * 79 + "\n")
    block.append("\n")
    block.append(
        "Source: data/zerodha_authoritative_2026-05-08.json (kite.trades() pull)\n"
    )
    block.append(
        "Reason: Bot's shutdown SQUARE_OFF at 14:34:46-48 placed market orders "
        "against\n         already-flat net positions (user had manually exited "
        "TATASTEEL/TMPV/\n         BAJAJHLDNG at 14:34:25-34 on Zerodha web "
        "before pressing Ctrl+C).\n         Those orders OPENED reverse-side "
        "ghost positions which the user then\n         manually closed at "
        "14:35:38-54.\n"
    )
    block.append("\n")
    block.append("CORRECTED ROUND-TRIPS (15 = 12 primary + 3 ghost):\n")
    block.append("-" * 79 + "\n")
    block.append(
        f"  {'Symbol':<12s} {'Side':>4s} {'Qty':>5s} {'Entry':>10s} "
        f"{'Exit':>10s} {'GrossPnL':>10s} {'Reason':<22s}\n"
    )
    for primary, ghost in summary["trips"]:
        for trip, reason in (
            (primary, "EXTERNAL_CLOSE" if primary["symbol"] in GHOST_SYMBOLS
             else "(see report above)"),
            (ghost, GHOST_REASON),
        ):
            if trip is None:
                continue
            block.append(
                f"  {trip['symbol']:<12s} {trip['side']:>4s} {trip['qty']:>5d} "
                f"{trip['entry_price']:>10.4f} {trip['exit_price']:>10.4f} "
                f"{trip['gross_pnl']:>+10.2f} {reason:<22s}\n"
            )
    block.append("-" * 79 + "\n")
    block.append("\n")
    block.append("CORRECTED TOTALS\n")
    block.append(f"  Gross P&L (round-trip sum)      : Rs {gross:>+10.2f}\n")
    block.append(f"  Buy turnover                    : Rs {aggregated['buy_turnover']:>10.2f}\n")
    block.append(f"  Sell turnover                   : Rs {aggregated['sell_turnover']:>10.2f}\n")
    block.append(f"  Total turnover                  : Rs {aggregated['total_turnover']:>10.2f}\n")
    block.append(f"  Number of round-trip orders     :    {aggregated['num_orders']:>10d}\n")
    block.append("\n")
    block.append("REGULATORY CHARGES (recomputed from Zerodha fills)\n")
    block.append(f"  Brokerage  (Rs 20 cap × orders) : Rs {aggregated['brokerage']:>10.2f}\n")
    block.append(f"  STT        (0.025% sell)        : Rs {aggregated['stt']:>10.2f}\n")
    block.append(f"  Exchange   (0.00307% turnover)  : Rs {aggregated['exchange_txn']:>10.2f}\n")
    block.append(f"  GST        (18% on bk+ex+sebi)  : Rs {aggregated['gst']:>10.2f}\n")
    block.append(f"  SEBI       (Rs 10/cr turnover)  : Rs {aggregated['sebi_charges']:>10.4f}\n")
    block.append(f"  Stamp duty (0.003% buy)         : Rs {aggregated['stamp_duty']:>10.2f}\n")
    block.append(f"  TOTAL CHARGES                   : Rs {charges:>10.2f}\n")
    block.append("\n")
    block.append(f"  NET P&L (gross − charges)       : Rs {net:>+10.2f}\n")
    block.append("\n")
    block.append("DELTA vs original report\n")
    block.append("  Gross  : -445.00  →  {:+.2f}   (delta {:+.2f})\n".format(
        gross, gross - (-445.00)))
    block.append("  Charges: 348.34   →  {:.2f}    (delta {:+.2f})\n".format(
        charges, charges - 348.34))
    block.append("  Net    : -793.34  →  {:+.2f}   (delta {:+.2f})\n".format(
        net, net - (-793.34)))
    block.append("\n")
    block.append(
        f"DB updates: {summary['ledger_updated']} ledger UPDATEs, "
        f"{summary['ledger_inserted']} ledger INSERTs, "
        f"{summary['trades_updated']} trades UPDATEs, "
        f"{summary['trades_inserted']} trades INSERTs.\n"
    )
    block.append("=" * 79 + "\n")

    with open(TRADING_REPORT_PATH, "a", encoding="utf-8") as f:
        f.writelines(block)


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main() -> None:
    if not os.path.exists(ZERODHA_JSON):
        sys.exit(
            f"Authoritative tape not found: {ZERODHA_JSON}\n"
            "Run scripts/_tmp_fetch_zerodha_0508.py first."
        )

    with open(ZERODHA_JSON) as f:
        tape = json.load(f)

    fills_by_symbol: dict[str, list[dict]] = {}
    for t in tape["trades"]:
        fills_by_symbol.setdefault(t["tradingsymbol"], []).append(t)

    # Build round-trips per symbol; preserve insertion order from kite.trades()
    trips_by_symbol: dict[str, list[dict]] = {}
    for symbol, fills in fills_by_symbol.items():
        trips_by_symbol[symbol] = build_roundtrips(fills)

    print("=" * 79)
    print("RECONCILIATION PREVIEW")
    print("=" * 79)
    grand_gross = 0.0
    grand_chg = 0.0
    for symbol, trips in trips_by_symbol.items():
        sym_total = sum(t["gross_pnl"] for t in trips)
        sym_chg = sum(trip_charges(t)["total_charges"] for t in trips)
        grand_gross += sym_total
        grand_chg += sym_chg
        marker = "★" if len(trips) > 1 else " "
        print(
            f"  {marker} {symbol:<12s}  trips={len(trips)}  "
            f"gross={sym_total:>+8.2f}  charges={sym_chg:>6.2f}  "
            f"net={sym_total - sym_chg:>+8.2f}"
        )
    print("-" * 79)
    print(f"  TOTAL gross={grand_gross:+.2f}  charges={grand_chg:.2f}  "
          f"net={grand_gross - grand_chg:+.2f}")
    print("=" * 79)

    summary = update_db(trips_by_symbol)
    print()
    print(f"DB update: ledger UPDATE={summary['ledger_updated']} "
          f"INSERT={summary['ledger_inserted']}; "
          f"trades UPDATE={summary['trades_updated']} "
          f"INSERT={summary['trades_inserted']}")

    gross, charges, net, aggregated = update_trading_data_json(trips_by_symbol)
    print(f"trading_data_08.json rewritten: gross={gross:+.2f} "
          f"charges={charges:.2f} net={net:+.2f}")

    append_reconciliation_block(gross, charges, net, aggregated, summary)
    print(f"Reconciliation block appended to {TRADING_REPORT_PATH}")


if __name__ == "__main__":
    main()
