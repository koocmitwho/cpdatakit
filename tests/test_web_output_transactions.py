"""Every workbench output shares staged production and recoverable registration."""

import json
import threading

import pytest
from test_web_workflows import _csrf, _request, _seed_curve, _wait_for_job

from cpdatakit.exceptions import CatalogError
from cpdatakit.web import create_app


def setup_operation(app, tmp_path, operation):
    home, project = _seed_curve(app, tmp_path)
    root = app.state.workspace / "projects" / str(project)
    dataset = app.state.catalog.list_datasets(project)[0].id
    output = {"report": "result.json", "plot": "result.png", "compare": "comparison"}[operation]
    payload = {"dataset_id": dataset, "schema": "curve", "output": output, "force": "true"}
    if operation == "report":
        payload["format"] = "json"
    elif operation == "plot":
        payload["kind"] = "stress-strain"
    else:
        for name, mean in (("left", 0.0), ("right", 1.0)):
            (root / f"{name}.json").write_text(
                json.dumps({"statistics": {"numeric_fields": {"stress": {"mean": mean}}}}),
                encoding="utf-8",
            )
        payload.update(left="left.json", right="right.json")
    return home, project, root / output, payload


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
    return reply.json()["job_id"]


def contents(target):
    if target.is_dir():
        return {
            p.relative_to(target).as_posix(): p.read_bytes()
            for p in target.rglob("*")
            if p.is_file()
        }
    return target.read_bytes() if target.exists() else None


@pytest.mark.parametrize("operation", ["report", "plot", "compare"])
@pytest.mark.parametrize("existing", [False, True])
def test_registration_failure_restores_every_output_type(
    tmp_path, monkeypatch, operation, existing
):
    app = create_app(tmp_path / "workspace")
    try:
        home, project, target, payload = setup_operation(app, tmp_path, operation)
        if existing:
            if operation == "compare":
                target.mkdir()
                (target / "previous.txt").write_bytes(b"previous output")
            else:
                target.write_bytes(b"previous output")
        before = contents(target)

        def reject(*args, **kwargs):
            raise CatalogError("Injected artifact registration failure")

        monkeypatch.setattr(app.state.catalog, "register_artifact", reject)
        job = queue(app, home, project, operation, payload)
        result = _wait_for_job(app, job)
        assert result["status"] == "failed"
        assert contents(target) == before
        assert app.state.catalog.list_artifacts(project) == ()
        versions = target.parent / ".artifacts"
        assert not list(versions.glob("*"))
    finally:
        app.state.jobs.shutdown()


@pytest.mark.parametrize(
    ("operation", "producer"),
    [("report", "build_report"), ("plot", "plot_declared_fields"), ("compare", "compare_reports")],
)
def test_cancellation_after_staging_preserves_the_previous_output(
    tmp_path, monkeypatch, operation, producer
):
    import cpdatakit.web.app as module

    app = create_app(tmp_path / "workspace")
    staged, release = threading.Event(), threading.Event()
    try:
        home, project, target, payload = setup_operation(app, tmp_path, operation)
        if operation == "compare":
            target.mkdir()
            (target / "old.txt").write_bytes(b"old output")
        else:
            target.write_bytes(b"old output")
        before = contents(target)
        original = getattr(module, producer)

        def produce(request):
            result = original(request)
            assert result.ok, result.to_dict()
            staged.set()
            assert release.wait(5)
            return result

        monkeypatch.setattr(module, producer, produce)
        job = queue(app, home, project, operation, payload)
        assert staged.wait(5)
        reply = _request(
            app,
            "POST",
            f"/api/jobs/{job}/cancel",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
        )
        assert reply.status_code == 200
        release.set()
        assert _wait_for_job(app, job)["status"] == "cancelled"
        assert contents(target) == before
        assert app.state.catalog.list_artifacts(project) == ()
    finally:
        release.set()
        app.state.jobs.shutdown()
