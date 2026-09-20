"""Statistics preserve integer evidence and stay finite for finite stored values."""

from __future__ import annotations

import json
import math
import sys

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from cpdatakit.application import ComparisonRequest, ReportRequest, build_report
from cpdatakit.application import compare_reports as compare_report_files
from cpdatakit.application.scientific import summarize_scientific
from cpdatakit.comparison import compare_reports
from cpdatakit.data import ScientificDataset
from cpdatakit.formats import NetCDFWriter, ZarrWriter
from cpdatakit.model import Dataset, ValidationResult
from cpdatakit.reporting import render_report_json
from cpdatakit.schema import make_field_schema, make_profile_schema
from cpdatakit.statistics import summarize_dataset


def _scientific(values):
    return ScientificDataset(xr.Dataset({"reading": ("record", values, {"units": "1"})}))


def _v1(values):
    contract = make_profile_schema(
        "numeric-fidelity", [make_field_schema("reading", "float", required=True, unit="1")]
    )
    return summarize_dataset(
        Dataset(pd.DataFrame({"reading": values})), contract, validation=ValidationResult()
    )


@pytest.mark.parametrize("api", ["scientific", "v1"])
@pytest.mark.parametrize(
    "values,minimum,maximum",
    [
        (np.array([2**53 - 1, 2**53 + 1], dtype="int64"), 2**53 - 1, 2**53 + 1),
        (np.array([-(2**63), 2**63 - 1], dtype="int64"), -(2**63), 2**63 - 1),
        (np.array([2**64 - 2, 2**64 - 1], dtype="uint64"), 2**64 - 2, 2**64 - 1),
    ],
)
def test_integer_extrema_are_exact_json_integers(api, values, minimum, maximum):
    if api == "scientific":
        summary = summarize_scientific(_scientific(values), ValidationResult())
        result = summary["fields"]["reading"]
    else:
        summary = _v1(values)
        result = summary["numeric_fields"]["reading"]

    assert type(result["min"]) is int
    assert type(result["max"]) is int
    assert result["min"] == minimum
    assert result["max"] == maximum
    assert json.loads(render_report_json(summary)) == summary


FINITE_CASES = [
    pytest.param(np.array([1e8, 1, -1e8], dtype="float32"), 1 / 3, id="float32-cancel"),
    pytest.param(
        np.array([3e38, 3e38], dtype="float32"),
        3.0000000054977558e38,
        id="float32-sum-overflow",
    ),
    pytest.param(
        np.array([sys.float_info.max, sys.float_info.max]),
        sys.float_info.max,
        id="float64-sum-overflow",
    ),
    pytest.param(
        np.array([sys.float_info.max, 1.0, -sys.float_info.max]),
        1 / 3,
        id="float64-cancel",
    ),
    pytest.param(
        np.array(
            [
                sys.float_info.max,
                sys.float_info.max,
                -sys.float_info.max,
                -sys.float_info.max,
                1e-100,
            ]
        ),
        2e-101,
        id="float64-intermediate-overflow-and-small-residual",
    ),
    pytest.param(np.array([5e-324, 5e-324]), 5e-324, id="subnormal"),
    pytest.param(np.array([-(2**63), 2**63 - 1], dtype="int64"), -0.5, id="integer-cancel"),
]


@pytest.mark.parametrize("api", ["scientific", "v1"])
@pytest.mark.parametrize("values,expected", FINITE_CASES)
def test_finite_stored_values_have_stable_mean_and_json(api, values, expected):
    original = values.copy()
    if api == "scientific":
        summary = summarize_scientific(_scientific(values), ValidationResult())
        result = summary["fields"]["reading"]
    else:
        summary = _v1(values)
        result = summary["numeric_fields"]["reading"]
        assert set(result) == {"min", "max", "mean", "std"}
        assert math.isfinite(result["std"])
    assert math.isfinite(result["mean"])
    assert result["mean"] == pytest.approx(expected, rel=1e-15, abs=0)
    assert json.loads(render_report_json(summary)) == summary
    np.testing.assert_array_equal(values, original)


