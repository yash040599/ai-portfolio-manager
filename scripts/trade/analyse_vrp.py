#!/usr/bin/env python3
"""Options Mode — measure the NIFTY variance risk premium (VRP).

THE QUESTION THIS ANSWERS
-------------------------
Both option strategies tested so far failed, and the premium model has been
validated against real quotes, so the failures are real. That leaves one
question worth asking before options mode is shelved:

    Does NIFTY implied volatility systematically exceed the volatility that
    actually follows?

That gap is the variance risk premium, and it is the *only* structural reason
option selling makes money anywhere in the world. If NIFTY weeklies carry a
healthy positive VRP, a premium-selling strategy has something real to harvest
and the earlier failures were about structure and sizing. If the VRP is flat or
negative, there is nothing to harvest and options mode should be shelved rather
than paused.

Unlike everything before it, this runs on RECORDED premiums
(`record_option_chain.py --backfill`), not modelled ones.

METHOD
------
For each trading day and each listed expiry:
  1. Take the near-ATM contracts (delta is most reliable, spreads tightest).
  2. Invert Black-Scholes on the real traded close to get implied vol.
  3. Measure the realised close-to-close vol of NIFTY from that day to expiry.
  4. VRP = IV - subsequent realised vol.

Averaging CE and PE at the same strike cancels most of the put/call skew and
any small spot mismatch, giving a cleaner ATM IV than either leg alone.

Usage:
    python scripts/trade/analyse_vrp.py
    python scripts/trade/analyse_vrp.py --max-dte 10
"""
from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import statistics
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config  # noqa: E402
from core.logger import Logger  # noqa: E402
from core.zerodha_client import ZerodhaClient  # noqa: E402
from option_pricing import implied_vol, realised_vol, years_to_expiry  # noqa: E402

DB_PATH = os.path.join("data", "options.db")


def load_bars(db_path: str = DB_PATH) -> list[dict]:
    if not os.path.exists(db_path):
        raise SystemExit(f"  No {db_path}. Run record_option_chain.py --backfill first.")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT expiry, strike, option_type, ts_ist, close "
            "FROM option_candles WHERE interval='day' AND close > 0"
        ).fetchall()
    return [dict(r) for r in rows]


