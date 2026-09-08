import threading

import numpy as np
import pytest
import xarray as xr
from test_web_workflows import _csrf, _request, _seed_curve, _wait_for_job

from cpdatakit.application.data_access import path_sha256
from cpdatakit.exceptions import CatalogError
from cpdatakit.web import create_app


def test_cancel_and_registration_failure_leave_no_unregistered_image(tmp_path, monkeypatch):
    app = create_app(tmp_path / "workspace")
    started, finish = threading.Event(), threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)
        root = app.state.workspace / "projects" / str(project)
        source = root / "uploads/field.nc"
        xr.Dataset({"temperature": (("y", "x"), np.ones((2, 2)), {"units": "K"})}).to_netcdf(
            source, engine="h5netcdf"
        )
        dataset = app.state.catalog.register_dataset(project, source, sha256=path_sha256(source))

        def reject(*args, **kwargs):
            started.set()
            assert finish.wait(5)
            raise CatalogError("Registration failed")

        monkeypatch.setattr(app.state.catalog, "register_artifact", reject)
        headers = {"X-CSRF-Token": _csrf(home)}
        queued = _request(
            app,
            "POST",
            f"/api/projects/{project}/slice",
            cookies=home.cookies,
            headers=headers,
            data={"dataset_id": dataset.id, "variable": "temperature", "x": "x", "y": "y"},
        )
        job = queued.json()["job_id"]
        assert started.wait(5)
        _request(app, "POST", f"/api/jobs/{job}/cancel", cookies=home.cookies, headers=headers)
        finish.set()
        assert _wait_for_job(app, job)["status"] == "cancelled"
        assert not list(root.glob("results/*.png"))
        assert not app.state.catalog.list_artifacts(project)
    finally:
        finish.set()
        app.state.jobs.shutdown()


@pytest.mark.parametrize("existing", [False, True])
def test_conversion_restores_previous_output_if_registration_fails(tmp_path, monkeypatch, existing):
    app = create_app(tmp_path / "workspace")
    try:
        home, project = _seed_curve(app, tmp_path)
        dataset = app.state.catalog.list_datasets(project)[0].id
        target = app.state.workspace / "projects" / str(project) / "result.h5"
        if existing:
            target.write_bytes(b"previous user output")

        def reject(*args, **kwargs):
            raise CatalogError("Registration failed")

        monkeypatch.setattr(app.state.catalog, "register_artifact", reject)
        queued = _request(
            app,
            "POST",
            f"/api/projects/{project}/convert",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
            data={"dataset_id": dataset, "output": "result.h5", "force": "true", "schema": "curve"},
        )
        result = _wait_for_job(app, queued.json()["job_id"])
        assert result["status"] == "failed"
        if existing:
            assert target.read_bytes() == b"previous user output"
        else:
            assert not target.exists()
        assert not app.state.catalog.list_artifacts(project)
    finally:
        app.state.jobs.shutdown()
