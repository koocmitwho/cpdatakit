"""Exact conversion values and explicit refusal at storage boundaries."""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from cpdatakit.data import ScientificDataset, dataset_to_scientific, scientific_to_dataset
from cpdatakit.exceptions import DataValidationError, LossyConversionError
from cpdatakit.formats.netcdf import NetCDFReader, NetCDFWriter
from cpdatakit.formats.zarr import ZarrReader, ZarrWriter
from cpdatakit.io import load_hdf5, load_hdf5_v2, write_hdf5, write_hdf5_v2
from cpdatakit.model import Dataset
from cpdatakit.schema import make_field_schema, make_profile_schema
from cpdatakit.validation import validate_dataset


@pytest.mark.parametrize("integer", [2**53 - 1, 2**53 + 1, -(2**63), 2**63 - 1, 2**64 - 1])
@pytest.mark.parametrize("missing", [None, pd.NA, np.nan])
def test_object_integer_with_missing_keeps_exact_value(integer, missing):
    source = Dataset(pd.DataFrame({"count": pd.Series([integer, missing], dtype=object)}))
    before = source.data.copy(deep=True)

    restored = scientific_to_dataset(dataset_to_scientific(source))

    assert isinstance(restored.data["count"].iloc[0], (int, np.integer))
    assert int(restored.data["count"].iloc[0]) == integer
    assert pd.isna(restored.data["count"].iloc[1])
    pd.testing.assert_frame_equal(source.data, before)


@pytest.mark.parametrize(
    "values", [[2**53 + 1, 0.5], [-(2**63), 2**64 - 1], [2**64 - 1, np.nan, 0.5]]
)
def test_mixed_numeric_scalars_keep_each_original_value(values):
    source = Dataset(pd.DataFrame({"value": pd.Series(values, dtype=object)}))
    restored = scientific_to_dataset(dataset_to_scientific(source))
    for expected, actual in zip(values, restored.data["value"], strict=True):
        if pd.isna(expected):
            assert pd.isna(actual)
        elif isinstance(expected, int):
            assert isinstance(actual, (int, np.integer))
            assert int(actual) == expected
        else:
            assert actual == expected


@pytest.mark.parametrize("dtype", ["int8", "int64", "uint64", "float32", "bool"])
def test_homogeneous_scalar_dtype_is_not_reinferred_through_python_lists(dtype):
    source = Dataset(pd.DataFrame({"value": np.array([0, 1], dtype=dtype)}))
    restored = scientific_to_dataset(dataset_to_scientific(source))
    pd.testing.assert_frame_equal(restored.data, source.data)


@pytest.mark.parametrize("dtype, integer", [("Int64", 2**53 + 1), ("UInt64", 2**64 - 1)])
def test_nullable_integer_series_does_not_pass_through_float_numpy_conversion(dtype, integer):
    source = Dataset(pd.DataFrame({"count": pd.Series([integer, pd.NA], dtype=dtype)}))
    before = source.data.copy(deep=True)
    restored = scientific_to_dataset(dataset_to_scientific(source))
    assert isinstance(restored.data["count"].iloc[0], (int, np.integer))
    assert int(restored.data["count"].iloc[0]) == integer
    assert pd.isna(restored.data["count"].iloc[1])
    pd.testing.assert_frame_equal(source.data, before)


def test_integer_larger_than_uint64_stays_exact_in_memory():
    source = Dataset(pd.DataFrame({"count": pd.Series([2**80 + 1], dtype=object)}))
    restored = scientific_to_dataset(dataset_to_scientific(source))
    assert restored.data["count"].iloc[0] == 2**80 + 1


@pytest.mark.parametrize("units", [None, [], "MPa"])
def test_invalid_metadata_unit_container_has_explicit_validation_error(units):
    source = ScientificDataset(xr.Dataset({"stress": ("record", [1.0])}), {"units": units})
    with pytest.raises(DataValidationError, match="unit"):
        scientific_to_dataset(source)


