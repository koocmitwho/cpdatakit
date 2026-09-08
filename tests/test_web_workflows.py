from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import h5py
import httpx

from cpdatakit.web import create_app


def _request(app, method: str, url: str, **kwargs: object) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        cookies = kwargs.pop("cookies", None)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1",
            cookies=cookies,
        ) as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(run())


def _seed_curve(app, tmp_path: Path) -> tuple[httpx.Response, int]:
    home = _request(app, "GET", "/")
    csrf = home.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    project = _request(
        app,
        "POST",
        "/api/projects",
        cookies=home.cookies,
        headers={"X-CSRF-Token": csrf},
        data={"name": "study"},
    ).json()
    project_id = int(project["id"])
    uploaded = _request(
        app,
        "POST",
        f"/api/projects/{project_id}/inspect",
        cookies=home.cookies,
        headers={"X-CSRF-Token": csrf},
        data={"schema": "curve"},
        files={"file": ("curve.csv", b"step,strain,stress\n0,0.0,0.0\n", "text/csv")},
    )
    assert uploaded.status_code == 200
    return home, project_id


def _csrf(home: httpx.Response) -> str:
    return home.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]


def test_conversion_cancel_during_final_registration_retains_completed_result(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path / "workspace")
    home, project_id = _seed_curve(app, tmp_path)
    catalog = app.state.catalog
    original = catalog.register_artifact
    registered = threading.Event()
    finish = threading.Event()

    def register(*args, **kwargs):
        result = original(*args, **kwargs)
        registered.set()
        assert finish.wait(5)
        return result

    monkeypatch.setattr(catalog, "register_artifact", register)
    try:
        dataset_id = catalog.list_datasets(project_id)[0].id
        queued = _request(
            app,
            "POST",
            f"/api/projects/{project_id}/convert",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
            data={"dataset_id": dataset_id, "schema": "curve", "output": "result.h5"},
        )
        job_id = queued.json()["job_id"]
        assert registered.wait(5)
        _request(
            app,
            "POST",
            f"/api/jobs/{job_id}/cancel",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
        )
        finish.set()
        result = _wait_for_job(app, job_id)
        assert result["status"] == "succeeded"
        assert result["result"]["artifact"].endswith("result.h5")
        assert catalog.get_job(job_id).status == "succeeded"
        artifact = catalog.list_artifacts(project_id)[0]
        assert (app.state.workspace / artifact.relative_path).is_file()
    finally:
        finish.set()
        app.state.jobs.shutdown()


