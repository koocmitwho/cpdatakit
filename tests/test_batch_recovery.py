"""Interruptions may be resumed only from durable, matching conversion evidence."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import cpdatakit.application.batch as batch


def single_batch(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("step,strain,stress\n0,0.0,0.0\n1,0.01,20.0\n")
    config = tmp_path / "batch.json"
    config.write_text(
        json.dumps(
            {
                "batch_version": 1,
                "defaults": {"schema": "curve"},
                "items": [{"input": "input.csv", "output": "output.h5"}],
            }
        )
    )
    return source, config, tmp_path / "run.json", tmp_path / "output.h5"


@pytest.mark.parametrize(
    "role", ["mapping", "schema", "config", "included_schema", "extended_schema"]
)
def test_batch_lock_preserves_every_configured_input(tmp_path, role):
    _, config, manifest, output = single_batch(tmp_path)
    lock = manifest.with_name(manifest.name + ".lock")
    payload = json.loads(config.read_text())
    lock.write_bytes(b"")
    if role in {"mapping", "schema"}:
        payload["items"][0][role] = lock.name
    elif role == "config":
        config = lock
    else:
        schema = tmp_path / "composed.json"
        field = "includes" if role == "included_schema" else "extends"
        schema.write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "profile": "thermal",
                    field: [lock.name] if field == "includes" else lock.name,
                }
            )
        )
        payload["items"][0]["schema"] = schema.name
    config.write_text(json.dumps(payload))
    before = lock.read_bytes()
    result = batch.run_batch(config, manifest)
    assert not result.ok
    assert lock.read_bytes() == before
    assert not manifest.exists()
    assert not output.exists()


@pytest.mark.parametrize("alias", ["hardlink", "symlink"])
def test_batch_lock_preserves_aliases_of_mapping_inputs(tmp_path, alias):
    _, config, manifest, _ = single_batch(tmp_path)
    lock = manifest.with_name(manifest.name + ".lock")
    mapping = tmp_path / "mapping.json"
    mapping.write_bytes(b"")
    if alias == "hardlink":
        os.link(mapping, lock)
    else:
        try:
            lock.symlink_to(mapping)
        except OSError:
            pytest.skip("symbolic link creation unavailable")
    payload = json.loads(config.read_text())
    payload["items"][0]["mapping"] = mapping.name
    config.write_text(json.dumps(payload))
    result = batch.run_batch(config, manifest)
    assert not result.ok
    assert mapping.read_bytes() == b""
    assert not manifest.exists()


def interrupt_after_output(monkeypatch, manifest, output):
    original = batch._json_write

    def interrupt(path, report):
        if path == manifest and output.exists() and report["items"][0]["status"] == "succeeded":
            raise KeyboardInterrupt("output published, manifest not completed")
        return original(path, report)

    monkeypatch.setattr(batch, "_json_write", interrupt)
    return original


def test_retry_recovers_output_published_before_manifest_completion(tmp_path, monkeypatch):
    _, config, manifest, output = single_batch(tmp_path)
    original = interrupt_after_output(monkeypatch, manifest, output)
    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(config, manifest)
    before = output.read_bytes()
    modified = output.stat().st_mtime_ns
    monkeypatch.setattr(batch, "_json_write", original)
    result = batch.run_batch(config, manifest, retry=True)
    assert result.ok, result.to_dict()
    assert result.value["counts"] == {"succeeded": 0, "failed": 0, "skipped": 1}
    assert output.read_bytes() == before
    assert output.stat().st_mtime_ns == modified
    record = result.value["items"][0]
    assert record["result"]["artifact"] == "output.h5"
    assert record["result"]["value"]["artifact"] == "output.h5"


@pytest.mark.parametrize("change", ["input", "output"])
def test_retry_refuses_changed_evidence_after_interruption(tmp_path, monkeypatch, change):
    source, config, manifest, output = single_batch(tmp_path)
    original = interrupt_after_output(monkeypatch, manifest, output)
    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(config, manifest)
    if change == "input":
        source.write_text("step,strain,stress\n0,0.0,500.0\n")
    else:
        output.write_bytes(b"changed user output")
    before = output.read_bytes()
    monkeypatch.setattr(batch, "_json_write", original)
    result = batch.run_batch(config, manifest, retry=True)
    assert not result.ok
    assert output.read_bytes() == before
    assert result.value["items"][0]["result"]["error"]["code"] == change + "_changed"


def test_batch_persists_intent_before_conversion_and_refuses_inputs_changed_during_it(
    tmp_path, monkeypatch
):
    source, config, manifest, output = single_batch(tmp_path)
    original = batch.convert_and_write
    captured = []

    def conversion(request):
        captured.append(json.loads(manifest.read_text())["items"][0])
        result = original(request)
        source.write_text("step,strain,stress\n0,0.0,100.0\n")
        return result

    monkeypatch.setattr(batch, "convert_and_write", conversion)
    result = batch.run_batch(config, manifest)
    assert not result.ok
    assert not output.exists()
    assert captured[0]["status"] == "running"
    assert captured[0]["fingerprint"]
    assert captured[0]["parameters"]["input_sha256"]
    assert Path(captured[0]["staged_output"]).name == output.name
    assert result.value["items"][0]["result"]["error"]["code"] == "input_changed"


def test_retry_reclaims_stale_process_lock(tmp_path):
    _, config, manifest, _ = single_batch(tmp_path)
    lock = manifest.with_name(manifest.name + ".lock")
    lock.write_text("2147483647")
    result = batch.run_batch(config, manifest, retry=True)
    assert result.ok, result.to_dict()


def test_recovery_reuses_completed_staging_without_reconverting(tmp_path, monkeypatch):
    _, config, manifest, output = single_batch(tmp_path)
    original = batch._json_write

    def interrupt(path, report):
        original(path, report)
        if report["items"][0]["status"] == "prepared":
            raise KeyboardInterrupt("prepared output, not yet published")

    monkeypatch.setattr(batch, "_json_write", interrupt)
    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(config, manifest)
    assert not output.exists()
    monkeypatch.setattr(batch, "_json_write", original)

    def unexpected_conversion(request):
        raise AssertionError("verified staged result should be recovered")

    monkeypatch.setattr(batch, "convert_and_write", unexpected_conversion)
    result = batch.run_batch(config, manifest, retry=True)
    assert result.ok, result.to_dict()
    assert output.exists()


def test_retry_recovers_after_completion_manifest_write_raises_oserror(tmp_path, monkeypatch):
    _, config, manifest, output = single_batch(tmp_path)
    original = batch._json_write
    interrupted = False

    def disk_failure(path, report):
        nonlocal interrupted
        if output.exists() and report["items"][0]["status"] == "succeeded" and not interrupted:
            interrupted = True
            raise OSError("manifest disk error")
        return original(path, report)

    monkeypatch.setattr(batch, "_json_write", disk_failure)
    assert not batch.run_batch(config, manifest).ok
    saved = output.read_bytes()
    result = batch.run_batch(config, manifest, retry=True)
    assert result.ok, result.to_dict()
    assert result.value["counts"]["skipped"] == 1
    assert output.read_bytes() == saved


def test_cleanup_failure_does_not_invalidate_completed_batch(tmp_path, monkeypatch):
    _, config, manifest, output = single_batch(tmp_path)
    original = Path.rmdir

    def cleanup_failure(path):
        if path.name.startswith(".output.h5.batch-"):
            raise PermissionError("cleanup unavailable")
        return original(path)

    monkeypatch.setattr(Path, "rmdir", cleanup_failure)
    result = batch.run_batch(config, manifest)
    assert result.ok, result.to_dict()
    assert output.exists()
    assert batch.run_batch(config, manifest, retry=True).ok


def test_recovered_batch_removes_empty_staging_directory(tmp_path, monkeypatch):
    _, config, manifest, output = single_batch(tmp_path)
    original = interrupt_after_output(monkeypatch, manifest, output)
    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(config, manifest)
    staged = Path(json.loads(manifest.read_text())["items"][0]["staged_output"])
    assert staged.parent.exists()
    monkeypatch.setattr(batch, "_json_write", original)
    assert batch.run_batch(config, manifest, retry=True).ok
    assert not staged.parent.exists()


@pytest.mark.parametrize("output_format", ["hdf5", "netcdf", "parquet", "zarr"])
def test_batch_recovers_real_format_outputs_from_prepared_staging(
    tmp_path, monkeypatch, output_format
):
    from test_hdf5_v2 import SCHEMA, _value

    from cpdatakit.formats.netcdf import NetCDFWriter

    _, config, manifest, _ = single_batch(tmp_path)
    payload = json.loads(config.read_text())
    suffix = {"hdf5": ".h5", "netcdf": ".nc", "parquet": ".parquet", "zarr": ".zarr"}[output_format]
    target = tmp_path / ("recovered" + suffix)
    payload["items"][0].update(output=target.name, output_format=output_format)
    if output_format != "parquet":
        source = tmp_path / "thermal.nc"
        NetCDFWriter().write(_value(), source)
        payload["items"][0].update(input=source.name, schema=str(SCHEMA))
    config.write_text(json.dumps(payload))
    original = batch._json_write

    def interrupt(path, report):
        original(path, report)
        if report["items"][0]["status"] == "prepared":
            raise KeyboardInterrupt

    monkeypatch.setattr(batch, "_json_write", interrupt)
    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(config, manifest)
    monkeypatch.setattr(batch, "_json_write", original)
    result = batch.run_batch(config, manifest, retry=True)
    assert result.ok, result.to_dict()
    assert target.exists()
    assert result.value["items"][0]["result"]["artifact"] == target.name


def test_retry_preserves_changed_prepared_staging(tmp_path, monkeypatch):
    _, config, manifest, output = single_batch(tmp_path)
    original = batch._json_write

    def interrupt(path, report):
        original(path, report)
        if report["items"][0]["status"] == "prepared":
            raise KeyboardInterrupt

    monkeypatch.setattr(batch, "_json_write", interrupt)
    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(config, manifest)
    staged = Path(json.loads(manifest.read_text())["items"][0]["staged_output"])
    staged.write_bytes(b"changed staging")
    monkeypatch.setattr(batch, "_json_write", original)
    result = batch.run_batch(config, manifest, retry=True)
    assert not result.ok
    assert result.value["items"][0]["result"]["error"]["code"] == "output_changed"
    assert not output.exists()
    assert staged.read_bytes() == b"changed staging"


def test_live_batch_lock_blocks_second_process_then_releases_after_crash(tmp_path):
    _, config, manifest, output = single_batch(tmp_path)
    ready = tmp_path / "ready"
    code = """
import sys, time
from pathlib import Path
import cpdatakit.application.batch as batch
def blocked(request):
    Path(sys.argv[3]).touch()
    time.sleep(120)
batch.convert_and_write = blocked
batch.run_batch(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    import time

    code = "import sys; sys.path[:] = " + repr(sys.path) + "\n" + code
    process = subprocess.Popen(
        [sys._base_executable, "-c", code, str(config), str(manifest), str(ready)],
        env={**os.environ},
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.02)
        assert ready.exists()
        assert not batch.run_batch(config, manifest, retry=True).ok
    finally:
        process.kill()
        process.wait(timeout=10)
    result = batch.run_batch(config, manifest, retry=True)
    assert result.ok, result.to_dict()
    assert output.exists()
