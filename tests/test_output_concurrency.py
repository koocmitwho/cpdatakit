import threading

from test_web_workflows import _csrf, _request, _seed_curve, _wait_for_job

from cpdatakit.application.data_access import path_sha256
from cpdatakit.exceptions import CatalogError
from cpdatakit.web import create_app


def _queue(app, home, project, dataset, output):
    return _request(
        app,
        "POST",
        f"/api/projects/{project}/convert",
        cookies=home.cookies,
        headers={"X-CSRF-Token": _csrf(home)},
        data={"dataset_id": dataset, "output": output, "force": "true", "schema": "curve"},
    ).json()["job_id"]


def test_output_named_previous_cannot_collide_with_internal_backup(tmp_path):
    app = create_app(tmp_path / "workspace")
    try:
        home, project = _seed_curve(app, tmp_path)
        dataset = app.state.catalog.list_datasets(project)[0].id
        target = app.state.workspace / "projects" / str(project) / "previous"
        target.write_bytes(b"old user output")
        job = _queue(app, home, project, dataset, "previous")
        assert _wait_for_job(app, job)["status"] == "succeeded"
        assert target.read_bytes().startswith(b"\x89HDF")
    finally:
        app.state.jobs.shutdown()


def test_failed_registration_cannot_roll_back_another_jobs_success(tmp_path, monkeypatch):
    app = create_app(tmp_path / "workspace")
    first_entered = threading.Event()
    second_registered = threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)
        dataset = app.state.catalog.list_datasets(project)[0].id
        target = app.state.workspace / "projects" / str(project) / "result.h5"
        target.write_bytes(b"old user output")
        original = app.state.catalog.register_artifact

        def register(*args, **kwargs):
            if not first_entered.is_set():
                first_entered.set()
                # Without serialization, job B registers inside job A's failure window.
                second_registered.wait(1)
                raise CatalogError("First registration failed")
            result = original(*args, **kwargs)
            second_registered.set()
            return result

        monkeypatch.setattr(app.state.catalog, "register_artifact", register)
        first = _queue(app, home, project, dataset, "result.h5")
        assert first_entered.wait(5)
        second = _queue(app, home, project, dataset, "result.h5")
        assert _wait_for_job(app, first)["status"] == "failed"
        assert _wait_for_job(app, second)["status"] == "succeeded"
        artifact = app.state.catalog.list_artifacts(project)[0]
        assert artifact.sha256 == path_sha256(target)
        assert target.read_bytes().startswith(b"\x89HDF")
    finally:
        app.state.jobs.shutdown()