@pytest.mark.parametrize(
    "values,expected",
    [
        (np.array([2**53, 2**53 + 1], dtype="int64"), 0.5),
        (np.array([-sys.float_info.max, sys.float_info.max]), sys.float_info.max),
        (np.array([sys.float_info.max, sys.float_info.max]), 0.0),
    ],
)
def test_v1_population_standard_deviation_remains_finite_and_stable(values, expected):
    result = _v1(values)["numeric_fields"]["reading"]
    assert result["std"] == pytest.approx(expected, rel=1e-15, abs=0)


@pytest.mark.parametrize("dtype", [object, "Int64", "UInt64"])
def test_v1_missing_integer_does_not_narrow_other_values(dtype):
    values = pd.Series([2**53 + 1, None], dtype=dtype)
    result = _v1(values)["numeric_fields"]["reading"]
    assert type(result["min"]) is int
    assert result["min"] == 2**53 + 1
    assert result["max"] == 2**53 + 1
    assert result["std"] == 0.0


@pytest.mark.parametrize("api", ["scientific", "v1"])
def test_numeric_object_arrays_keep_missingness_and_mixed_integer_evidence(api):
    values = np.array([2**53 + 1, 0.5, -(2**53), None], dtype=object)
    original = values.copy()
    if api == "scientific":
        summary = summarize_scientific(_scientific(values), ValidationResult())
        result = summary["fields"]["reading"]
        assert result["missing_count"] == 1
        assert result["dtype"] == "object"
    else:
        summary = _v1(values)
        result = summary["numeric_fields"]["reading"]
        assert summary["missing_values"]["reading"] == 1
    assert result["min"] == -(2**53)
    assert result["max"] == 2**53 + 1
    assert type(result["max"]) is int
    assert result["mean"] == 0.5
    np.testing.assert_array_equal(values, original)


def test_v1_numeric_strings_are_still_coerced_without_losing_large_integer():
    summary = _v1(pd.Series([str(2**53 + 1), None, "invalid"], dtype=object))
    result = summary["numeric_fields"]["reading"]
    assert result["min"] == 2**53 + 1
    assert result["max"] == 2**53 + 1
    assert summary["missing_values"]["reading"] == 1
    assert summary["infinite_values"]["reading"] == 0


def test_scientific_non_numeric_objects_do_not_acquire_numeric_statistics():
    result = summarize_scientific(
        _scientific(np.array(["air", None, "water"], dtype=object)), ValidationResult()
    )["fields"]["reading"]
    assert result["missing_count"] == 1
    assert "mean" not in result


@pytest.mark.parametrize("values", [np.array([], dtype=float), np.array([np.nan, np.nan])])
def test_empty_or_all_missing_retains_each_summary_contract(values):
    scientific = summarize_scientific(_scientific(values), ValidationResult())["fields"]["reading"]
    assert {key: scientific[key] for key in ("min", "max", "mean")} == {
        "min": None,
        "max": None,
        "mean": None,
    }
    assert scientific["count"] == len(values)
    assert scientific["missing_count"] == len(values)
    assert _v1(values)["numeric_fields"]["reading"] == "not available"


def _report(tmp_path, backend, name, values, *, declared_dtype=None):
    writer = NetCDFWriter() if backend == "netcdf" else ZarrWriter()
    source = tmp_path / (name + (".nc" if backend == "netcdf" else ".zarr"))
    writer.write(_scientific(values), source)
    contract = {
        "profile": "numeric-fidelity",
        "schema_version": "2.0",
        "dimensions": [{"name": "record", "length": len(values)}],
        "coordinates": [],
        "variables": [
            {
                "name": "reading",
                "dims": ["record"],
                "dtype": declared_dtype or ("integer" if values.dtype.kind in "iu" else "float"),
                "unit": "1",
                "role": "measured_field",
            }
        ],
    }
    output = tmp_path / (name + ".json")
    result = build_report(ReportRequest(source, contract, output, format="json"))
    assert result.ok, result.to_dict()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report == result.value.report
    return output, report


@pytest.mark.parametrize("backend", ["netcdf", "zarr"])
@pytest.mark.parametrize("values,expected", FINITE_CASES)
def test_real_backend_json_reports_preserve_finite_mean(tmp_path, backend, values, expected):
    _, report = _report(tmp_path, backend, "finite", values)
    assert report["validation"]["valid"] is True
    summary = report["statistics"]["fields"]["reading"]
    assert math.isfinite(summary["mean"])
    assert summary["mean"] == pytest.approx(expected, rel=1e-15, abs=0)


