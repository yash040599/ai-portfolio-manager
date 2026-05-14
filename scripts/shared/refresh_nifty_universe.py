"""
scripts/shared/refresh_nifty_universe.py
========================================

One-shot helper to refresh `shared/nifty_universe.py` from the
official NSE constituent CSVs. Run after every NSE semi-annual
rebalance (March + September circulars) and after any major
demerger / corporate action that changes index membership.

Usage:
    python scripts/shared/refresh_nifty_universe.py

Output is written to `data/_nse_indices.json` for diff review +
prints a side-by-side comparison vs the current shared module.
Does NOT auto-edit `shared/nifty_universe.py` — the operator
should:

  1. Sanity-check the diff (open positions in the dropped names?)
  2. Hand-edit the four tier constants in nifty_universe.py
  3. Re-run this script — second run should report zero changes.

Source URLs (NSE official, updated daily):
  https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv
  https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv
  https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

# Allow running from project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from shared.nifty_universe import (   # noqa: E402
    NIFTY50, NIFTY100_EXTRA, NIFTY150_EXTRA, NIFTY200_EXTRA,
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")
DUMMIES = {"DUMMYVEDL1", "DUMMYVEDL2", "DUMMYVEDL3", "DUMMYVEDL4"}


def _fetch(name: str) -> list[str]:
    url = f"https://nsearchives.nseindia.com/content/indices/ind_{name}.csv"
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8", errors="replace")
    syms = []
    for ln in text.splitlines()[1:]:
        cols = [c.strip().strip('"') for c in ln.split(',')]
        if len(cols) >= 3 and cols[2]:
            syms.append(cols[2])
    return [s for s in syms if s not in DUMMIES]


def main() -> int:
    print("Fetching NSE constituent CSVs ...")
    n50 = _fetch("nifty50list")
    n100 = _fetch("nifty100list")
    n200 = _fetch("nifty200list")
    print(f"  NIFTY 50  : {len(n50)} symbols")
    print(f"  NIFTY 100 : {len(n100)} symbols (post-DUMMYVEDL strip)")
    print(f"  NIFTY 200 : {len(n200)} symbols (post-DUMMYVEDL strip)")

    n100_extra = sorted(set(n100) - set(n50))
    n200_extra_full = sorted(set(n200) - set(n100))
    n150_extra = n200_extra_full[:50]
    n200_extra = n200_extra_full[50:]
    print(f"  Computed NIFTY100_EXTRA: {len(n100_extra)} (expected 50)")
    print(f"  Computed NIFTY150_EXTRA: {len(n150_extra)} (expected 50)")
    print(f"  Computed NIFTY200_EXTRA: {len(n200_extra)} (expected 50)")

    # Diff vs current shared module
    def _diff(label: str, current: list[str], fresh: list[str]) -> None:
        cur_set = set(current); fresh_set = set(fresh)
        added = sorted(fresh_set - cur_set)
        removed = sorted(cur_set - fresh_set)
        print(f"\n[{label}]")
        if not added and not removed:
            print("  no changes")
            return
        if added:
            print(f"  + added ({len(added)}): {', '.join(added)}")
        if removed:
            print(f"  − removed ({len(removed)}): {', '.join(removed)}")

    _diff("NIFTY 50", NIFTY50, n50)
    _diff("NIFTY100_EXTRA", NIFTY100_EXTRA, n100_extra)
    _diff("NIFTY150_EXTRA", NIFTY150_EXTRA, n150_extra)
    _diff("NIFTY200_EXTRA", NIFTY200_EXTRA, n200_extra)

    out = {
        "nifty50_fresh": sorted(n50),
        "nifty100_extra_fresh": n100_extra,
        "nifty150_extra_fresh": n150_extra,
        "nifty200_extra_fresh": n200_extra,
    }
    out_path = os.path.join("data", "_nse_indices.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nFresh data saved to {out_path}")
    print("Edit shared/nifty_universe.py by hand to apply, then "
          "re-run this script — second run should report zero changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
