from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from cpdatakit.jobs import JobManager, JobStatus


def test_job_manager_records_success_with_basenames_and_operation_log(tmp_path: Path) -> None:
    manager = JobManager(max_workers=1)
    try:
        handle = manager.submit(
            "validate",
            lambda cancel: {"valid": not cancel.is_set()},
            input_path=tmp_path / "private" / "input.csv",
            output_path=tmp_path / "private" / "report.json",
        )

        result = manager.wait(handle.id, timeout=2)

        assert result.status == JobStatus.SUCCEEDED
        assert result.result == {"valid": True}
        assert result.input_filename == "input.csv"
        assert result.output_filename == "report.json"
        assert result.operation_log == ("queued", "running", "succeeded")
        assert result.started_at and result.finished_at
    finally:
        manager.shutdown()


def test_job_manager_cancels_a_cooperative_running_job() -> None:
    manager = JobManager(max_workers=1)
    started = threading.Event()
    try:

        def work(cancel: threading.Event) -> str:
            started.set()
            while not cancel.is_set():
                time.sleep(0.01)
            return "ignored after cancellation"

        handle = manager.submit("long-operation", work)
        assert started.wait(timeout=2)
        assert manager.cancel(handle.id)

        result = manager.wait(handle.id, timeout=2)

        assert result.status == JobStatus.CANCELLED
        assert result.result is None
        assert result.operation_log[-1] == "cancelled"
    finally:
        manager.shutdown()


def test_job_manager_sanitizes_unexpected_errors_and_supports_timeout() -> None:
    manager = JobManager(max_workers=1)
    gate = threading.Event()
    try:

        def work(cancel: threading.Event) -> None:
            gate.wait(timeout=2)
            raise RuntimeError("failed at C:\\private\\secret.csv")

        handle = manager.submit("failing-operation", work)
        with pytest.raises(TimeoutError):
            manager.wait(handle.id, timeout=0.01, raise_timeout=True)
        gate.set()

        result = manager.wait(handle.id, timeout=2)

        assert result.status == JobStatus.FAILED
        assert result.error == "Unexpected job failure."
        assert "private" not in result.error
    finally:
        manager.shutdown()


def test_arbitrary_job_data_is_not_interpreted_as_an_operation_status() -> None:
    manager = JobManager(max_workers=1)
    try:
        handle = manager.submit("read-record", lambda cancel: {"status": "failed", "sample": 7})
        result = manager.wait(handle.id, timeout=2)
        assert result.status == JobStatus.SUCCEEDED
        assert result.result == {"status": "failed", "sample": 7}
    finally:
        manager.shutdown()


def test_queued_cancellation_notifies_callbacks_with_terminal_state() -> None:
    manager = JobManager(max_workers=1)
    gate = threading.Event()
    started = threading.Event()
    observed = []
    try:

        def blocking(cancel):
            started.set()
            gate.wait(timeout=5)

        manager.submit("blocking", blocking)
        assert started.wait(timeout=2)
        handle = manager.submit("queued", lambda cancel: "unused")
        manager.add_done_callback(handle.id, lambda record: observed.append(record.status))
        assert manager.cancel(handle.id)
        assert observed == [JobStatus.CANCELLED]
        assert manager.get(handle.id).operation_log == ("queued", "cancelled")
    finally:
        gate.set()
        manager.shutdown()
