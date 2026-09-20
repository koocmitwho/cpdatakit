"""Real process exits leave readable, identity-bound output recovery evidence."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_web_workflows import _csrf, _request

from cpdatakit.web import create_app

CRASH_SCRIPT = r"""
import os, sys
from pathlib import Path
from cpdatakit.web import create_app
from cpdatakit.web import outputs
from cpdatakit.web.artifacts import register_snapshot
from cpdatakit.application import ReportRequest, ComparisonRequest, build_report, compare_reports
from cpdatakit.jobs.manager import JobContext
workspace, boundary, kind = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
app = create_app(workspace)
project = app.state.catalog.create_project('Crash test').id
root = workspace / 'projects' / str(project)
root.mkdir(parents=True)
source = root / 'input.csv'
source.write_text('step,strain,stress\n0,0,0\n1,0.1,100\n')
if kind == 'comparison':
    left, right = root / 'left.json', root / 'right.json'
    left.write_text('{}')
    right.write_text('{}')
    target = root / 'comparison'
    target.mkdir()
    (target / 'old.txt').write_bytes(b'previous output')
    request = ComparisonRequest(left, right, target, workspace=workspace, force=True)
    producer = compare_reports
else:
    target = root / 'report.json'
    target.write_bytes(b'previous output')
    request = ReportRequest(source, 'curve', target, format='json', workspace=workspace, force=True)
    producer = build_report
original_replace, original_publish = os.replace, outputs.publish_file
original_directory = outputs.publish_directory
def replace(source, target):
    if boundary == 'before_backup' and Path(target).name == 'previous': os._exit(23)
    result = original_replace(source, target)
    if boundary == 'backup' and Path(target).name == 'previous': os._exit(23)
    return result
def publish(source, target, **kwargs):
    if boundary == 'before_publish': os._exit(23)
    result = original_publish(source, target, **kwargs)
    if boundary == 'published': os._exit(23)
    return result
def publish_directory(source, target):
    if boundary == 'before_publish': os._exit(23)
    result = original_directory(source, target)
    if boundary == 'published': os._exit(23)
    return result
original_register = app.state.catalog.register_artifact
def register(*args, **kwargs):
    if boundary == 'snapshot': os._exit(23)
    record = original_register(*args, **kwargs)
    if boundary == 'registered': os._exit(23)
    return record
os.replace, outputs.publish_file = replace, publish
outputs.publish_directory = publish_directory
app.state.catalog.register_artifact = register
outputs.produce_registered(
    request, JobContext(), produce=producer, operation='build_report',
    register=lambda path, **kwargs: register_snapshot(app.state.catalog, workspace, project,
        path, kind='report', metadata={}, **kwargs))