@pytest.mark.parametrize(
    "rows",
    [
        [np.array([2**53 + 1], dtype="int64"), np.array([0.5], dtype="float64")],
        [np.array([-1], dtype="int64"), np.array([2**64 - 1], dtype="uint64")],
        [[[2**53 + 1, 0.5]], [[1, 2]]],
        [np.array([1]), np.array(["2"])],
        [np.array([True]), np.array([2])],
    ],
)
def test_array_promotion_that_changes_values_or_categories_is_rejected(rows):
    source = Dataset(pd.DataFrame({"tensor": rows}))
    before = deepcopy(rows)
    with pytest.raises(LossyConversionError, match="tensor"):
        dataset_to_scientific(source)
    for actual, expected in zip(source.data["tensor"], before, strict=True):
        np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("coordinate", [False, True])
@pytest.mark.parametrize(
    "attrs, metadata_units",
    [({"units": "MPa"}, {}), ({"unit": "MPa", "units": "MPa"}, {"stress": "MPa"})],
)
def test_declared_units_survive_variables_and_coordinates(coordinate, attrs, metadata_units):
    data = xr.Dataset({"stress": ("record", [1.0, 2.0], attrs)})
    if coordinate:
        data = data.assign(value=("record", [3.0, 4.0])).set_coords("stress")
    source = ScientificDataset(data, {"units": metadata_units})
    before = source.copy()
    restored = scientific_to_dataset(source)
    assert restored.metadata["units"]["stress"] == "MPa"
    xr.testing.assert_identical(source.data, before.data)
    assert source.metadata == before.metadata


@pytest.mark.parametrize("coordinate", [False, True])
@pytest.mark.parametrize(
    "attrs, metadata_units",
    [
        ({"unit": "MPa", "units": "Pa"}, {}),
        ({"units": "MPa"}, {"stress": "Pa"}),
        ({"unit": "MPa"}, {"stress": "Pa"}),
        ({"units": 12}, {}),
    ],
)
def test_conflicting_or_invalid_units_are_rejected_without_mutation(
    coordinate, attrs, metadata_units
):
    data = xr.Dataset({"stress": ("record", [1.0, 2.0], attrs)})
    if coordinate:
        data = data.assign(value=("record", [3.0, 4.0])).set_coords("stress")
    source = ScientificDataset(data, {"units": metadata_units})
    before = source.copy()
    with pytest.raises(DataValidationError, match="unit"):
        scientific_to_dataset(source)
    xr.testing.assert_identical(source.data, before.data)
    assert source.metadata == before.metadata


def _schema(value):
    return {
        "profile": "numeric-fidelity",
        "schema_version": "2.0",
        "dimensions": [
            {"name": name, "length": length} for name, length in value.data.sizes.items()
        ],
        "variables": [
            {
                "name": name,
                "dims": list(array.dims),
                "dtype": "integer" if array.dtype.kind in "iu" else "float",
                "unit": "1",
                "role": "fixture",
            }
            for name, array in value.data.data_vars.items()
        ],
    }


def _write_read(value, target, backend):
    if backend == "hdf5":
        write_hdf5_v2(value, target, _schema(value))
        return load_hdf5_v2(target)
    if backend == "zarr":
        ZarrWriter().write(value, target)
        return ZarrReader().load(target)
    NetCDFWriter(engine=backend).write(value, target)
    return NetCDFReader(engine=backend).load(target)


@pytest.mark.parametrize("backend", ["hdf5", "h5netcdf", "netcdf4", "zarr"])
@pytest.mark.parametrize(
    "values",
    [
        np.array([-(2**63), 2**53 - 1, 2**53 + 1, 2**63 - 1], dtype="int64"),
        np.array([0, 2**53 + 1, 2**63, 2**64 - 1], dtype="uint64"),
    ],
)
def test_integer_boundaries_round_trip_through_real_formats(tmp_path, backend, values):
    source = Dataset(pd.DataFrame({"count": values}), {"units": {"count": "1"}})
    scientific = dataset_to_scientific(source)
    suffix = {"hdf5": ".h5", "zarr": ".zarr"}.get(backend, ".nc")
    loaded = _write_read(scientific, tmp_path / f"values{suffix}", backend)
    restored = scientific_to_dataset(loaded)
    assert restored.data["count"].dtype == values.dtype
    assert [int(item) for item in restored.data["count"]] == [int(item) for item in values]
    assert restored.metadata["units"]["count"] == "1"
    np.testing.assert_array_equal(source.data["count"], values)


