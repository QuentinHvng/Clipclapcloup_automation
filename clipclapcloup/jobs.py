"""Background work with live progress.

Cutting a video takes minutes, so it runs on a thread while the window stays
responsive and polls for progress. Everything happens in-process now, so a
job reports its real state directly instead of a parent parsing a child's
console output.
"""
from __future__ import annotations

import secrets
import threading
import traceback
from datetime import datetime, timezone

RUNNING = "running"
DONE = "done"
ERROR = "error"

_LOCK = threading.RLock()
_JOBS: dict[str, "Job"] = {}
_MAX_LOG_LINES = 400


class Job:
    def __init__(self, kind: str) -> None:
        self.id = secrets.token_hex(6)
        self.kind = kind
        self.status = RUNNING
        self.phase = ""
        self.detail = ""
        self.percent = 0.0
        self.log: list[str] = []
        self.result = None
        self.error: str | None = None
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # -- called from inside the worker ---------------------------------

    def update(self, phase: str | None = None, percent: float | None = None, detail: str | None = None) -> None:
        with _LOCK:
            if phase is not None:
                self.phase = phase
            if detail is not None:
                self.detail = detail
            if percent is not None:
                # never let a bar go backwards; it reads as a bug
                self.percent = max(self.percent, max(0.0, min(100.0, percent)))

    def say(self, line: str) -> None:
        """Add a line to the job's log, shown under 'Details' in the UI."""
        with _LOCK:
            self.log.append(line)
            del self.log[:-_MAX_LOG_LINES]

    # -- read by the HTTP layer ----------------------------------------

    def snapshot(self, since: int = 0) -> dict:
        with _LOCK:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "phase": self.phase,
                "detail": self.detail,
                "percent": round(self.percent, 1),
                "log": self.log[since:],
                "log_total": len(self.log),
                "result": self.result if self.status == DONE else None,
                "error": self.error,
            }


def start(kind: str, target) -> Job:
    """Run target(job) on a background thread and return the Job at once."""
    job = Job(kind)
    with _LOCK:
        _JOBS[job.id] = job

    def runner() -> None:
        try:
            result = target(job)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as the job error
            with _LOCK:
                job.status = ERROR
                job.error = str(exc) or exc.__class__.__name__
            job.say(traceback.format_exc(limit=3))
        else:
            with _LOCK:
                job.status = DONE
                job.result = result
                job.percent = 100.0

    threading.Thread(target=runner, daemon=True, name=f"job-{kind}-{job.id}").start()
    return job


def get(job_id: str) -> Job | None:
    with _LOCK:
        return _JOBS.get(job_id)


def phase_scaler(start_percent: float, end_percent: float):
    """Map a phase's own 0-100 progress onto its slice of the overall bar,
    so the single bar in the UI advances smoothly from start to finish."""
    span = end_percent - start_percent

    def scale(inner_percent: float) -> float:
        return start_percent + span * max(0.0, min(100.0, inner_percent)) / 100

    return scale
