from __future__ import annotations

import json

import h5py
import numpy as np
import pandas as pd
import pytest
import xarray as xr

from cpdatakit.application import (
    ConvertRequest,
    DatasetRequest,
    ImportInspectRequest,
    ReadLimits,
    ReportRequest,
    ResolveSchemaRequest,
    build_report,
    convert_and_write,
    import_and_inspect,
    resolve_schema_and_mapping,
    validate_and_summarize,
)
from cpdatakit.data import ScientificDataset
from cpdatakit.formats import NetCDFWriter, ParquetWriter, ZarrWriter
from cpdatakit.io import load_dataset, load_hdf5_v2, write_hdf5_v2
from cpdatakit.model import Dataset


@pytest.fixture
def thermal_schema():
    return {
        "schema_version": "2.0",
        "profile": "thermal",
        "dimensions": [{"name": "time", "length": 2}, {"name": "x", "length": 3}],
        "coordinates": [
            {"name": "time", "dims": ["time"], "dtype": "float", "unit": "s"},
            {"name": "x", "dims": ["x"], "dtype": "float", "unit": "mm"},
        ],
        "variables": [
            {
                "name": "temperature",
                "dims": ["time", "x"],
                "dtype": "float",
                "unit": "K",
                "role": "measured_field",
            }
        ],
    }


@pytest.fixture
def thermal_value():
    return ScientificDataset(
        xr.Dataset(
            {
                "temperature": (
                    ("time", "x"),
                    [[280.0, 281.0, 282.0], [290.0, 291.0, 292.0]],
                    {"unit": "K"},
                )
            },
            coords={
                "time": ("time", [0.0, 10.0], {"unit": "s"}),
                "x": ("x", [0.0, 1.0, 2.0], {"unit": "mm"}),
            },
        ),
        {"provenance": {"source_description": "synthetic fixture"}},
    )


@pytest.fixture(params=["netcdf", "zarr", "hdf5"])
def thermal_input(request, tmp_path, thermal_value, thermal_schema):
    suffix = {"netcdf": ".nc", "zarr": ".zarr", "hdf5": ".h5"}[request.param]
    path = tmp_path / ("thermal" + suffix)
    if request.param == "hdf5":
        write_hdf5_v2(thermal_value, path, thermal_schema)
    else:
        writer = NetCDFWriter() if request.param == "netcdf" else ZarrWriter()
        writer.write(thermal_value, path)
    return path


def test_scientific_service_workflow_preserves_dimensions_and_provenance(
    tmp_path,
    thermal_input,
    thermal_schema,
):
    resolved = resolve_schema_and_mapping(ResolveSchemaRequest(thermal_schema))
    assert resolved.ok, resolved.to_dict()
    inspected = import_and_inspect(ImportInspectRequest(thermal_input, thermal_schema))
    assert inspected.ok, inspected.to_dict()
    assert inspected.value["dimensions"] == {"time": 2, "x": 3}
    assert inspected.value["record_count"] == 2
    checked = validate_and_summarize(DatasetRequest(thermal_input, thermal_schema))
    assert checked.ok, checked.to_dict()
    assert checked.value.validation.valid
    assert checked.value.summary["fields"]["temperature"]["max"] == 292.0
    target = tmp_path / "converted.h5"
    converted = convert_and_write(ConvertRequest(thermal_input, thermal_schema, target))
    assert converted.ok, converted.to_dict()
    with h5py.File(target) as handle:
        assert handle.attrs["format_version"] == "2.0"
    loaded = load_hdf5_v2(target)
    assert loaded.data.temperature.dims == ("time", "x")
    np.testing.assert_array_equal(loaded.data.temperature, [[280, 281, 282], [290, 291, 292]])
    assert loaded.metadata["provenance"]["input_filename"] == thermal_input.name
    assert len(loaded.metadata["provenance"]["input_sha256"]) == 64
    assert loaded.metadata["validation_summary"]["valid"] is True
    report = build_report(ReportRequest(thermal_input, thermal_schema, tmp_path / "report.html"))
    assert report.ok, report.to_dict()
    assert report.value.report["validation"]["valid"]
    assert "temperature" in (tmp_path / "report.html").read_text(encoding="utf-8")
    assert str(tmp_path) not in json.dumps(report.to_dict())


def test_new_format_inspection_obeys_record_limits(thermal_input):
    result = import_and_inspect(
        ImportInspectRequest(thermal_input, read_limits=ReadLimits(max_records=1))
    )
    assert not result.ok
    assert result.error.code == "read_limit_exceeded"


