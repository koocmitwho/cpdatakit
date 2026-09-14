"""File publication must preserve competing outputs and failed replacements."""

from pathlib import Path

import matplotlib.pyplot as plt
import pytest
from test_hdf5_v2 import SCHEMA, _value

from cpdatakit.exceptions import OutputExistsError
from cpdatakit.io import write_hdf5, write_hdf5_v2
from cpdatakit.plotting import save_figure
from cpdatakit.schema import load_schema
from cpdatakit.validation import validate_dataset


@pytest.mark.parametrize("kind", ["hdf5", "hdf5_v2", "netcdf", "parquet"])
def test_file_writer_rejects_a_target_created_while_serializing(tmp_path, monkeypatch, curve, kind):
    import tempfile

    from cpdatakit.formats.netcdf import NetCDFWriter
    from cpdatakit.formats.parquet import ParquetWriter

    suffix = {"hdf5": ".h5", "hdf5_v2": ".h5", "netcdf": ".nc", "parquet": ".parquet"}[kind]
    target = tmp_path / ("output" + suffix)
    original = tempfile.mkstemp

    def competing_file(*args, **kwargs):
        result = original(*args, **kwargs)
        target.write_bytes(b"other process output")
        return result

    monkeypatch.setattr(tempfile, "mkstemp", competing_file)
    with pytest.raises(OutputExistsError):
        if kind == "hdf5":
            schema = load_schema("curve")
            write_hdf5(curve, target, schema, validate_dataset(curve, schema))
        elif kind == "hdf5_v2":
            write_hdf5_v2(_value(), target, SCHEMA)
        elif kind == "netcdf":
            NetCDFWriter().write(_value(), target)
        else:
            ParquetWriter().write(curve, target)
    assert target.read_bytes() == b"other process output"
    assert list(tmp_path.iterdir()) == [target]


def test_plot_rejects_target_appearing_during_render(tmp_path, monkeypatch):
    target = tmp_path / "plot.png"
    figure, axis = plt.subplots()
    axis.plot([0, 1], [0, 2])
    original = figure.savefig

    def competing_file(*args, **kwargs):
        target.write_bytes(b"other plot")
        return original(*args, **kwargs)

    monkeypatch.setattr(figure, "savefig", competing_file)
    try:
        with pytest.raises(OutputExistsError):
            save_figure(figure, target)
        assert target.read_bytes() == b"other plot"
        assert list(tmp_path.iterdir()) == [target]
    finally:
        plt.close(figure)


def test_failed_plot_render_preserves_previous_file_with_force(tmp_path, monkeypatch):
    target = tmp_path / "plot.svg"
    target.write_bytes(b"previous complete plot")
    figure, _ = plt.subplots()

    def interrupted(path, **kwargs):
        Path(path).write_bytes(b"partial rendering")
        raise OSError("render failed")

    monkeypatch.setattr(figure, "savefig", interrupted)
    try:
        with pytest.raises(OSError, match="render failed"):
            save_figure(figure, target, force=True)
        assert target.read_bytes() == b"previous complete plot"
        assert list(tmp_path.iterdir()) == [target]
    finally:
        plt.close(figure)


def test_zarr_writer_preserves_directory_appearing_after_final_preflight(tmp_path, monkeypatch):
    from cpdatakit.formats.zarr import ZarrWriter

    target = tmp_path / "output.zarr"
    original = Path.exists
    checks = 0

    def competing_directory(path):
        nonlocal checks
        result = original(path)
        if path == target:
            checks += 1
            if checks == 2:
                target.mkdir()
        return result

    monkeypatch.setattr(Path, "exists", competing_directory)
    with pytest.raises(OutputExistsError):
        ZarrWriter().write(_value(), target)
    assert target.is_dir()
    assert not list(target.iterdir())


def test_completed_plot_is_successful_when_staging_cleanup_fails(tmp_path, monkeypatch):
    target = tmp_path / "plot.png"
    original = Path.unlink

    def cannot_cleanup(path, *args, **kwargs):
        if path.parent == tmp_path and path.name.startswith(".plot.png."):
            raise PermissionError("injected staging cleanup failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", cannot_cleanup)
    figure, axis = plt.subplots()
    axis.plot([0, 1], [0, 2])
    try:
        assert save_figure(figure, target) == target
        assert target.read_bytes().startswith(b"\x89PNG")
        assert len(list(tmp_path.glob(".plot.png.*"))) == 1
    finally:
        plt.close(figure)


@pytest.mark.parametrize("kind", ["hdf5", "hdf5_v2", "netcdf", "parquet"])
def test_completed_writer_retains_success_when_staging_cannot_be_removed(
    tmp_path, monkeypatch, curve, kind
):
    from cpdatakit.formats.netcdf import NetCDFWriter
    from cpdatakit.formats.parquet import ParquetWriter

    suffix = {"hdf5": ".h5", "hdf5_v2": ".h5", "netcdf": ".nc", "parquet": ".parquet"}[kind]
    target = tmp_path / ("output" + suffix)
    original = Path.unlink

    def cannot_cleanup(path, *args, **kwargs):
        if path.parent == tmp_path and path.name.startswith(".output"):
            raise PermissionError("injected staging cleanup failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", cannot_cleanup)
    if kind == "hdf5":
        schema = load_schema("curve")
        result = write_hdf5(curve, target, schema, validate_dataset(curve, schema))
    elif kind == "hdf5_v2":
        result = write_hdf5_v2(_value(), target, SCHEMA)
    elif kind == "netcdf":
        result = NetCDFWriter().write(_value(), target)
    else:
        result = ParquetWriter().write(curve, target)
    assert result == target
    assert target.stat().st_size > 0
    stage = list(tmp_path.glob(".output*"))
    assert len(stage) == 1 and stage[0].samefile(target)
