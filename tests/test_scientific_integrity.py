"""Regression coverage for scientific information that must not be silently dropped."""

from copy import deepcopy

import h5py
import numpy as np
import pytest
import xarray as xr
from test_hdf5_v2 import SCHEMA, _value

from cpdatakit.data import LossyConversionError, ScientificDataset, scientific_to_dataset
from cpdatakit.exceptions import DataReadError, DataValidationError
from cpdatakit.formats import Selection
from cpdatakit.io import load_hdf5_v2, write_hdf5_v2
from cpdatakit.schemas import resolve_schema_v2


@pytest.mark.parametrize("coordinate", [298.15, "sample-17"])
def test_tabular_conversion_rejects_scalar_coordinates_without_losing_source(coordinate):
    value = ScientificDataset(
        xr.Dataset(
            {"stress": ("record", [10.0, 20.0], {"unit": "MPa"})}, coords={"condition": coordinate}
        ),
        {"provenance": {"source": "synthetic"}},
    )
    before = value.copy()
    with pytest.raises(LossyConversionError, match="condition"):
        scientific_to_dataset(value, record_dim="record")
    xr.testing.assert_identical(value.data, before.data)
    assert value.metadata == before.metadata


def test_hdf5_round_trip_preserves_global_attrs_in_their_own_namespace(tmp_path):
    value = _value()
    value.data.attrs = {
        "sample_id": "specimen-17",
        "history": ["prepared", "measured"],
        "source": {"doi": "synthetic", "reviewed": True, "note": None},
        "temperature": 298.15,
    }
    value.metadata["sample_id"] = "separate application metadata"
    before = value.copy()
    path = tmp_path / "attributes.h5"
    write_hdf5_v2(value, path, resolve_schema_v2(SCHEMA))
    full = load_hdf5_v2(path)
    xr.testing.assert_identical(full.data, before.data)
    assert full.metadata["sample_id"] == "separate application metadata"
    assert full.metadata["provenance"] == before.metadata["provenance"]
    xr.testing.assert_identical(value.data, before.data)
    assert value.metadata == before.metadata


def test_hdf5_old_envelope_without_global_attrs_remains_readable(tmp_path):
    path = tmp_path / "legacy.h5"
    write_hdf5_v2(_value(), path, resolve_schema_v2(SCHEMA))
    with h5py.File(path, "r+") as handle:
        if "attributes_json" in handle["metadata"].attrs:
            del handle["metadata"].attrs["attributes_json"]
    assert load_hdf5_v2(path).data.attrs == {}


@pytest.mark.parametrize("payload", ["not JSON", "[1, 2]"])
def test_hdf5_rejects_malformed_global_attrs(tmp_path, payload):
    path = tmp_path / "malformed.h5"
    write_hdf5_v2(_value(), path, resolve_schema_v2(SCHEMA))
    with h5py.File(path, "r+") as handle:
        handle["metadata"].attrs["attributes_json"] = payload
    with pytest.raises(DataReadError, match="attributes_json"):
        load_hdf5_v2(path)


@pytest.mark.parametrize("attribute", [object(), (1, 2)])
def test_hdf5_rejects_global_attrs_that_cannot_round_trip_as_json(tmp_path, attribute):
    value = _value()
    value.data.attrs["unsupported"] = attribute
    target = tmp_path / "preserved.h5"
    target.write_bytes(b"previous output")
    with pytest.raises(DataValidationError, match="attributes"):
        write_hdf5_v2(value, target, resolve_schema_v2(SCHEMA), force=True)
    assert target.read_bytes() == b"previous output"


@pytest.mark.parametrize("fields", [("temperature",), ("stage",)])
def test_hdf5_selection_preserves_associated_auxiliary_and_scalar_coordinates(tmp_path, fields):
    value = _value()
    value.data = value.data.assign_coords(ambient=((), 298.15, {"unit": "K"}))
    value.data.attrs["sample_id"] = "specimen-17"
    schema = deepcopy(resolve_schema_v2(SCHEMA).schema.to_dict())
    schema["coordinates"].append({"name": "ambient", "dims": [], "dtype": "float", "unit": "K"})
    path = tmp_path / "coordinates.h5"
    write_hdf5_v2(value, path, schema)
    full = load_hdf5_v2(path)
    selected = load_hdf5_v2(path, selection=Selection(fields, 1, 3))
    xr.testing.assert_identical(selected.data, full.data[list(fields)].isel(time=slice(1, 3)))
    assert selected.data.attrs == value.data.attrs


def test_hdf5_record_selection_uses_the_first_requested_field(tmp_path):
    value = ScientificDataset(
        xr.Dataset({"a": ("time", [0.0, 1.0]), "z": ("record", np.arange(4, dtype=float))}),
        {"units": {"a": "1", "z": "1"}},
    )
    schema = {
        "profile": "separate-axes",
        "schema_version": "2.0",
        "dimensions": [{"name": "time", "length": 2}, {"name": "record", "length": 4}],
        "variables": [
            {"name": name, "dims": [dim], "dtype": "float", "unit": "1", "role": "fixture"}
            for name, dim in [("a", "time"), ("z", "record")]
        ],
    }
    path = tmp_path / "order.h5"
    write_hdf5_v2(value, path, schema)
    full = load_hdf5_v2(path)
    selected = load_hdf5_v2(path, selection=Selection(("z", "a"), 1, 3))
    xr.testing.assert_identical(selected.data, full.data[["z", "a"]].isel(record=slice(1, 3)))
    assert list(selected.data.data_vars) == ["z", "a"]
