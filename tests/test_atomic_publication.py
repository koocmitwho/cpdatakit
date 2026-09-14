"""Real publication and process races at the common filesystem boundary."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import cpdatakit._atomic as atomic
from cpdatakit.exceptions import CPDataKitError, OutputExistsError


def test_competing_processes_publish_exactly_one_complete_file(tmp_path):
    target = tmp_path / "result"
    gate = tmp_path / "go"
    code = (
        "import sys; sys.path[:] = "
        + repr(sys.path)
        + "\n"
        + """
import time
from pathlib import Path
from cpdatakit._atomic import publish_file
from cpdatakit.exceptions import OutputExistsError
stage, target, gate = map(Path, sys.argv[1:])
while not gate.exists():
    time.sleep(0.01)
try:
    publish_file(stage, target)
except OutputExistsError:
    sys.exit(3)
"""
    )
    sources = [tmp_path / f"stage-{i}" for i in range(4)]
    for index, source in enumerate(sources):
        source.write_bytes(bytes([index]) * 4096)
    processes = [
        subprocess.Popen([sys._base_executable, "-c", code, str(source), str(target), str(gate)])
        for source in sources
    ]
    try:
        gate.touch()
        outcomes = [process.wait(timeout=20) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    assert sorted(outcomes) == [0, 3, 3, 3]
    winner = outcomes.index(0)
    assert target.read_bytes() == bytes([winner]) * 4096
    assert not sources[winner].exists()
    assert all(path.exists() for index, path in enumerate(sources) if index != winner)


def test_unsupported_hard_links_fail_without_changing_either_file(tmp_path, monkeypatch):
    source, target = tmp_path / "stage", tmp_path / "target"
    source.write_bytes(b"complete")

    def unsupported(*args, **kwargs):
        raise OSError("operation not supported")

    monkeypatch.setattr(atomic.os, "link", unsupported)
    with pytest.raises(CPDataKitError, match="hard-link"):
        atomic.publish_file(source, target)
    assert not target.exists()
    assert source.read_bytes() == b"complete"


def test_explicit_force_replaces_and_consumes_staging(tmp_path):
    source, target = tmp_path / "stage", tmp_path / "target"
    source.write_bytes(b"new complete file")
    target.write_bytes(b"old file")
    assert atomic.publish_file(source, target, force=True) == target
    assert target.read_bytes() == b"new complete file"
    assert not source.exists()


def test_committed_file_remains_successful_when_staging_cleanup_fails(
    tmp_path, monkeypatch, caplog
):
    source, target = tmp_path / "stage", tmp_path / "target"
    source.write_bytes(b"complete output")
    original = Path.unlink

    def cannot_cleanup(path, *args, **kwargs):
        if path == source:
            raise PermissionError("injected staging cleanup failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", cannot_cleanup)
    assert atomic.publish_file(source, target) == target
    assert source.samefile(target)
    assert target.read_bytes() == b"complete output"
    assert "staging" in caplog.text and str(source) in caplog.text


@pytest.mark.parametrize("occupied", [False, True])
def test_directory_publication_preserves_even_empty_existing_targets(tmp_path, occupied):
    source, target = tmp_path / "stage", tmp_path / "target"
    source.mkdir()
    (source / "array").write_bytes(b"complete array")
    if occupied:
        target.mkdir()
    publish = getattr(atomic, "publish_directory", None)
    assert callable(publish), "directory publication requires an atomic no-clobber boundary"
    if occupied:
        with pytest.raises(OutputExistsError):
            publish(source, target)
        assert (source / "array").read_bytes() == b"complete array"
        assert not list(target.iterdir())
    else:
        assert publish(source, target) == target
        assert not source.exists()
        assert (target / "array").read_bytes() == b"complete array"


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_native_exclusive_rename_preserves_existing_directory(tmp_path, monkeypatch, platform):
    import ctypes
    import errno
    from types import SimpleNamespace

    source, target = tmp_path / "stage", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "data").write_bytes(b"complete")

    def native(*args):
        if platform == "darwin":
            assert args == (os.fsencode(source), os.fsencode(target), 4)
        else:
            assert args == (-100, os.fsencode(source), -100, os.fsencode(target), 1)
        ctypes.set_errno(errno.EEXIST)
        return -1

    monkeypatch.setattr(atomic.sys, "platform", platform)
    monkeypatch.setattr(
        ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(renamex_np=native, renameat2=native)
    )

    def unsafe_rename(*args):
        raise AssertionError("POSIX plain rename could overwrite an empty directory")

    monkeypatch.setattr(atomic.os, "rename", unsafe_rename)
    with pytest.raises(OutputExistsError):
        atomic.publish_directory(source, target)
    assert (source / "data").read_bytes() == b"complete"
    assert not list(target.iterdir())
