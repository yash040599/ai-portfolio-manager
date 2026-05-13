# ================================================================
# modes/dashboard/swing_actions.py
# ================================================================
# Background job runner for swing scan (Dashboard D31).
#
# Pattern mirrors modes/dashboard/portfolio_actions.py: single-
# threaded, single-flight, the browser polls /api/swing/run_status.
# ================================================================

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


_TOKEN_PATH = os.path.join("data", "access_token.json")


def _has_valid_token_today() -> bool:
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
class SwingJobStatus:
    job_id: int
    mode: str                       # 'NOAI' | 'AI'
    status: str = "RUNNING"         # RUNNING | DONE | FAILED
    started_at: Any = field(default_factory=now_ist)
    finished_at: Any = None
    error: str = ""
    db_run_id: int | None = None
    trigger_source: str = ""
    swing_capital: float = 0.0


_LOCK = threading.Lock()
_NEXT_JOB_ID = 1
_JOBS: dict[int, SwingJobStatus] = {}
_ACTIVE_JOB: SwingJobStatus | None = None


def _next_job_id_unlocked() -> int:
    global _NEXT_JOB_ID
    i = _NEXT_JOB_ID
    _NEXT_JOB_ID += 1
    return i


# ── Public API ─────────────────────────────────────────────────

def submit_swing_run(*, mode: str = "NOAI",
                     trigger_source: str = "DASHBOARD_BUTTON",
                     swing_capital: float = 0.0,
                     ) -> SwingJobStatus:
    """Submit a swing scan. Returns existing job if one is in-flight."""
    global _ACTIVE_JOB

    with _LOCK:
        if _ACTIVE_JOB is not None and _ACTIVE_JOB.status == "RUNNING":
            return _ACTIVE_JOB
        job = SwingJobStatus(
            job_id=_next_job_id_unlocked(),
            mode=str(mode).upper(),
            trigger_source=trigger_source,
            swing_capital=swing_capital,
        )
        _JOBS[job.job_id] = job
        _ACTIVE_JOB = job

    t = threading.Thread(
        target=_run_job, args=(job,),
        name=f"swing-job-{job.job_id}", daemon=True,
    )
    t.start()
    return job


def get_swing_status(job_id: int) -> SwingJobStatus | None:
    return _JOBS.get(int(job_id))


def latest_swing_status() -> SwingJobStatus | None:
    if not _JOBS:
        return None
    return _JOBS[max(_JOBS)]


# ── Worker ─────────────────────────────────────────────────────

def _run_job(job: SwingJobStatus) -> None:
    global _ACTIVE_JOB
    log = Logger(f"SwingJob#{job.job_id}")
    sys.stderr.write(f"[swing] worker {job.job_id} starting "
                     f"(mode={job.mode})\n")
    try:
        if not _has_valid_token_today():
            raise RuntimeError(
                "No valid Zerodha access token for today. Open the "
                "Login page and complete the manual paste-back flow first."
            )

        from modes.swing.manager import SwingManager
        use_ai = (job.mode == "AI")
        runner = SwingManager(Config, use_ai=use_ai)
        capital = job.swing_capital if job.swing_capital > 0 else None
        result = runner.run(trigger_source=job.trigger_source, force=True,
                            swing_capital=capital)

        if result is None:
            job.status = "DONE"
            job.error = "No result (scan skipped or blocked)"
        elif result.blocked_reason:
            job.status = "DONE"
            job.error = result.blocked_reason
        else:
            job.db_run_id = result.run_id
            job.status = "DONE"

        sys.stderr.write(f"[swing] worker {job.job_id} DONE "
                         f"(run_id={job.db_run_id})\n")
    except Exception as exc:
        log.error(f"Swing job #{job.job_id} failed: {exc}")
        log.debug(traceback.format_exc())
        job.status = "FAILED"
        job.error = str(exc)[:500]
        sys.stderr.write(f"[swing] worker {job.job_id} FAILED: "
                         f"{job.error}\n")
    finally:
        job.finished_at = now_ist()
        with _LOCK:
            if _ACTIVE_JOB is job:
                _ACTIVE_JOB = None
