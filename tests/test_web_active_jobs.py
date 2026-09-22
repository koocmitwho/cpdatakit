"""Current workers remain reachable independently of persisted history pages."""

import json
import re
import threading

import pytest
from test_web_runtime_limits import queue_report
from test_web_workflows import _csrf, _request, _seed_curve, _wait_for_job

from cpdatakit.web import create_app


def _page_state(response):
    match = re.search(
        r'<script id="project-resources" type="application/json">(.*?)</script>',
        response.text,
        re.DOTALL,
    )
    assert match
    return json.loads(match.group(1))


def test_active_job_survives_fifty_newer_records_and_is_cancellable(tmp_path, monkeypatch):
    import cpdatakit.web.app as module

    app = create_app(tmp_path / "workspace", max_pending_jobs=2)
    entered, release = threading.Event(), threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)
        original = module.build_report

        def blocked(request):
            entered.set()
            # Keep the worker active during slow catalog I/O; finally always releases it.
            release.wait()
            return original(request)

        monkeypatch.setattr(module, "build_report", blocked)
        queued = queue_report(app, home, project, "long.json")
        assert queued.status_code == 202
        job_id = queued.json()["job_id"]
        assert entered.wait(5)
        for index in range(61):
            app.state.catalog.register_job(
                project,
                job_id=f"new-{index}",
                operation="report",
                status="succeeded",
                result={"large": "details"},
            )
        other = app.state.catalog.create_project("Other project").id
        original_list = app.state.catalog.list_jobs

        def bounded_history(*args, **kwargs):
            assert kwargs.get("limit") in (2, 50)
            assert kwargs.get("include_result") is False
            return original_list(*args, **kwargs)

        with monkeypatch.context() as guard:
            guard.setattr(app.state.catalog, "list_jobs", bounded_history)
            for url in (
                f"/api/projects/{project}?limit=50&newest_first=true",
                f"/api/projects/{project}?limit=2&offset=50",
                f"/projects/{project}",
            ):
                response = _request(app, "GET", url)
                assert response.status_code == 200
                state = _page_state(response) if url.startswith("/projects") else response.json()
                assert [item["id"] for item in state["active_jobs"]] == [job_id]
                assert "result" not in state["active_jobs"][0]
                assert len(state["active_jobs"][0]["operation_log"]) <= 1
                if url.startswith("/projects"):
                    assert response.text.count(f'data-job-id="{job_id}"') == 1
            assert (
                _request(app, "GET", f"/api/projects/{other}?limit=50").json()["active_jobs"] == []
            )
        legacy = _request(app, "GET", f"/api/projects/{project}").json()
        assert len(legacy["jobs"]) == 62
        assert legacy["jobs"][-1]["result"] == {"large": "details"}
        cancelled = _request(
            app,
            "POST",
            f"/api/jobs/{job_id}/cancel",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
        )
        assert cancelled.status_code == 200
        release.set()
        assert _wait_for_job(app, job_id)["status"] == "cancelled"
        state = _request(
            app, "GET", f"/api/projects/{project}?limit=50&offset=50&newest_first=true"
        ).json()
        assert state["active_jobs"] == []
        assert next(row for row in state["jobs"] if row["id"] == job_id)["status"] == "cancelled"
    finally:
        release.set()
        app.state.jobs.shutdown()


def test_restarted_app_does_not_claim_persisted_running_jobs(tmp_path):
    app = create_app(tmp_path / "workspace")
    project = app.state.catalog.create_project("Old session").id
    app.state.catalog.register_job(
        project, job_id="old-running", operation="report", status="running"
    )
    app.state.jobs.shutdown()
    restarted = create_app(tmp_path / "workspace")
    try:
        for url in (f"/api/projects/{project}?limit=50", f"/projects/{project}"):
            response = _request(restarted, "GET", url)
            state = _page_state(response) if url.startswith("/projects") else response.json()
            assert state["active_jobs"] == []
            assert state["jobs"][0]["id"] == "old-running"
            assert state["jobs"][0]["active"] is False
    finally:
        restarted.state.jobs.shutdown()


@pytest.mark.parametrize("html", [False, True])
def test_completion_between_active_snapshot_and_history_is_not_lost(tmp_path, monkeypatch, html):
    import cpdatakit.web.app as module

    app = create_app(tmp_path / "workspace")
    entered, release = threading.Event(), threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)
        original = module.build_report

        def blocked(request):
            entered.set()
            assert release.wait(10)
            return original(request)

        monkeypatch.setattr(module, "build_report", blocked)
        job_id = queue_report(app, home, project, "race.json").json()["job_id"]
        assert entered.wait(5)
        original_list = app.state.catalog.list_jobs

        def finish_then_list(*args, **kwargs):
            release.set()
            assert app.state.jobs.wait(job_id, timeout=5).status.value == "succeeded"
            return original_list(*args, **kwargs)

        monkeypatch.setattr(app.state.catalog, "list_jobs", finish_then_list)
        url = f"/projects/{project}" if html else f"/api/projects/{project}?limit=50"
        response = _request(app, "GET", url)
        state = _page_state(response) if html else response.json()
        assert job_id in {row["id"] for row in state["jobs"] + state["active_jobs"]}
        assert _request(app, "GET", f"/api/jobs/{job_id}").json()["status"] == "succeeded"
        if html:
            assert response.text.count(f'data-job-id="{job_id}"') == 1
        subsequent = _request(app, "GET", f"/api/projects/{project}?limit=50").json()
        assert subsequent["active_jobs"] == []
        assert subsequent["jobs"][0]["status"] == "succeeded"
    finally:
        release.set()
        app.state.jobs.shutdown()


@pytest.mark.parametrize("html", [False, True])
def test_terminal_worker_overlay_precedes_delayed_catalog_callback(tmp_path, monkeypatch, html):
    app = create_app(tmp_path / "workspace")
    entered, release = threading.Event(), threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)
        add_callback = app.state.jobs.add_done_callback

        def delayed(job_id, callback):
            def wait_then_sync(record):
                entered.set()
                assert release.wait(10)
                callback(record)

            return add_callback(job_id, wait_then_sync)

        monkeypatch.setattr(app.state.jobs, "add_done_callback", delayed)
        job_id = queue_report(app, home, project, "delayed.json").json()["job_id"]
        assert entered.wait(5)
        assert app.state.jobs.get(job_id).status.value == "succeeded"
        assert app.state.catalog.get_job(job_id).status in {"queued", "running"}
        url = f"/projects/{project}" if html else f"/api/projects/{project}?limit=50"
        response = _request(app, "GET", url)
        state = _page_state(response) if html else response.json()
        summary = next(row for row in state["jobs"] if row["id"] == job_id)
        assert summary["status"] == "succeeded"
        assert summary["active"] is False
        assert "result" not in summary
        assert state["active_jobs"] == []
    finally:
        release.set()
        app.state.jobs.shutdown()