@pytest.mark.parametrize("backend", ["netcdf", "zarr"])
@pytest.mark.parametrize(
    "values,minimum,maximum",
    [
        (np.array([2**53 - 1, 2**53 + 1], dtype="int64"), 2**53 - 1, 2**53 + 1),
        (np.array([-(2**63), 2**63 - 1], dtype="int64"), -(2**63), 2**63 - 1),
        (np.array([2**64 - 2, 2**64 - 1], dtype="uint64"), 2**64 - 2, 2**64 - 1),
    ],
)
def test_real_backend_integer_extrema_survive_json(tmp_path, backend, values, minimum, maximum):
    _, report = _report(tmp_path, backend, "integers", values)
    assert report["validation"]["valid"] is True
    summary = report["statistics"]["fields"]["reading"]
    assert type(summary["min"]) is int
    assert type(summary["max"]) is int
    assert summary["min"] == minimum
    assert summary["max"] == maximum


@pytest.mark.parametrize("backend", ["netcdf", "zarr"])
def test_real_integer_reports_keep_one_unit_comparison_delta(tmp_path, backend):
    left, before = _report(tmp_path, backend, "before", np.array([2**53], dtype="int64"))
    right, after = _report(tmp_path, backend, "after", np.array([2**53 + 1], dtype="int64"))
    assert before["statistics"]["fields"]["reading"]["min"] == 2**53
    assert after["statistics"]["fields"]["reading"]["max"] == 2**53 + 1
    output = tmp_path / "comparison"
    result = compare_report_files(ComparisonRequest(left, right, output))
    assert result.ok, result.to_dict()
    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    changed = {item["metric"]: item for item in comparison["statistics"]["changed"]}
    assert changed["min"]["delta"] == 1
    assert changed["max"]["delta"] == 1
    assert type(changed["min"]["delta"]) is int
    assert comparison["statistics"]["incomparable"] == []


@pytest.mark.parametrize("backend", ["netcdf", "zarr"])
def test_real_extreme_report_comparison_marks_unrepresentable_delta(tmp_path, backend):
    left, _ = _report(tmp_path, backend, "negative", np.array([-sys.float_info.max]))
    right, _ = _report(tmp_path, backend, "positive", np.array([sys.float_info.max]))
    output = tmp_path / "extreme-comparison"
    result = compare_report_files(ComparisonRequest(left, right, output))
    assert result.ok, result.to_dict()
    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["statistics"]["changed"] == []
    unavailable = comparison["statistics"]["unavailable"]
    assert {item["metric"] for item in unavailable} == {"min", "max", "mean"}
    assert all("finite" in item["reason"].lower() for item in unavailable)
    assert all(item["left"] == -sys.float_info.max for item in unavailable)
    assert all(item["right"] == sys.float_info.max for item in unavailable)


def test_v1_comparison_marks_unrepresentable_delta_without_discarding_operands():
    before = {"statistics": _v1(np.array([-sys.float_info.max]))}
    after = {"statistics": _v1(np.array([sys.float_info.max]))}
    result = compare_reports(before, after)["statistics"]
    assert result["changed"] == []
    assert {item["metric"] for item in result["unavailable"]} == {"min", "max", "mean"}
    assert all("finite" in item["reason"].lower() for item in result["unavailable"])
    assert all(item["left"] == -sys.float_info.max for item in result["unavailable"])


@pytest.mark.parametrize("integer", [2**64, 2**80 + 1])
def test_v1_python_integer_beyond_uint64_is_finite(integer):
    summary = _v1(pd.Series([integer, None], dtype=object))
    result = summary["numeric_fields"]["reading"]
    assert result["min"] == result["max"] == integer
    assert type(result["max"]) is int
    assert result["std"] == 0.0
    assert summary["infinite_values"]["reading"] == 0


def test_v1_mixed_integer_float_comparison_keeps_one_unit_difference():
    before = {"statistics": _v1(np.array([2**53 + 1], dtype="int64"))}
    after = {"statistics": _v1(np.array([2**53], dtype="float64"))}
    result = compare_reports(before, after)["statistics"]
    changed = {item["metric"]: item for item in result["changed"]}
    assert changed["min"]["delta"] == -1
    assert changed["max"]["delta"] == -1


