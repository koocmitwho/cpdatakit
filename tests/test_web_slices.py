import json

import numpy as np
import xarray as xr
from test_web_workflows import _csrf, _request, _seed_curve, _wait_for_job

from cpdatakit.web import create_app


def test_uploaded_thermal_field_can_be_inspected_plotted_and_downloaded(tmp_path):
    app = create_app(tmp_path / "workspace")
    try:
        home, project = _seed_curve(app, tmp_path)
        source = tmp_path / "field.nc"
        data = xr.Dataset(
            {"temperature": (("time", "y", "x"), np.arange(24.0).reshape(3, 2, 4))},
            coords={"time": [0.0, 10.0, 20.0], "y": [0.0, 1.0], "x": [0.0, 1.0, 2.0, 3.0]},
        )
        data.temperature.attrs["units"] = "K"
        data.to_netcdf(source, engine="h5netcdf")
        headers = {"X-CSRF-Token": _csrf(home)}
        upload = _request(
            app,
            "POST",
            f"/api/projects/{project}/inspect",
            cookies=home.cookies,
            headers=headers,
            files={"file": (source.name, source.read_bytes())},
        )
        dataset = upload.json()["dataset_id"]
        structure = _request(app, "GET", f"/api/projects/{project}/datasets/{dataset}/structure")
        assert structure.status_code == 200
        assert structure.json()["value"]["dimensions"] == {"time": 3, "y": 2, "x": 4}
        response = _request(
            app,
            "POST",
            f"/api/projects/{project}/slice",
            cookies=home.cookies,
            headers=headers,
            data={
                "dataset_id": dataset,
                "variable": "temperature",
                "x": "x",
                "y": "y",
                "indices": json.dumps({"time": 2}),
                "vmin": "0",
                "vmax": "25",
                "cmap": "magma",
            },
        )
        assert response.status_code == 202
        result = _wait_for_job(app, response.json()["job_id"])
        assert result["status"] == "succeeded", result
        artifact = app.state.catalog.list_artifacts(project)[0]
        assert artifact.metadata["slice"]["time"]["index"] == 2
        image = _request(app, "GET", f"/api/projects/{project}/artifacts/{artifact.id}")
        assert image.headers["content-type"] == "image/png"
        assert image.content.startswith(b"\x89PNG")
        rejected = _request(
            app, "POST", f"/api/projects/{project}/slice", data={"dataset_id": dataset}
        )
        assert rejected.status_code in {403, 422}
        other = _request(
            app,
            "POST",
            "/api/projects",
            cookies=home.cookies,
            headers=headers,
            data={"name": "other"},
        ).json()["id"]
        assert (
            _request(app, "GET", f"/api/projects/{other}/datasets/{dataset}/structure").status_code
            == 404
        )
    finally:
        app.state.jobs.shutdown()
