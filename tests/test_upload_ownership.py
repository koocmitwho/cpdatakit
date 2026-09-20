"""Upload publication and rollback never adopt or delete concurrent writers."""

import json
import os
import shutil
from pathlib import Path

import pytest
import xarray as xr
from test_web_multiformat import LocalClient, project, upload_schema

from cpdatakit import application as api
from cpdatakit.exceptions import CatalogError
from cpdatakit.formats import ZarrWriter
from cpdatakit.web import app as app_module
from cpdatakit.web import create_app, uploads, workbench

pytest_plugins = ["test_application_multiformat"]


def contents(path):
    if path.is_dir():
        return {
            entry.relative_to(path).as_posix(): entry.read_bytes()
            for entry in path.rglob("*")
            if entry.is_file()
        }
    return path.read_bytes() if path.exists() else None


@pytest.fixture(params=["file", "directory"])
def upload_case(tmp_path, request, thermal_value, thermal_schema):
    app = create_app(tmp_path / "workspace")
    client = LocalClient(app)
    home = client.get("/")
    client.headers["X-CSRF-Token"] = home.text.split('name="csrf_token" value="', 1)[1].split(
        '"', 1
    )[0]
    pid = project(client)
    if request.param == "file":
        source = tmp_path / "curve.csv"
        source.write_bytes(b"step,strain,stress\n0,0,0\n1,0.01,12\n")
        suffix = "inspect"
        data = {}
        files = {"file": (source.name, source.read_bytes())}
    else:
        source = ZarrWriter().write(thermal_value, tmp_path / "thermal.zarr")
        suffix = "inspect-zarr"
        data = {"schema": upload_schema(client, pid, thermal_schema)}
        files = [
            ("files", (f"{source.name}/{entry.relative_to(source).as_posix()}", entry.read_bytes()))
            for entry in source.rglob("*")
            if entry.is_file()
        ]
    target = app.state.workspace / "projects" / str(pid) / "uploads" / source.name
    before = contents(source)

    def upload():
        return client.post(f"/api/projects/{pid}/{suffix}", data=data, files=files)

    yield app, pid, source, target, upload
    assert contents(source) == before
    app.state.jobs.shutdown()


def change_target(target, change):
    if change in {"replace", "replace-identical"}:
        moved = target.with_name("moved-original")
        target.rename(moved)
        if change == "replace-identical":
            if moved.is_dir():
                shutil.copytree(moved, target)
            else:
                shutil.copyfile(moved, target)
            return contents(target)
        if target.suffix == ".zarr":
            target.mkdir()
        else:
            target.touch()
    if target.is_dir():
        (target / "foreign.txt").write_bytes(b"foreign writer")
    else:
        target.write_bytes(b"foreign writer")
    return contents(target)


@pytest.mark.parametrize("change", ["replace", "modify", "replace-identical"])
@pytest.mark.parametrize("registration_succeeds", [False, True])
def test_registration_change_preserves_foreign_upload_and_does_not_register_it(
    upload_case, monkeypatch, change, registration_succeeds
):
    app, pid, _source, target, upload = upload_case
    register = app.state.catalog.register_dataset
    foreign = None

    def mutate(*args, **kwargs):
        nonlocal foreign
        foreign = change_target(target, change)
        if registration_succeeds:
            return register(*args, **kwargs)
        raise CatalogError("injected catalog failure")

    monkeypatch.setattr(app.state.catalog, "register_dataset", mutate)
    response = upload()
    assert response.status_code >= 400
    assert contents(target) == foreign
    assert app.state.catalog.list_datasets(pid) == ()
    recovery = response.json()["recovery"]
    assert recovery["reason"] == "target_changed"
    assert (
        json.loads((app.state.workspace / recovery["directory"] / "recovery.json").read_text())
        == recovery
    )


def test_ordinary_registration_failure_removes_only_the_owned_upload(upload_case, monkeypatch):
    app, pid, _source, target, upload = upload_case

    def fail(*args, **kwargs):
        raise CatalogError("injected catalog failure")

    monkeypatch.setattr(app.state.catalog, "register_dataset", fail)
    response = upload()
    assert response.status_code >= 400
    assert not target.exists()
    assert app.state.catalog.list_datasets(pid) == ()


