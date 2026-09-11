"""Registered artifact IDs keep their bytes even when an output name is reused."""

import asyncio
import hashlib
import io
import json
import threading
import zipfile

import httpx
import pytest
from test_hdf5_v2 import SCHEMA, _value
from test_web_workflows import _csrf, _request, _seed_curve, _wait_for_job

from cpdatakit.application.data_access import path_sha256
from cpdatakit.exceptions import CatalogError
from cpdatakit.formats import NetCDFWriter
from cpdatakit.schemas import resolve_schema_v2
from cpdatakit.web import create_app


def queue(app, home, project, operation, payload):
    reply = _request(
        app,
        "POST",
        f"/api/projects/{project}/{operation}",
        cookies=home.cookies,
        headers={"X-CSRF-Token": _csrf(home)},
        data=payload,
    )
    assert reply.status_code == 202, reply.text
    record = _wait_for_job(app, reply.json()["job_id"])
    assert record["status"] == "succeeded", record
    return record["result"]


def download(app, project, record):
    response = _request(app, "GET", f"/api/projects/{project}/artifacts/{record.id}")
    assert response.status_code == 200, response.text[:500]
    return response.content


@pytest.mark.parametrize(
    ("operation", "output", "extra"),
    [
        ("convert", "result.h5", {}),
        ("report", "result.json", {"format": "json"}),
        ("plot", "result.png", {"kind": "stress-strain"}),
    ],
)
def test_overwrites_preserve_each_registered_file_version_after_restart(
    tmp_path, operation, output, extra
):
    workspace = tmp_path / "workspace"
    app = create_app(workspace)
    records, expected = [], []
    try:
        home, project = _seed_curve(app, tmp_path)
        dataset = app.state.catalog.list_datasets(project)[0]
        first = queue(
            app,
            home,
            project,
            operation,
            {"dataset_id": dataset.id, "schema": "curve", "output": output, **extra},
        )
        records.append(app.state.catalog.list_artifacts(project)[-1])
        expected.append(download(app, project, records[0]))
        root = workspace / "projects" / str(project)
        second_source = root / "uploads/second.csv"
        second_source.write_text("step,strain,stress\n0,0.0,0.0\n1,0.01,900.0\n")
        second = app.state.catalog.register_dataset(
            project, second_source, sha256=path_sha256(second_source)
        )
        last = queue(
            app,
            home,
            project,
            operation,
            {
                "dataset_id": second.id,
                "schema": "curve",
                "output": output,
                "force": "true",
                **extra,
            },
        )
        records.append(app.state.catalog.list_artifacts(project)[-1])
        expected.append(download(app, project, records[1]))
        assert expected[0] != expected[1]
        assert download(app, project, records[0]) == expected[0]
        assert records[0].relative_path != records[1].relative_path
        assert first["artifact"] == records[0].relative_path
        assert last["artifact"] == records[1].relative_path
        assert (root / output).read_bytes() == expected[1]
        for record, content in zip(records, expected, strict=True):
            assert hashlib.sha256(content).hexdigest() == record.sha256
        (root / output).write_bytes(b"later modification of the user-selected output")
    finally:
        app.state.jobs.shutdown()
    restored = create_app(workspace)
    try:
        for record, content in zip(records, expected, strict=True):
            assert download(restored, project, record) == content
    finally:
        restored.state.jobs.shutdown()


def test_zarr_artifact_versions_preserve_directory_contents(tmp_path):
    app = create_app(tmp_path / "workspace")
    try:
        home, project = _seed_curve(app, tmp_path)
        schema = resolve_schema_v2(SCHEMA).schema.to_dict()
        uploaded = _request(
            app,
            "POST",
            f"/api/projects/{project}/schemas",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
            files={"file": ("schema.json", json.dumps(schema).encode(), "application/json")},
        )
        assert uploaded.status_code == 201, uploaded.text
        selector = uploaded.json()["selector"]
        root = app.state.workspace / "projects" / str(project)
        records, archives = [], []
        for index in range(2):
            source = root / f"uploads/field-{index}.nc"
            value = _value()
            value.data.temperature.values[:] += index * 100
            NetCDFWriter().write(value, source)
            dataset = app.state.catalog.register_dataset(
                project, source, sha256=path_sha256(source)
            )
            queue(
                app,
                home,
                project,
                "convert",
                {
                    "dataset_id": dataset.id,
                    "schema": selector,
                    "output": "field.zarr",
                    "output_format": "zarr",
                    "force": "true",
                },
            )
            record = app.state.catalog.list_artifacts(project)[-1]
            records.append(record)
            archives.append(download(app, project, record))
        assert archives[0] != archives[1]
        assert download(app, project, records[0]) == archives[0]
        for record, payload in zip(records, archives, strict=True):
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                assert all(name.startswith("field.zarr/") for name in archive.namelist())
            assert path_sha256(app.state.workspace / record.relative_path) == record.sha256
    finally:
        app.state.jobs.shutdown()


