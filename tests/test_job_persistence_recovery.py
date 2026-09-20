"""Real reports survive catalog outages without a browser polling their details."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_web_runtime_limits import queue_report
from test_web_workflows import _request, _seed_curve

from cpdatakit.exceptions import CatalogError
from cpdatakit.web import create_app


def wait_until(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        threading.Event().wait(0.01)
    assert predicate()


def capture_admission(app, monkeypatch):
    """Capture the actual durable pre-execution state at the registration barrier."""
    registered = {}
    original = app.state.catalog.register_job

    def register(*args, **kwargs):
        record = original(*args, **kwargs)
        assert record.status in {"queued", "running"}
        assert record.result is None
        registered[record.id] = record
        return record

    monkeypatch.setattr(app.state.catalog, "register_job", register)
    return registered


@pytest.mark.parametrize("error_type", [CatalogError, sqlite3.OperationalError, OSError])
def test_report_completion_is_retried_without_any_http_poll(tmp_path, monkeypatch, error_type):
    app = create_app(tmp_path / "workspace")
    failed = threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)
        original = app.state.catalog.update_job

        def transient(*args, **kwargs):
            if kwargs["status"] == "succeeded" and not failed.is_set():
                failed.set()
                raise error_type("temporary catalog outage")
            return original(*args, **kwargs)

        monkeypatch.setattr(app.state.catalog, "update_job", transient)
        job = queue_report(app, home, project, "report.json").json()["job_id"]
        assert failed.wait(5)
        assert app.state.jobs.get(job).status.value == "succeeded"
        assert len(app.state.catalog.list_artifacts(project)) == 1
        wait_until(lambda: app.state.catalog.get_job(job).status == "succeeded")
        record = app.state.catalog.get_job(job)
        assert record.result["artifact"]
        assert len(app.state.catalog.list_artifacts(project)) == 1
    finally:
        app.state.jobs.shutdown()


def test_pending_results_are_observable_retained_and_replayed_on_restart(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    app = create_app(workspace, max_retained_jobs=1, max_pending_jobs=1)
    admitted = capture_admission(app, monkeypatch)
    failed = threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)

        def reject(*args, **kwargs):
            failed.set()
            raise CatalogError("persistent catalog outage")

        monkeypatch.setattr(app.state.catalog, "update_job", reject)
        job = queue_report(app, home, project, "one.json").json()["job_id"]
        assert failed.wait(5)
        detail = _request(app, "GET", f"/api/projects/{project}?limit=50").json()
        row = next(row for row in detail["jobs"] if row["id"] == job)
        assert row["status"] == "succeeded"
        assert row.get("persistence", {}).get("state") == "pending"
        assert row["persistence"]["evidence_saved"] is True
        pending_page = _request(app, "GET", f"/projects/{project}").text
        assert 'data-persistence="pending"' in pending_page
        assert "结果等待保存" in pending_page
        second = queue_report(app, home, project, "two.json").json()["job_id"]
        app.state.jobs.wait(second, timeout=5, raise_timeout=True)
        assert queue_report(app, home, project, "three.json").status_code == 503
        assert len(app.state.jobs.list()) == 2
        start = time.monotonic()
        app.state.jobs.shutdown()
        assert time.monotonic() - start < 3
        assert app.state.catalog.get_job(job) == admitted[job]
    finally:
        app.state.jobs.shutdown()
    restarted = create_app(workspace)
    try:
        for identifier in (job, second):
            result = _request(restarted, "GET", f"/api/jobs/{identifier}").json()
            assert result["status"] == "succeeded"
            assert result["result"]["artifact"]
            assert restarted.state.catalog.get_job(identifier).status == "succeeded"
        assert len(restarted.state.catalog.list_artifacts(project)) == 2
    finally:
        restarted.state.jobs.shutdown()


def test_process_exit_between_completion_evidence_and_catalog_save_replays_result(tmp_path):
    script = r"""
