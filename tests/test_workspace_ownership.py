"""A workbench owns its workspace until every previous worker has stopped."""

import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from cpdatakit.exceptions import CPDataKitError
from cpdatakit.web import create_app


def test_second_app_cannot_reclassify_a_live_owners_job(tmp_path):
    first = create_app(tmp_path)
    try:
        project = first.state.catalog.create_project("Owner").id
        first.state.catalog.register_job(
            project, job_id="live", operation="report", status="running"
        )
        with pytest.raises(CPDataKitError, match=r"workspace.*use|workspace.*owned"):
            second = create_app(tmp_path / "projects" / "..")
            second.state.jobs.shutdown()
        assert first.state.catalog.get_job("live").status == "running"
    finally:
        first.state.jobs.shutdown()
    with TestClient(create_app(tmp_path), base_url="http://localhost") as client:
        assert client.get("/api/jobs/live").json()["status"] == "failed"
    final = create_app(tmp_path)
    final.state.jobs.shutdown()


def test_nonwaiting_shutdown_keeps_ownership_until_worker_stops(tmp_path):
    app = create_app(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def work(context):
        entered.set()
        assert release.wait(10)
        (tmp_path / "last-write").write_text("finished", encoding="utf-8")

    app.state.jobs.submit("writer", work)
    assert entered.wait(5)
    try:
        app.state.jobs.shutdown(wait=False)
        with pytest.raises(CPDataKitError):
            other = create_app(tmp_path)
            other.state.jobs.shutdown()
        assert not (tmp_path / "last-write").exists()
    finally:
        release.set()
        app.state.jobs.shutdown()
    other = create_app(tmp_path)
    other.state.jobs.shutdown()
    assert (tmp_path / "last-write").read_text() == "finished"


def test_process_lock_is_released_by_abnormal_exit(tmp_path):
    script = """
import os, sys
from cpdatakit.web import create_app
app = create_app(sys.argv[1])
print('READY', flush=True)
sys.stdin.readline()
os._exit(23)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[1] / "src"),
        },
    )
    try:
        assert process.stdout.readline().strip() == "READY"
        with pytest.raises(CPDataKitError):
            other = create_app(tmp_path)
            other.state.jobs.shutdown()
        process.communicate("exit\n", timeout=15)
        assert process.returncode == 23
        restarted = create_app(tmp_path)
        restarted.state.jobs.shutdown()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_startup_failure_releases_workspace(tmp_path, monkeypatch):
    from cpdatakit.catalog import SQLiteCatalog

    original = SQLiteCatalog.initialize
    with monkeypatch.context() as patch:
        patch.setattr(
            SQLiteCatalog, "initialize", lambda self: (_ for _ in ()).throw(RuntimeError("failure"))
        )
        with pytest.raises(RuntimeError, match="failure"):
            create_app(tmp_path)
    monkeypatch.setattr(SQLiteCatalog, "initialize", original)
    app = create_app(tmp_path)
    app.state.jobs.shutdown()


def test_late_startup_failure_joins_workers_before_unlock(tmp_path, monkeypatch):
    import cpdatakit.web.app as module

    entered, release = threading.Event(), threading.Event()

    def fail_after_worker_started(app, **kwargs):
        def write(context):
            entered.set()
            assert release.wait(10)
            (tmp_path / "startup-worker").write_bytes(b"finished")

        app.state.jobs.submit("startup work", write)
        assert entered.wait(5)
        raise RuntimeError("late startup failure")

    with ThreadPoolExecutor(max_workers=1) as executor, monkeypatch.context() as patch:
        patch.setattr(module, "install_slices", fail_after_worker_started)
        started = executor.submit(create_app, tmp_path)
        try:
            assert entered.wait(5)
            with pytest.raises(CPDataKitError):
                create_app(tmp_path)
            assert not started.done()
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="late startup failure"):
            started.result(timeout=5)
    app = create_app(tmp_path)
    app.state.close()
    assert (tmp_path / "startup-worker").read_bytes() == b"finished"


@pytest.mark.parametrize("wait", [False, True])
def test_shutdown_holds_workspace_until_inflight_upload_request_finishes(
    tmp_path, monkeypatch, wait
):
    from test_web_workflows import _csrf, _request, _seed_curve

    import cpdatakit.web.app as module

    app = create_app(tmp_path / "workspace")
    home, project = _seed_curve(app, tmp_path)
    original = module.import_and_inspect
    entered, release = threading.Event(), threading.Event()

    def blocked(request, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(request, **kwargs)

    monkeypatch.setattr(module, "import_and_inspect", blocked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(
            _request,
            app,
            "POST",
            f"/api/projects/{project}/inspect",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
            data={"schema": "curve"},
            files={"file": ("late.csv", b"step,strain,stress\n0,0,0\n", "text/csv")},
        )
        try:
            assert entered.wait(5)
            closing = executor.submit(app.state.jobs.shutdown, wait=wait)
            if not wait:
                closing.result(timeout=5)
            else:
                # The public closed property means admission has stopped.
                for _ in range(1000):
                    if app.state.jobs.closed:
                        break
                    threading.Event().wait(0.001)
                assert app.state.jobs.closed
            with pytest.raises(CPDataKitError):
                other = create_app(app.state.workspace)
                other.state.close()
            assert _request(app, "GET", "/health").status_code == 503
        finally:
            release.set()
            assert pending.result(timeout=5).status_code == 200
            closing.result(timeout=5)
            app.state.close()
    restarted = create_app(app.state.workspace)
    try:
        assert len(restarted.state.catalog.list_datasets(project)) == 2
    finally:
        restarted.state.close()


def test_closing_from_inside_a_request_does_not_wait_on_itself(tmp_path):
    from test_web_workflows import _request

    app = create_app(tmp_path)

    @app.get("/close-owned-resources")
    def close():
        app.state.close()
        return {"closed": True}

    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(_request, app, "GET", "/close-owned-resources").result(
            timeout=5
        ).json() == {"closed": True}
    app.state.close()
    restarted = create_app(tmp_path)
    restarted.state.close()


def test_response_background_cleanup_keeps_ownership(tmp_path):
    from fastapi.responses import Response
    from starlette.background import BackgroundTask
    from test_web_workflows import _request

    app = create_app(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def cleanup():
        entered.set()
        assert release.wait(10)
        (tmp_path / "cleanup-finished").write_bytes(b"finished")

    @app.get("/download-with-cleanup")
    def download():
        return Response(b"download", background=BackgroundTask(cleanup))

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(_request, app, "GET", "/download-with-cleanup")
        try:
            assert entered.wait(5)
            app.state.jobs.shutdown(wait=False)
            with pytest.raises(CPDataKitError):
                create_app(tmp_path)
        finally:
            release.set()
            assert pending.result(timeout=5).content == b"download"
            app.state.close()
    restarted = create_app(tmp_path)
    restarted.state.close()