def test_successful_upload_and_conflict_preserve_bytes(upload_case):
    app, pid, source, target, upload = upload_case
    response = upload()
    assert response.status_code == 200, response.text
    assert contents(target) == contents(source)
    duplicate = upload()
    assert duplicate.status_code == 409
    assert contents(target) == contents(source)
    assert len(app.state.catalog.list_datasets(pid)) == 1


@pytest.mark.parametrize("change", ["replace", "modify", "replace-identical"])
@pytest.mark.parametrize("second_writer", [False, True])
def test_change_between_rollback_check_and_detach_retains_quarantine_evidence(
    upload_case, monkeypatch, change, second_writer
):
    app, pid, _source, target, upload = upload_case
    rename = os.rename
    foreign = None

    def race(source, destination, *args, **kwargs):
        nonlocal foreign
        if Path(source) == target and Path(destination).name == "unregistered":
            foreign = change_target(target, change)
            result = rename(source, destination, *args, **kwargs)
            if second_writer:
                target.write_bytes(b"new writer after detach")
            return result
        return rename(source, destination, *args, **kwargs)

    def fail(*args, **kwargs):
        raise CatalogError("injected catalog failure")

    monkeypatch.setattr(os, "rename", race)
    monkeypatch.setattr(app.state.catalog, "register_dataset", fail)
    response = upload()
    assert response.status_code >= 400
    assert foreign is not None, "rollback must detach and recheck before deleting"
    recovery = response.json()["recovery"]
    assert recovery["reason"] == "quarantined_target_changed"
    assert contents(app.state.workspace / recovery["unregistered_upload"]) == foreign
    assert (
        json.loads((app.state.workspace / recovery["directory"] / "recovery.json").read_text())
        == recovery
    )
    if second_writer:
        assert target.read_bytes() == b"new writer after detach"
    assert app.state.catalog.list_datasets(pid) == ()


@pytest.mark.parametrize("change", ["replace", "modify"])
def test_changed_publication_is_not_adopted_as_the_uploaded_dataset(
    upload_case, monkeypatch, change
):
    app, pid, _source, target, upload = upload_case
    foreign = None

    def race(action):
        def call(source, destination, *args, **kwargs):
            nonlocal foreign
            result = action(source, destination, *args, **kwargs)
            if Path(destination) == target:
                foreign = change_target(target, change)
            return result

        return call

    monkeypatch.setattr(app_module, "publish_file", race(app_module.publish_file))
    monkeypatch.setattr(workbench, "publish_directory", race(workbench.publish_directory))
    response = upload()
    assert response.status_code >= 400
    assert contents(target) == foreign
    assert app.state.catalog.list_datasets(pid) == ()
    assert response.json()["recovery"]["reason"] == "target_changed"


def test_concurrent_upload_name_is_never_overwritten(upload_case, monkeypatch):
    app, pid, _source, target, upload = upload_case
    foreign = None

    def race(action):
        def call(staged, destination, *args, **kwargs):
            nonlocal foreign
            if target.suffix == ".zarr":
                target.mkdir()
                (target / "foreign.txt").write_bytes(b"existing writer")
            else:
                target.write_bytes(b"existing writer")
            foreign = contents(target)
            return action(staged, destination, *args, **kwargs)

        return call

    monkeypatch.setattr(app_module, "publish_file", race(app_module.publish_file))
    monkeypatch.setattr(workbench, "publish_directory", race(workbench.publish_directory))
    response = upload()
    assert response.status_code == 409, response.text
    assert contents(target) == foreign
    assert app.state.catalog.list_datasets(pid) == ()


