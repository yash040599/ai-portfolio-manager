"""Background-job runner for "Analyse now" buttons (D26 / D27).

Single-threaded, single-process, single-user — the typical laptop
deployment. The browser polls `/api/run_status?id=...` until the
worker thread completes.

Concurrency model: at most ONE analyse run at a time. A second
"Analyse now" click while a run is in-flight returns the existing
run id (`COALESCE` semantics) instead of spawning a duplicate.

Run state is persisted in a tiny in-memory dict keyed by `run_id`
(monotonic int from a thread-safe counter). The `PortfolioSnapshot`
itself is written to `data/portfolio_analyses.db` by the analyser
exactly as in the CLI flow — the dashboard does not duplicate
storage; it just polls.

Out of scope for this module:
- HTTP routing (lives in `modes/dashboard/server.py`).
- Page rendering (lives in `modes/dashboard/portfolio_page.py`).

This module owns ONLY the worker-thread lifecycle and the in-memory
job-status dict.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any

from config import Config, now_ist
from core.logger import Logger
from modes.analyze.analyser import PortfolioAnalyser


_TOKEN_PATH = os.path.join("data", "access_token.json")


def _has_valid_token_today() -> bool:
    """True when `data/access_token.json` is stamped with today's IST
    date (Zerodha tokens expire at midnight). Used as a pre-flight
    in the worker so the analyser never reaches the interactive
    `input()` prompt that would block a thread with no stdin."""
    if not os.path.exists(_TOKEN_PATH):
        return False
    try:
        with open(_TOKEN_PATH, encoding="utf-8") as f:
            saved = json.load(f)
    except Exception:
        return False
    return saved.get("date") == str(now_ist().date())


# ── Job state ───────────────────────────────────────────────────

@dataclass
class JobStatus:
    job_id: int
    mode: str                       # 'NOAI' | 'AI'
    scope: str                      # 'all' | 'symbol:<X>'
    status: str = "RUNNING"         # RUNNING | DONE | FAILED
    started_at: Any = field(default_factory=now_ist)
    finished_at: Any = None
    error: str = ""
    db_run_id: int | None = None    # link to portfolio_runs.run_id


_LOCK = threading.Lock()
_NEXT_JOB_ID = 1
_JOBS: dict[int, JobStatus] = {}
_ACTIVE_JOB: JobStatus | None = None     # current in-flight job (for de-dupe)


def _next_job_id() -> int:
    global _NEXT_JOB_ID
    with _LOCK:
        i = _NEXT_JOB_ID
        _NEXT_JOB_ID += 1
        return i


# ── Public API ─────────────────────────────────────────────────

def submit_run(*, mode: str, scope: str = "all") -> JobStatus:
    """Submit an analyse run. `mode` is 'NOAI' or 'AI'. `scope` is
    `all` (full portfolio) or `symbol:<SYMBOL>` (targeted drill-down).

    Returns the existing `JobStatus` when a run is already in flight
    (single-job semantics) — the caller must check `status.job_id`
    against the requested run to know whether it was queued or
    coalesced.
    """
    global _ACTIVE_JOB

    with _LOCK:
        if _ACTIVE_JOB is not None and _ACTIVE_JOB.status == "RUNNING":
            return _ACTIVE_JOB
        job = JobStatus(job_id=_next_job_id_unlocked(),
                        mode=str(mode).upper(),
                        scope=str(scope))
        _JOBS[job.job_id] = job
        _ACTIVE_JOB = job

    t = threading.Thread(
        target=_run_job, args=(job,),
        name=f"analyse-job-{job.job_id}", daemon=True,
    )
    t.start()
    return job


def get_status(job_id: int) -> JobStatus | None:
    return _JOBS.get(int(job_id))


def latest_status() -> JobStatus | None:
    """Return the most recently submitted job (for the page poll)."""
    if not _JOBS:
        return None
    return _JOBS[max(_JOBS)]


# ── Internal ────────────────────────────────────────────────────

def _next_job_id_unlocked() -> int:
    """Counter step inside an outer lock — must NOT re-acquire _LOCK."""
    global _NEXT_JOB_ID
    i = _NEXT_JOB_ID
    _NEXT_JOB_ID += 1
    return i


def _run_job(job: JobStatus) -> None:
    """Worker thread. Owns the full analyse run + status update.

    Belt-and-braces: every code path through this function MUST end
    with `job.status` set to either DONE or FAILED before the `finally`
    records `finished_at`. Earlier versions silently left status as
    RUNNING when the analyser's interactive `input()` blocked the
    thread — the pre-flight token check below now refuses to start
    when there's no valid token, surfacing a clear error to the UI.
    """
    global _ACTIVE_JOB
    log = Logger(f"DashboardJob#{job.job_id}")
    sys.stderr.write(f"[dashboard] worker {job.job_id} starting "
                     f"(mode={job.mode}, scope={job.scope})\n")
    try:
        if not _has_valid_token_today():
            raise RuntimeError(
                "No valid Zerodha access token for today. Open the "
                "Login page (Auth: Re-login pill) and complete the "
                "manual paste-back flow first."
            )

        use_ai = (job.mode == "AI")
        runner = PortfolioAnalyser(Config, use_ai=use_ai)
        snapshot = runner.run()
        if snapshot is None:
            raise RuntimeError(
                "Analyse run produced no snapshot. Check the bot log "
                "for details (logs/portfolio.log)."
            )

        from modes.analyze.persistence import latest_run
        r = latest_run()
        if r:
            job.db_run_id = int(r["run_id"])
        job.status = "DONE"
        sys.stderr.write(f"[dashboard] worker {job.job_id} DONE "
                         f"(db_run_id={job.db_run_id})\n")
    except Exception as exc:  # noqa: BLE001 — surface to UI
        log.error(f"Analyse job #{job.job_id} failed: {exc}")
        log.debug(traceback.format_exc())
        job.status = "FAILED"
        job.error = str(exc)[:500]
        sys.stderr.write(f"[dashboard] worker {job.job_id} FAILED: "
                         f"{job.error}\n")
    finally:
        job.finished_at = now_ist()
        with _LOCK:
            if _ACTIVE_JOB is job:
                _ACTIVE_JOB = None


def estimate_ai_cost(holdings_count: int) -> float:
    """Returns rupees-per-run estimate for an AI flow.

    Used by the confirm-banner before launching an "Analyse all (AI)".
    """
    per_call = float(getattr(Config, "CLAUDE_COST_PER_CALL", 3.0))
    return holdings_count * per_call
