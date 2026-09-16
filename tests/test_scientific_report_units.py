"""Reports must use observed units consistently with scientific validation."""

from __future__ import annotations

import json

import pytest
import xarray as xr

from cpdatakit.application import ComparisonRequest, ReportRequest, build_report
from cpdatakit.application import compare_reports as compare_report_files
from cpdatakit.comparison import compare_reports
from cpdatakit.data import ScientificDataset
from cpdatakit.formats import NetCDFWriter, ZarrWriter

UNITS = {"temperature": "K", "position": "mm", "probe": "K", "ambient": "K"}


def _schema(*, string_scalar=False):
    return {
        "profile": "observed-units",
        "schema_version": "2.0",
        "dimensions": [{"name": "position", "length": 2}],
        "coordinates": [
            {"name": "position", "dims": ["position"], "dtype": "float", "unit": "mm"},
            {"name": "probe", "dims": ["position"], "dtype": "float", "unit": "K"},
            {
                "name": "ambient",
                "dims": [],
                "dtype": "string" if string_scalar else "float",
                "unit": None if string_scalar else "K",
            },
        ],
        "variables": [
            {
                "name": "temperature",
                "dims": ["position"],
                "dtype": "float",
                "unit": "K",
                "role": "measured_field",
            }
        ],
    }


def _value(source="attrs", *, offset=0.0, string_scalar=False):
    dataset = xr.Dataset(
        {"temperature": ("position", [300.0 + offset, 302.0 + offset])},
        coords={
            "position": ("position", [0.0, 1.0]),
            "probe": ("position", [298.0, 299.0]),
            "ambient": "air" if string_scalar else 298.0,
        },
    )
    units = {**UNITS, "ambient": None} if string_scalar else UNITS.copy()
    metadata = {"units": units.copy()} if source in {"metadata", "matching"} else {}
    for name, unit in units.items():
        if unit is None:
            continue
        if source in {"attrs", "matching"}:
            dataset[name].attrs["unit"] = unit
        if source in {"units_attrs", "matching"}:
            dataset[name].attrs["units"] = unit
    return ScientificDataset(dataset, metadata)


def _report(tmp_path, backend, name, value, *, string_scalar=False):
    writer = NetCDFWriter() if backend == "netcdf" else ZarrWriter()
    source = tmp_path / f"{name}{'.nc' if backend == 'netcdf' else '.zarr'}"
    writer.write(value, source)
    output = tmp_path / f"{name}.json"
    result = build_report(
        ReportRequest(source, _schema(string_scalar=string_scalar), output, format="json")
    )
    assert result.ok, result.to_dict()
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert stored == result.value.report
    return output, stored


@pytest.mark.parametrize("backend", ["netcdf", "zarr"])
@pytest.mark.parametrize("source", ["metadata", "attrs", "units_attrs", "matching"])
def test_reports_resolve_all_array_units_and_compare_100_kelvin(tmp_path, backend, source):
    left, before = _report(tmp_path, backend, "left", _value(source))
    right, after = _report(tmp_path, backend, "right", _value(source, offset=100.0))

    for report in (before, after):
        assert report["validation"]["valid"] is True
        assert report["validation"]["errors"] == []
        assert {
            name: info["unit"] for name, info in report["statistics"]["fields"].items()
        } == UNITS
        assert {info["name"]: info["unit"] for info in report["fields"]} == UNITS
    result = compare_report_files(ComparisonRequest(left, right, tmp_path / "comparison"))
    assert result.ok, result.to_dict()
    comparison = json.loads((tmp_path / "comparison/comparison.json").read_text(encoding="utf-8"))
    assert comparison["statistics"]["incomparable"] == []
    assert {
        (item["field"], item["metric"], item["unit"]): item["delta"]
        for item in comparison["statistics"]["changed"]
    } == {
        ("temperature", "min", "K"): 100.0,
        ("temperature", "max", "K"): 100.0,
        ("temperature", "mean", "K"): 100.0,
    }


@pytest.mark.parametrize("backend", ["netcdf", "zarr"])
@pytest.mark.parametrize("field", list(UNITS))
@pytest.mark.parametrize("source", ["metadata", "units_attrs"])
def test_conflicting_unit_report_survives_and_blocks_affected_deltas(
    tmp_path, backend, field, source
):
    _, before = _report(tmp_path, backend, "left", _value())
    value = _value(offset=100.0)
    if source == "metadata":
        value.metadata["units"] = {field: "m" if field == "position" else "degC"}
    else:
        value.data[field].attrs["units"] = "m" if field == "position" else "degC"
    _, after = _report(tmp_path, backend, "right", value)

    assert after["validation"]["valid"] is False
    assert after["statistics"]["quality_status"] == "invalid"
    assert {item["code"] for item in after["validation"]["errors"] if item["field"] == field} == {
        "unit_conflict",
        "unit_mismatch",
    }
    assert after["statistics"]["fields"][field]["unit"] is None
    for left, right in ((before, after), (after, before)):
        comparison = compare_reports(left, right)["statistics"]
        assert comparison["changed"] == []
        assert {field, "temperature"} <= {item["field"] for item in comparison["incomparable"]}
        assert all(item["reason"] for item in comparison["incomparable"])


@pytest.mark.parametrize("backend", ["netcdf", "zarr"])
def test_conflicting_null_unit_scalar_still_blocks_dependent_variable(tmp_path, backend):
    _, before = _report(tmp_path, backend, "left", _value(string_scalar=True), string_scalar=True)
    value = _value(offset=100.0, string_scalar=True)
    value.data["ambient"].attrs["unit"] = "K"
    value.metadata["units"] = {"ambient": "degC"}
    _, after = _report(tmp_path, backend, "right", value, string_scalar=True)

    assert after["validation"]["valid"] is False
    assert any(item["code"] == "unit_conflict" for item in after["validation"]["errors"])
    assert after["statistics"]["fields"]["ambient"]["unit"] is None
    for left, right in ((before, after), (after, before)):
        comparison = compare_reports(left, right)["statistics"]
        assert comparison["changed"] == []
        assert any(
            item["field"] == "temperature" and "ambient" in item["reason"]
            for item in comparison["incomparable"]
        )


@pytest.mark.parametrize("backend", ["netcdf", "zarr"])
@pytest.mark.parametrize("field", list(UNITS))
@pytest.mark.parametrize("problem", ["missing", "incompatible"])
def test_missing_or_incompatible_observed_units_are_not_filled_from_schema(
    tmp_path, backend, field, problem
):
    _, before = _report(tmp_path, backend, "left", _value())
    value = _value(offset=100.0)
    value.data[field].attrs.clear()
    if problem == "incompatible":
        value.data[field].attrs["unit"] = "m" if field == "position" else "degC"
    _, after = _report(tmp_path, backend, "right", value)

    assert after["validation"]["valid"] is False
    assert any(item["code"] == "unit_mismatch" for item in after["validation"]["errors"])
    assert after["statistics"]["fields"][field]["unit"] == (
        None if problem == "missing" else "m" if field == "position" else "degC"
    )
    comparison = compare_reports(before, after)["statistics"]
    assert comparison["changed"] == []
    assert any(
        item["field"] == "temperature" and "unit" in item["reason"]
        for item in comparison["incomparable"]
    )
