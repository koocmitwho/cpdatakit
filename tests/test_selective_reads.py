import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import xarray as xr

from cpdatakit.data import ScientificDataset
from cpdatakit.exceptions import DataReadError
from cpdatakit.formats import (
    NetCDFReader,
    NetCDFWriter,
    ParquetReader,
    Selection,
    ZarrReader,
    ZarrWriter,
)


@pytest.fixture(params=["netcdf", "zarr"])
def field_file(request, tmp_path):
    data = xr.Dataset(
        {
            "temperature": (("time", "y", "x"), np.arange(120).reshape(6, 4, 5)),
            "unused": (("time", "y", "x"), np.ones((6, 4, 5))),
        },
        coords={
            "time": ("time", np.arange(6) * 10, {"units": "s"}),
            "y": [0.0, 1.0, 2.0, 3.0],
            "x": [0.0, 1.0, 2.0, 3.0, 4.0],
            "stage": ("time", ["a", "b", "c", "d", "e", "f"]),
        },
    )
    data.temperature.attrs["units"] = "K"
    value = ScientificDataset(data, {"source_doi": "fixture", "units": {"temperature": "K"}})
    if request.param == "netcdf":
        path, reader, writer = tmp_path / "field.nc", NetCDFReader(), NetCDFWriter()
    else:
        path, reader, writer = tmp_path / "field.zarr", ZarrReader(), ZarrWriter()
    writer.write(value, path)
    return path, reader


def test_xarray_reads_select_fields_and_records_before_materialization(field_file, monkeypatch):
    path, reader = field_file
    full = reader.load(path)
    original = xr.Dataset.load
    observed = []

    def bounded_load(dataset, **kwargs):
        observed.append((set(dataset.data_vars), dict(dataset.sizes)))
        assert "unused" not in dataset.data_vars, "unselected variable was materialized"
        assert dataset.sizes["time"] == 2, "unselected records were materialized"
        return original(dataset, **kwargs)

    monkeypatch.setattr(xr.Dataset, "load", bounded_load)
    result = reader.load(path, selection=Selection(("temperature",), 2, 4))
    xr.testing.assert_identical(result.data, full.data[["temperature"]].isel(time=slice(2, 4)))
    assert result.metadata == full.metadata
    assert result.source == path
    assert observed


def test_named_slices_preserve_dimensions_coordinates_and_units(field_file):
    path, reader = field_file
    full = reader.load(path)
    result = reader.load(
        path,
        selection=Selection(
            fields=("temperature",), indexers={"time": 2, "y": slice(1, 3), "x": slice(0, 5, 2)}
        ),
    )
    assert result.data.temperature.shape == (1, 2, 3)
    assert result.data.temperature.values.tolist() == [[[45, 47, 49], [50, 52, 54]]]
    xr.testing.assert_identical(
        result.data,
        full.data[["temperature"]].isel(time=slice(2, 3), y=slice(1, 3), x=slice(0, 5, 2)),
    )
    assert result.data.temperature.attrs["units"] == "K"


@pytest.mark.parametrize("indexers", [{"missing": 0}, {"time": 6}, {"x": slice(0, 6)}])
def test_named_slices_reject_unknown_or_out_of_range_axes(field_file, indexers):
    path, reader = field_file
    with pytest.raises(DataReadError):
        reader.load(path, selection=Selection(fields=("temperature",), indexers=indexers))


def test_record_range_and_named_range_cannot_silently_override_each_other(field_file):
    path, reader = field_file
    with pytest.raises(DataReadError, match="time"):
        reader.load(path, selection=Selection(("temperature",), 1, 3, {"time": 2}))


@pytest.mark.parametrize(
    "indexers", [{"time": -1}, {"time": True}, {"x": slice(2, 1)}, {"x": slice(None, None, 0)}]
)
def test_selection_rejects_invalid_named_ranges(indexers):
    with pytest.raises(ValueError):
        Selection(indexers=indexers)


