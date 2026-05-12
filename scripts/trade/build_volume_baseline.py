"""
scripts/trade/build_volume_baseline.py
================================================================
Build / refresh per-symbol intraday volume baselines (Roadmap #260).

Replaces the linear daily-volume pro-rating used by the Scanner's
RVol gate with a per-symbol per-hour baseline derived from the last
N completed trading days of 15-min candles in `data/candle_cache.db`.

Usage
-----
    python scripts/trade/build_volume_baseline.py
    python scripts/trade/build_volume_baseline.py --lookback 30
    python scripts/trade/build_volume_baseline.py --universe NIFTY100
    python scripts/trade/build_volume_baseline.py --dry-run

Output: writes `data/volume_baseline.db` with one row per
(symbol, exchange, hour_bucket). After building, flip
`Config.INTRADAY_VOLUME_BASELINE_ENABLED = True` to switch the
scanner over.

Methodology
-----------
For each symbol and each completed trading day in the lookback:
  total_vol_for_day = sum of every 15-min candle's volume that day
  cum_vol[H]        = sum of every 15-min candle's volume up to the
                      end of hour H (inclusive)
  share[H]          = cum_vol[H] / total_vol_for_day
The baseline is the mean(share[H]) over the lookback window. We also
compute the sample stdev so future operators can spot symbols with
unstable curves (e.g. earnings days swamping the mean).

Reads from the existing 15-min candle cache populated by
`shared/candle_cache.py` during normal trading. No network calls.
================================================================
"""

from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import statistics
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

# Force UTF-8 stdout so summary stays readable on Windows cp1252.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import Config  # noqa: E402
from modes.trade.volume_baseline import (  # noqa: E402
    SESSION_HOURS, ensure_db, upsert_baseline, get_baseline_summary,
)
from modes.trade.stock_scanner import StockScanner  # noqa: E402

CANDLE_DB = os.path.join(PROJECT_ROOT, "data", "candle_cache.db")


def _resolve_universe(name: str | None) -> list[str]:
    """Use the same universe loader the live scanner uses."""
    cfg = Config()
    if name:
        cfg.SCAN_UNIVERSE = name.upper()
    # StockScanner expects a config + claude + log; we only call
    # `get_universe()` which doesn't touch network. Stub the deps.
    class _NullClaude:  # noqa: D401
        def __init__(self, *a, **k): pass
    class _NullLog:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
        def debug(self, *a, **k): pass
        def success(self, *a, **k): pass
    s = StockScanner(cfg, _NullClaude(), _NullLog())
    return list(s.get_universe())


