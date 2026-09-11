"""HDF5 validation summaries must describe the values actually written."""

import json
from copy import deepcopy

import h5py
import numpy as np
import pytest
from test_hdf5_v2 import SCHEMA, _value

from cpdatakit.application import ConvertRequest, convert_and_write
from cpdatakit.application.scientific import validate_scientific
from cpdatakit.exceptions import DataValidationError
from cpdatakit.formats import NetCDFWriter
from cpdatakit.io import load_hdf5_v2, write_hdf5_v2
from cpdatakit.schemas import resolve_schema_v2


def invalid_value(kind):
    value = _value()
    value.metadata.pop("validation_summary", None)
    if kind == "nan":
        value.data.temperature.values[0, 0, 0] = np.nan
    elif kind == "infinity":
        value.data.temperature.values[0, 0, 0] = np.inf
    elif kind == "dtype":
        value.data["temperature"] = value.data.temperature.astype("int64")
    elif kind == "unit":
        value.data.temperature.attrs["unit"] = "degC"
        value.metadata["units"]["temperature"] = "degC"
    elif kind == "missing_unit":
        value.data.temperature.attrs.pop("unit")
        value.metadata["units"].pop("temperature")
    elif kind == "unit_conflict":
        value.data.temperature.attrs["units"] = "MPa"
    return value


@pytest.mark.parametrize(
    "kind", ["nan", "infinity", "dtype", "unit", "missing_unit", "unit_conflict"]
)
def test_hdf5_writer_rejects_invalid_values_before_output_mutation(tmp_path, kind):
    value = invalid_value(kind)
    target = tmp_path / "invalid.h5"
    schema = resolve_schema_v2(SCHEMA)
    assert not validate_scientific(value, schema).valid
    with pytest.raises(DataValidationError, match="validation"):
        write_hdf5_v2(value, target, schema)
    assert not target.exists()
    target.write_bytes(b"previous output")
    with pytest.raises(DataValidationError, match="validation"):
        write_hdf5_v2(value, target, schema, force=True)
    assert target.read_bytes() == b"previous output"


@pytest.mark.parametrize("kind", ["nan", "dtype", "unit", "missing_unit", "unit_conflict"])
def test_explicit_invalid_writes_record_fresh_validation_and_preserve_the_problem(tmp_path, kind):
    value = invalid_value(kind)
    value.metadata["validation_summary"] = {"valid": True, "error_count": 0, "warning_count": 0}
    before = deepcopy(value.metadata)
    path = tmp_path / "allowed.h5"
    schema = resolve_schema_v2(SCHEMA)
    expected = validate_scientific(value, schema)
    write_hdf5_v2(value, path, schema, allow_invalid=True)
    loaded = load_hdf5_v2(path)
    assert not validate_scientific(loaded, schema).valid
    assert loaded.metadata["validation_summary"] == {
        "valid": False,
        "error_count": len(expected.errors),
        "warning_count": len(expected.warnings),
    }
    if kind == "missing_unit":
        assert "unit" not in loaded.data.temperature.attrs
        assert "temperature" not in loaded.metadata["units"]
    with h5py.File(path) as handle:
        root_summary = json.loads(handle.attrs["validation_summary_json"])
        metadata = json.loads(handle["metadata"].attrs["metadata_json"])
        assert root_summary == metadata["validation_summary"]
    assert value.metadata == before


def test_hdf5_writer_recomputes_stale_validation_metadata(tmp_path):
    value = _value()
    value.metadata["validation_summary"] = {"valid": False, "error_count": 8, "warning_count": 3}
    before = deepcopy(value.metadata)
    target = tmp_path / "valid.h5"
    write_hdf5_v2(value, target, resolve_schema_v2(SCHEMA))
    assert load_hdf5_v2(target).metadata["validation_summary"] == {
        "valid": True,
        "error_count": 0,
        "warning_count": 0,
    }
    assert value.metadata == before


def test_scientific_conversion_forwards_explicit_invalid_output_policy(tmp_path):
    source = tmp_path / "source.nc"
    value = invalid_value("nan")
    NetCDFWriter().write(value, source)
    target = tmp_path / "target.h5"
    rejected = convert_and_write(ConvertRequest(source, SCHEMA, target))
    assert not rejected.ok and rejected.error.code == "validation_failed"
    assert not target.exists()
    allowed = convert_and_write(ConvertRequest(source, SCHEMA, target, allow_invalid=True))
    assert allowed.ok, allowed.to_dict()
    assert not load_hdf5_v2(target).metadata["validation_summary"]["valid"]
