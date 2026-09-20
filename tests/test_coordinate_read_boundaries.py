"""Exercise actual disk reads, including coordinate reads made while opening."""

from collections import Counter

import cftime
import h5py
import numpy as np
import pytest
import xarray as xr
from xarray.backends.netCDF4_ import NetCDF4ArrayWrapper
from zarr.storage import LocalStore

from cpdatakit.exceptions import DataReadError
from cpdatakit.formats import NetCDFReader, ReadLimits, Selection, ZarrReader
from cpdatakit.jobs.manager import JobContext


@pytest.fixture(params=["h5netcdf", "netcdf4", "zarr"])
def backend(request):
    return request.param


def write_dataset(dataset, backend, tmp_path, *, chunks=None):
    if backend == "zarr":
        path = tmp_path / "field.zarr"
        encoding = (
            {name: {"chunks": (chunks,)} for name in dataset.variables}
            if chunks is not None
            else None
        )
        dataset.to_zarr(path, mode="w", consolidated=False, zarr_format=3, encoding=encoding)
        return path, ZarrReader()
    path = tmp_path / "field.nc"
    dataset.to_netcdf(path, engine=backend)
    return path, NetCDFReader(engine=backend)


def trace_disk_reads(monkeypatch, backend):
    """Observe real returned arrays or local chunk buffers without replacing reads."""
    reads = []
    if backend == "h5netcdf":
        original = h5py.Dataset.__getitem__

        def getitem(array, key, **kwargs):
            result = original(array, key, **kwargs)
            reads.append((array.name.lstrip("/"), repr(key), np.asarray(result).size))
            return result

        monkeypatch.setattr(h5py.Dataset, "__getitem__", getitem)
    elif backend == "netcdf4":
        original = NetCDF4ArrayWrapper._getitem

        def getitem(array, key):
            result = original(array, key)
            reads.append((array.variable_name, repr(key), np.asarray(result).size))
            return result

        monkeypatch.setattr(NetCDF4ArrayWrapper, "_getitem", getitem)
    else:
        original = LocalStore.get

        async def get(store, key, *args, **kwargs):
            result = await original(store, key, *args, **kwargs)
            if "/c/" in key and result is not None:
                reads.append((key.split("/", 1)[0], key, len(result)))
            return result

        monkeypatch.setattr(LocalStore, "get", get)
    return reads


@pytest.fixture
def large_field(backend, tmp_path):
    dataset = xr.Dataset(
        {
            "temperature": ("record", np.arange(10_000, dtype=np.float64) + 300),
            "unused": ("record", np.ones(10_000)),
        },
        coords={"record": np.arange(10_000, dtype=np.int64)},
    )
    return write_dataset(dataset, backend, tmp_path, chunks=128)


@pytest.mark.parametrize("reject", [False, True])
def test_inspection_checks_record_limit_without_reading_arrays(
    large_field, backend, monkeypatch, reject
):
    path, reader = large_field
    reads = trace_disk_reads(monkeypatch, backend)
    if reject:
        with pytest.raises(DataReadError, match="record limit"):
            reader.inspect(path, limits=ReadLimits(max_records=1))
    else:
        description = reader.inspect(path, limits=ReadLimits())
        assert description["record_count"] == 10_000
        assert description["dimensions"] == {"record": 10_000}
    assert reads == [], f"structure inspection read array payloads: {reads}"


@pytest.mark.parametrize("with_context", [False, True])
def test_single_record_read_does_not_materialize_unselected_coordinate_values(
    large_field, backend, monkeypatch, with_context
):
    path, reader = large_field
    reads = trace_disk_reads(monkeypatch, backend)
    loaded = reader.load(
        path,
        selection=Selection(("temperature",), 4_321, 4_322),
        context=JobContext() if with_context else None,
    ).data
    assert loaded.temperature.values.tolist() == [4_621.0]
    assert loaded.record.values.tolist() == [4_321]
    assert loaded.sel(record=4_321).temperature.item() == 4_621.0
    assert {name for name, _, _ in reads} == {"record", "temperature"}
    if backend == "zarr":
        assert Counter(key for _, key, _ in reads) == {
            "record/c/33": 1,
            "temperature/c/33": 1,
        }
        assert all(size > 0 for _, _, size in reads)
    else:
        assert Counter(
            {
                name: sum(n for field, _, n in reads if field == name)
                for name in ("record", "temperature")
            }
        ) == {
            "record": 1,
            "temperature": 1,
        }, reads