@pytest.mark.parametrize("scalar", [np.float32, np.float64, np.int64])
def test_report_comparison_accepts_numpy_real_scalars(scalar):
    before = {"statistics": {"numeric_fields": {"reading": {"min": scalar(1)}}}}
    after = {"statistics": {"numeric_fields": {"reading": {"min": scalar(2)}}}}
    result = compare_reports(before, after)["statistics"]
    assert result["changed"][0]["delta"] == 1


@pytest.mark.parametrize("float_type", [float, np.float64])
def test_numpy_integer_float_comparison_does_not_promote_before_equality(float_type):
    before = {"statistics": {"numeric_fields": {"reading": {"min": np.int64(2**53 + 1)}}}}
    after = {"statistics": {"numeric_fields": {"reading": {"min": float_type(2**53)}}}}
    changed = compare_reports(before, after)["statistics"]["changed"]
    assert len(changed) == 1
    assert changed[0]["delta"] == -1


@pytest.mark.parametrize(
    "invalid", [[1, 2], np.array([1, 2]), {"x": 1}, np.array([[1, 2], [3, 4]]), np.array(1)]
)
def test_invalid_compound_values_keep_validation_instead_of_crashing_summary(invalid):
    contract = make_profile_schema(
        "numeric-fidelity", [make_field_schema("reading", "float", required=True, unit="1")]
    )
    summary = summarize_dataset(Dataset(pd.DataFrame({"reading": [invalid]})), contract)
    assert summary["quality_status"] == "invalid"
    assert summary["error_count"] > 0
    assert summary["numeric_fields"]["reading"] == "not available"


@pytest.mark.parametrize("backend", ["netcdf", "zarr"])
def test_real_mixed_storage_report_comparison_keeps_one_unit_difference(tmp_path, backend):
    left, before = _report(
        tmp_path,
        backend,
        "mixed-before",
        np.array([2**53 + 1], dtype="int64"),
        declared_dtype="float",
    )
    right, after = _report(
        tmp_path,
        backend,
        "mixed-after",
        np.array([2**53], dtype="float64"),
        declared_dtype="float",
    )
    # A float declaration flags the integer storage dtype. Reports retain both
    # that validation evidence and the exact integer aggregates being compared.
    assert before["validation"]["valid"] is False
    assert before["validation"]["errors"]
    assert after["validation"]["valid"] is True
    output = tmp_path / "mixed-comparison"
    result = compare_report_files(ComparisonRequest(left, right, output))
    assert result.ok, result.to_dict()
    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    changed = {item["metric"]: item for item in comparison["statistics"]["changed"]}
    assert changed["min"]["delta"] == -1
    assert changed["max"]["delta"] == -1
    assert comparison["statistics"]["incomparable"] == []

    before["statistics"]["fields"]["reading"]["min"] = np.int64(2**53 + 1)
    after["statistics"]["fields"]["reading"]["min"] = np.float64(2**53)
    changed = {
        item["metric"]: item for item in compare_reports(before, after)["statistics"]["changed"]
    }
    assert changed["min"]["delta"] == -1


@pytest.mark.parametrize("backend", ["netcdf", "zarr"])
def test_real_nonfinite_inputs_keep_validation_evidence_and_finite_aggregates(tmp_path, backend):
    values = np.array([np.nan, np.inf, -np.inf, 1.0, 3.0])
    _, report = _report(tmp_path, backend, "nonfinite", values)
    assert report["validation"]["valid"] is False
    assert report["validation"]["errors"]
    summary = report["statistics"]["fields"]["reading"]
    assert summary["count"] == 5
    assert summary["missing_count"] == 1
    assert {key: summary[key] for key in ("min", "max", "mean")} == {
        "min": 1.0,
        "max": 3.0,
        "mean": 2.0,
    }
    legacy = _v1(values)
    assert legacy["missing_values"]["reading"] == 1
    assert legacy["infinite_values"]["reading"] == 2
    assert legacy["numeric_fields"]["reading"]["mean"] == 2.0
