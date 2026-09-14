"""Project pages stay bounded while older records remain explicitly reachable."""

from __future__ import annotations

import hashlib
import json
import re

import pytest
from test_web import _request

from cpdatakit.exceptions import SchemaError
from cpdatakit.schema import schema_to_json
from cpdatakit.web import create_app
from cpdatakit.web.workbench import select_schema


def _seed(app, *, count=1):
    catalog = app.state.catalog
    project = catalog.create_project("Paging study")
    root = app.state.workspace / "projects" / str(project.id)
    root.mkdir(parents=True)
    schema_path = root / "schema.json"
    schema_path.write_text(schema_to_json("curve"), encoding="utf-8")
    schema_hash = hashlib.sha256(schema_path.read_bytes()).hexdigest()
    for index in range(count):
        path = root / f"dataset-{index}.csv"
        path.write_bytes(b"step,strain,stress\n0,0,0\n")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        catalog.register_dataset(project.id, path, sha256=digest)
        catalog.register_artifact(project.id, path, kind="conversion", sha256=digest)
        catalog.register_schema(
            project.id, name=f"schema-{index}", version="1.0", path=schema_path, sha256=schema_hash
        )
        catalog.register_job(
            project.id,
            job_id=f"job-{project.id}-{index}",
            operation="convert",
            status="succeeded",
            output_filename=path.name,
            result={"message": "details loaded on demand"},
        )
    return project.id


def test_project_page_renders_recent_resource_page_with_counts_and_older_controls(tmp_path):
    app = create_app(tmp_path / "workspace")
    try:
        project = _seed(app, count=61)
        response = _request(app, "GET", f"/projects/{project}")
        assert response.status_code == 200
        match = re.search(
            r'<script id="project-resources" type="application/json">(.*?)</script>',
            response.text,
            re.DOTALL,
        )
        assert match is not None
        state = json.loads(match.group(1))
        for resource in ("datasets", "artifacts", "schemas", "jobs"):
            assert len(state[resource]) == 50
            assert state["pagination"]["counts"][resource] == 61
            assert state["pagination"]["has_more"][resource] is True
            assert f'data-load-more="{resource}"' in response.text
        assert state["datasets"][0]["relative_path"].endswith("dataset-60.csv")
        assert state["jobs"][0]["id"] == f"job-{project}-60"
        assert "details loaded on demand" not in response.text
        assert f'data-job-id="job-{project}-60" data-job-status="succeeded"' in response.text
        assert 'data-job-details="' in response.text
    finally:
        app.state.jobs.shutdown()


def test_schema_selection_uses_direct_record_lookup_and_keeps_project_boundary(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path / "workspace")
    try:
        first, second = _seed(app), _seed(app)
        own_schema = app.state.catalog.list_schemas(first)[0]
        foreign_schema = app.state.catalog.list_schemas(second)[0]

        def fail_scan(*args, **kwargs):
            raise AssertionError("Schema selection should not scan the project history")

        monkeypatch.setattr(app.state.catalog, "list_schemas", fail_scan)
        assert select_schema(app, first, f"schema:{own_schema.id}").profile == "curve"
        with pytest.raises(SchemaError, match="belong"):
            select_schema(app, first, f"schema:{foreign_schema.id}")
        with pytest.raises(SchemaError):
            select_schema(app, first, "schema:invalid")
    finally:
        app.state.jobs.shutdown()


def test_artifact_download_uses_direct_lookup_and_rejects_another_project(tmp_path, monkeypatch):
    app = create_app(tmp_path / "workspace")
    try:
        first, second = _seed(app), _seed(app)
        own = app.state.catalog.list_artifacts(first)[0]
        foreign = app.state.catalog.list_artifacts(second)[0]

        def fail_scan(*args, **kwargs):
            raise AssertionError("Artifact download should not scan the project history")

        monkeypatch.setattr(app.state.catalog, "list_artifacts", fail_scan)
        downloaded = _request(app, "GET", f"/api/projects/{first}/artifacts/{own.id}")
        assert downloaded.status_code == 200
        assert downloaded.content == b"step,strain,stress\n0,0,0\n"
        rejected = _request(app, "GET", f"/api/projects/{first}/artifacts/{foreign.id}")
        assert rejected.status_code == 404
    finally:
        app.state.jobs.shutdown()
