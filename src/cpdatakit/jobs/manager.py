"""Thread-based local job lifecycle with explicit cancellation ownership."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..exceptions import CPDataKitError, JobError
from ..inspection import sanitize_error_message, sanitize_for_output

JobFunction = Callable[[threading.Event], Any]
logger = logging.getLogger(__name__)


class JobFailure(JobError):
    """An explicitly failed operation whose structured result should be retained."""

    def __init__(self, message: str, *, result: Any) -> None:
        super().__init__(message)
        self.result = result


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class JobHandle:
    id: str


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: str
    operation: str
    status: JobStatus
    started_at: str | None
    finished_at: str | None
    input_filename: str | None
    output_filename: str | None
    operation_log: tuple[str, ...]
    result: Any = None
    error: str | None = None


@dataclass
class _JobState:
    record: JobRecord
    cancel_event: threading.Event
    future: Future[Any] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _basename(path: str | Path | None) -> str | None:
    return Path(path).name if path is not None else None


class JobManager:
    """Own and cancel in-process jobs without exposing thread or callable handles."""

    def __init__(self, *, max_workers: int = 2) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
            raise ValueError("max_workers must be a positive integer")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="cpdatakit-job"
        )
        self._lock = threading.RLock()
        self._jobs: dict[str, _JobState] = {}
        self._closed = False

    def _state(self, job_id: str) -> _JobState:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise JobError(f"Job does not exist: {job_id}") from exc

    def _update(self, state: _JobState, **changes: Any) -> None:
        with self._lock:
            record = state.record
            for item in changes.get("operation_log", ()):
                changes["operation_log"] = (*record.operation_log, item)
            state.record = JobRecord(
                id=record.id,
                operation=record.operation,
                status=changes.get("status", record.status),
                started_at=changes.get("started_at", record.started_at),
                finished_at=changes.get("finished_at", record.finished_at),
                input_filename=record.input_filename,
                output_filename=record.output_filename,
                operation_log=changes.get("operation_log", record.operation_log),
                result=changes.get("result", record.result),
                error=changes.get("error", record.error),
            )

    def _run(self, state: _JobState, function: JobFunction) -> None:
        if state.cancel_event.is_set():
            self._update(
                state, status=JobStatus.CANCELLED, finished_at=_now(), operation_log=("cancelled",)
            )
            return
        self._update(state, status=JobStatus.RUNNING, started_at=_now(), operation_log=("running",))
        try:
            result = function(state.cancel_event)
        except Exception as exc:
            if state.cancel_event.is_set():
                self._update(
                    state,
                    status=JobStatus.CANCELLED,
                    finished_at=_now(),
                    operation_log=("cancelled",),
                    result=None,
                    error=None,
                )
                return
            if isinstance(exc, CPDataKitError):
                message = sanitize_error_message(exc)
            else:
                correlation_id = uuid.uuid4().hex
                logger.exception("Unexpected job failure correlation_id=%s", correlation_id)
                message = "Unexpected job failure."
            self._update(
                state,
                status=JobStatus.FAILED,
                finished_at=_now(),
                operation_log=("failed",),
                result=sanitize_for_output(exc.result) if isinstance(exc, JobFailure) else None,
                error=message,
            )
            return
        if state.cancel_event.is_set():
            self._update(
                state,
                status=JobStatus.CANCELLED,
                finished_at=_now(),
                operation_log=("cancelled",),
                result=None,
                error=None,
            )
            return
        self._update(
            state,
            status=JobStatus.SUCCEEDED,
            finished_at=_now(),
            operation_log=("succeeded",),
            result=sanitize_for_output(result),
            error=None,
        )

    def submit(
        self,
        operation: str,
        function: JobFunction,
        *,
        input_path: str | Path | None = None,
        output_path: str | Path | None = None,
    ) -> JobHandle:
        """Schedule a callable that accepts the owned cancellation event."""

        if not isinstance(operation, str) or not operation.strip():
            raise JobError("Job operation must be non-empty")
        if not callable(function):
            raise JobError("Job function must be callable")
        with self._lock:
            if self._closed:
                raise JobError("Job manager is shut down")
            job_id = uuid.uuid4().hex
            state = _JobState(
                JobRecord(
                    job_id,
                    operation,
                    JobStatus.QUEUED,
                    None,
                    None,
                    _basename(input_path),
                    _basename(output_path),
                    ("queued",),
                ),
                threading.Event(),
            )
            self._jobs[job_id] = state
            state.future = self._executor.submit(self._run, state, function)
            return JobHandle(job_id)

    def get(self, job_id: str) -> JobRecord:
        """Return an immutable snapshot for a known job."""

        return self._state(job_id).record

    def list(self) -> tuple[JobRecord, ...]:
        """Return snapshots in creation order."""

        with self._lock:
            return tuple(state.record for state in self._jobs.values())

    def cancel(self, job_id: str) -> bool:
        """Request cooperative cancellation; return false for terminal jobs."""

        state = self._state(job_id)
        with self._lock:
            if state.record.status in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
            }:
                return False
            state.cancel_event.set()
            if state.future is not None and state.future.cancel():
                self._update(
                    state,
                    status=JobStatus.CANCELLED,
                    finished_at=_now(),
                    operation_log=("cancelled",),
                )
            return True

    def wait(
        self, job_id: str, *, timeout: float | None = None, raise_timeout: bool = False
    ) -> JobRecord:
        """Wait for a job, optionally returning its current snapshot on timeout."""

        state = self._state(job_id)
        if state.future is None:  # pragma: no cover - submit always installs a future
            raise JobError(f"Job has no scheduled future: {job_id}")
        try:
            state.future.result(timeout=timeout)
        except TimeoutError:
            if raise_timeout:
                raise
        return state.record

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait)