def test_comparison_artifact_versions_preserve_bundle_members(tmp_path):
    app = create_app(tmp_path / "workspace")
    try:
        home, project = _seed_curve(app, tmp_path)
        root = app.state.workspace / "projects" / str(project)
        left, right = root / "left.json", root / "right.json"
        left.write_text(json.dumps({"statistics": {"numeric_fields": {"stress": {"mean": 0.0}}}}))
        records, payloads = [], []
        for mean in (1.0, 2.0):
            right.write_text(
                json.dumps({"statistics": {"numeric_fields": {"stress": {"mean": mean}}}})
            )
            queue(
                app,
                home,
                project,
                "compare",
                {
                    "left": "left.json",
                    "right": "right.json",
                    "output": "comparison",
                    "force": "true",
                },
            )
            record = app.state.catalog.list_artifacts(project)[-1]
            records.append(record)
            payloads.append(download(app, project, record))
        assert payloads[0] != payloads[1]
        assert download(app, project, records[0]) == payloads[0]
        for record in records:
            assert path_sha256(app.state.workspace / record.relative_path) == record.sha256
        changed = app.state.workspace / records[0].relative_path / "comparison.html"
        changed.write_bytes(b"changed bundle member")
        rejected = _request(app, "GET", f"/api/projects/{project}/artifacts/{records[0].id}")
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "artifact_changed"
    finally:
        app.state.jobs.shutdown()


def test_changed_legacy_artifact_is_not_served_under_its_old_digest(tmp_path):
    app = create_app(tmp_path / "workspace")
    try:
        _, project = _seed_curve(app, tmp_path)
        target = app.state.workspace / "projects" / str(project) / "legacy.json"
        target.write_bytes(b'{"original": true}')
        record = app.state.catalog.register_artifact(
            project, target, kind="report", sha256=path_sha256(target)
        )
        assert download(app, project, record) == target.read_bytes()
        target.write_bytes(b'{"replaced": true}')
        rejected = _request(app, "GET", f"/api/projects/{project}/artifacts/{record.id}")
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "artifact_changed"
    finally:
        app.state.jobs.shutdown()


def test_snapshot_storage_cannot_be_targeted_by_output_forms(tmp_path):
    app = create_app(tmp_path / "workspace")
    try:
        home, project = _seed_curve(app, tmp_path)
        dataset = app.state.catalog.list_datasets(project)[0]
        response = _request(
            app,
            "POST",
            f"/api/projects/{project}/convert",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
            data={
                "dataset_id": dataset.id,
                "schema": "curve",
                "output": ".artifacts/attempt.h5",
                "force": "true",
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "path_rejected"
    finally:
        app.state.jobs.shutdown()


def test_failed_snapshot_registration_cleans_up_and_restores_existing_output(tmp_path, monkeypatch):
    app = create_app(tmp_path / "workspace")
    try:
        home, project = _seed_curve(app, tmp_path)
        root = app.state.workspace / "projects" / str(project)
        dataset = app.state.catalog.list_datasets(project)[0]
        target = root / "result.h5"
        target.write_bytes(b"previous output")
        observed = []

        def reject(project_id, path, **kwargs):
            observed.append(path)
            assert path != target
            assert path.is_file()
            raise CatalogError("Injected registration failure")

        monkeypatch.setattr(app.state.catalog, "register_artifact", reject)
        queued = _request(
            app,
            "POST",
            f"/api/projects/{project}/convert",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
            data={
                "dataset_id": dataset.id,
                "schema": "curve",
                "output": "result.h5",
                "force": "true",
            },
        )
        result = _wait_for_job(app, queued.json()["job_id"])
        assert result["status"] == "failed"
        assert observed and all(not path.exists() for path in observed)
        assert target.read_bytes() == b"previous output"
        assert not list((root / ".artifacts").glob("*"))
        assert not app.state.catalog.list_artifacts(project)
    finally:
        app.state.jobs.shutdown()


def test_artifact_digest_verification_keeps_health_requests_responsive(tmp_path, monkeypatch):
    import cpdatakit.web.workbench as workbench

    app = create_app(tmp_path / "workspace")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    try:
        _, project = _seed_curve(app, tmp_path)
        target = app.state.workspace / "projects" / str(project) / "result.json"
        target.write_bytes(b'{"original": true}')
        record = app.state.catalog.register_artifact(
            project, target, kind="report", sha256=path_sha256(target)
        )
        original = workbench.artifact_digest

        def slow_digest(path, artifact):
            entered.set()
            release.wait(2)
            finished.set()
            return original(path, artifact)

        monkeypatch.setattr(workbench, "artifact_digest", slow_digest)

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client:
                pending = asyncio.create_task(
                    client.get(f"/api/projects/{project}/artifacts/{record.id}")
                )
                assert await asyncio.to_thread(entered.wait, 2)
                try:
                    health = await client.get("/health")
                    assert health.status_code == 200
                    assert not finished.is_set(), "Digest blocked the event loop"
                finally:
                    release.set()
                    response = await pending
                assert response.status_code == 200

        asyncio.run(run())
    finally:
        release.set()
        app.state.jobs.shutdown()