@pytest.mark.parametrize("with_context", [False, True])
def test_time_decoding_reads_bounded_dtype_endpoints_and_the_selected_range(
    backend, tmp_path, monkeypatch, with_context
):
    times = np.arange("2000-01-01", "2027-05-19", dtype="datetime64[D]").astype("datetime64[ns]")
    dataset = xr.Dataset(
        {"temperature": ("record", np.arange(10_000, dtype=np.float64) + 300)},
        coords={"record": times},
    )
    path, reader = write_dataset(dataset, backend, tmp_path, chunks=128)
    reads = trace_disk_reads(monkeypatch, backend)
    loaded = reader.load(
        path,
        selection=Selection(("temperature",), 4_321, 4_322),
        context=JobContext() if with_context else None,
    ).data
    assert loaded.sel(record=times[4_321]).temperature.item() == 4_621.0
    if backend == "zarr":
        assert Counter(key for _, key, _ in reads) == {
            "record/c/0": 1,
            "record/c/78": 1,
            "record/c/33": 1,
            "temperature/c/33": 1,
        }
    else:
        assert sorted((name, size) for name, _, size in reads) == [
            ("record", 1),
            ("record", 1),
            ("record", 1),
            ("temperature", 1),
        ], reads


@pytest.mark.parametrize("reject", [False, True])
def test_time_inspection_checks_limits_before_bounded_dtype_detection(
    backend, tmp_path, monkeypatch, reject
):
    times = np.arange("2000-01-01", "2027-05-19", dtype="datetime64[D]").astype("datetime64[ns]")
    dataset = xr.Dataset(
        {"temperature": ("record", np.arange(10_000, dtype=np.float64))},
        coords={"record": times},
    )
    path, reader = write_dataset(dataset, backend, tmp_path, chunks=128)
    reads = trace_disk_reads(monkeypatch, backend)
    if reject:
        with pytest.raises(DataReadError, match="record limit"):
            reader.inspect(path, limits=ReadLimits(max_records=1))
        assert reads == []
    else:
        description = reader.inspect(path, limits=ReadLimits())
        fields = description["field_details"] if backend == "zarr" else description["variables"]
        assert fields["record"]["dtype"] == "datetime64[ns]"
        assert fields["record"]["unit"] is None
        if backend == "zarr":
            assert Counter(key for _, key, _ in reads) == {"record/c/0": 1, "record/c/78": 1}
        else:
            assert sorted((name, size) for name, _, size in reads) == [("record", 1)] * 2


@pytest.mark.parametrize("with_context", [False, True])
@pytest.mark.parametrize("missing_endpoints", [False, True])
def test_masked_int64_time_keeps_nanosecond_precision_and_nat(
    backend, tmp_path, monkeypatch, with_context, missing_endpoints
):
    fill = np.iinfo(np.int64).min
    times = 1_700_000_000_000_000_000 + 2 * np.arange(10_000, dtype=np.int64)
    times[4_321:4_324] = [1_700_000_000_000_000_001, 1_700_000_000_000_000_003, fill]
    if missing_endpoints:
        times[[0, -1]] = fill
    dataset = xr.Dataset(
        {"temperature": ("record", np.arange(10_000, dtype=np.float64))},
        coords={
            "record": (
                "record",
                times,
                {"units": "nanoseconds since 1970-01-01", "_FillValue": fill},
            )
        },
    )
    path, reader = write_dataset(dataset, backend, tmp_path, chunks=128)
    reads = trace_disk_reads(monkeypatch, backend)
    loaded = reader.load(
        path,
        selection=Selection(("temperature",), 4_321, 4_324),
        context=JobContext() if with_context else None,
    ).data
    assert loaded.record.values.view(np.int64).tolist() == [
        1_700_000_000_000_000_001,
        1_700_000_000_000_000_003,
        fill,
    ]
    assert np.isnat(loaded.record.values[2])
    assert (
        loaded.sel(record=np.datetime64(1_700_000_000_000_000_003, "ns")).temperature.item() == 4322
    )
    if backend == "zarr":
        assert Counter(key for _, key, _ in reads) == {
            "record/c/0": 1,
            "record/c/78": 1,
            "record/c/33": 1,
            "temperature/c/33": 1,
        }
    else:
        assert sorted((name, size) for name, _, size in reads) == [
            ("record", 1),
            ("record", 1),
            ("record", 3),
            ("temperature", 3),
        ]


