#!/usr/bin/env python3
"""Options Mode — Phase O-2.4 / O-2.5: NIFTY option chain recorder + probe.

WHY THIS IS URGENT
------------------
Every options backtest in this repo so far (v1.0 directional buying, O-3.2
iron condor) priced its premiums with a model, because we have no historical
NIFTY option chain. Kite's `instruments("NFO")` dump lists only *live*
contracts, so an expired weekly series cannot be looked up, cannot be given
an instrument token, and therefore cannot be back-filled at any price.

That makes this dataset strictly forward-accumulating: every trading day this
does not run is a day of option data that is gone permanently. Two months of
daily snapshots is the difference between "we modelled the credit" and "we
measured it".

TWO MODES
---------
  --probe   Answers O-2.5 once: what does Zerodha actually serve for NFO?
            Reports listed expiries, chain depth, whether intraday and daily
            historical candles come back for a live option contract, and how
            far back they go. Run this first; it decides whether the recorder
            below is a stopgap or the only option.

  (default) Snapshots the near-expiry NIFTY chain into data/options.db.
            Safe to run repeatedly; rows are keyed by (snapshot_ts, symbol).

Usage:
    python scripts/trade/record_option_chain.py --probe
    python scripts/trade/record_option_chain.py
    python scripts/trade/record_option_chain.py --expiries 2 --window 0.08
"""
from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from config import Config, now_ist  # noqa: E402
from core.logger import Logger  # noqa: E402
from core.zerodha_client import ZerodhaClient  # noqa: E402

DB_PATH = os.path.join("data", "options.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS option_chain_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_ts   TEXT    NOT NULL,
    trade_date    TEXT    NOT NULL,
    underlying    TEXT    NOT NULL,
    spot          REAL,
    tradingsymbol TEXT    NOT NULL,
    expiry        TEXT    NOT NULL,
    strike        REAL    NOT NULL,
    option_type   TEXT    NOT NULL,
    last_price    REAL,
    bid           REAL,
    ask           REAL,
    volume        INTEGER,
    oi            INTEGER,
    lot_size      INTEGER,
    UNIQUE(snapshot_ts, tradingsymbol)
);
CREATE INDEX IF NOT EXISTS idx_chain_date   ON option_chain_snapshots(trade_date);
CREATE INDEX IF NOT EXISTS idx_chain_expiry ON option_chain_snapshots(expiry, strike);