def test_parquet_skips_unrelated_row_groups_and_preserves_pandas_index(tmp_path, monkeypatch):
    path = tmp_path / "data.parquet"
    frame = pd.DataFrame(
        {"a": range(30), "b": range(30, 60)}, index=pd.Index(range(100, 130), name="source_id")
    )
    pq.write_table(pa.Table.from_pandas(frame), path, row_group_size=5)
    original = pq.ParquetFile.iter_batches
    observed = []

    def batches(file, *args, **kwargs):
        observed.extend(kwargs["row_groups"])
        return original(file, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("selected read scanned the whole Parquet file")

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", batches)
    monkeypatch.setattr(pq, "read_table", forbidden)
    result = ParquetReader().load(path, selection=Selection(("a",), 12, 18))
    pd.testing.assert_frame_equal(result.data, frame[["a"]].iloc[12:18].reset_index(drop=True))
    assert observed == [2, 3]
    projected = ParquetReader().load(path, selection=Selection(("a",)))
    pd.testing.assert_frame_equal(projected.data, frame[["a"]])


@pytest.mark.parametrize("start,stop,expected", [(10, 10, []), (20, None, []), (0, 0, [])])
def test_parquet_empty_ranges_keep_column_dtypes(tmp_path, start, stop, expected):
    path = tmp_path / "data.parquet"
    pq.write_table(pa.table({"a": np.arange(10, dtype=np.int64)}), path, row_group_size=3)
    result = ParquetReader().load(path, selection=Selection(("a",), start, stop))
    assert result.data.a.tolist() == expected
    assert str(result.data.a.dtype) == "int64"


def test_parquet_rejects_unknown_columns_and_named_dimensions(tmp_path):
    path = tmp_path / "data.parquet"
    pq.write_table(pa.table({"a": [1, 2]}), path)
    for selection in (Selection(("unknown",)), Selection(indexers={"x": 1})):
        with pytest.raises(DataReadError):
            ParquetReader().load(path, selection=selection)


def test_xarray_chunk_loading_observes_cancellation_between_variables(field_file):
    from cpdatakit.jobs.manager import JobCancelled, JobContext

    path, reader = field_file
    seen = []
    context = JobContext()

    def progress(stage):
        seen.append(stage)
        context.set()

    context.on_progress = progress
    with pytest.raises(JobCancelled):
        reader.load(path, context=context)
    assert len(seen) == 1


def test_xarray_chunk_loading_matches_ordinary_read(field_file):
    from cpdatakit.jobs.manager import JobContext

    path, reader = field_file
    stages = []
    loaded = reader.load(path, context=JobContext(stages.append))
    xr.testing.assert_identical(loaded.data, reader.load(path).data)
    assert any("temperature" in stage for stage in stages)


def test_hdf5_v2_slices_disk_arrays_before_materialization(tmp_path, monkeypatch):
    import h5py

    from cpdatakit.io import load_hdf5_v2, write_hdf5_v2

    path = tmp_path / "field.h5"
    data = ScientificDataset(
        xr.Dataset(
            {"temperature": (("time", "y", "x"), np.arange(120).reshape(6, 4, 5))},
            coords={"time": np.arange(6), "y": np.arange(4), "x": np.arange(5)},
        )
    )
    schema = {
        "schema_version": "2.0",
        "profile": "field",
        "dimensions": [
            {"name": "time", "length": 6},
            {"name": "y", "length": 4},
            {"name": "x", "length": 5},
        ],
        "variables": [
            {
                "name": "temperature",
                "dtype": "integer",
                "unit": "1",
                "role": "fixture",
                "dims": ["time", "y", "x"],
            }
        ],
    }
    schema["coordinates"] = [
        {"name": n, "dims": [n], "dtype": "integer", "unit": "1"} for n in ("time", "y", "x")
    ]
    write_hdf5_v2(data, path, schema)
    original = h5py.Dataset.__getitem__

    def getitem(array, key):
        if array.name == "/variables/temperature":
            assert key != Ellipsis, "HDF5 materialized the full variable before slicing"
        return original(array, key)

    monkeypatch.setattr(h5py.Dataset, "__getitem__", getitem)
    result = load_hdf5_v2(
        path, selection=Selection(("temperature",), indexers={"time": 2, "y": slice(1, 3)})
    )
    assert result.data.temperature.shape == (1, 2, 5)
    assert result.data.temperature.values.tolist() == [[[45, 46, 47, 48, 49], [50, 51, 52, 53, 54]]]