def load_spot(z: ZerodhaClient, first: str, last: str) -> dict[str, float]:
    d0 = datetime.date.fromisoformat(first) - datetime.timedelta(days=10)
    d1 = datetime.date.fromisoformat(last) + datetime.timedelta(days=10)
    bars = z.get_historical("NIFTY 50", "NSE", d0, d1, "day")
    return {str(b["date"])[:10]: float(b["close"]) for b in bars}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-dte", type=int, default=30,
                    help="ignore contracts further out than this (default 30)")
    ap.add_argument("--min-dte", type=int, default=2,
                    help="ignore contracts closer than this (default 2)")
    ap.add_argument("--atm-band", type=float, default=0.01,
                    help="strike within this fraction of spot counts as ATM")
    args = ap.parse_args()

    bars = load_bars()
    dates = sorted({b["ts_ist"][:10] for b in bars})
    print(f"\n  Loaded {len(bars):,} recorded option bars over "
          f"{len(dates)} sessions [{dates[0]} .. {dates[-1]}]")

    z = ZerodhaClient(Config, Logger("VRP"))
    z.login()
    spot = load_spot(z, dates[0], dates[-1])
    spot_dates = sorted(spot)
    print(f"  NIFTY spot for {len(spot_dates)} sessions")

    # Group the ATM prints by (date, expiry) so CE/PE can be averaged.
    atm: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list))
    for b in bars:
        date = b["ts_ist"][:10]
        s = spot.get(date)
        if not s or abs(b["strike"] - s) > s * args.atm_band:
            continue
        iv = implied_vol(
            b["close"], s, b["strike"],
            years_to_expiry(
                (datetime.date.fromisoformat(b["expiry"])
                 - datetime.date.fromisoformat(date)).days),
            b["option_type"],
        )
        if iv:
            atm[(date, b["expiry"])][b["option_type"]].append(iv)

    rows = []
    for (date, expiry), legs in sorted(atm.items()):
        d0 = datetime.date.fromisoformat(date)
        d1 = datetime.date.fromisoformat(expiry)
        dte = (d1 - d0).days
        if not (args.min_dte <= dte <= args.max_dte):
            continue

        ivs = [statistics.median(v) for v in legs.values() if v]
        if not ivs:
            continue
        iv = statistics.fmean(ivs)

        window = [spot[d] for d in spot_dates if date <= d <= expiry]
        rv = realised_vol(window)
        if rv is None:
            continue
        rows.append({"date": date, "expiry": expiry, "dte": dte,
                     "iv": iv, "rv": rv, "vrp": iv - rv})

    if not rows:
        raise SystemExit("  No ATM observations with a complete forward window.")

    vrps = sorted(r["vrp"] for r in rows)
    ivs = [r["iv"] for r in rows]
    rvs = [r["rv"] for r in rows]
    med = statistics.median(vrps)
    positive = sum(1 for v in vrps if v > 0)

    print(f"\n  NIFTY variance risk premium  (ATM, {args.min_dte}-{args.max_dte} DTE, "
          f"{len(rows)} observations)")
    print(f"  {'='*78}")
    print(f"    Mean implied vol   : {statistics.fmean(ivs) * 100:>6.2f}%")
    print(f"    Mean realised vol  : {statistics.fmean(rvs) * 100:>6.2f}%")
    print(f"    Mean VRP           : {statistics.fmean(vrps) * 100:>+6.2f} vol points")
    print(f"    Median VRP         : {med * 100:>+6.2f} vol points")
    print(f"    VRP > 0            : {positive}/{len(rows)} = "
          f"{positive / len(rows) * 100:.1f}% of observations")
    print("\n    VRP percentiles (vol points):")
    for q in (10, 25, 50, 75, 90):
        print(f"      p{q:<3} = {vrps[int(len(vrps) * q / 100)] * 100:>+7.2f}")

    by_dte: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        bucket = ("2-5 DTE" if r["dte"] <= 5 else
                  "6-10 DTE" if r["dte"] <= 10 else
                  "11-20 DTE" if r["dte"] <= 20 else "21-30 DTE")
        by_dte[bucket].append(r["vrp"])
    print("\n    By tenor (median VRP, vol points):")
    for bucket in ("2-5 DTE", "6-10 DTE", "11-20 DTE", "21-30 DTE"):
        v = by_dte.get(bucket)
        print(f"      {bucket:<10} n={len(v):>4}  "
              f"median={statistics.median(v) * 100:>+7.2f}" if v else
              f"      {bucket:<10} n=   0  (no coverage)")

    share_positive = positive / len(rows)
    print(f"\n  {'='*78}")
    # Judge on consistency as well as size: a small premium earned on 4 days
    # out of 5 is a far better foundation than a large one earned on half.
    if med > 0.010 and share_positive > 0.65:
        print(f"  POSITIVE VRP: {med * 100:+.2f} vol points at the median, positive on "
              f"{share_positive * 100:.0f}% of observations.")
        print("  -> Implied consistently exceeds subsequent realised. There IS a")
        print("     premium for a seller to harvest on NIFTY.")
        print("  -> So the condor's failure was about HOW it was sold, not whether")
        print("     there was anything to sell.")
    elif med < -0.005:
        print(f"  NEGATIVE VRP ({med * 100:+.2f} vol points).")
        print("  -> Realised vol EXCEEDS implied: option BUYERS are favoured.")
        print("  -> Selling premium on NIFTY is structurally a losing trade.")
    else:
        print(f"  FLAT VRP ({med * 100:+.2f} vol points, "
              f"{share_positive * 100:.0f}% of observations positive).")
        print("  -> No systematic edge for either buyer or seller.")
        print("  -> Options mode should be SHELVED, not merely paused.")

    # The VRP only means something for the tenor the strategy actually trades.
    short = len(by_dte.get("2-5 DTE", [])) + len(by_dte.get("6-10 DTE", []))
    if short < 20:
        print(f"\n  CAUTION: only {short} observations at 2-10 DTE, which is where")
        print("  backtest_options_condor.py trades. The premium measured above is")
        print("  mostly a LONGER-tenor number and must not be assumed to hold at")
        print("  1-2 DTE, where gamma dominates theta. Keep backfilling weeklies.")

    print(f"\n  Caveat: {len(rows)} observations from a short recorded history in a")
    print("  single volatility regime. Directional, not conclusive.")
    print(f"  {'='*78}\n")


if __name__ == "__main__":
    main()