CREATE TABLE IF NOT EXISTS option_candles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tradingsymbol TEXT    NOT NULL,
    underlying    TEXT    NOT NULL,
    expiry        TEXT    NOT NULL,
    strike        REAL    NOT NULL,
    option_type   TEXT    NOT NULL,
    interval      TEXT    NOT NULL,
    ts_ist        TEXT    NOT NULL,
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL,
    volume        INTEGER,
    lot_size      INTEGER,
    fetched_at    TEXT,
    UNIQUE(tradingsymbol, interval, ts_ist)
);
CREATE INDEX IF NOT EXISTS idx_candles_expiry ON option_candles(expiry, strike, option_type);
CREATE INDEX IF NOT EXISTS idx_candles_ts     ON option_candles(ts_ist);
"""


def ensure_db(path: str = DB_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)


def connect_broker() -> ZerodhaClient:
    missing = Config.validate()
    if missing:
        raise SystemExit(f"  Missing in .env: {', '.join(missing)}")
    z = ZerodhaClient(Config, Logger("OptionChain"))
    z.login()
    return z


def nifty_spot(z: ZerodhaClient) -> float:
    """Spot NIFTY from the index quote."""
    q = z.get_quotes_safe([{"symbol": "NIFTY 50", "exchange": "NSE"}]) or {}
    payload = q.get("NSE:NIFTY 50")
    return float(payload.get("last_price", 0) or 0) if isinstance(payload, dict) else 0.0


# ════════════════════════════════════════════════════════════════════
# PROBE (O-2.5)
# ════════════════════════════════════════════════════════════════════

def probe(z: ZerodhaClient) -> None:
    """Establish exactly what option data Zerodha will give us."""
    print("\n  Zerodha NFO data probe (Phase O-2.5)")
    print(f"  {'='*88}")

    spot = nifty_spot(z)
    print(f"  NIFTY spot: {spot:,.2f}" if spot else "  NIFTY spot: unavailable")

    expiries = z.list_option_expiries("NIFTY")
    print(f"\n  Listed NIFTY option expiries: {len(expiries)}")
    for e in expiries[:8]:
        dte = (e - now_ist().date()).days
        print(f"    {e.isoformat()}  ({dte:+d} days)")
    if len(expiries) > 8:
        print(f"    ... and {len(expiries) - 8} more")

    if not expiries:
        print("\n  No expiries listed — cannot probe further.")
        return

    # Confirm the expiry weekday actually in force. The condor backtest
    # assumes Thursday; if NSE has moved index weeklies, that assumption
    # silently mis-dates every simulated trade.
    weekdays = {e.weekday() for e in expiries[:6]}
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    print(f"\n  Expiry weekday(s) in force: {', '.join(names[w] for w in sorted(weekdays))}")
    print("    -> pass --expiry-weekday to backtest_options_condor.py if not Thu(3)")

    chain = z.get_option_chain("NIFTY", expiries[0], spot=spot or None,
                               strike_window=0.04, with_quotes=True)
    print(f"\n  Nearest-expiry chain ({expiries[0]}): {len(chain)} contracts "
          f"within +/-4% of spot")
    quoted = [c for c in chain if c.get("last_price")]
    print(f"    with live quotes: {len(quoted)}")
    for c in quoted[:6]:
        print(f"    {c['tradingsymbol']:<22} {c['option_type']} {c['strike']:>8,.0f}  "
              f"LTP Rs.{c.get('last_price', 0):>8.2f}  "
              f"bid {c.get('bid', 0):>7.2f} / ask {c.get('ask', 0):>7.2f}  "
              f"OI {c.get('oi', 0):>10,}  lot {c.get('lot_size')}")

    if quoted:
        print(f"\n  Lot size reported by Kite: {quoted[0].get('lot_size')}  "
              f"(config.OPTIONS_NIFTY_LOT_SIZE = {Config.OPTIONS_NIFTY_LOT_SIZE})")

    # ── The decisive question: is there history behind a contract? ──
    if not chain:
        return
    probe_sym = (quoted or chain)[0]["tradingsymbol"]
    today = now_ist().date()
    print(f"\n  Historical data probe on {probe_sym}")
    for label, interval, days in (("daily", "day", 120),
                                  ("15-minute", "15minute", 20),
                                  ("1-minute", "minute", 5)):
        try:
            rows = z.get_historical(
                probe_sym, "NFO",
                today - datetime.timedelta(days=days), today, interval)
            if rows:
                first = rows[0].get("date")
                last = rows[-1].get("date")
                print(f"    {label:<10} {len(rows):>5} bars   {first} .. {last}")
            else:
                print(f"    {label:<10} {'0':>5} bars   (empty response)")
        except Exception as exc:
            print(f"    {label:<10} FAILED: {type(exc).__name__}: {exc}")

    print(f"\n  {'-'*88}")
    print("  How to read this:")
    print("    * History returned for a LIVE contract only proves live series work.")
    print("      Expired weeklies are absent from instruments(\"NFO\"), so they have")
    print("      no resolvable token and cannot be back-filled -> keep recording daily.")
    print("    * If daily bars reach back months, we can reconstruct the life of each")
    print("      contract once recorded, rather than needing intraday snapshots.")
    print(f"  {'='*88}\n")


# ════════════════════════════════════════════════════════════════════
# RECORD (O-2.4)
# ════════════════════════════════════════════════════════════════════

def record(z: ZerodhaClient, *, expiries_ahead: int, window: float,
           db_path: str = DB_PATH) -> int:
    """Snapshot the near-expiry chains into SQLite. Returns rows written."""
    ensure_db(db_path)
    spot = nifty_spot(z)
    if spot <= 0:
        print("  Could not read NIFTY spot — is the market open?")
        return 0

    ts = now_ist().isoformat()
    trade_date = now_ist().date().isoformat()
    expiries = z.list_option_expiries("NIFTY")[:expiries_ahead]
    written = 0

    with sqlite3.connect(db_path) as conn:
        for exp in expiries:
            chain = z.get_option_chain("NIFTY", exp, spot=spot,
                                       strike_window=window, with_quotes=True)
            rows = [
                (ts, trade_date, "NIFTY", spot, c["tradingsymbol"], c["expiry"],
                 c["strike"], c["option_type"], c.get("last_price"), c.get("bid"),
                 c.get("ask"), c.get("volume"), c.get("oi"), c.get("lot_size"))
                for c in chain
            ]
            conn.executemany(
                "INSERT OR IGNORE INTO option_chain_snapshots ("
                "snapshot_ts, trade_date, underlying, spot, tradingsymbol, expiry,"
                "strike, option_type, last_price, bid, ask, volume, oi, lot_size"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            written += len(rows)
            print(f"    {exp}  {len(rows):>4} contracts")

    print(f"\n  Recorded {written} rows at {ts} (spot {spot:,.2f}) -> {db_path}")
    return written


def summarise(db_path: str = DB_PATH) -> None:
    """How much history have we accumulated so far?"""
    if not os.path.exists(db_path):
        print(f"  No database yet at {db_path} — run without --probe to start.")
        return
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT trade_date), MIN(trade_date), "
            "MAX(trade_date) FROM option_chain_snapshots"
        ).fetchone()
        candles = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT tradingsymbol), "
            "COUNT(DISTINCT expiry), MIN(ts_ist), MAX(ts_ist) FROM option_candles"
        ).fetchone()
    total, days, first, last = row
    print(f"\n  Chain snapshots : {total:,} rows across {days} trading day(s) "
          f"[{first} .. {last}]")
    c_rows, c_syms, c_exp, c_first, c_last = candles
    print(f"  Option candles  : {c_rows:,} bars across {c_syms:,} contracts, "
          f"{c_exp} expiries [{(c_first or '')[:10]} .. {(c_last or '')[:10]}]")
    if not c_rows and not total:
        print("  Nothing recorded yet — start with --backfill.")


# ════════════════════════════════════════════════════════════════════
# BACKFILL (O-2.4, the fast path)
# ════════════════════════════════════════════════════════════════════

def backfill(z: ZerodhaClient, *, expiries_ahead: int, window: float,
             interval: str, days: int, db_path: str = DB_PATH) -> int:
    """Fetch the full traded history of every currently-listed contract.

    The probe showed Kite serves historical candles for live NFO contracts
    all the way back to listing. Weeklies list ~a month before expiry, so
    one backfill run captures an entire contract's premium path rather than
    the single daily point a snapshot gives us.

    Still forward-only overall: once a series expires it leaves the
    instrument dump and this can never reach it again. Run before expiry.
    """
    ensure_db(db_path)
    spot = nifty_spot(z)
    if spot <= 0:
        print("  Could not read NIFTY spot — cannot centre the strike window.")
        return 0

    today = now_ist().date()
    start = today - datetime.timedelta(days=days)
    fetched_at = now_ist().isoformat()
    expiries = z.list_option_expiries("NIFTY")[:expiries_ahead]
    written = 0

    with sqlite3.connect(db_path) as conn:
        for exp in expiries:
            chain = z.get_option_chain("NIFTY", exp, spot=spot,
                                       strike_window=window, with_quotes=False)
            print(f"\n    {exp}  {len(chain)} contracts within +/-{window:.0%}")
            exp_rows = 0
            for i, c in enumerate(chain, 1):
                sym = c["tradingsymbol"]
                try:
                    bars = z.get_historical(sym, "NFO", start, today, interval)
                except Exception as exc:
                    print(f"      {sym}: {type(exc).__name__}: {exc}")
                    continue
                rows = [
                    (sym, "NIFTY", c["expiry"], c["strike"], c["option_type"],
                     interval, str(b.get("date")), b.get("open"), b.get("high"),
                     b.get("low"), b.get("close"), b.get("volume") or 0,
                     c.get("lot_size"), fetched_at)
                    for b in bars
                ]
                conn.executemany(
                    "INSERT OR IGNORE INTO option_candles ("
                    "tradingsymbol, underlying, expiry, strike, option_type,"
                    "interval, ts_ist, open, high, low, close, volume,"
                    "lot_size, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                exp_rows += len(rows)
                if i % 20 == 0:
                    conn.commit()
                    print(f"      {i}/{len(chain)} contracts, {exp_rows:,} bars")
            conn.commit()
            written += exp_rows
            print(f"      done: {exp_rows:,} bars")

    print(f"\n  Backfilled {written:,} option bars ({interval}) -> {db_path}")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true",
                    help="report what option data Zerodha serves, then exit")
    ap.add_argument("--backfill", action="store_true",
                    help="fetch full traded history of every listed contract")
    ap.add_argument("--interval", default="day",
                    choices=["day", "60minute", "30minute", "15minute",
                             "5minute", "minute"],
                    help="candle interval for --backfill (default: day)")
    ap.add_argument("--days", type=int, default=120,
                    help="how far back to request for --backfill (default 120)")
    ap.add_argument("--expiries", type=int, default=2,
                    help="how many upcoming expiries to cover (default 2)")
    ap.add_argument("--window", type=float, default=0.06,
                    help="strike window as a fraction of spot (default 0.06)")
    ap.add_argument("--summary", action="store_true",
                    help="show accumulated history and exit")
    args = ap.parse_args()

    if args.summary:
        summarise()
        return

    z = connect_broker()
    if args.probe:
        probe(z)
        return

    if args.backfill:
        print(f"\n  Backfilling {args.interval} candles — {args.expiries} "
              f"expiries, +/-{args.window:.0%} strikes, {args.days}d lookback")
        backfill(z, expiries_ahead=args.expiries, window=args.window,
                 interval=args.interval, days=args.days)
        summarise()
        return

    print(f"\n  Recording NIFTY chain — {args.expiries} expiries, "
          f"+/-{args.window:.0%} strikes")
    record(z, expiries_ahead=args.expiries, window=args.window)
    summarise()


if __name__ == "__main__":
    main()
