import threading

import pytest

from cpdatakit.exceptions import JobError
from cpdatakit.jobs import JobManager, JobStatus


def test_pending_bound_rejects_excess_work_and_releases_capacity_on_completion():
    manager = JobManager(max_workers=1, max_pending_jobs=2)
    started, gate = threading.Event(), threading.Event()
    try:

        def work(context):
            started.set()
            assert gate.wait(5)
            return {"complete": True}

        first = manager.submit("running", work)
        assert started.wait(2)
        second = manager.submit("queued", lambda context: 2)
        with pytest.raises(JobError, match="pending"):
            manager.submit("excess", lambda context: 3)
        gate.set()
        assert manager.wait(first.id, timeout=5).status == JobStatus.SUCCEEDED
        assert manager.wait(second.id, timeout=5).result == 2
        third = manager.submit("capacity-released", lambda context: 3)
        assert manager.wait(third.id, timeout=5).result == 3
        assert len(manager.list()) == 3
    finally:
        gate.set()
        manager.shutdown()


def test_progress_logs_bound_count_and_entry_length_keep_latest_terminal_status():
    manager = JobManager(max_workers=1, max_log_entries=3, max_log_entry_chars=20)
    try:

        def work(context):
            for i in range(100):
                context.checkpoint(f"stage {i}: " + "x" * 500)
            return "complete"

        handle = manager.submit("logged", work)
        record = manager.wait(handle.id, timeout=5)
        assert len(record.operation_log) == 3
        assert record.operation_log[0].startswith("stage 98:")
        assert record.operation_log[1].startswith("stage 99:")
        assert all(len(entry) <= 20 for entry in record.operation_log)
        assert record.operation_log[-1] == "succeeded"
    finally:
        manager.shutdown()


def test_discard_rejects_active_jobs_and_preserves_terminal_callback_result():
    manager = JobManager(max_workers=1)
    gate, started, notified = threading.Event(), threading.Event(), threading.Event()
    snapshots = []
    try:

        def work(context):
            started.set()
            assert gate.wait(5)
            return {"retained": [1, 2, 3]}

        active = manager.submit("active", work)
        assert started.wait(2)
        queued = manager.submit("queued", lambda context: None)
        discard = getattr(manager, "discard", None)
        assert callable(discard), "durable callers need terminal-only release"
        for handle in (active, queued):
            with pytest.raises(JobError, match="terminal"):
                discard(handle.id)

        def complete(record):
            snapshots.append(record)
            snapshots.append(manager.discard(record.id))
            notified.set()

        manager.add_done_callback(active.id, complete)
        gate.set()
        assert notified.wait(5)
        assert snapshots[0] == snapshots[1]
        assert snapshots[0].result == {"retained": [1, 2, 3]}
        with pytest.raises(JobError, match="does not exist"):
            manager.get(active.id)
        with pytest.raises(JobError, match="does not exist"):
            manager.discard(active.id)
        assert manager.wait(queued.id, timeout=5).status == JobStatus.SUCCEEDED
    finally:
        gate.set()
        manager.shutdown()


@pytest.mark.parametrize("option", ["max_pending_jobs", "max_log_entries", "max_log_entry_chars"])
@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "2"])
def test_optional_runtime_bounds_require_positive_integers(option, invalid):
    with pytest.raises(ValueError, match=option):
        JobManager(**{option: invalid})


def test_job_status_retains_legacy_enum_string_and_json_behavior():
    import json

    assert str(JobStatus.SUCCEEDED) == "JobStatus.SUCCEEDED"
    assert json.dumps(JobStatus.SUCCEEDED) == '"succeeded"'
    assert JobStatus.SUCCEEDED == "succeeded"


def test_repeated_durable_completion_callbacks_can_release_all_retained_state():
    manager = JobManager(max_workers=1, max_pending_jobs=2, max_log_entries=3)
    persisted = []
    try:
        for index in range(100):
            notified = threading.Event()
            handle = manager.submit(
                "small-operation", lambda context, value=index: {"value": value}
            )

            def completed(record, finished=notified):
                persisted.append(record.result)
                manager.discard(record.id)
                finished.set()

            manager.add_done_callback(handle.id, completed)
            assert notified.wait(2)
            assert manager.list() == ()
        assert len(persisted) == 100
        assert persisted[0] == {"value": 0}
        assert persisted[-1] == {"value": 99}
    finally:
        manager.shutdown()
