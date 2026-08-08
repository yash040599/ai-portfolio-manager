#!/usr/bin/env python3
"""Options Mode — validate the synthetic premium model against real quotes.

WHY
---
Every options verdict in this repo so far (v1.0 directional buying PF 0.42,
O-3.2 iron condor PF 0.45 OOS) was measured against *modelled* premiums, so
each carried the same caveat: if the model priced options wrongly, the verdict
was wrong too.

`record_option_chain.py --backfill` now gives us real traded premiums. This
script settles the question by pricing the same contracts with
`option_pricing.py` and reporting the error.

HOW TO READ THE RESULT
----------------------
  Model / actual > 1  -> we OVERSTATED premiums. A condor seller would have
                         collected less than the backtest assumed, so the real
                         strategy is WORSE than the PF 0.45 we measured.
  Model / actual < 1  -> we UNDERSTATED premiums. The seller collects more
                         than assumed, so the condor deserves a re-run before
                         it is written off.

Only OTM contracts are compared. ITM premium is dominated by intrinsic value,
which any model gets right by construction and which would flatter the fit.

Usage:
    python scripts/trade/validate_premium_model.py
    python scripts/trade/validate_premium_model.py --iv-uplift 1.0
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
from option_pricing import (  # noqa: E402
    SMILE_CURV, SMILE_SLOPE, parkinson_vol, price_strike, years_to_expiry,
)

DB_PATH = os.path.join("data", "options.db")


def load_option_bars(db_path: str = DB_PATH) -> list[dict]:
    if not os.path.exists(db_path):
        raise SystemExit(f"  No {db_path}. Run record_option_chain.py --backfill first.")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT tradingsymbol, expiry, strike, option_type, ts_ist, close, volume "
            "FROM option_candles WHERE interval='day' AND close > 0 ORDER BY ts_ist"
        ).fetchall()
    return [dict(r) for r in rows]


def load_nifty_spot(z: ZerodhaClient, first: str, last: str) -> dict[str, dict]:
    """NIFTY daily candles keyed by date, for the backfilled window."""
    d0 = datetime.date.fromisoformat(first[:10]) - datetime.timedelta(days=40)
    d1 = datetime.date.fromisoformat(last[:10])
    bars = z.get_historical("NIFTY 50", "NSE", d0, d1, "day")
    out = {}
    for b in bars:
        key = str(b["date"])[:10]
        out[key] = {"open": b["open"], "high": b["high"],
                    "low": b["low"], "close": b["close"]}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iv-uplift", type=float, default=1.35,
                    help="the multiplier the backtest used (default 1.35)")
    ap.add_argument("--min-premium", type=float, default=1.0,
                    help="ignore contracts below this premium (noise)")
    args = ap.parse_args()

    bars = load_option_bars()
    if not bars:
        raise SystemExit("  No option candles recorded yet.")
    print(f"\n  Loaded {len(bars):,} real option bars "
          f"[{bars[0]['ts_ist'][:10]} .. {bars[-1]['ts_ist'][:10]}]")

    z = ZerodhaClient(Config, Logger("ValidateModel"))
    z.login()
    spot_by_date = load_nifty_spot(z, bars[0]["ts_ist"], bars[-1]["ts_ist"])
    dates = sorted(spot_by_date)
    print(f"  NIFTY spot for {len(dates)} sessions")

    # Rolling Parkinson vol needs the bars strictly before each date.
    series = [dict(spot_by_date[d], date=d) for d in dates]
    vol_by_date = {}
    for i, d in enumerate(dates):
        vol_by_date[d] = parkinson_vol(series[:i], 20) if i >= 20 else None

    ratios: list[float] = []
    by_bucket: dict[str, list[float]] = defaultdict(list)
    compared = 0

    for b in bars:
        date = b["ts_ist"][:10]
        spot = spot_by_date.get(date, {}).get("close")
        vol = vol_by_date.get(date)
        if not spot or not vol or b["close"] < args.min_premium:
            continue

        strike, kind = b["strike"], b["option_type"]
        # OTM only — intrinsic would flatter the fit.
        if kind == "CE" and strike <= spot:
            continue
        if kind == "PE" and strike >= spot:
            continue

        exp = datetime.date.fromisoformat(b["expiry"])
        dte = (exp - datetime.date.fromisoformat(date)).days
        if dte < 1:
            continue

        t = years_to_expiry(dte)
        model = price_strike(spot, strike, t, vol * args.iv_uplift, kind,
                             curv=SMILE_CURV, slope=SMILE_SLOPE)
        if model <= 0:
            continue

        ratio = model / b["close"]
        ratios.append(ratio)
        compared += 1

        moneyness = abs(strike - spot) / spot * 100
        bucket = ("0-1% OTM" if moneyness < 1 else
                  "1-2% OTM" if moneyness < 2 else
                  "2-3% OTM" if moneyness < 3 else "3%+ OTM")
        by_bucket[f"{bucket} {kind}"].append(ratio)

    if not ratios:
        raise SystemExit("  No comparable contracts — need more backfilled data.")

    ratios.sort()
    med = statistics.median(ratios)
    print(f"\n  Model / actual premium  (IV uplift {args.iv_uplift}x, "
          f"{compared:,} OTM observations)")
    print(f"  {'='*76}")
    for q in (10, 25, 50, 75, 90):
        print(f"    p{q:<3} = {ratios[int(len(ratios) * q / 100)]:.2f}x")
    print(f"    mean = {statistics.fmean(ratios):.2f}x")

    print("\n  By moneyness (median ratio)")
    print(f"  {'-'*76}")
    for key in sorted(by_bucket):
        vals = by_bucket[key]
        print(f"    {key:<16} n={len(vals):>5}  median={statistics.median(vals):>6.2f}x")

    print(f"\n  {'='*76}")
    if med > 1.15:
        print(f"  Model OVERSTATES premium by ~{(med - 1) * 100:.0f}% at the median.")
        print("  -> A condor seller collects LESS than the backtest assumed.")
        print("  -> The PF 0.45 verdict was OPTIMISTIC; the real strategy is worse.")
    elif med < 0.85:
        print(f"  Model UNDERSTATES premium by ~{(1 - med) * 100:.0f}% at the median.")
        print("  -> A condor seller collects MORE than the backtest assumed.")
        print("  -> Re-run backtest_options_condor.py with a higher --iv-uplift")
        print("     before writing the strategy off.")
    else:
        print(f"  Model is within +/-15% of market (median {med:.2f}x).")
        print("  -> Synthetic-premium verdicts stand; the strategies really do fail.")
    print(f"  {'='*76}\n")


if __name__ == "__main__":
    main()