raise AssertionError('crash boundary was not reached')
"""


def crash_workspace(tmp_path, boundary, kind="report"):
    workspace = tmp_path / "workspace"
    result = subprocess.run(
        [sys.executable, "-c", CRASH_SCRIPT, str(workspace), boundary, kind],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    assert result.returncode == 23, result.stderr
    return workspace


@pytest.mark.parametrize(
    "boundary", ["before_backup", "backup", "before_publish", "published", "snapshot", "registered"]
)
@pytest.mark.parametrize("kind", ["report", "comparison"])
def test_every_crash_boundary_has_readonly_evidence_and_safe_recovery(tmp_path, boundary, kind):
    workspace = crash_workspace(tmp_path, boundary, kind)
    app = create_app(workspace)
    try:
        before = {
            p.relative_to(workspace).as_posix(): p.read_bytes()
            for p in workspace.rglob("*")
            if p.is_file() and p.suffix not in {".sqlite3", ".lock"}
        }
        response = _request(app, "GET", "/api/projects/1/recovery")
        assert response.status_code == 200, response.text
        entries = response.json()["items"]
        assert len(entries) == 1
        item = entries[0]
        assert item["state"] in {"recoverable", "registered"}
        assert {row["role"] for row in item["candidates"]} >= {"new", "previous"}
        after = {
            p.relative_to(workspace).as_posix(): p.read_bytes()
            for p in workspace.rglob("*")
            if p.is_file() and p.suffix not in {".sqlite3", ".lock"}
        }
        assert before == after
        home = _request(app, "GET", "/")
        url = f"/api/projects/1/recovery/{item['id']}/previous"
        recovered = _request(
            app, "POST", url, cookies=home.cookies, headers={"X-CSRF-Token": _csrf(home)}
        )
        assert recovered.status_code == 200, recovered.text
        recovered_path = workspace / recovered.json()["path"]
        recovered_file = recovered_path / "old.txt" if kind == "comparison" else recovered_path
        assert recovered_file.read_bytes() == b"previous output"
        again = _request(
            app, "POST", url, cookies=home.cookies, headers={"X-CSRF-Token": _csrf(home)}
        )
        assert again.json()["path"] == recovered.json()["path"]
        assert len(app.state.catalog.list_artifacts(1)) == (1 if boundary == "registered" else 0)
        assert "Recovery" in _request(app, "GET", "/projects/1/recovery").text
    finally:
        app.state.jobs.shutdown()


@pytest.mark.parametrize("change", ["target", "manifest", "destination", "project"])
def test_recovery_conflicts_preserve_all_evidence(tmp_path, change):
    workspace = crash_workspace(tmp_path, "snapshot")
    app = create_app(workspace)
    try:
        response = _request(app, "GET", "/api/projects/1/recovery")
        assert response.status_code == 200
        item = response.json()["items"][0]
        if change == "target":
            (workspace / "projects/1/report.json").write_bytes(b"foreign replacement")
        elif change == "manifest":
            manifest = next((workspace / ".output-transactions/1").glob("*.json"))
            manifest.write_text('{"broken":', encoding="utf-8")
        elif change == "destination":
            destination = workspace / item["candidates"][0]["destination"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"unrelated destination")
        else:
            other = app.state.catalog.create_project("Other").id
            assert _request(app, "GET", f"/api/projects/{other}/recovery").json()["items"] == []
        before = {
            p: p.read_bytes()
            for p in workspace.rglob("*")
            if p.is_file() and p.suffix not in {".sqlite3", ".lock"}
        }
        home = _request(app, "GET", "/")
        project = 2 if change == "project" else 1
        role = item["candidates"][0]["role"] if change == "destination" else "previous"
        response = _request(
            app,
            "POST",
            f"/api/projects/{project}/recovery/{item['id']}/{role}",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
        )
        assert response.status_code in {404, 409}
        assert all(
            path.exists() and path.read_bytes() == content for path, content in before.items()
        )
    finally:
        app.state.jobs.shutdown()


@pytest.mark.parametrize(
    "recovered", [[], {"previous": []}, {"previous": {"path": "projects/1/x", "signature": []}}]
)
def test_malformed_recovered_records_are_conflicts_before_offering_actions(tmp_path, recovered):
    workspace = crash_workspace(tmp_path, "snapshot")
    manifest = next((workspace / ".output-transactions/1").glob("*.json"))
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["recovered"] = recovered
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    app = create_app(workspace)
    try:
        result = _request(app, "GET", "/api/projects/1/recovery")
        assert result.status_code == 200
        assert result.json()["items"][0]["state"] == "conflict"
        assert "<button>Recover" not in _request(app, "GET", "/projects/1/recovery").text
        home = _request(app, "GET", "/")
        response = _request(
            app,
            "POST",
            f"/api/projects/1/recovery/{payload['id']}/previous",
            cookies=home.cookies,
            headers={"X-CSRF-Token": _csrf(home)},
        )
        assert response.status_code == 409
        assert json.loads(manifest.read_text(encoding="utf-8")) == payload
    finally:
        app.state.close()
