"""
Strategy stability-window check (Roadmap #245).

A read-only guardrail that helps the user *not* tune a strategy
subsystem while it is still inside its post-change evaluation window.

Why it exists
-------------
The 2026-04-27 analyst pass found that re-tuning strategy code almost
every trading day made the live ledger uninterpretable: every parameter
change resets the meaningful sample size for the rule that changed.
Roadmap #245 documents the policy; this script is the enforcement
helper. It NEVER modifies code and NEVER blocks anything; it just
prints what is open and what would violate the policy.

Policy (default values; tweak via CLI flags)
--------------------------------------------
* Each shipped strategy / risk / execution change opens a 10-trading-day
  no-tune window for the **subsystem** it touched (entry pipeline, exit
  pipeline, scanner-scoring, risk-gates, budget-regime).
* Bug fixes (no behaviour change in normal market conditions) are
  exempt. Mark them with the literal token ``bugfix-during-stability-window``
  somewhere in the commit **subject line** (the script only scans the
  subject; tokens in the body are not detected).
* Doc-only and test-only commits are ignored.
* Two unrelated subsystems can be tuned in parallel (windows are
  per-subsystem, not global).
* If a #NNNR removal/revert trigger fires inside another item's window,
  the removal wins (data beats process). Mark such commits with
  ``removal-trigger-fired``.

Usage
-----
::

    python scripts/strategy_stability_check.py
    python scripts/strategy_stability_check.py --window-days 10 --lookback 60
    python scripts/strategy_stability_check.py --json   # machine-readable

Exit code is always 0 — this is informational, not a CI gate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Force stdout to UTF-8 on Windows cp1252 consoles so commit-subject
# unicode (—, →, ×, etc.) does not crash the report. Falls back to
# replacement characters if even that fails.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── Subsystem mapping ────────────────────────────────────────────────
# Each strategy-relevant file maps to one or more subsystems. A commit
# that touches the file opens / extends the window for all listed
# subsystems. Keys are POSIX paths relative to the repo root because
# `git log --name-only` emits POSIX separators on every platform.

SUBSYSTEM_FILES: dict[str, tuple[str, ...]] = {
    "services/order_engine.py":      ("entry-pipeline", "exit-pipeline", "risk-gates"),
    "services/stock_scanner_v2.py":  ("scanner-scoring",),
    "services/technical_indicators.py": ("scanner-scoring",),
    "services/candle_patterns.py":   ("scanner-scoring",),
    "portfolio/manager_v2.py":       ("entry-pipeline", "exit-pipeline"),
    "portfolio/manager.py":          ("entry-pipeline", "exit-pipeline"),
    "config.py":                     ("entry-pipeline", "exit-pipeline",
                                      "risk-gates", "scanner-scoring",
                                      "budget-regime"),
}

EXEMPT_TOKENS = ("bugfix-during-stability-window", "removal-trigger-fired")


@dataclass
class Commit:
    sha:         str
    iso_date:    str           # YYYY-MM-DD (commit date, local)
    subject:     str
    files:       list[str]     = field(default_factory=list)
    subsystems:  set[str]      = field(default_factory=set)
    exempt:      bool          = False
    exempt_tag:  str | None    = None


# ── Git helpers ──────────────────────────────────────────────────────

def _git(*args: str) -> str:
    """Run a git command in the project root; return stdout text."""
    return subprocess.check_output(
        ["git", *args],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _load_commits(lookback_days: int) -> list[Commit]:
    """Return commits from the last ``lookback_days`` whose changeset
    intersects ``SUBSYSTEM_FILES``. Most-recent first.

    Uses ``--name-only`` so we can group files per commit safely.
    """
    since = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()
    raw = _git(
        "log",
        f"--since={since}",
        "--date=short",
        # Use NUL-delimited records so commit subjects with newlines
        # cannot bleed into the file list.
        "--format=%x00%H%x09%cd%x09%s",
        "--name-only",
    )
    commits: list[Commit] = []
    current: Commit | None = None
    tracked = set(SUBSYSTEM_FILES.keys())
    for line in raw.split("\n"):
        if line.startswith("\x00"):
            if current is not None:
                commits.append(current)
            payload = line[1:]
            try:
                sha, date_s, subject = payload.split("\t", 2)
            except ValueError:
                # Malformed header — skip, keep going.
                current = None
                continue
            current = Commit(sha=sha[:8], iso_date=date_s, subject=subject)
        elif line.strip() and current is not None:
            current.files.append(line.strip())
    if current is not None:
        commits.append(current)

    # Filter to commits that actually touched a tracked file, and
    # annotate them with subsystem + exemption.
    out: list[Commit] = []
    for c in commits:
        hit_files = [f for f in c.files if f in tracked]
        if not hit_files:
            continue
        c.files = hit_files
        for f in hit_files:
            c.subsystems.update(SUBSYSTEM_FILES[f])
        for tok in EXEMPT_TOKENS:
            if tok in c.subject.lower():
                c.exempt = True
                c.exempt_tag = tok
                break
        out.append(c)
    return out


# ── Trading-day arithmetic (NSE, Mon–Fri, no holiday calendar) ────
# We deliberately do NOT pull a holiday list — the policy is "10
# trading days" approximated as 10 weekdays. Erring on the side of a
# slightly longer window costs nothing; building a holiday-aware
# calendar would invite drift bugs of its own.

def _trading_days_between(start: dt.date, end: dt.date) -> int:
    if end <= start:
        return 0
    days = 0
    d = start
    while d < end:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def _add_trading_days(start: dt.date, n: int) -> dt.date:
    d = start
    added = 0
    while added < n:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


# ── Window computation ───────────────────────────────────────────────

@dataclass
class WindowState:
    subsystem:           str
    last_tune_sha:       str
    last_tune_date:      str
    last_tune_subject:   str
    closes_on:           str    # ISO date
    days_remaining:      int    # trading days


def _open_windows(commits: list[Commit], window_days: int,
                  today: dt.date) -> list[WindowState]:
    """For each subsystem, find the most recent non-exempt tuning commit
    and compute when its window closes."""
    most_recent: dict[str, Commit] = {}
    for c in commits:                         # commits are most-recent-first
        if c.exempt:
            continue
        for s in c.subsystems:
            most_recent.setdefault(s, c)

    states: list[WindowState] = []
    for sub, c in most_recent.items():
        try:
            tune_date = dt.date.fromisoformat(c.iso_date)
        except ValueError:
            continue
        closes = _add_trading_days(tune_date, window_days)
        remaining = _trading_days_between(today, closes)
        if remaining <= 0:
            continue
        states.append(WindowState(
            subsystem        = sub,
            last_tune_sha    = c.sha,
            last_tune_date   = c.iso_date,
            last_tune_subject= c.subject,
            closes_on        = closes.isoformat(),
            days_remaining   = remaining,
        ))
    states.sort(key=lambda s: (-s.days_remaining, s.subsystem))
    return states


@dataclass
class Violation:
    sha:           str
    date:          str
    subject:       str
    subsystem:     str
    earlier_sha:   str
    earlier_date:  str
    earlier_subject: str


def _violations(commits: list[Commit], window_days: int,
                policy_effective: dt.date) -> list[Violation]:
    """A violation is a non-exempt tuning commit that landed inside the
    stability window of an earlier non-exempt tuning commit on the same
    subsystem.

    Only commits dated on or after ``policy_effective`` can BE violations
    — the prior tune that opens the window may pre-date the policy. This
    keeps historical churn out of the report while still catching the
    case where today's commit lands inside a window opened yesterday.

    `commits` is most-recent-first; we walk it in chronological order so
    each commit can be checked against the prior accepted tune per
    subsystem.
    """
    chronological = list(reversed(commits))
    last_tune: dict[str, Commit] = {}
    out: list[Violation] = []
    for c in chronological:
        if c.exempt:
            continue
        try:
            c_date = dt.date.fromisoformat(c.iso_date)
        except ValueError:
            continue
        for sub in c.subsystems:
            prior = last_tune.get(sub)
            if prior is not None:
                try:
                    p_date = dt.date.fromisoformat(prior.iso_date)
                except ValueError:
                    last_tune[sub] = c
                    continue
                gap = _trading_days_between(p_date, c_date)
                if gap < window_days and c_date >= policy_effective:
                    out.append(Violation(
                        sha             = c.sha,
                        date            = c.iso_date,
                        subject         = c.subject,
                        subsystem       = sub,
                        earlier_sha     = prior.sha,
                        earlier_date    = prior.iso_date,
                        earlier_subject = prior.subject,
                    ))
            last_tune[sub] = c
    return out


# ── Rendering ────────────────────────────────────────────────────────

def _render_text(opens: list[WindowState], viols: list[Violation],
                 window_days: int, lookback: int, today: dt.date,
                 policy_effective: dt.date) -> str:
    lines: list[str] = []
    lines.append(f"Strategy-stability check  (today = {today.isoformat()}, "
                 f"window = {window_days} trading days, "
                 f"lookback = {lookback} days, "
                 f"policy effective from {policy_effective.isoformat()})")
    lines.append("=" * 78)

    if not opens:
        lines.append("")
        lines.append("Open windows: none. Every subsystem is free to be tuned.")
    else:
        lines.append("")
        lines.append("Open windows (do NOT tune these subsystems until the close date):")
        lines.append("")
        lines.append(f"  {'Subsystem':<18} {'Last tune':<12} {'Closes':<12} {'Days left':>9}  Commit")
        lines.append(f"  {'-'*18} {'-'*12} {'-'*12} {'-'*9}  {'-'*40}")
        for w in opens:
            subject = w.last_tune_subject
            if len(subject) > 60:
                subject = subject[:57] + "..."
            lines.append(
                f"  {w.subsystem:<18} {w.last_tune_date:<12} "
                f"{w.closes_on:<12} {w.days_remaining:>9}  {w.last_tune_sha} {subject}"
            )

    lines.append("")
    if not viols:
        lines.append("Violations since policy effective: none.")
    else:
        SHOW = 10
        lines.append(f"Violations since policy effective: {len(viols)} "
                     f"(showing first {min(len(viols), SHOW)})")
        lines.append("")
        for v in viols[:SHOW]:
            lines.append(
                f"  {v.date} {v.sha}  [{v.subsystem}]  {v.subject}"
            )
            lines.append(
                f"      tuned again within window opened by "
                f"{v.earlier_date} {v.earlier_sha}: {v.earlier_subject}"
            )
            lines.append("")

    lines.append("")
    lines.append("This check is informational. It never blocks a commit or push.")
    lines.append("Exempt a commit by including 'bugfix-during-stability-window'")
    lines.append("or 'removal-trigger-fired' in its commit subject (Roadmap #245).")
    return "\n".join(lines) + "\n"


def _render_json(opens: list[WindowState], viols: list[Violation],
                 window_days: int, lookback: int, today: dt.date,
                 policy_effective: dt.date) -> str:
    payload = {
        "today":               today.isoformat(),
        "window_days":         window_days,
        "lookback_days":       lookback,
        "policy_effective":    policy_effective.isoformat(),
        "open_windows": [
            {
                "subsystem":          w.subsystem,
                "last_tune_sha":      w.last_tune_sha,
                "last_tune_date":     w.last_tune_date,
                "last_tune_subject":  w.last_tune_subject,
                "closes_on":          w.closes_on,
                "days_remaining":     w.days_remaining,
            }
            for w in opens
        ],
        "violations": [
            {
                "sha":              v.sha,
                "date":             v.date,
                "subject":          v.subject,
                "subsystem":        v.subsystem,
                "earlier_sha":      v.earlier_sha,
                "earlier_date":     v.earlier_date,
                "earlier_subject":  v.earlier_subject,
            }
            for v in viols
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


# ── CLI ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--window-days", type=int, default=10,
                   help="Trading days a strategy change locks its subsystem (default: 10).")
    p.add_argument("--lookback",    type=int, default=60,
                   help="Calendar days of git history to scan (default: 60).")
    p.add_argument("--policy-effective", default=dt.date.today().isoformat(),
                   help="YYYY-MM-DD; only commits on or after this date can BE violations "
                        "(default: today). Prevents historical churn from flooding the report.")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON instead of the text report.")
    args = p.parse_args(argv)

    try:
        policy_effective = dt.date.fromisoformat(args.policy_effective)
    except ValueError:
        print(f"--policy-effective must be YYYY-MM-DD (got {args.policy_effective!r}).",
              file=sys.stderr)
        return 2

    try:
        commits = _load_commits(args.lookback)
    except FileNotFoundError:
        print("git not found on PATH. Stability check skipped.", file=sys.stderr)
        return 0
    except subprocess.CalledProcessError as e:
        print(f"git log failed (exit {e.returncode}). Stability check skipped.",
              file=sys.stderr)
        return 0

    today = dt.date.today()
    opens = _open_windows(commits, args.window_days, today)
    viols = _violations(commits, args.window_days, policy_effective)

    out = (_render_json if args.json
           else _render_text)(opens, viols, args.window_days, args.lookback,
                              today, policy_effective)
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