@pytest.mark.parametrize("backend", ["hdf5", "h5netcdf", "netcdf4", "zarr"])
@pytest.mark.parametrize("values", [[2**53 + 1, None], [2**64 - 1, 0.5], ["a", None], [True, None]])
def test_formats_reject_object_values_that_cannot_be_written_losslessly(tmp_path, backend, values):
    scientific = ScientificDataset(
        xr.Dataset({"value": ("record", np.array(values, dtype=object), {"unit": "1"})})
    )
    before = scientific.copy()
    suffix = {"hdf5": ".h5", "zarr": ".zarr"}.get(backend, ".nc")
    target = tmp_path / f"rejected{suffix}"
    with pytest.raises(DataValidationError):
        _write_read(scientific, target, backend)
    assert not target.exists()
    xr.testing.assert_identical(scientific.data, before.data)


@pytest.mark.parametrize("backend", ["hdf5", "h5netcdf", "netcdf4", "zarr"])
def test_exactly_representable_mixed_array_rows_survive_real_formats(tmp_path, backend):
    source = Dataset(
        pd.DataFrame(
            {"tensor": [np.array([[2**53 - 1, 2]], dtype="int64"), np.array([[0.5, 1.5]])]}
        ),
        {"units": {"tensor": "1"}},
    )
    scientific = dataset_to_scientific(source)
    suffix = {"hdf5": ".h5", "zarr": ".zarr"}.get(backend, ".nc")
    restored = scientific_to_dataset(_write_read(scientific, tmp_path / f"mixed{suffix}", backend))
    assert int(restored.data["tensor"].iloc[0][0, 0]) == 2**53 - 1
    assert restored.data["tensor"].iloc[1].tolist() == [[0.5, 1.5]]


def _legacy_schema():
    return make_profile_schema(
        "numeric-fidelity", [make_field_schema("value", "float", unit="1", allow_missing=True)]
    )


@pytest.mark.parametrize(
    "values",
    [
        pd.Series([2**53 + 1, None], dtype=object),
        pd.Series([2**53 + 1, pd.NA], dtype="Int64"),
        pd.Series([2**64 - 1, pd.NA], dtype="UInt64"),
        pd.Series([2**64 - 1, 0.5], dtype=object),
        pd.Series(["a", None], dtype=object),
        pd.Series([True, None], dtype=object),
        pd.Series([np.array([2**53 + 1]), np.array([0.5])]),
        pd.Series([[[2**53 + 1, 0.5]], [[1, 2]]]),
    ],
)
def test_default_hdf5_rejects_lossy_values_even_when_invalid_output_is_allowed(tmp_path, values):
    source = Dataset(pd.DataFrame({"value": values}))
    schema = _legacy_schema()
    validation = validate_dataset(source, schema)
    target = tmp_path / "default.h5"
    target.write_bytes(b"keep previous output")
    with pytest.raises(DataValidationError):
        write_hdf5(source, target, schema, validation, allow_invalid=True, force=True)
    assert target.read_bytes() == b"keep previous output"
    assert list(tmp_path.glob(".default.h5.*")) == []


@pytest.mark.parametrize(
    "values",
    [
        pd.Series([-(2**63), 2**53 + 1, 2**63 - 1], dtype="int64"),
        pd.Series([0, 2**63, 2**64 - 1], dtype="uint64"),
        pd.Series([2**53 + 1, 2], dtype=object),
        pd.Series([2**53 - 1, 0.5], dtype=object),
        pd.Series([np.array([2**53 - 1]), np.array([0.5])]),
    ],
)
def test_default_hdf5_keeps_exact_values_when_numeric_representation_exists(tmp_path, values):
    source = Dataset(pd.DataFrame({"value": values}), {"units": {"value": "1"}})
    schema = _legacy_schema()
    target = tmp_path / "exact-default.h5"
    write_hdf5(source, target, schema, validate_dataset(source, schema), allow_invalid=True)
    restored = load_hdf5(target)
    for expected, actual in zip(values, restored.data["value"], strict=True):
        if isinstance(expected, (int, np.integer)):
            assert int(actual) == int(expected)
        elif isinstance(expected, np.ndarray):
            if expected.dtype.kind in "iu":
                assert [int(item) for item in actual] == [int(item) for item in expected]
            else:
                np.testing.assert_array_equal(actual, expected)
        else:
            assert actual == expected
    assert restored.metadata["units"]["value"] == "1"
