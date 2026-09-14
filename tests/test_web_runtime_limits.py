"""The workbench bounds live state while retaining queryable job history."""

import threading

from test_web_workflows import _csrf, _request, _seed_curve, _wait_for_job

from cpdatakit.exceptions import CatalogError
from cpdatakit.web import create_app


def queue_report(app, home, project, output):
    dataset = app.state.catalog.list_datasets(project)[0].id
    return _request(
        app,
        "POST",
        f"/api/projects/{project}/report",
        cookies=home.cookies,
        headers={"X-CSRF-Token": _csrf(home)},
        data={"dataset_id": dataset, "schema": "curve", "output": output, "format": "json"},
    )


def test_completed_jobs_are_evicted_only_after_persisting_their_results(tmp_path):
    workspace = tmp_path / "workspace"
    app = create_app(workspace, max_retained_jobs=2, max_log_entries=4)
    expected = {}
    try:
        home, project = _seed_curve(app, tmp_path)
        for index in range(5):
            queued = queue_report(app, home, project, f"result-{index}.json")
            assert queued.status_code == 202, queued.text
            job_id = queued.json()["job_id"]
            result = _wait_for_job(app, job_id)
            assert result["status"] == "succeeded", result
            expected[job_id] = result["result"]
            assert len(result["operation_log"]) <= 4
        assert len(app.state.jobs.list()) <= 2
        assert len(app.state.catalog.list_jobs(project)) == 5
        for job_id, result in expected.items():
            loaded = _request(app, "GET", f"/api/jobs/{job_id}")
            assert loaded.status_code == 200
            assert loaded.json()["result"] == result
            assert app.state.catalog.get_job(job_id).result == result
        first = next(iter(expected))
        cancelled = _request(
            app,
            "POST",
            f"/api/jobs/{first}/cancel",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "succeeded"
    finally:
        app.state.jobs.shutdown()
    restarted = create_app(workspace, max_retained_jobs=2)
    try:
        for job_id, result in expected.items():
            response = _request(restarted, "GET", f"/api/jobs/{job_id}")
            assert response.status_code == 200
            assert response.json()["result"] == result
    finally:
        restarted.state.jobs.shutdown()


def test_queue_capacity_rejects_extra_work_without_creating_a_catalog_job(tmp_path, monkeypatch):
    import cpdatakit.web.app as module

    app = create_app(tmp_path / "workspace", max_pending_jobs=1)
    entered, release = threading.Event(), threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)
        original = module.build_report

        def blocked(request):
            entered.set()
            assert release.wait(5)
            return original(request)

        monkeypatch.setattr(module, "build_report", blocked)
        queued = queue_report(app, home, project, "first.json")
        assert queued.status_code == 202
        assert entered.wait(5)
        rejected = queue_report(app, home, project, "second.json")
        assert rejected.status_code == 503
        assert len(app.state.catalog.list_jobs(project)) == 1
        release.set()
        assert _wait_for_job(app, queued.json()["job_id"])["status"] == "succeeded"
    finally:
        release.set()
        app.state.jobs.shutdown()


def test_failed_job_registration_never_starts_the_producer(tmp_path, monkeypatch):
    import cpdatakit.web.app as module

    app = create_app(tmp_path / "workspace")
    started, release = threading.Event(), threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)
        original = module.build_report

        def producer(request):
            started.set()
            release.wait(2)
            return original(request)

        def reject(*args, **kwargs):
            started.wait(0.2)
            raise CatalogError("Injected job registration failure")

        monkeypatch.setattr(module, "build_report", producer)
        monkeypatch.setattr(app.state.catalog, "register_job", reject)
        response = queue_report(app, home, project, "unregistered.json")
        assert response.status_code == 500
        release.set()
        app.state.jobs.shutdown()
        assert not started.is_set()
        assert app.state.jobs.list() == ()
        assert not (app.state.workspace / "projects" / str(project) / "unregistered.json").exists()
    finally:
        release.set()
        app.state.jobs.shutdown()


def test_project_api_paginates_summaries_and_preserves_legacy_full_queries(tmp_path):
    app = create_app(tmp_path / "workspace")
    try:
        _, project = _seed_curve(app, tmp_path)
        root = app.state.workspace / "projects" / str(project)
        for index in range(5):
            path = root / "uploads" / f"{index}.csv"
            path.write_text("step,strain,stress\n0,0,0\n")
            app.state.catalog.register_dataset(project, path, sha256="a" * 64)
            app.state.catalog.register_job(
                project,
                job_id=f"history-{index}",
                operation="validate",
                status="succeeded",
                operation_log=("queued", "running", "succeeded"),
                result={"details": f"payload-{index}"},
            )
        full = _request(app, "GET", f"/api/projects/{project}").json()
        assert len(full["datasets"]) == 6
        assert len(full["jobs"]) == 5
        page = _request(app, "GET", f"/api/projects/{project}?limit=2&offset=1&newest_first=true")
        assert page.status_code == 200
        payload = page.json()
        assert [row["id"] for row in payload["datasets"]] == [5, 4]
        assert [row["id"] for row in payload["jobs"]] == ["history-3", "history-2"]
        assert all(
            "result" not in row and len(row["operation_log"]) <= 1 for row in payload["jobs"]
        )
        assert payload["pagination"]["counts"] == {
            "datasets": 6,
            "artifacts": 0,
            "schemas": 0,
            "jobs": 5,
        }
        assert payload["pagination"]["has_more"]["datasets"]
        for query in ("limit=0", "limit=201", "offset=-1"):
            assert _request(app, "GET", f"/api/projects/{project}?{query}").status_code == 422
    finally:
        app.state.jobs.shutdown()
