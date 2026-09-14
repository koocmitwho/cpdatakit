"""Registration failures preserve foreign outputs and release job admission."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from cpdatakit.application import ComparisonRequest, ReportRequest, build_report, compare_reports
from cpdatakit.application.data_access import path_sha256
from cpdatakit.catalog import SQLiteCatalog
from cpdatakit.exceptions import CatalogError
from cpdatakit.jobs.manager import JobContext
from cpdatakit.web import outputs
from cpdatakit.web.artifacts import register_snapshot


def _operation(root, kind, existing=False):
    if kind == "comparison":
        left, right = root / "left.json", root / "right.json"
        left.write_text("{}")
        right.write_text("{}")
        target = root / "comparison"
        request = ComparisonRequest(left, right, target, workspace=root, force=existing)
        producer = compare_reports
    else:
        source = root / "input.csv"
        source.write_text("step,strain,stress\n0,0,0\n")
        target = root / "report.json"
        request = ReportRequest(
            source, "curve", target, format="json", workspace=root, force=existing
        )
        producer = build_report
    if existing:
        if kind == "comparison":
            target.mkdir()
            (target / "old.txt").write_bytes(b"original output")
        else:
            target.write_bytes(b"original output")
    return request, producer, target


def _contents(path):
    if path.is_dir():
        return {
            entry.relative_to(path).as_posix(): entry.read_bytes()
            for entry in path.rglob("*")
            if entry.is_file()
        }
    return path.read_bytes() if path.exists() else None


def test_comparison_registration_failure_retains_structured_cause_and_input_names(tmp_path):
    request, producer, target = _operation(tmp_path, "comparison")

    def reject(path, **kwargs):
        raise CatalogError("Catalog rejected the comparison snapshot")

    result = outputs.produce_registered(
        request, JobContext(), produce=producer, register=reject, operation="compare_reports"
    )

    assert result.status == "failed"
    assert "Catalog rejected the comparison snapshot" in result.error.message
    assert result.provenance["left_filename"] == "left.json"
    assert result.provenance["right_filename"] == "right.json"
    assert not target.exists()


@pytest.mark.parametrize("kind", ["report", "comparison"])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("change", ["replace", "modify", "replace-identical"])
def test_failed_registration_preserves_foreign_target_and_previous_backup(
    tmp_path, kind, existing, change
):
    request, producer, target = _operation(tmp_path, kind, existing)
    previous = _contents(target)
    foreign = None

    def replace_before_failure(path, **kwargs):
        nonlocal foreign
        if change == "modify":
            if path.is_dir():
                (path / "foreign.txt").write_bytes(b"foreign addition")
            else:
                path.write_bytes(b"foreign modified output")
        else:
            replacement = tmp_path / "replacement"
            if path.is_dir():
                if change == "replace-identical":
                    shutil.copytree(path, replacement)
                else:
                    replacement.mkdir()
                    (replacement / "foreign.txt").write_bytes(b"foreign replacement")
                path.rename(tmp_path / "externally-moved-output")
            else:
                replacement.write_bytes(
                    path.read_bytes() if change == "replace-identical" else b"foreign replacement"
                )
            os.replace(replacement, path)
        foreign = _contents(path)
        raise CatalogError("Snapshot registration failed after a concurrent write")

    result = outputs.produce_registered(
        request,
        JobContext(),
        produce=producer,
        register=replace_before_failure,
        operation="compare_reports" if kind == "comparison" else "build_report",
    )

    assert result.status == "failed"
    assert _contents(target) == foreign
    recovery = result.provenance["recovery"]
    assert recovery["reason"] == "target_changed"
    assert json.loads((tmp_path / recovery["directory"] / "recovery.json").read_text()) == recovery
    if existing:
        assert _contents(tmp_path / recovery["previous_output"]) == previous


@pytest.mark.parametrize("kind", ["report", "comparison"])
def test_rollback_restoration_does_not_overwrite_a_new_target(tmp_path, monkeypatch, kind):
    request, producer, target = _operation(tmp_path, kind, existing=True)
    previous = _contents(target)
    original_replace = outputs.os.replace
    original_file = outputs.publish_file
    original_directory = outputs.publish_directory
    appeared = False

    def compete(source, destination):
        nonlocal appeared
        if Path(source).name == "previous" and Path(destination) == target:
            appeared = True
            target.write_bytes(b"foreign output during restoration")

    def replace(source, destination):
        compete(source, destination)
        return original_replace(source, destination)

    def publish_file(source, destination, **kwargs):
        compete(source, destination)
        return original_file(source, destination, **kwargs)

    def publish_directory(source, destination):
        compete(source, destination)
        return original_directory(source, destination)

    monkeypatch.setattr(outputs.os, "replace", replace)
    monkeypatch.setattr(outputs, "publish_file", publish_file)
    monkeypatch.setattr(outputs, "publish_directory", publish_directory)

    def reject(path, **kwargs):
        raise CatalogError("Snapshot registration failed")

    result = outputs.produce_registered(
        request,
        JobContext(),
        produce=producer,
        register=reject,
        operation="compare_reports" if kind == "comparison" else "build_report",
    )

    assert appeared
    assert result.status == "failed"
    assert target.read_bytes() == b"foreign output during restoration"
    assert _contents(tmp_path / result.provenance["recovery"]["previous_output"]) == previous


@pytest.mark.parametrize("exception_kind", ["sqlite", "os", "runtime"])
def test_any_registration_failure_releases_admission_and_shutdown(tmp_path, exception_kind):
    script = (
        "import sys; sys.path[:] = "
        + repr(sys.path)
        + "\n"
        + r"""