def _wait_for_job(app, job_id: str) -> dict[str, object]:
    for _ in range(100):
        response = _request(app, "GET", f"/api/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"succeeded", "failed", "cancelled"}:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"job did not finish: {job_id}")


def test_project_resource_and_capability_routes_use_stable_service_envelopes(
    tmp_path: Path,
) -> None:
    app = create_app(tmp_path)
    home, project_id = _seed_curve(app, tmp_path)

    project = _request(app, "GET", f"/api/projects/{project_id}")
    capabilities = _request(app, "GET", "/api/capabilities")

    assert project.status_code == 200
    assert project.json()["project"]["id"] == project_id
    assert project.json()["datasets"][0]["relative_path"].endswith("curve.csv")
    assert project.json()["schemas"] == []
    assert project.json()["jobs"] == []
    assert capabilities.status_code == 200
    assert capabilities.json()["operation"] == "discover_capabilities"
    assert any(item["name"] == "local-ui" for item in capabilities.json()["value"]["items"])
    assert _csrf(home)


def test_validate_route_uses_uploaded_dataset_and_application_service(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    home, project_id = _seed_curve(app, tmp_path)
    dataset_id = app.state.catalog.list_datasets(project_id)[0].id

    response = _request(
        app,
        "POST",
        f"/api/projects/{project_id}/validate",
        cookies=home.cookies,
        headers={"X-CSRF-Token": _csrf(home)},
        data={"dataset_id": str(dataset_id), "schema": "curve"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["operation"] == "validate_and_summarize"
    assert payload["status"] == "succeeded"
    assert payload["value"]["validation"]["valid"] is True


def test_archive_uploads_are_rejected_before_storage(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    home, project_id = _seed_curve(app, tmp_path)

    response = _request(
        app,
        "POST",
        f"/api/projects/{project_id}/inspect",
        cookies=home.cookies,
        headers={"X-CSRF-Token": _csrf(home)},
        data={"schema": "curve"},
        files={"file": ("bundle.zip", b"../secret.txt", "application/zip")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "archive_rejected"
    assert not (tmp_path / "projects" / str(project_id) / "uploads" / "bundle.zip").exists()


def test_convert_route_requires_overwrite_and_records_hdf5_v1_artifact(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    home, project_id = _seed_curve(app, tmp_path)
    dataset_id = app.state.catalog.list_datasets(project_id)[0].id
    output = tmp_path / "projects" / str(project_id) / "results" / "curve.h5"
    output.parent.mkdir(parents=True)
    output.write_text("keep", encoding="utf-8")

    blocked = _request(
        app,
        "POST",
        f"/api/projects/{project_id}/convert",
        cookies=home.cookies,
        headers={"X-CSRF-Token": _csrf(home)},
        data={"dataset_id": str(dataset_id), "schema": "curve", "output": "results/curve.h5"},
    )
    assert blocked.status_code == 409
    assert output.read_text(encoding="utf-8") == "keep"

    accepted = _request(
        app,
        "POST",
        f"/api/projects/{project_id}/convert",
        cookies=home.cookies,
        headers={"X-CSRF-Token": _csrf(home)},
        data={
            "dataset_id": str(dataset_id),
            "schema": "curve",
            "output": "results/curve.h5",
            "force": "true",
        },
    )

    assert accepted.status_code == 202
    job_id = accepted.json()["job_id"]
    assert app.state.catalog.get_job(job_id).status in {"queued", "running", "succeeded"}
    job = _wait_for_job(app, job_id)
    assert job["status"] == "succeeded"
    assert job["result"]["status"] == "succeeded"
    assert app.state.catalog.get_job(job_id).status == "succeeded"
    with h5py.File(output, "r") as handle:
        assert handle.attrs["format_version"] == "1.0"
    artifacts = app.state.catalog.list_artifacts(project_id)
    assert [item.kind for item in artifacts] == ["convert"]


def test_report_plot_and_compare_routes_use_jobs_and_local_outputs(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    home, project_id = _seed_curve(app, tmp_path)
    dataset_id = app.state.catalog.list_datasets(project_id)[0].id
    csrf = _csrf(home)

    report = _request(
        app,
        "POST",
        f"/api/projects/{project_id}/report",
        cookies=home.cookies,
        headers={"X-CSRF-Token": csrf},
        data={
            "dataset_id": str(dataset_id),
            "schema": "curve",
            "output": "results/report.json",
            "format": "json",
        },
    )
    plot = _request(
        app,
        "POST",
        f"/api/projects/{project_id}/plot",
        cookies=home.cookies,
        headers={"X-CSRF-Token": csrf},
        data={
            "dataset_id": str(dataset_id),
            "schema": "curve",
            "kind": "stress-strain",
            "output": "results/curve.png",
        },
    )
    assert report.status_code == 202
    assert plot.status_code == 202
    report_job = _wait_for_job(app, report.json()["job_id"])
    plot_job = _wait_for_job(app, plot.json()["job_id"])
    assert report_job["status"] == "succeeded"
    assert plot_job["status"] == "succeeded"
    assert (tmp_path / "projects" / str(project_id) / "results" / "report.json").exists()
    assert (tmp_path / "projects" / str(project_id) / "results" / "curve.png").exists()

    left = tmp_path / "projects" / str(project_id) / "results" / "left.json"
    right = tmp_path / "projects" / str(project_id) / "results" / "right.json"
    report_payload = json.loads(
        (tmp_path / "projects" / str(project_id) / "results" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    left.write_text(json.dumps(report_payload), encoding="utf-8")
    right.write_text(json.dumps(report_payload), encoding="utf-8")
    compared = _request(
        app,
        "POST",
        f"/api/projects/{project_id}/compare",
        cookies=home.cookies,
        headers={"X-CSRF-Token": csrf},
        data={"left": "results/left.json", "right": "results/right.json", "output": "results/cmp"},
    )

    assert compared.status_code == 202
    compare_job = _wait_for_job(app, compared.json()["job_id"])
    assert compare_job["status"] == "succeeded"
    assert (tmp_path / "projects" / str(project_id) / "results" / "cmp" / "manifest.json").exists()


def test_job_cancel_route_requests_cooperative_cancellation(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    home = _request(app, "GET", "/")
    started = threading.Event()

    def work(cancel: threading.Event) -> str:
        started.set()
        while not cancel.is_set():
            time.sleep(0.01)
        return "ignored"

    handle = app.state.jobs.submit("long-operation", work)
    assert started.wait(timeout=2)
    cancelled = _request(
        app,
        "POST",
        f"/api/jobs/{handle.id}/cancel",
        cookies=home.cookies,
        headers={"X-CSRF-Token": _csrf(home)},
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] in {"running", "cancelled"}
    assert _wait_for_job(app, handle.id)["status"] == "cancelled"


def test_failed_conversion_records_failed_job_and_retains_service_findings(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    try:
        home, project_id = _seed_curve(app, tmp_path)
        dataset_id = app.state.catalog.list_datasets(project_id)[0].id
        response = _request(
            app,
            "POST",
            f"/api/projects/{project_id}/convert",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
            data={"dataset_id": str(dataset_id), "schema": "point", "output": "results/invalid.h5"},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        app.state.jobs.wait(job_id, timeout=5, raise_timeout=True)
        for _ in range(100):
            record = app.state.catalog.get_job(job_id)
            if record.status == "failed":
                break
            time.sleep(0.01)
        assert record.status == "failed"
        assert record.finished_at
        assert record.error
        project = _request(app, "GET", f"/api/projects/{project_id}").json()
        assert project["jobs"][0]["status"] == "failed"
        result = _request(app, "GET", f"/api/jobs/{job_id}").json()
        assert result["status"] == "failed"
        assert result["result"]["error"]["code"] == "validation_failed"
        assert result["result"]["value"]["validation"]["valid"] is False
        assert result["error"]
        assert app.state.catalog.get_job(job_id).status == "failed"
        assert app.state.catalog.list_artifacts(project_id) == ()
        assert not (tmp_path / "projects" / str(project_id) / "results" / "invalid.h5").exists()
    finally:
        app.state.jobs.shutdown()


def test_polling_cannot_overwrite_completed_catalog_state(tmp_path, monkeypatch):
    import importlib

    web_module = importlib.import_module("cpdatakit.web.app")
    app = create_app(tmp_path)
    gate = threading.Event()
    started = threading.Event()
    original_convert = web_module.convert_and_write

    def blocked_convert(request, **kwargs):
        started.set()
        assert gate.wait(timeout=5)
        return original_convert(request, **kwargs)

    monkeypatch.setattr(web_module, "convert_and_write", blocked_convert)
    try:
        home, project_id = _seed_curve(app, tmp_path)
        dataset_id = app.state.catalog.list_datasets(project_id)[0].id
        response = _request(
            app,
            "POST",
            f"/api/projects/{project_id}/convert",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
            data={"dataset_id": str(dataset_id), "schema": "point", "output": "results/invalid.h5"},
        )
        job_id = response.json()["job_id"]
        assert started.wait(timeout=2)
        original_get = app.state.jobs.get
        first_read = True

        def delayed_snapshot(requested_id):
            nonlocal first_read
            record = original_get(requested_id)
            if first_read:
                first_read = False
                assert record.status.value == "running"
                gate.set()
                app.state.jobs.wait(job_id, timeout=5, raise_timeout=True)
                for _ in range(100):
                    if app.state.catalog.get_job(job_id).status == "failed":
                        break
                    time.sleep(0.01)
                assert app.state.catalog.get_job(job_id).status == "failed"
            return record

        monkeypatch.setattr(app.state.jobs, "get", delayed_snapshot)
        assert _request(app, "GET", f"/api/jobs/{job_id}").status_code == 200
        assert app.state.catalog.get_job(job_id).status == "failed"
    finally:
        gate.set()
        app.state.jobs.shutdown()
