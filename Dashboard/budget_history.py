"""Per-day budget history (Roadmap addendum 2026-04-23).

Each trading day's report (`reports/trading/<YYYY>/<MM>/trading_data_<DD>.json`)
records the budget actually used that day under `config.budget` —
this is the value the bot was given via `--max` (or default), not
necessarily the static `Config.MAX_BUDGET_INR`. The dashboard uses
this so the % return for any window is computed against the real
deployed capital, not against today's config.

Pure read-only over the filesystem. Missing days fall back to
`Config.MAX_BUDGET_INR` so the dashboard never crashes on a gap.
"""

from __future__ import annotations

import datetime
import json
import os
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR  = PROJECT_ROOT / "reports" / "trading"


def _path_for(date: str) -> Path:
    """Filesystem path for `reports/trading/<YYYY>/<MM>/trading_data_<DD>.json`."""
    d = datetime.date.fromisoformat(date)
    return REPORTS_DIR / f"{d.year:04d}" / f"{d.month:02d}" / f"trading_data_{d.day:02d}.json"


@lru_cache(maxsize=512)
def budget_for(date: str) -> float | None:
    """Return the budget used on a given trading date, or None when unknown.

    Cached because dashboard rendering may call this once per day in
    the window — repeated FY-wide views shouldn't keep re-reading the
    same JSONs.
    """
    p = _path_for(date)
    if not p.exists():
        return None
    try:
        with p.open(encoding="utf-8") as fh:
            data = json.load(fh)
        cfg = data.get("config") or {}
        b = cfg.get("budget")
        return float(b) if b is not None else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def average_budget(dates: list[str], fallback: float) -> float:
    """Mean of recorded per-day budgets across `dates`, with `fallback`
    substituted for any day missing a report.

    Used to size the % return denominator when the dashboard window
    spans days where the bot was run with different `--max` values.
    """
    if not dates:
        return fallback
    total = 0.0
    for d in dates:
        b = budget_for(d)
        total += b if b is not None else fallback
    return total / len(dates)


__all__ = ["budget_for", "average_budget", "REPORTS_DIR"]