import json
import sqlite3
import threading
from pathlib import Path
from test_web_workflows import _csrf, _request, _seed_curve
from cpdatakit.web import create_app
root = Path(sys.argv[1])
app = create_app(root / 'workspace')
home, project = _seed_curve(app, root)
dataset = app.state.catalog.list_datasets(project)[0].id
entered = threading.Event()
original_submit = app.state.jobs.submit
def submit(operation, function, **kwargs):
    def started(context):
        entered.set()
        return function(context)
    return original_submit(operation, started, **kwargs)
app.state.jobs.submit = submit
def fail_registration(*args, **kwargs):
    assert entered.wait(5), 'worker did not start'
    errors = {'sqlite': sqlite3.OperationalError, 'os': OSError, 'runtime': RuntimeError}
    raise errors[sys.argv[2]]('Injected job registration failure')
app.state.catalog.register_job = fail_registration
try:
    response = _request(app, 'POST', f'/api/projects/{project}/report',
        cookies=home.cookies, headers={'X-CSRF-Token': _csrf(home)},
        data={'dataset_id': dataset, 'schema': 'curve', 'output': 'result.json', 'format': 'json'})
    response_status = response.status_code
except Exception as exc:
    response_status = type(exc).__name__
app.state.jobs.shutdown()
print(json.dumps({'response_status': response_status, 'remaining_jobs': len(app.state.jobs.list()),
    'output_exists': (root / 'workspace/projects' / str(project) / 'result.json').exists()}))
