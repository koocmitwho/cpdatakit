from __future__ import annotations

import asyncio
import io
import json
import time
import zipfile

import h5py
import httpx
import pytest

from cpdatakit.formats import NetCDFWriter, ZarrWriter
from cpdatakit.web import create_app

pytest_plugins = ["test_application_multiformat"]


class LocalClient:
    def __init__(self, app):
        self.app = app
        self.cookies = httpx.Cookies()
        self.headers = {}

    def request(self, method, url, **kwargs):
        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url="http://127.0.0.1",
                cookies=self.cookies,
                headers=self.headers,
            ) as client:
                response = await client.request(method, url, **kwargs)
                self.cookies.update(response.cookies)
                return response

        return asyncio.run(run())

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "workspace")
    client = LocalClient(app)
    home = client.get("/")
    token = home.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
    client.headers["X-CSRF-Token"] = token
    yield client
    app.state.jobs.shutdown()


def project(client):
    response = client.post("/api/projects", data={"name": "Thermal study"})
    assert response.status_code == 201
    return response.json()["id"]


def upload_schema(client, project_id, schema):
    response = client.post(
        f"/api/projects/{project_id}/schemas",
        files={"file": ("thermal.json", json.dumps(schema), "application/json")},
    )
    assert response.status_code == 201, response.text
    return response.json()["selector"]


def wait_job(client, response):
    assert response.status_code == 202, response.text
    for _ in range(200):
        job = client.get(f"/api/jobs/{response.json()['job_id']}").json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.01)
    pytest.fail("Job did not finish")


@pytest.mark.parametrize("kind", ["netcdf", "zarr"])
def test_project_upload_validate_convert_report_and_download(
    client,
    tmp_path,
    thermal_schema,
    thermal_value,
    kind,
):
    pid = project(client)
    schema = upload_schema(client, pid, thermal_schema)
    page = client.get(f"/projects/{pid}")
    assert page.status_code == 200
    assert schema in page.text
    if kind == "netcdf":
        path = NetCDFWriter().write(thermal_value, tmp_path / "field.nc")
        upload = client.post(
            f"/api/projects/{pid}/inspect",
            data={"schema": schema},
            files={"file": (path.name, path.read_bytes())},
        )
    else:
        path = ZarrWriter().write(thermal_value, tmp_path / "field.zarr")
        files = [
            ("files", (f"field.zarr/{p.relative_to(path).as_posix()}", p.read_bytes()))
            for p in path.rglob("*")
            if p.is_file()
        ]
        upload = client.post(
            f"/api/projects/{pid}/inspect-zarr", data={"schema": schema}, files=files
        )
    assert upload.status_code == 200, upload.text
    dataset_id = upload.json()["dataset_id"]
    data = {"schema": schema, "dataset_id": dataset_id}
    checked = client.post(f"/api/projects/{pid}/validate", data=data)
    assert checked.status_code == 200, checked.text
    assert checked.json()["value"]["validation"]["valid"]
    converted = wait_job(
        client,
        client.post(f"/api/projects/{pid}/convert", data={**data, "output": "results/field.h5"}),
    )
    assert converted["status"] == "succeeded", converted
    report = wait_job(
        client,
        client.post(f"/api/projects/{pid}/report", data={**data, "output": "results/report.html"}),
    )
    assert report["status"] == "succeeded", report
    artifacts = client.get(f"/api/projects/{pid}").json()["artifacts"]
    for artifact in artifacts:
        response = client.get(f"/api/projects/{pid}/artifacts/{artifact['id']}")
        assert response.status_code == 200
        if artifact["kind"] == "convert":
            with h5py.File(io.BytesIO(response.content)) as handle:
                assert handle.attrs["format_version"] == "2.0"
                assert handle["variables/temperature"].shape == (2, 3)
        else:
            assert "temperature" in response.text
            assert "sandbox" in response.headers["content-security-policy"]
    refreshed = client.get(f"/projects/{pid}")
    assert "field" in refreshed.text and "report.html" in refreshed.text
    assert str(tmp_path) not in refreshed.text