def _fetch_candles(symbol: str, exchange: str, lookback_days: int) -> list[dict]:
    """Pull 15-min candles for `symbol` from the candle cache."""
    if not os.path.isfile(CANDLE_DB):
        return []
    cutoff = datetime.date.today() - datetime.timedelta(days=lookback_days + 5)
    with sqlite3.connect(CANDLE_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT candle_date, volume FROM candle_cache
                WHERE symbol = ? AND exchange = ? AND interval = '15minute'
                  AND candle_date >= ?
                ORDER BY candle_date ASC""",
            (symbol, exchange, str(cutoff)),
        ).fetchall()
    out = []
    for r in rows:
        try:
            ts = datetime.datetime.fromisoformat(r["candle_date"])
        except ValueError:
            continue
        out.append({"ts": ts, "volume": int(r["volume"] or 0)})
    return out


def _compute_shares(candles: list[dict]) -> dict[datetime.date, dict[int, float]]:
    """Group by day, compute cumulative-share-by-hour for each day."""
    by_day: dict[datetime.date, list[dict]] = {}
    for c in candles:
        d = c["ts"].date()
        by_day.setdefault(d, []).append(c)
    out: dict[datetime.date, dict[int, float]] = {}
    for d, day_candles in by_day.items():
        day_candles.sort(key=lambda x: x["ts"])
        total = sum(c["volume"] for c in day_candles)
        if total <= 0:
            continue
        # Skip clearly-incomplete days (eg holidays / partial sessions).
        if len(day_candles) < 8:
            continue
        cum: dict[int, float] = {h: 0.0 for h in SESSION_HOURS}
        running = 0.0
        for c in day_candles:
            running += c["volume"]
            h = c["ts"].hour
            for hbar in SESSION_HOURS:
                if h <= hbar:
                    cum[hbar] = max(cum[hbar], running)
        # Convert to share, clamp the last bucket to 1.0.
        share = {h: min(1.0, v / total) for h, v in cum.items()}
        # Make non-decreasing (rounding artifacts).
        last = 0.0
        for h in SESSION_HOURS:
            share[h] = max(share[h], last)
            last = share[h]
        out[d] = share
    return out


def _aggregate_baselines(per_day_shares: dict, lookback_days: int) -> dict[int, tuple[float, float, int]]:
    """
    Return {hour: (mean_share, stdev_share, samples)}. Caps to the
    most recent `lookback_days` complete days.
    """
    sorted_days = sorted(per_day_shares.keys())[-lookback_days:]
    per_hour: dict[int, list[float]] = {h: [] for h in SESSION_HOURS}
    for d in sorted_days:
        for h, share in per_day_shares[d].items():
            if 0.0 < share <= 1.0:
                per_hour[h].append(share)
    out: dict[int, tuple[float, float, int]] = {}
    for h, vals in per_hour.items():
        if not vals:
            continue
        mean = statistics.fmean(vals)
        sd = statistics.pstdev(vals) if len(vals) >= 2 else 0.0
        out[h] = (round(mean, 4), round(sd, 4), len(vals))
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Rebuild data/volume_baseline.db from candle_cache.db (#260)."
    )
    parser.add_argument("--lookback", type=int, default=Config.INTRADAY_VOLUME_BASELINE_LOOKBACK_DAYS,
                        help="Trading days of history to use (default from config).")
    parser.add_argument("--universe", default=None,
                        help="Override SCAN_UNIVERSE for this build.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute and report — do not write to DB.")
    parser.add_argument("--symbol", default=None,
                        help="Build for a single symbol (debugging).")
    args = parser.parse_args()

    if not os.path.isfile(CANDLE_DB):
        print(f"  ! Candle cache not found at {CANDLE_DB}. Run the bot at "
              "least once so 15-min candles get cached.")
        sys.exit(1)

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        symbols = _resolve_universe(args.universe)
    print(f"  Universe: {len(symbols)} symbols   lookback: {args.lookback} days")

    if not args.dry_run:
        ensure_db()

    built_today = str(datetime.date.today())
    n_built = 0
    n_skipped = 0
    sample_floor = max(3, Config.INTRADAY_VOLUME_BASELINE_MIN_SAMPLES // 3)

    for sym in symbols:
        candles = _fetch_candles(sym, "NSE", args.lookback)
        if not candles:
            n_skipped += 1
            continue
        per_day = _compute_shares(candles)
        if len(per_day) < sample_floor:
            n_skipped += 1
            continue
        agg = _aggregate_baselines(per_day, args.lookback)
        if not agg:
            n_skipped += 1
            continue
        if args.dry_run:
            print(f"  {sym:<14} hours={len(agg)}  samples={max(s for _,_,s in agg.values())}")
        else:
            for h, (mean, sd, samples) in agg.items():
                upsert_baseline(sym, "NSE", h, mean, sd, samples, built_today)
        n_built += 1

    info = get_baseline_summary()
    print(f"\n  Built: {n_built} symbol(s)   Skipped: {n_skipped}")
    print(f"  DB    : {info['db']}  rows={info['rows']}  symbols={info['symbols']}")
    if args.dry_run:
        print("  (dry-run — no writes performed)")
    else:
        print("  Done. Set Config.INTRADAY_VOLUME_BASELINE_ENABLED = True "
              "to activate.")


if __name__ == "__main__":
    main()
