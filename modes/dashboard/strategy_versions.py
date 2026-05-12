"""Strategy-version overlay data source (Roadmap D13 / V2 #246).

Reads the per-day `trading_data_DD.json` reports written by
`modes/trade/report_writer.py::save_trading_day` (which stamps
`config.git_sha` at session start) and exposes:

  * `strategy_shas(date_from, date_to)` - {date: short_sha} for every
    trading day in the inclusive window that has a report with a SHA.
  * `commit_subject(sha)` - the commit subject for a SHA, looked up via
    `git log -1 --pretty=%s <sha>`. Cached per-process; returns None
    for SHAs git has GC'd or that never existed in this checkout.
  * `boundaries(shas)` - the subset of dates where the SHA changed vs
    the previous day in the window. These are what the dashboard
    actually overlays as vertical reference lines.

Stdlib only, never raises (silent on missing files, missing git, bad
JSON). Safe to import at dashboard startup even on machines without git.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
from pathlib import Path

# Three .parent hops: strategy_versions.py -> dashboard/ -> modes/ -> root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR  = PROJECT_ROOT / "reports" / "trading"


def _report_path(d: datetime.date) -> Path:
    return REPORTS_DIR / f"{d.year:04d}" / f"{d.month:02d}" / f"trading_data_{d.day:02d}.json"


def strategy_shas(date_from: str, date_to: str) -> dict[str, str]:
    """Map ISO trading date -> short git SHA, for every day with a report.

    Days without a report, or with a report that pre-dates the
    git-stamping change (no `config.git_sha` field), are silently
    skipped. The dashboard's boundary computation handles the gaps.
    """
    try:
        d_from = datetime.date.fromisoformat(date_from)
        d_to   = datetime.date.fromisoformat(date_to)
    except (TypeError, ValueError):
        return {}
    if d_to < d_from:
        return {}

    out: dict[str, str] = {}
    d = d_from
    one_day = datetime.timedelta(days=1)
    while d <= d_to:
        p = _report_path(d)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                sha = (data.get("config") or {}).get("git_sha")
                if isinstance(sha, str) and sha.strip():
                    out[d.isoformat()] = sha.strip()
            except (OSError, json.JSONDecodeError):
                pass
        d += one_day
    return out


_SUBJECT_CACHE: dict[str, str | None] = {}


def commit_subject(sha: str) -> str | None:
    """Return the commit subject for ``sha``, or None if not resolvable.

    Cached per-process. Looks up via `git log -1 --pretty=%s <sha>` so
    a SHA that has been GC'd or rebased away returns None gracefully.
    """
    if not sha:
        return None
    # Defensive: only short or long hex SHAs are valid revspecs. Reject
    # anything else (a hand-edited JSON with e.g. "--output=..." would
    # otherwise be passed to git as a flag). Cheap regex check.
    import re
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", sha):
        return None
    if sha in _SUBJECT_CACHE:
        return _SUBJECT_CACHE[sha]
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%s", sha, "--"],
            cwd=PROJECT_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
            encoding="utf-8",
            errors="replace",
        ).strip()
        _SUBJECT_CACHE[sha] = out or None
    except (FileNotFoundError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, OSError):
        _SUBJECT_CACHE[sha] = None
    return _SUBJECT_CACHE[sha]


def boundaries(shas: dict[str, str]) -> list[dict]:
    """Return the boundary days as a list of {date, sha, subject}.

    A "boundary" is a date whose SHA differs from the immediately
    preceding date that has a recorded SHA in the same map. The first
    dated entry in the map is also a boundary (start of window).
    Dates are visited in ISO order; the date strings sort
    chronologically so a plain sort is enough.
    """
    if not shas:
        return []
    items = sorted(shas.items())
    out: list[dict] = []
    prev_sha: str | None = None
    for date, sha in items:
        if sha != prev_sha:
            out.append({
                "date":    date,
                "sha":     sha,
                "subject": commit_subject(sha),
            })
            prev_sha = sha
    return out


__all__ = ["strategy_shas", "commit_subject", "boundaries"]