import os, sys
from pathlib import Path
from test_web_workflows import _seed_curve
from test_web_runtime_limits import queue_report
from cpdatakit.web import create_app
app = create_app(Path(sys.argv[1]))
home, project = _seed_curve(app, Path(sys.argv[1]))
original = app.state.catalog.update_job
def exit_before_save(*args, **kwargs):
    if kwargs['status'] == 'succeeded':
        assert len(app.state.catalog.list_artifacts(project)) == 1
        os._exit(24)
    return original(*args, **kwargs)
app.state.catalog.update_job = exit_before_save
queue_report(app, home, project, 'report.json')
app.state.jobs.shutdown()
raise AssertionError('completion checkpoint was not reached')
"""
    repository = Path(__file__).parents[1]
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(map(str, (repository / "src", repository / "tests"))),
        },
    )
    assert result.returncode == 24, result.stderr
    assert len(list((tmp_path / ".job-completions").glob("*.json"))) == 1
    app = create_app(tmp_path)
    try:
        (record,) = app.state.catalog.list_jobs(1)
        assert record.status == "succeeded"
        assert record.result["artifact"]
        assert (tmp_path / record.result["artifact"]).is_file()
        assert len(app.state.catalog.list_artifacts(1)) == 1
    finally:
        app.state.jobs.shutdown()


def test_both_storage_failures_are_visible_and_shutdown_is_bounded(tmp_path, monkeypatch):
    import cpdatakit.web.persistence as module

    app = create_app(tmp_path / "workspace")
    admitted = capture_admission(app, monkeypatch)
    failed = threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)

        def no_evidence(*args, **kwargs):
            raise OSError("Disk unavailable")

        def no_catalog(*args, **kwargs):
            failed.set()
            raise CatalogError("Catalog unavailable")

        monkeypatch.setattr(module, "atomic_json", no_evidence)
        monkeypatch.setattr(app.state.catalog, "update_job", no_catalog)
        job = queue_report(app, home, project, "report.json").json()["job_id"]
        assert failed.wait(5)
        payload = _request(app, "GET", f"/api/jobs/{job}").json()
        assert payload["status"] == "succeeded"
        assert payload["persistence"]["state"] == "pending"
        assert payload["persistence"]["evidence_saved"] is False
        wait_until(lambda: app.state.persistence.status(job)["attempts"] == 6)
        # No detail polling is needed to exhaust the independent bounded retry.
        assert app.state.catalog.get_job(job) == admitted[job]
        start = time.monotonic()
        app.state.close()
        assert time.monotonic() - start < 3
    finally:
        app.state.jobs.shutdown()


def test_completion_arriving_during_retry_worker_exit_gets_its_own_retries(tmp_path, monkeypatch):
    app = create_app(tmp_path / "workspace")
    admitted = capture_admission(app, monkeypatch)
    exited, release = threading.Event(), threading.Event()
    new_failed = threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)
        retry = app.state.persistence._retry

        def final_gap():
            retry()
            exited.set()
            assert release.wait(10)

        monkeypatch.setattr(app.state.persistence, "_retry", final_gap)
        update = app.state.catalog.update_job

        def fail(*args, **kwargs):
            artifact = (kwargs.get("result") or {}).get("artifact", "")
            if artifact.endswith("old.json"):
                raise CatalogError("persistent old result outage")
            if artifact.endswith("new.json") and not new_failed.is_set():
                new_failed.set()
                raise CatalogError("temporary new result outage")
            return update(*args, **kwargs)

        monkeypatch.setattr(app.state.catalog, "update_job", fail)
        old = queue_report(app, home, project, "old.json").json()["job_id"]
        assert exited.wait(5)
        assert app.state.persistence.status(old)["attempts"] == 6
        new = queue_report(app, home, project, "new.json").json()["job_id"]
        assert new_failed.wait(5)
        release.set()
        wait_until(lambda: app.state.catalog.get_job(new).status == "succeeded")
        assert app.state.catalog.get_job(old) == admitted[old]
    finally:
        release.set()
        app.state.close()


def test_retry_finishing_between_shutdown_thread_reads_releases_ownership(tmp_path, monkeypatch):
    app = create_app(tmp_path / "workspace")
    entered, release = threading.Event(), threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)
        update = app.state.catalog.update_job
        attempts = 0

        def delayed(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise CatalogError("first attempt fails")
            entered.set()
            assert release.wait(10)
            return update(*args, **kwargs)

        monkeypatch.setattr(app.state.catalog, "update_job", delayed)
        queue_report(app, home, project, "saved.json")
        assert entered.wait(5)
        persistence = app.state.persistence
        worker = persistence._thread

        def close_during_retry_exit():
            observed = 0

            def trace(frame, event, arg):
                nonlocal observed
                if (
                    event == "line"
                    and frame.f_code is persistence.close.__func__.__code__
                    and persistence._stop.is_set()
                ):
                    observed += 1
                    if observed == 2:
                        release.set()
                        worker.join(timeout=5)
                        assert not worker.is_alive()
                return trace

            sys.settrace(trace)
            try:
                app.state.close()
            finally:
                sys.settrace(None)

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(close_during_retry_exit).result(timeout=10)
        restarted = create_app(app.state.workspace)
        restarted.state.close()
    finally:
        release.set()
        app.state.close()


@pytest.mark.parametrize("damage", ["missing_result", "missing_error", "project", "log_type"])
def test_corrupt_completion_evidence_is_retained_without_replay_or_shutdown_failure(
    tmp_path, monkeypatch, damage
):
    app = create_app(tmp_path / "workspace")
    admitted = capture_admission(app, monkeypatch)
    failed = threading.Event()
    home, project = _seed_curve(app, tmp_path)

    def reject(*args, **kwargs):
        failed.set()
        raise CatalogError("catalog unavailable")

    monkeypatch.setattr(app.state.catalog, "update_job", reject)
    job = queue_report(app, home, project, "evidence.json").json()["job_id"]
    assert failed.wait(5)
    app.state.close()
    manifest = next((app.state.workspace / ".job-completions").glob("*.json"))
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if damage.startswith("missing_"):
        data["record"].pop(damage.removeprefix("missing_"))
    elif damage == "project":
        data["project_id"] = project + 1
    else:
        data["record"]["operation_log"] = {"invalid": "log"}
    manifest.write_text(json.dumps(data), encoding="utf-8")
    before = manifest.read_bytes()
    restarted = create_app(app.state.workspace)
    try:
        assert restarted.state.catalog.get_job(job) == admitted[job]
        assert restarted.state.persistence.pending == {}
        assert manifest.read_bytes() == before
        assert restarted.state.persistence.recovery_errors
    finally:
        restarted.state.close()
    final = create_app(app.state.workspace)
    final.state.close()


def test_unexpected_final_save_failure_releases_workspace_after_draining(tmp_path, monkeypatch):
    app = create_app(tmp_path / "workspace")
    failed = threading.Event()
    home, project = _seed_curve(app, tmp_path)

    def reject(*args, **kwargs):
        failed.set()
        raise CatalogError("catalog unavailable")

    monkeypatch.setattr(app.state.catalog, "update_job", reject)
    queue_report(app, home, project, "pending.json")
    assert failed.wait(5)
    # The retry worker is deliberately stopped before injecting an unexpected
    # final-save error, so the test can inspect safe resource release.
    app.state.persistence._stop.set()
    with app.state.persistence.lock:
        worker = app.state.persistence._thread
    if worker is not None:
        worker.join(timeout=5)

    def unexpected(*args, **kwargs):
        raise RuntimeError("unexpected final save failure")

    monkeypatch.setattr(app.state.catalog, "update_job", unexpected)
    with pytest.raises(RuntimeError, match="unexpected final save failure"):
        app.state.close()
    restarted = create_app(app.state.workspace)
    restarted.state.close()
