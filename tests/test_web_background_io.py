from __future__ import annotations

import asyncio
import io
import json
import threading
import zipfile
from pathlib import Path

import httpx
import pytest
from test_web_workflows import _csrf, _request, _seed_curve

from cpdatakit.formats import ZarrWriter
from cpdatakit.web import authoring, create_app, slices, workbench
from cpdatakit.web.artifacts import register_snapshot

pytest_plugins = ["test_application_multiformat"]


class BlockingOperation:
    """Pause one real I/O operation until a concurrent health request completes."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.expired = False

    def install(self, monkeypatch, owner, name, predicate=lambda *args, **kwargs: True):
        original = getattr(owner, name)

        def gated(*args, **kwargs):
            if predicate(*args, **kwargs) and not self.entered.is_set():
                self.entered.set()
                # Rescue a regressed event loop instead of hanging the test process.
                self.expired = not self.release.wait(2)
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, gated)


async def _request_while_health_remains_available(app, home, gate, method, url, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
        cookies=home.cookies,
        headers={"X-CSRF-Token": _csrf(home)},
    ) as client:
        task = asyncio.create_task(client.request(method, url, **kwargs))
        try:
            assert await asyncio.to_thread(gate.entered.wait, 5), "operation was not exercised"
            health = await asyncio.wait_for(client.get("/health"), timeout=5)
            assert health.status_code == 200
            assert not gate.expired, "blocking project I/O prevented the health request"
            assert not task.done(), "operation completed before its gate was released"
        finally:
            gate.release.set()
            response = await task
        return response


@pytest.mark.parametrize(
    "operation",
    [
        "schema-store",
        "schema-draft",
        "mapping-store",
        "mapping-preview",
        "mapping-cleanup",
        "structure",
    ],
)
def test_authoring_and_structure_io_leave_the_event_loop_available(
    tmp_path, monkeypatch, operation
):
    app = create_app(tmp_path / "workspace")
    try:
        home, project = _seed_curve(app, tmp_path)
        dataset = app.state.catalog.list_datasets(project)[0].id
        gate = BlockingOperation()
        if operation == "schema-store":
            from cpdatakit.schema import schema_to_dict

            gate.install(monkeypatch, app.state.catalog, "register_schema")
            method, url = "POST", f"/api/projects/{project}/schemas"
            arguments = {"files": {"file": ("curve.json", json.dumps(schema_to_dict("curve")))}}
        elif operation == "schema-draft":
            gate.install(monkeypatch, authoring, "draft_schema")
            method, url = "GET", f"/api/projects/{project}/datasets/{dataset}/schema-draft"
            arguments = {}
        elif operation == "structure":
            gate.install(monkeypatch, slices, "import_and_inspect")
            method, url = "GET", f"/api/projects/{project}/datasets/{dataset}/structure"
            arguments = {}
        else:
            if operation == "mapping-store":
                gate.install(monkeypatch, authoring, "load_mapping_file")
            elif operation == "mapping-preview":
                gate.install(monkeypatch, authoring, "preview_mapping")
            else:
                gate.install(
                    monkeypatch,
                    Path,
                    "unlink",
                    lambda path, **kwargs: path.name.startswith("mapping-"),
                )
            method, url = "POST", f"/api/projects/{project}/mapping-preview"
            arguments = {
                "data": {
                    "dataset_id": dataset,
                    "schema": "curve",
                    "mapping_json": '{"mappings": []}',
                }
            }

        response = asyncio.run(
            _request_while_health_remains_available(app, home, gate, method, url, **arguments)
        )

        assert response.status_code == (201 if operation == "schema-store" else 200), response.text
        if operation.startswith("mapping"):
            assert response.json()["value"]["validation"]["valid"]
            assert not list(
                (app.state.workspace / "projects" / str(project) / "schemas").glob("mapping-*.json")
            )
        elif operation == "schema-draft":
            assert response.json()["value"]["ready"] is False
    finally:
        app.state.jobs.shutdown()


@pytest.mark.parametrize("operation", ["inspect", "hash", "promote", "register", "cleanup"])
def test_zarr_upload_io_leaves_the_event_loop_available(
    tmp_path, monkeypatch, thermal_value, thermal_schema, operation
):
    app = create_app(tmp_path / "workspace")
    try:
        home, project = _seed_curve(app, tmp_path)
        uploaded_schema = _request(
            app,
            "POST",
            f"/api/projects/{project}/schemas",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
            files={"file": ("thermal.json", json.dumps(thermal_schema))},
        )
        assert uploaded_schema.status_code == 201
        source = ZarrWriter().write(thermal_value, tmp_path / "field.zarr")
        files = [
            ("files", (f"field.zarr/{entry.relative_to(source).as_posix()}", entry.read_bytes()))
            for entry in source.rglob("*")
            if entry.is_file()
        ]
        gate = BlockingOperation()
        if operation == "inspect":
            gate.install(monkeypatch, workbench, "import_and_inspect")
        elif operation == "hash":
            gate.install(monkeypatch, workbench, "path_sha256", lambda path: path.suffix == ".zarr")
        elif operation == "promote":
            gate.install(
                monkeypatch,
                workbench.os,
                "replace",
                lambda source, target: Path(source).suffix == ".zarr",
            )
        elif operation == "register":
            gate.install(monkeypatch, app.state.catalog, "register_dataset")
        else:
            gate.install(monkeypatch, workbench.shutil, "rmtree")

        response = asyncio.run(
            _request_while_health_remains_available(
                app,
                home,
                gate,
                "POST",
                f"/api/projects/{project}/inspect-zarr",
                files=files,
                data={"schema": uploaded_schema.json()["selector"]},
            )
        )

        assert response.status_code == 200, response.text
        datasets = app.state.catalog.list_datasets(project)
        assert response.json()["dataset_id"] in {record.id for record in datasets}
        uploads = app.state.workspace / "projects" / str(project) / "uploads"
        assert (uploads / "field.zarr" / "zarr.json").is_file()
        assert not list(uploads.glob(".zarr-upload-*"))
    finally:
        app.state.jobs.shutdown()


@pytest.mark.parametrize("operation", ["digest", "archive", "cleanup"])
def test_directory_download_io_leaves_the_event_loop_available(tmp_path, monkeypatch, operation):
    app = create_app(tmp_path / "workspace")
    try:
        home, project = _seed_curve(app, tmp_path)
        result = app.state.workspace / "projects" / str(project) / "result.zarr"
        result.mkdir()
        (result / "zarr.json").write_text('{"zarr_format": 3}', encoding="utf-8")
        record = register_snapshot(
            app.state.catalog, app.state.workspace, project, result, kind="convert", metadata={}
        )
        gate = BlockingOperation()
        if operation == "digest":
            gate.install(monkeypatch, workbench, "artifact_digest")
        elif operation == "archive":
            gate.install(monkeypatch, zipfile.ZipFile, "write")
        else:
            gate.install(monkeypatch, Path, "unlink", lambda path, **kwargs: path.suffix == ".zip")

        response = asyncio.run(
            _request_while_health_remains_available(
                app,
                home,
                gate,
                "GET",
                f"/api/projects/{project}/artifacts/{record.id}",
            )
        )

        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.read("result.zarr/zarr.json") == b'{"zarr_format": 3}'
    finally:
        app.state.jobs.shutdown()