def test_schema_and_artifact_resources_cannot_cross_projects(client, thermal_schema):
    first, second = project(client), project(client)
    schema = upload_schema(client, first, thermal_schema)
    rejected = client.post(
        f"/api/projects/{second}/inspect",
        data={"schema": schema},
        files={"file": ("curve.csv", b"step,strain,stress\n0,0,0\n")},
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "unsupported_schema"
    response = client.get(f"/api/projects/{second}/artifacts/999")
    assert response.status_code == 404


@pytest.mark.parametrize("reference", ["../secret.json", "C:/secret.json", "https://example.org/x"])
def test_schema_upload_rejects_external_composition(client, thermal_schema, reference):
    pid = project(client)
    thermal_schema["extends"] = reference
    response = client.post(
        f"/api/projects/{pid}/schemas", files={"file": ("schema.json", json.dumps(thermal_schema))}
    )
    assert response.status_code == 400
    assert client.get(f"/api/projects/{pid}").json()["schemas"] == []


@pytest.mark.parametrize(
    "name",
    ["field.zarr/../secret", "../field.zarr/zarr.json", "field.zarr/C:/secret", "other/zarr.json"],
)
def test_zarr_upload_rejects_unsafe_paths_and_cleans_staging(client, name):
    pid = project(client)
    response = client.post(f"/api/projects/{pid}/inspect-zarr", files=[("files", (name, b"{}"))])
    assert response.status_code == 400
    root = client.app.state.workspace / "projects" / str(pid) / "uploads"
    assert list(root.iterdir()) == []


def test_failed_upload_can_be_corrected_and_retried(client):
    pid = project(client)
    url = f"/api/projects/{pid}/inspect"
    response = client.post(url, files={"file": ("curve.csv", b"invalid,\xff")})
    assert response.status_code == 400
    retry = client.post(url, files={"file": ("curve.csv", b"step,strain,stress\n0,0,0\n")})
    assert retry.status_code == 200


def test_zarr_conversion_download_is_a_complete_archive(
    client, tmp_path, thermal_schema, thermal_value
):
    pid = project(client)
    schema = upload_schema(client, pid, thermal_schema)
    path = NetCDFWriter().write(thermal_value, tmp_path / "field.nc")
    uploaded = client.post(
        f"/api/projects/{pid}/inspect",
        data={"schema": schema},
        files={"file": (path.name, path.read_bytes())},
    ).json()
    job = wait_job(
        client,
        client.post(
            f"/api/projects/{pid}/convert",
            data={
                "dataset_id": uploaded["dataset_id"],
                "schema": schema,
                "output": "results/field.zarr",
                "output_format": "zarr",
            },
        ),
    )
    assert job["status"] == "succeeded", job
    artifact = client.get(f"/api/projects/{pid}").json()["artifacts"][0]
    downloaded = client.get(f"/api/projects/{pid}/artifacts/{artifact['id']}")
    assert downloaded.status_code == 200
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert "field.zarr/zarr.json" in archive.namelist()
        assert "field.zarr/temperature/zarr.json" in archive.namelist()


def test_invalid_dataset_reports_findings_and_failed_conversion_job(
    client, tmp_path, thermal_schema, thermal_value
):
    pid = project(client)
    schema = upload_schema(client, pid, thermal_schema)
    thermal_value.data.temperature.attrs["unit"] = "degC"
    source = NetCDFWriter().write(thermal_value, tmp_path / "invalid.nc")
    upload = client.post(
        f"/api/projects/{pid}/inspect",
        data={"schema": schema},
        files={"file": (source.name, source.read_bytes())},
    ).json()
    data = {"schema": schema, "dataset_id": upload["dataset_id"]}
    checked = client.post(f"/api/projects/{pid}/validate", data=data).json()
    assert checked["value"]["validation"]["valid"] is False
    job = wait_job(
        client,
        client.post(f"/api/projects/{pid}/convert", data={**data, "output": "results/invalid.h5"}),
    )
    assert job["status"] == "failed"
    assert job["result"]["error"]["code"] == "validation_failed"
    assert client.get(f"/api/projects/{pid}").json()["artifacts"] == []


@pytest.mark.parametrize("operation", ["schemas", "inspect-zarr"])
def test_new_upload_routes_require_csrf(client, operation):
    pid = project(client)
    client.headers.clear()
    name = "files" if operation == "inspect-zarr" else "file"
    response = client.post(f"/api/projects/{pid}/{operation}", files={name: ("x.json", b"{}")})
    assert response.status_code == 403


def test_zarr_upload_enforces_total_size_and_removes_staging(client):
    pid = project(client)
    client.app.state.upload_limit = 4
    response = client.post(
        f"/api/projects/{pid}/inspect-zarr", files=[("files", ("field.zarr/zarr.json", b"12345"))]
    )
    assert response.status_code == 413
    assert list((client.app.state.workspace / "projects" / str(pid) / "uploads").iterdir()) == []


def test_conversion_cannot_overwrite_an_uploaded_input(client):
    pid = project(client)
    uploaded = client.post(
        f"/api/projects/{pid}/inspect",
        files={
            "file": ("curve.csv", b"step,strain,stress\n0,0,0\n"),
        },
    ).json()
    response = client.post(
        f"/api/projects/{pid}/convert",
        data={
            "dataset_id": uploaded["dataset_id"],
            "output": "uploads/curve.csv",
            "force": "true",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "path_rejected"


def test_completed_job_and_results_survive_app_restart(client):
    pid = project(client)
    uploaded = client.post(
        f"/api/projects/{pid}/inspect",
        files={
            "file": ("curve.csv", b"step,strain,stress\n0,0,0\n"),
        },
    ).json()
    job = wait_job(
        client,
        client.post(
            f"/api/projects/{pid}/report",
            data={
                "dataset_id": uploaded["dataset_id"],
                "output": "results/report.html",
            },
        ),
    )
    app = create_app(client.app.state.workspace)
    restarted = LocalClient(app)
    response = restarted.get(f"/api/jobs/{job['id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    app.state.jobs.shutdown()


def test_custom_tabular_schema_supports_validation_conversion_and_plot(client):
    from cpdatakit.schema import schema_to_dict

    pid = project(client)
    contract = schema_to_dict("curve")
    contract["profile"] = "custom-curve"
    schema = upload_schema(client, pid, contract)
    uploaded = client.post(
        f"/api/projects/{pid}/inspect",
        data={"schema": schema},
        files={
            "file": ("curve.csv", b"step,strain,stress\n0,0,0\n1,0.1,10\n"),
        },
    ).json()
    data = {"dataset_id": uploaded["dataset_id"], "schema": schema}
    checked = client.post(f"/api/projects/{pid}/validate", data=data)
    assert checked.json()["value"]["validation"]["valid"]
    converted = wait_job(
        client,
        client.post(
            f"/api/projects/{pid}/convert",
            data={
                **data,
                "output": "results/curve.parquet",
                "output_format": "parquet",
            },
        ),
    )
    assert converted["status"] == "succeeded", converted
    plotted = wait_job(
        client,
        client.post(
            f"/api/projects/{pid}/plot",
            data={
                **data,
                "output": "results/curve.png",
                "kind": "xy",
                "x": "strain",
                "y": "stress",
            },
        ),
    )
    assert plotted["status"] == "succeeded", plotted


def test_job_interrupted_by_a_restart_is_not_shown_as_running(client):
    pid = project(client)
    client.app.state.catalog.register_job(
        pid, job_id="stale-job", operation="convert", status="running"
    )
    response = client.get("/api/jobs/stale-job")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "restart" in response.json()["error"].lower()