@pytest.mark.parametrize("with_context", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_selection_keeps_cftime_labels_from_full_axis_dtype(backend, tmp_path, with_context, empty):
    dataset = xr.Dataset(
        {"value": ("time", [1.0, 2.0])},
        coords={
            "time": (
                "time",
                [0, 146_097],
                {"units": "days since 1600-01-01", "calendar": "standard"},
            )
        },
    )
    path, reader = write_dataset(dataset, backend, tmp_path)
    loaded = reader.load(
        path,
        selection=Selection(("value",), 1, 1 if empty else 2),
        context=JobContext() if with_context else None,
    ).data
    assert isinstance(loaded.indexes["time"], xr.CFTimeIndex)
    if not empty:
        assert loaded.sel(time=cftime.DatetimeGregorian(2000, 1, 1)).value.item() == 2.0


@pytest.mark.parametrize("with_context", [False, True])
def test_nonmonotonic_time_can_decode_out_of_range_interior_selection(
    backend, tmp_path, with_context
):
    dataset = xr.Dataset(
        {"value": ("time", [1.0, 2.0, 3.0])},
        coords={
            "time": (
                "time",
                [0, -146_097, 1],
                {"units": "days since 2000-01-01", "calendar": "standard"},
            )
        },
    )
    path, reader = write_dataset(dataset, backend, tmp_path)
    loaded = reader.load(
        path,
        selection=Selection(("value",), 1, 2),
        context=JobContext() if with_context else None,
    ).data
    assert loaded.sel(time=cftime.DatetimeGregorian(1600, 1, 1)).value.item() == 2.0


@pytest.mark.parametrize("with_context", [False, True])
def test_deferred_cf_decoding_preserves_packing_unsigned_strings_and_inspection(
    backend, tmp_path, with_context
):
    dataset = xr.Dataset(
        {
            "packed": (
                "record",
                np.array([10, -99, 30], dtype=np.int16),
                {"_FillValue": -99, "scale_factor": 0.5, "add_offset": 100.0},
            ),
            "unsigned": ("record", np.array([-2, 0, 1], dtype=np.int16), {"_Unsigned": "true"}),
            "label": ("record", ["first", "second", "third"]),
        },
        coords={"record": [0, 1, 2]},
    )
    path, reader = write_dataset(dataset, backend, tmp_path)
    if backend == "zarr":
        source = xr.open_zarr(path, consolidated=False, chunks=None)
    else:
        source = xr.open_dataset(path, engine=backend)
    with source:
        expected = source.load().isel(record=slice(1, 3))
    loaded = reader.load(
        path,
        selection=Selection(("packed", "unsigned", "label"), 1, 3),
        context=JobContext() if with_context else None,
    ).data
    xr.testing.assert_identical(loaded, expected)
    description = reader.inspect(path, limits=ReadLimits())
    fields = description["field_details"] if backend == "zarr" else description["variables"]
    assert fields["packed"]["dtype"] == str(expected.packed.dtype)
    assert fields["unsigned"]["dtype"] == str(expected.unsigned.dtype)


@pytest.mark.parametrize("with_context", [False, True])
def test_cf_decoding_does_not_concatenate_an_already_decoded_character_dimension(
    backend, tmp_path, with_context
):
    dataset = xr.Dataset({"label": ("record", np.array([b"a", b"b", b"c"], dtype="S1"))})
    path, reader = write_dataset(dataset, backend, tmp_path)
    if backend == "zarr":
        source = xr.open_zarr(path, consolidated=False, chunks=None)
    else:
        source = xr.open_dataset(path, engine=backend)
    with source:
        expected = source.load()
    loaded = reader.load(path, context=JobContext() if with_context else None).data
    xr.testing.assert_identical(loaded, expected)
    description = reader.inspect(path, limits=ReadLimits())
    fields = description["field_details"] if backend == "zarr" else description["variables"]
    assert fields["label"]["dims"] == list(expected.label.dims)
    assert fields["label"]["shape"] == list(expected.label.shape)
    assert fields["label"]["dtype"] == str(expected.label.dtype)


@pytest.mark.parametrize("with_context", [False, True])
def test_selected_time_bounds_inherit_cf_units_before_parent_field_is_projected_out(
    backend, tmp_path, with_context
):
    dataset = xr.Dataset(
        {
            "time": (
                "record",
                [0, 1, 2, 3],
                {"units": "days since 2000-01-01", "bounds": "time_bounds"},
            ),
            "time_bounds": (("record", "bounds"), [[0, 1], [1, 2], [2, 3], [3, 4]]),
        }
    )
    path, reader = write_dataset(dataset, backend, tmp_path)
    if backend == "zarr":
        source = xr.open_zarr(path, consolidated=False, chunks=None)
    else:
        source = xr.open_dataset(path, engine=backend)
    with source:
        expected = source[["time_bounds"]].isel(record=slice(1, 2)).load()
    loaded = reader.load(
        path,
        selection=Selection(("time_bounds",), 1, 2),
        context=JobContext() if with_context else None,
    ).data
    xr.testing.assert_identical(loaded, expected)
    description = reader.inspect(path, limits=ReadLimits())
    fields = description["field_details"] if backend == "zarr" else description["variables"]
    assert fields["time_bounds"]["dtype"] == str(expected.time_bounds.dtype)


@pytest.mark.parametrize("with_context", [False, True])
@pytest.mark.parametrize("time_coordinate", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_selected_dataset_retains_indexes_time_coordinates_and_field_order(
    backend, tmp_path, with_context, time_coordinate, empty
):
    coordinate = (
        np.arange("2025-01-01", "2025-01-07", dtype="datetime64[D]").astype("datetime64[ns]")
        if time_coordinate
        else np.arange(6, dtype=np.int64) * 10
    )
    dataset = xr.Dataset(
        {
            "first": ("time", np.arange(6)),
            "second": ("time", np.arange(6) + 20),
            "unused": ("time", np.arange(6) + 40),
        },
        coords={
            "time": ("time", coordinate, {"axis": "T"}),
            "stage": ("time", np.arange(6) + 100, {"units": "1"}),
            "reference": ((), 273.15, {"units": "K"}),
        },
        attrs={"title": "coordinate semantics"},
    )
    if time_coordinate:
        dataset = dataset.assign_coords(
            observed_at=("time", coordinate + np.timedelta64(12, "h")),
            reference_time=coordinate[0],
            elapsed=("time", np.arange(6).astype("timedelta64[h]")),
        )
        dataset.observed_at.attrs["long_name"] = "sample timestamp"
    else:
        dataset.time.attrs["units"] = "s"
    path, reader = write_dataset(dataset, backend, tmp_path)
    stop = 2 if empty else 4
    loaded = reader.load(
        path,
        selection=Selection(("second", "first"), 2, stop),
        context=JobContext() if with_context else None,
    ).data
    expected = dataset[["second", "first"]].isel(time=slice(2, stop))
    xr.testing.assert_identical(loaded, expected)
    assert tuple(loaded.data_vars) == ("second", "first")
    assert "time" in loaded.indexes
    assert loaded.indexes["time"].equals(expected.indexes["time"])
    if not empty:
        assert loaded.sel(time=coordinate[2])["second"].item() == 22