@pytest.mark.parametrize("failure", ["create", "remove"])
def test_rollback_io_failure_returns_recovery_and_keeps_uploaded_content(
    upload_case, monkeypatch, failure
):
    app, pid, source, target, upload = upload_case
    original_mkdtemp, original_rmtree = uploads.tempfile.mkdtemp, uploads.shutil.rmtree

    def cannot_create(*args, **kwargs):
        if kwargs.get("prefix") == ".upload-recovery-":
            raise PermissionError("injected recovery directory failure")
        return original_mkdtemp(*args, **kwargs)

    def cannot_remove(path, *args, **kwargs):
        if Path(path).name.startswith(".upload-recovery-"):
            raise PermissionError("injected quarantined cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    def reject(*args, **kwargs):
        raise CatalogError("injected catalog failure")

    monkeypatch.setattr(app.state.catalog, "register_dataset", reject)
    monkeypatch.setattr(uploads.tempfile, "mkdtemp", cannot_create)
    if failure == "remove":
        monkeypatch.setattr(uploads.tempfile, "mkdtemp", original_mkdtemp)
        monkeypatch.setattr(uploads.shutil, "rmtree", cannot_remove)
    response = upload()
    assert response.status_code == 500
    recovery = response.json()["recovery"]
    assert recovery["reason"] == "upload_cleanup_failed"
    assert response.json()["error"]["action"]
    if failure == "create":
        assert recovery["record_written"] is False
        assert contents(target) == contents(source)
    else:
        assert contents(app.state.workspace / recovery["unregistered_upload"]) == contents(source)
    assert app.state.catalog.list_datasets(pid) == ()


def test_successful_upload_survives_private_staging_cleanup_failure(upload_case, monkeypatch):
    app, pid, source, target, upload = upload_case
    rmtree = shutil.rmtree
    unlink = Path.unlink

    def fail_directory(path, *args, **kwargs):
        if Path(path).name.startswith(".") and Path(path).parent == target.parent:
            raise PermissionError("injected cleanup failure")
        return rmtree(path, *args, **kwargs)

    def fail_file(path, *args, **kwargs):
        if path.name.startswith(".") and path.parent == target.parent:
            raise PermissionError("injected cleanup failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_directory)
    monkeypatch.setattr(Path, "unlink", fail_file)
    response = upload()
    assert response.status_code == 200, response.text
    assert contents(target) == contents(source)
    assert len(app.state.catalog.list_datasets(pid)) == 1


def slice_request(tmp_path, **kwargs):
    source = tmp_path / "temperature.nc"
    xr.Dataset({"temperature": (("y", "x"), [[300.0, 301.0], [302.0, 303.0]])}).to_netcdf(
        source, engine="h5netcdf"
    )
    return api.SliceRequest(source, "temperature", "x", "y", {}, tmp_path / "slice.png", **kwargs)


def test_slice_concurrent_publication_never_overwrites(tmp_path, monkeypatch):
    request = slice_request(tmp_path)
    before = request.data.read_bytes()
    replace, link = os.replace, os.link

    def compete(action):
        def call(source, target, *args, **kwargs):
            if Path(target) == request.output:
                request.output.write_bytes(b"concurrent image")
            return action(source, target, *args, **kwargs)

        return call

    monkeypatch.setattr(os, "replace", compete(replace))
    monkeypatch.setattr(os, "link", compete(link))
    result = api.plot_scientific_slice(request)
    assert not result.ok
    assert request.output.read_bytes() == b"concurrent image"
    assert request.data.read_bytes() == before


def test_slice_cleanup_failure_does_not_reverse_success(tmp_path, monkeypatch):
    request = slice_request(tmp_path)
    unlink = Path.unlink

    def fail(path, *args, **kwargs):
        if path.name.startswith(".slice.png."):
            raise PermissionError("injected cleanup failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail)
    result = api.plot_scientific_slice(request)
    assert result.ok, result.to_dict()
    assert request.output.read_bytes().startswith(b"\x89PNG")


def test_slice_explicit_force_replaces_existing_output(tmp_path):
    request = slice_request(tmp_path, force=True)
    request.output.write_bytes(b"old image")
    result = api.plot_scientific_slice(request)
    assert result.ok, result.to_dict()
    assert request.output.read_bytes().startswith(b"\x89PNG")