@pytest.mark.parametrize(
    "defect,code",
    [
        ("dimensions", "dimension_mismatch"),
        ("missing", "missing_field"),
        ("dtype", "invalid_dtype"),
        ("unit", "unit_mismatch"),
        ("nonfinite", "non_finite"),
        ("extra", "undeclared_field"),
    ],
)
def test_scientific_validation_reports_findings_and_blocks_output(
    tmp_path,
    thermal_schema,
    thermal_value,
    defect,
    code,
):
    data = thermal_value.data
    if defect == "dimensions":
        thermal_value.data = data.isel(x=slice(0, 2))
    elif defect == "missing":
        thermal_value.data = data.drop_vars("temperature")
    elif defect == "dtype":
        data["temperature"] = data.temperature.astype(str)
    elif defect == "unit":
        data.temperature.attrs["unit"] = "degC"
    elif defect == "nonfinite":
        data.temperature.values[0, 0] = np.nan
    else:
        data["extra"] = data.temperature.copy()
    source = NetCDFWriter().write(thermal_value, tmp_path / "invalid.nc")
    checked = validate_and_summarize(DatasetRequest(source, thermal_schema))
    assert checked.ok, checked.to_dict()
    assert code in {issue.code for issue in checked.value.validation.errors}
    target = tmp_path / "invalid.h5"
    converted = convert_and_write(ConvertRequest(source, thermal_schema, target))
    assert converted.error.code == "validation_failed"
    assert not target.exists()


def test_parquet_uses_legacy_schema_validation_and_hdf5_default(tmp_path):
    value = Dataset(pd.DataFrame({"step": [0, 1], "strain": [0.0, 0.1], "stress": [0.0, 10.0]}))
    source = ParquetWriter().write(value, tmp_path / "curve.parquet")
    inspected = import_and_inspect(ImportInspectRequest(source, "curve"))
    assert inspected.ok, inspected.to_dict()
    assert inspected.value["record_count"] == 2
    target = tmp_path / "curve.h5"
    result = convert_and_write(ConvertRequest(source, "curve", target))
    assert result.ok, result.to_dict()
    assert load_dataset(target).metadata["schema_version"] == "1.0"


@pytest.mark.parametrize("format_name,suffix", [("netcdf", ".nc"), ("zarr", ".zarr")])
def test_conversion_can_select_scientific_writer(
    tmp_path, thermal_input, thermal_schema, format_name, suffix
):
    target = tmp_path / ("output" + suffix)
    result = convert_and_write(
        ConvertRequest(thermal_input, thermal_schema, target, output_format=format_name)
    )
    assert result.ok, result.to_dict()
    checked = validate_and_summarize(DatasetRequest(target, thermal_schema))
    assert checked.ok and checked.value.validation.valid


def test_scientific_to_parquet_is_rejected_without_flattening(
    tmp_path, thermal_input, thermal_schema
):
    target = tmp_path / "lossy.parquet"
    result = convert_and_write(
        ConvertRequest(thermal_input, thermal_schema, target, output_format="parquet")
    )
    assert not result.ok
    assert result.error.code == "data_validation_error"
    assert not target.exists()


def test_service_reads_classic_netcdf(tmp_path, thermal_schema, thermal_value):
    path = tmp_path / "classic.nc"
    thermal_value.data.to_netcdf(path, engine="netcdf4", format="NETCDF3_CLASSIC")
    inspected = import_and_inspect(ImportInspectRequest(path, thermal_schema))
    assert inspected.ok, inspected.to_dict()
    checked = validate_and_summarize(DatasetRequest(path, thermal_schema))
    assert checked.ok and checked.value.validation.valid


def test_schema_version_mismatch_and_scientific_mapping_return_expected_errors(
    tmp_path,
    thermal_input,
    thermal_schema,
):
    mismatch = validate_and_summarize(DatasetRequest(thermal_input, "curve"))
    assert mismatch.error.code == "schema_error"
    mapping = tmp_path / "mapping.json"
    mapping.write_text('{"mappings": [{"source": "temperature", "target": "temperature"}]}')
    mapped = validate_and_summarize(DatasetRequest(thermal_input, thermal_schema, mapping))
    assert mapped.error.code == "data_validation_error"


def test_service_resolves_schema_v2_paths_and_tabular_custom_schema(tmp_path, thermal_schema):
    source = tmp_path / "schema.json"
    source.write_text(json.dumps(thermal_schema))
    resolved = resolve_schema_and_mapping(ResolveSchemaRequest(source))
    assert resolved.ok and resolved.value.schema.schema_version == "2.0"
    source.write_text("invalid json")
    invalid = resolve_schema_and_mapping(ResolveSchemaRequest(source))
    assert invalid.error.code == "schema_error"
