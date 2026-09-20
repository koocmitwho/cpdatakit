"""Thread-based local job lifecycle with explicit cancellation ownership."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..exceptions import CPDataKitError, JobError
from ..inspection import sanitize_error_message, sanitize_for_output

type JobFunction = Callable[[threading.Event], Any]
logger = logging.getLogger(__name__)


class JobCancelled(JobError):
    """Cancellation observed at an operation checkpoint."""


class JobContext(threading.Event):
    """An Event compatible context with cooperative progress checkpoints."""

    def __init__(self, on_progress=None):
        super().__init__()
        self.on_progress = on_progress

    def checkpoint(self, stage: str) -> None:
        if self.is_set():
            raise JobCancelled("Operation cancelled before " + stage)
        if self.on_progress is not None:
            self.on_progress(stage)


@dataclass(frozen=True, slots=True)
class CommittedResult:
    """An output has been promoted and registered; later cancellation cannot undo it."""

    value: Any


class JobFailure(JobError):
    """A failed operation with its structured result."""

    def __init__(self, message: str, *, result: Any) -> None:
        super().__init__(message)
        self.result = result


class JobStatus(str, Enum):  # noqa: UP042 - StrEnum changes the public str(JobStatus) contract.
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
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _basename(path: str | Path | None) -> str | None:
    return Path(path).name if path is not None else None


class JobManager:
    """Own and cancel in-process jobs without exposing thread or callable handles."""

    def __init__(
        self,
        *,
        max_workers: int = 2,
        max_pending_jobs: int | None = None,
        max_log_entries: int | None = None,
        max_log_entry_chars: int | None = None,
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
            raise ValueError("max_workers must be a positive integer")
        for name, value in (
            ("max_pending_jobs", max_pending_jobs),
            ("max_log_entries", max_log_entries),
            ("max_log_entry_chars", max_log_entry_chars),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer or None")
        self._max_pending_jobs = max_pending_jobs
        self._max_log_entries = max_log_entries
        self._max_log_entry_chars = max_log_entry_chars
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="cpdatakit-job"
        )
        self._lock = threading.RLock()
        self._jobs: dict[str, _JobState] = {}
        self._closed = False
        self._shutdown_complete = threading.Event()
        self._shutdown_callbacks: list[Callable[[], None]] = []
        self._shutdown_wait_guards: list[Callable[[], bool]] = []
        self._shutdown_guard = threading.Lock()

    def _state(self, job_id: str) -> _JobState:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise JobError(f"Job does not exist: {job_id}") from exc

    def _update(self, state: _JobState, **changes: Any) -> None:
        with self._lock:
            record = state.record
            if "operation_log" in changes:
                entries = tuple(
                    str(item)[: self._max_log_entry_chars] for item in changes["operation_log"]
                )
                log = (*record.operation_log, *entries)
                changes["operation_log"] = (
                    log[-self._max_log_entries :] if self._max_log_entries is not None else log
                )
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
        if isinstance(result, CommittedResult):
            result = result.value
        elif state.cancel_event.is_set():
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
            if (
                self._max_pending_jobs is not None
                and sum(
                    state.record.status in {JobStatus.QUEUED, JobStatus.RUNNING}
                    for state in self._jobs.values()
                )
                >= self._max_pending_jobs
            ):
                raise JobError("Maximum pending job capacity reached; wait for a job to finish")
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
                    ("queued"[: self._max_log_entry_chars],),
                ),
                JobContext(),
            )
            state.cancel_event.on_progress = lambda stage: self._update(
                state, operation_log=(stage,)
            )
            self._jobs[job_id] = state
            state.future = self._executor.submit(self._run, state, function)
            return JobHandle(job_id)

    def add_done_callback(self, job_id: str, callback: Callable[[JobRecord], None]) -> None:
        """Call the subscriber when the job finishes, or immediately if it has finished."""
        state = self._state(job_id)
        if state.future is None:
            raise JobError(f"Job has no scheduled future: {job_id}")

        def notify(future: Future[Any]) -> None:
            if future.cancelled():
                with self._lock:
                    if state.record.status != JobStatus.CANCELLED:
                        self._update(
                            state,
                            status=JobStatus.CANCELLED,
                            finished_at=_now(),
                            operation_log=("cancelled",),
                        )
            callback(state.record)

        state.future.add_done_callback(notify)

    def get(self, job_id: str) -> JobRecord:
        """Return an immutable snapshot for a known job."""

        return self._state(job_id).record

    def list(self) -> tuple[JobRecord, ...]:
        """Return snapshots in creation order."""

        with self._lock:
            return tuple(state.record for state in self._jobs.values())

    def discard(self, job_id: str) -> JobRecord:
        """Release terminal in-memory state after the caller has persisted its result.

        Active jobs cannot be discarded. Callbacks already subscribed to a job
        retain their final snapshot even when another callback discards it.
        """
        with self._lock:
            state = self._state(job_id)
            if state.record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                raise JobError("Only terminal jobs can be discarded")
            del self._jobs[job_id]
            return state.record

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
            future = state.future
        # Future.cancel calls subscribers immediately. Release the job lock first.
        if future is not None and future.cancel():
            with self._lock:
                if state.record.status != JobStatus.CANCELLED:
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

    def add_shutdown_callback(
        self, callback: Callable[[], None], *, can_wait: Callable[[], bool] | None = None
    ) -> None:
        """Close an owned resource after all workers and completion callbacks stop."""
        with self._lock:
            if self._closed:
                raise JobError("Job manager is shut down")
            self._shutdown_callbacks.append(callback)
            if can_wait is not None:
                self._shutdown_wait_guards.append(can_wait)

    @property
    def closed(self) -> bool:
        return self._closed

    def _finish_shutdown(self) -> None:
        with self._shutdown_guard:
            if self._shutdown_complete.is_set():
                return
            self._executor.shutdown(wait=True)
            try:
                for callback in self._shutdown_callbacks:
                    callback()
            finally:
                self._shutdown_complete.set()

    def shutdown(self, *, wait: bool = True) -> None:
        # A request cannot synchronously drain itself. Its finalizer retains all
        # resource ownership until that request leaves the ASGI application.
        wait = wait and all(guard() for guard in self._shutdown_wait_guards)
        with self._lock:
            first = not self._closed
            self._closed = True
        if wait:
            self._finish_shutdown()
        elif first:
            self._executor.shutdown(wait=False)
            threading.Thread(
                target=self._finish_shutdown, name="cpdatakit-close", daemon=True
            ).start()
