"""
Replay clock regression check for Stage 1 T1.1.

This verifies that scanner-style replay scoring uses the injected
historical candle timestamp for session-scoped features such as VWAP and
ORB. Before T1.1, those features looked at today's wall-clock date and
returned empty/NO_DATA for historical candles.
"""

from __future__ import annotations

import datetime
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from scripts.trade.backtest import (  # noqa: E402
    _list_symbols,
    _load_15m,
    _load_day,
    _resolve_candle_source,
    _resolve_daily_source,
    score_bar_scanner,
)


def _find_replay_snapshot() -> tuple[str, datetime.datetime, dict]:
    start = datetime.date(2026, 4, 7)
    end = datetime.date(2026, 4, 24)
    source_kind, intraday_db = _resolve_candle_source(None)
    daily_db = _resolve_daily_source(intraday_db, source_kind)
    symbols = ["RELIANCE"] + [s for s in _list_symbols(intraday_db, source_kind) if s != "RELIANCE"]

    for symbol in symbols:
        candles = _load_15m(symbol, "NSE", start, end, intraday_db, source_kind)
        if len(candles) < 80:
            continue
        daily = _load_day(
            symbol, "NSE", start - datetime.timedelta(days=90), end,
            daily_db, source_kind,
        )
        for idx in range(80, len(candles) - 1):
            bar = candles[idx]
            if not (datetime.time(10, 0) <= bar["ts"].time() <= datetime.time(14, 30)):
                continue
            window = candles[max(0, idx - 80): idx + 1]
            same_day = [c for c in window if c["ts"].date() == bar["ts"].date()]
            if len(same_day) < 5:
                continue
            snapshot = score_bar_scanner(symbol, window, daily, bar["ts"])
            if snapshot:
                return symbol, bar["ts"], snapshot

    raise RuntimeError("No suitable replay snapshot found in backtest data")


def main() -> int:
    symbol, as_of, snapshot = _find_replay_snapshot()
    tech = snapshot.get("technical", {})
    failures = []

    if float(snapshot.get("vwap", 0) or 0) <= 0:
        failures.append("session VWAP is zero")
    if float(tech.get("vwap", {}).get("vwap", 0) or 0) <= 0:
        failures.append("technical VWAP is zero")
    if tech.get("orb", {}).get("signal") in (None, "NO_DATA", "NONE"):
        failures.append(f"ORB did not bind to replay session: {tech.get('orb')}")

    if failures:
        print("FAIL replay clock check")
        print(f"  Snapshot: {symbol} @ {as_of.isoformat()}")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("PASS replay clock check")
    print(f"  Snapshot: {symbol} @ {as_of.isoformat()}")
    print(f"  Score: {snapshot['combined_score']:+.1f}")
    print(f"  VWAP: Rs.{snapshot['vwap']:.2f}")
    print(f"  ORB: {tech['orb']['signal']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())