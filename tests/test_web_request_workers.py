"""Core request handlers must release the event loop during synchronous work."""

import asyncio
import threading

import httpx
import pytest
from test_web_workflows import _csrf, _request, _seed_curve

from cpdatakit.exceptions import CatalogError
from cpdatakit.web import create_app


@pytest.mark.parametrize("operation", ["validate", "inspect", "capabilities"])
def test_core_sync_work_does_not_block_health(tmp_path, monkeypatch, operation):
    import cpdatakit.web.app as module

    app = create_app(tmp_path / "workspace")
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    try:
        home, project = _seed_curve(app, tmp_path)
        dataset = app.state.catalog.list_datasets(project)[0].id
        function = {
            "validate": "validate_and_summarize",
            "inspect": "import_and_inspect",
            "capabilities": "discover_capabilities",
        }[operation]
        original = getattr(module, function)

        def slow(*args, **kwargs):
            entered.set()
            release.wait(2)
            done.set()
            return original(*args, **kwargs)

        monkeypatch.setattr(module, function, slow)

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1",
                cookies=home.cookies,
                headers={"X-CSRF-Token": _csrf(home)},
            ) as client:
                if operation == "capabilities":
                    request = client.get("/api/capabilities")
                elif operation == "validate":
                    request = client.post(
                        f"/api/projects/{project}/validate",
                        data={"dataset_id": dataset, "schema": "curve"},
                    )
                else:
                    request = client.post(
                        f"/api/projects/{project}/inspect",
                        data={"schema": "curve"},
                        files={"file": ("another.csv", b"step,strain,stress\n0,0,0\n")},
                    )
                pending = asyncio.create_task(request)
                assert await asyncio.to_thread(entered.wait, 3)
                try:
                    health = await client.get("/health")
                    assert health.status_code == 200
                    assert not done.is_set(), "Synchronous work blocked the event loop"
                finally:
                    release.set()
                    response = await pending
                assert response.status_code == 200, response.text

        asyncio.run(run())
    finally:
        release.set()
        app.state.jobs.shutdown()


def test_upload_registration_failure_allows_retry_with_the_same_filename(tmp_path, monkeypatch):
    app = create_app(tmp_path / "workspace")
    try:
        home, project = _seed_curve(app, tmp_path)
        target = app.state.workspace / "projects" / str(project) / "uploads/new.csv"
        headers = {"X-CSRF-Token": _csrf(home)}
        original = app.state.catalog.register_dataset

        def reject(*args, **kwargs):
            raise CatalogError("Injected registration failure")

        monkeypatch.setattr(app.state.catalog, "register_dataset", reject)
        response = _request(
            app,
            "POST",
            f"/api/projects/{project}/inspect",
            cookies=home.cookies,
            headers=headers,
            data={"schema": "curve"},
            files={"file": ("new.csv", b"step,strain,stress\n0,0,0\n")},
        )
        assert response.status_code == 500
        assert not target.exists(), "An unregistered upload blocks a valid retry"
        monkeypatch.setattr(app.state.catalog, "register_dataset", original)
        retried = _request(
            app,
            "POST",
            f"/api/projects/{project}/inspect",
            cookies=home.cookies,
            headers=headers,
            data={"schema": "curve"},
            files={"file": ("new.csv", b"step,strain,stress\n0,0,0\n")},
        )
        assert retried.status_code == 200
        assert target.exists()
    finally:
        app.state.jobs.shutdown()