"""
    )
    try:
        result = subprocess.run(
            [sys._base_executable, "-c", script, str(tmp_path), exception_kind],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("failed admission left a worker blocked during shutdown")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "response_status": 500,
        "remaining_jobs": 0,
        "output_exists": False,
    }


@pytest.mark.parametrize("kind", ["report", "comparison"])
@pytest.mark.parametrize("second_writer", [False, True])
def test_foreign_replacement_during_rollback_detachment_is_never_deleted(
    tmp_path, monkeypatch, kind, second_writer
):
    request, producer, target = _operation(tmp_path, kind, existing=True)
    previous = _contents(target)
    original_rename = outputs.os.rename
    original_publish_file = outputs.publish_file
    detached = False

    def replace_before_detaching(source, destination):
        nonlocal detached
        if Path(source) == target and Path(destination).name == "unregistered":
            detached = True
            original_rename(target, tmp_path / "externally-moved-output")
            target.write_bytes(b"first foreign writer")
        return original_rename(source, destination)

    def compete_with_restore(source, destination, **kwargs):
        if second_writer and Path(source).name == "unregistered" and Path(destination) == target:
            target.write_bytes(b"second foreign writer")
        return original_publish_file(source, destination, **kwargs)

    monkeypatch.setattr(outputs.os, "rename", replace_before_detaching)
    monkeypatch.setattr(outputs, "publish_file", compete_with_restore)

    def reject(path, **kwargs):
        raise CatalogError("Snapshot registration failed")

    result = outputs.produce_registered(
        request,
        JobContext(),
        produce=producer,
        register=reject,
        operation="compare_reports" if kind == "comparison" else "build_report",
    )

    assert detached
    assert result.status == "failed"
    recovery = result.provenance["recovery"]
    assert _contents(tmp_path / recovery["previous_output"]) == previous
    if second_writer:
        assert target.read_bytes() == b"second foreign writer"
        assert (tmp_path / recovery["unregistered_output"]).read_bytes() == b"first foreign writer"
    else:
        assert target.read_bytes() == b"first foreign writer"


def _project_catalog(workspace):
    catalog = SQLiteCatalog(workspace / "catalog.sqlite3", workspace)
    catalog.initialize()
    project = catalog.create_project("snapshot binding")
    root = workspace / "projects" / str(project.id)
    root.mkdir(parents=True)
    return catalog, project.id, root


@pytest.mark.parametrize("kind", ["report", "comparison"])
@pytest.mark.parametrize("existing", [False, True])
def test_registration_binds_snapshot_to_produced_bytes_before_concurrent_replacement(
    tmp_path, kind, existing
):
    catalog, project, root = _project_catalog(tmp_path)
    request, producer, target = _operation(root, kind, existing)
    request = replace(request, workspace=tmp_path)
    previous = _contents(target)
    foreign = None

    def replace_then_register(path, **kwargs):
        nonlocal foreign
        path.rename(root / "externally-moved-output")
        if kind == "comparison":
            path.mkdir()
            (path / "foreign.txt").write_bytes(b"foreign output")
        else:
            path.write_bytes(b"foreign output")
        foreign = _contents(path)
        return register_snapshot(catalog, tmp_path, project, path, kind=kind, metadata={}, **kwargs)

    result = outputs.produce_registered(
        request,
        JobContext(),
        produce=producer,
        register=replace_then_register,
        operation="compare_reports" if kind == "comparison" else "build_report",
    )

    assert result.status == "failed"
    assert catalog.list_artifacts(project) == ()
    assert _contents(target) == foreign
    assert not list((root / ".artifacts").glob("version-*"))
    if existing:
        assert _contents(tmp_path / result.provenance["recovery"]["previous_output"]) == previous


def test_snapshot_rejects_a_known_digest_mismatch_before_creating_catalog_state(tmp_path):
    catalog, project, root = _project_catalog(tmp_path)
    target = root / "output.json"
    target.write_bytes(b"original output")
    expected = path_sha256(target)
    target.write_bytes(b"foreign output")

    with pytest.raises(CatalogError, match="changed"):
        register_snapshot(
            catalog, tmp_path, project, target, kind="report", metadata={}, expected_sha256=expected
        )

    assert target.read_bytes() == b"foreign output"
    assert catalog.list_artifacts(project) == ()
    assert not (root / ".artifacts").exists()


@pytest.mark.parametrize("kind", ["report", "comparison"])
def test_matching_produced_bytes_remain_registered_and_downloadable(tmp_path, kind):
    catalog, project, root = _project_catalog(tmp_path)
    request, producer, target = _operation(root, kind)
    request = replace(request, workspace=tmp_path)

    def register(path, **kwargs):
        return register_snapshot(catalog, tmp_path, project, path, kind=kind, metadata={}, **kwargs)

    result = outputs.produce_registered(
        request,
        JobContext(),
        produce=producer,
        register=register,
        operation="compare_reports" if kind == "comparison" else "build_report",
    )

    assert result.status == "succeeded", result.to_dict()
    (record,) = catalog.list_artifacts(project)
    assert result.provenance["artifact_id"] == record.id
    assert result.artifact == record.relative_path
    assert _contents(tmp_path / record.relative_path) == _contents(target)
    assert record.sha256 == path_sha256(target)
