from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from cpdatakit.application import ComparisonRequest, ReportRequest, build_report
from cpdatakit.application import compare_reports as compare_report_files
from cpdatakit.comparison import compare_reports, render_comparison_markdown
from cpdatakit.data import ScientificDataset
from cpdatakit.exceptions import CPDataKitError, OutputExistsError, SchemaError
from cpdatakit.io import write_hdf5_v2
from cpdatakit.schema_diff import diff_schemas, render_schema_diff_markdown, write_schema_diff
from cpdatakit.schemas import resolve_schema_v2, schema_v2_sha256


def _schema() -> dict:
    return {
        "profile": "thermal-field",
        "schema_version": "2.0",
        "dimensions": [{"name": "time", "length": 2}, {"name": "x", "length": 2}],
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


def _report(*, mean=301.5, contract=None) -> dict:
    schema = _schema() if contract is None else contract
    return {
        "schema": resolve_schema_v2(schema).to_dict(),
        "fields": [{"name": "temperature", "dims": ["time", "x"], "shape": [2, 2], "unit": "K"}],
        "statistics": {
            "dimensions": {"time": 2, "x": 2},
            "fields": {
                "temperature": {
                    "dtype": "float64",
                    "dims": ["time", "x"],
                    "shape": [2, 2],
                    "unit": "K",
                    "count": 4,
                    "missing_count": 0,
                    "min": 300.0,
                    "max": 303.0,
                    "mean": mean,
                }
            },
        },
        "validation": {"valid": True, "errors": [], "warnings": []},
    }


def _real_report(tmp_path: Path, suffix: str, offset: float) -> Path:
    dataset = xr.Dataset(
        {
            "temperature": (
                ("time", "x"),
                [[300.0 + offset, 301.0 + offset], [302.0 + offset, 303.0 + offset]],
                {"unit": "K", "role": "measured_field"},
            )
        },
        coords={
            "time": ("time", [0.0, 1.0], {"unit": "s"}),
            "x": ("x", [0.0, 1.0], {"unit": "mm"}),
        },
    )
    data = tmp_path / f"temperature-{offset}{suffix}"
    if suffix == ".nc":
        dataset.to_netcdf(data, engine="h5netcdf")
    else:
        write_hdf5_v2(ScientificDataset(dataset), data, resolve_schema_v2(_schema()))
    output = data.with_suffix(".json")
    result = build_report(ReportRequest(data=data, schema=_schema(), output=output, format="json"))
    assert result.ok, result.error
    assert result.value.report["validation"]["valid"] is True
    return output


@pytest.mark.parametrize("suffix", [".nc", ".h5"])
def test_compare_real_scientific_reports_exposes_100_kelvin_change(tmp_path: Path, suffix: str):
    left = _real_report(tmp_path, suffix, 0.0)
    right = _real_report(tmp_path, suffix, 100.0)
    result = compare_report_files(
        ComparisonRequest(left=left, right=right, output=tmp_path / "comparison")
    )

    assert result.ok, result.error
    comparison = json.loads((tmp_path / "comparison" / "comparison.json").read_text())
    assert comparison["schema"]["classification"] == "identical"
    assert comparison["statistics"]["incomparable"] == []
    assert all(item["unit"] == "K" for item in comparison["statistics"]["changed"])
    assert "| K |" in (tmp_path / "comparison" / "comparison.md").read_text()
    assert {item["metric"]: item["delta"] for item in comparison["statistics"]["changed"]} == {
        "min": 100.0,
        "max": 100.0,
        "mean": 100.0,
    }


@pytest.mark.parametrize(
    ("section", "index", "property", "value"),
    [
        ("dimensions", 0, "length", 3),
        ("coordinates", 1, "unit", "m"),
        ("coordinates", 1, "dims", ["time"]),
        ("variables", 0, "dims", ["x", "time"]),
        ("variables", 0, "dtype", "integer"),
        ("variables", 0, "unit", "degC"),
        ("variables", 0, "role", "predicted_field"),
        ("variables", 0, "components", ["xx", "yy"]),
        ("variables", 0, "attributes", {"reference_frame": "sample"}),
    ],
)
def test_schema_v2_diff_records_structural_semantic_changes(section, index, property, value):
    target = _schema()
    target[section][index][property] = value
    result = diff_schemas(_schema(), target)
    assert result["classification"] == "breaking"
    assert result["requires_explicit_data_mapping"] is True
    assert result[section]["changed"] == [
        {"name": target[section][index]["name"], "changes": [property]}
    ]
    assert result["source"]["sha256"] == schema_v2_sha256(_schema())


def test_schema_v2_diff_records_conventions_and_declaration_order():
    target = _schema()
    target["dimensions"].reverse()
    target["conventions"] = {"coordinate_system": "sample"}
    result = diff_schemas(_schema(), target)
    assert result["classification"] == "breaking"
    assert result["dimensions"]["order_changed"] is True
    assert result["conventions_changed"] == ["coordinate_system"]
    rendered = render_schema_diff_markdown(result)
    assert "## Dimensions" in rendered
    assert "Order changed: True" in rendered


def test_schema_v2_diff_accepts_resolved_objects_and_paths(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(_schema()), encoding="utf-8")
    resolved = resolve_schema_v2(path)
    result = diff_schemas(path, resolved)
    assert result["classification"] == "identical"
    assert result["requires_explicit_data_mapping"] is False
    assert result["fields"] == {"added": [], "removed": [], "changed": []}
    assert diff_schemas(resolved.schema, resolved.to_dict())["classification"] == "identical"


def test_schema_v2_addition_removal_keeps_coordinates_and_variables_distinct():
    target = _schema()
    target["coordinates"].pop()
    target["variables"].append(
        {
            "name": "pressure",
            "dims": ["time", "x"],
            "dtype": "float",
            "unit": "Pa",
            "role": "measured_field",
        }
    )
    result = diff_schemas(_schema(), target)
    assert result["coordinates"]["removed"] == ["x"]
    assert result["variables"]["added"] == ["pressure"]
    assert result["fields"]["added"] == ["pressure"]
    assert result["fields"]["removed"] == ["x"]


@pytest.mark.parametrize(
    "change", ["unit", "dims", "role", "coordinate_unit", "length", "conventions"]
)
def test_scientific_comparison_withholds_deltas_for_incomparable_contracts(change):
    schema = _schema()
    if change == "unit":
        schema["variables"][0]["unit"] = "degC"
    elif change == "dims":
        schema["variables"][0]["dims"].reverse()
    elif change == "role":
        schema["variables"][0]["role"] = "predicted_field"
    elif change == "coordinate_unit":
        schema["coordinates"][1]["unit"] = "m"
    elif change == "length":
        schema["dimensions"][0]["length"] = 3
    else:
        schema["conventions"] = {"reference_frame": "sample"}
    result = compare_reports(_report(), _report(mean=401.5, contract=schema))
    assert result["statistics"]["changed"] == []
    assert any(
        item["field"] == "temperature" and item["reason"]
        for item in result["statistics"]["incomparable"]
    )
    rendered = render_comparison_markdown(result)
    assert "## Incomparable statistics" in rendered
    assert "temperature" in rendered


@pytest.mark.parametrize(
    ("property", "value"), [("unit", "degC"), ("dims", ["x", "time"]), ("shape", [4, 1])]
)
def test_scientific_comparison_checks_observed_structure_before_deltas(property, value):
    right = _report(mean=401.5)
    right["statistics"]["fields"]["temperature"][property] = value
    result = compare_reports(_report(), right)
    assert not result["statistics"]["changed"]
    assert result["statistics"]["incomparable"]


@pytest.mark.parametrize("unavailable", [None, "not available", float("nan")])
def test_scientific_comparison_exposes_values_unavailable_on_both_sides(unavailable):
    result = compare_reports(_report(mean=unavailable), _report(mean=unavailable))
    assert not result["statistics"]["changed"]
    assert any(
        item["field"] == "temperature" and item["metric"] == "mean"
        for item in result["statistics"]["unavailable"]
    )
    json.dumps(result, allow_nan=False)


def test_scientific_comparison_does_not_treat_missing_statistics_as_identical():
    left = _report()
    del left["statistics"]
    result = compare_reports(left, deepcopy(left))
    assert result["statistics"]["unavailable"]


def test_scientific_comparison_preserves_added_removed_fields_without_numeric_deltas():
    right = _report()
    right["schema"]["resolved"]["variables"][0]["name"] = "prediction"
    right["fields"][0]["name"] = "prediction"
    right["statistics"]["fields"]["prediction"] = right["statistics"]["fields"].pop("temperature")
    result = compare_reports(_report(), right)
    assert result["structure"]["fields_added"] == ["prediction"]
    assert result["structure"]["fields_removed"] == ["temperature"]
    assert not result["statistics"]["changed"]
    assert {item["field"] for item in result["statistics"]["incomparable"]} == {
        "temperature",
        "prediction",
    }


def test_cross_version_comparison_requires_explicit_mapping():
    v1 = {
        "profile": "curve",
        "schema_version": "1.0",
        "fields": [{"name": "temperature", "dtype": "float", "unit": "K"}],
    }
    with pytest.raises(SchemaError, match=r"[Cc]ross-version.*explicit"):
        diff_schemas(v1, _schema())
    with pytest.raises(SchemaError, match=r"[Cc]ross-version.*explicit"):
        compare_reports({"schema": v1}, _report())


def test_numeric_to_string_change_is_incomparable_in_both_directions():
    left = _report()
    right = _report()
    variable = right["schema"]["resolved"]["variables"][0]
    variable["dtype"], variable["unit"] = "string", None
    right["statistics"]["fields"]["temperature"] = {"dtype": "str32", "count": 4}
    for before, after in ((left, right), (right, left)):
        result = compare_reports(before, after)
        assert not result["statistics"]["changed"]
        assert any(item["field"] == "temperature" for item in result["statistics"]["incomparable"])


def test_missing_scientific_schema_blocks_numeric_comparison_explicitly():
    left = _report()
    del left["schema"]
    result = compare_reports(left, _report(mean=401.5))
    assert result["schema"]["classification"] == "not available"
    assert not result["statistics"]["changed"]
    assert any(
        item["field"] == "temperature" and "schema" in item["reason"].lower()
        for item in result["statistics"]["incomparable"]
    )


def test_v1_comparison_withholds_deltas_for_unit_conflicts_without_changing_result_keys():
    left = {
        "schema": {
            "profile": "curve",
            "schema_version": "1.0",
            "fields": [{"name": "temperature", "dtype": "float", "unit": "K"}],
        },
        "statistics": {"numeric_fields": {"temperature": {"mean": 300.0}}},
    }
    right = deepcopy(left)
    right["schema"]["fields"][0]["unit"] = "degC"
    right["statistics"]["numeric_fields"]["temperature"]["mean"] = 26.85
    result = compare_reports(left, right)
    assert set(result["statistics"]) == {"changed", "unavailable"}
    assert not result["statistics"]["changed"]
    assert any(
        item["field"] == "temperature" and "unit" in item["reason"]
        for item in result["statistics"]["unavailable"]
    )


def test_comparison_markdown_includes_dimension_and_coordinate_diffs():
    target = _schema()
    target["dimensions"][0]["length"] = 3
    target["coordinates"][0]["unit"] = "min"
    result = compare_reports(_report(), _report(contract=target))
    rendered = render_comparison_markdown(result)
    assert "Dimensions" in rendered
    assert "length" in rendered
    assert "Coordinates" in rendered
    assert "unit" in rendered


def test_cli_schema_diff_supports_scientific_paths(tmp_path):
    from cpdatakit.cli import main

    left, right, output = tmp_path / "left.json", tmp_path / "right.json", tmp_path / "diff.json"
    left.write_text(json.dumps(_schema()), encoding="utf-8")
    changed = _schema()
    changed["variables"][0]["dims"].reverse()
    right.write_text(json.dumps(changed), encoding="utf-8")
    assert main(["schema", "diff", str(left), str(right), "--output", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["variables"]["changed"] == [{"name": "temperature", "changes": ["dims"]}]


def test_schema_diff_publication_preserves_a_concurrently_created_output(tmp_path, monkeypatch):
    target = tmp_path / "diff.json"
    original_write = Path.write_text

    def competitor_during_write(path, content, *args, **kwargs):
        if not target.exists():
            original_write(target, "concurrent output", encoding="utf-8")
        return original_write(path, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", competitor_during_write)
    with pytest.raises(OutputExistsError):
        write_schema_diff(diff_schemas(_schema(), _schema()), target)
    assert target.read_text(encoding="utf-8") == "concurrent output"
    assert list(tmp_path.iterdir()) == [target]


def test_schema_diff_failed_force_write_preserves_previous_bytes(tmp_path, monkeypatch):
    target = tmp_path / "diff.json"
    target.write_bytes(b"previous report")
    original_write = Path.write_text

    def partial_write(path, content, *args, **kwargs):
        original_write(path, "partial", encoding="utf-8")
        raise OSError("injected disk write failure")

    monkeypatch.setattr(Path, "write_text", partial_write)
    with pytest.raises(CPDataKitError, match="Cannot write schema diff"):
        write_schema_diff(diff_schemas(_schema(), _schema()), target, force=True)
    assert target.read_bytes() == b"previous report"
    assert list(tmp_path.iterdir()) == [target]


def test_scientific_comparison_reason_is_stable_across_process_hash_seeds():
    schema = _schema()
    schema["coordinates"][0]["unit"] = "ms"
    schema["coordinates"][1]["unit"] = "m"
    reports = json.dumps([_report(), _report(contract=schema)])
    script = (
        "import json,sys; from cpdatakit.comparison import compare_reports; "
        "result=compare_reports(*json.load(sys.stdin)); "
        "print(next(item['reason'] for item in result['statistics']['incomparable'] "
        "if item['field']=='temperature'))"
    )
    for seed in ("0", "1"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            input=reports,
            text=True,
            capture_output=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert result.stdout.strip() == "Coordinate declaration differs: time"


def _real_coordinate_reports(tmp_path, suffix, change, *, relation="related"):
    expected_schema = _schema()
    if relation == "scalar":
        coordinate_dims = []
    else:
        expected_schema["dimensions"].append({"name": "calibration", "length": 2})
        coordinate_dims = ["time", "calibration"] if relation == "related" else ["calibration"]
    expected_schema["coordinates"].append(
        {"name": "probe", "dims": coordinate_dims, "dtype": "float", "unit": "mm"}
    )
    reports = []
    for name, offset in (("left", 0.0), ("right", 100.0)):
        source_schema = deepcopy(expected_schema)
        if name == "right":
            if change == "unit":
                source_schema["coordinates"][-1]["unit"] = "m"
            elif change == "dims":
                source_schema["coordinates"][-1]["dims"].reverse()
            else:
                source_schema["dimensions"][-1]["length"] = 3
        coordinate = source_schema["coordinates"][-1]
        lengths = {item["name"]: item["length"] for item in source_schema["dimensions"]}
        shape = tuple(lengths[dim] for dim in coordinate["dims"])
        value = xr.Dataset(
            {"temperature": (("time", "x"), np.full((2, 2), 300.0 + offset), {"unit": "K"})},
            coords={
                "time": ("time", [0.0, 1.0], {"unit": "s"}),
                "x": ("x", [0.0, 1.0], {"unit": "mm"}),
                "probe": (coordinate["dims"], np.ones(shape), {"unit": coordinate["unit"]}),
            },
        )
        path = tmp_path / f"{name}{suffix}"
        if suffix == ".nc":
            value.to_netcdf(path, engine="h5netcdf")
        else:
            # Write a valid source, then apply the selected comparison contract to it.
            write_hdf5_v2(ScientificDataset(value), path, source_schema)
        result = build_report(
            ReportRequest(path, expected_schema, path.with_suffix(".json"), format="json")
        )
        assert result.ok, result.to_dict()
        assert result.value.report["validation"]["valid"] is (name == "left")
        reports.append(result.value.report)
    return reports


@pytest.mark.parametrize("suffix", [".nc", ".h5"])
@pytest.mark.parametrize("change", ["unit", "dims", "shape"])
def test_real_observed_coordinate_conflicts_block_dependent_variable_deltas(
    tmp_path, suffix, change
):
    left, right = _real_coordinate_reports(tmp_path, suffix, change)

    for before, after in ((left, right), (right, left)):
        result = compare_reports(before, after)
        assert not any(item["field"] == "temperature" for item in result["statistics"]["changed"])
        reason = next(
            item["reason"]
            for item in result["statistics"]["incomparable"]
            if item["field"] == "temperature"
        )
        assert "probe" in reason
        assert change in reason


@pytest.mark.parametrize("suffix", [".nc", ".h5"])
def test_unrelated_observed_coordinate_conflict_does_not_block_variable_deltas(tmp_path, suffix):
    reports = _real_coordinate_reports(tmp_path, suffix, "unit", relation="unrelated")

    result = compare_reports(*reports)

    assert {
        item["metric"]: item["delta"]
        for item in result["statistics"]["changed"]
        if item["field"] == "temperature"
    } == {"min": 100.0, "max": 100.0, "mean": 100.0}
    assert any(item["field"] == "probe" for item in result["statistics"]["incomparable"])


@pytest.mark.parametrize("suffix", [".nc", ".h5"])
def test_scalar_observed_coordinate_conflict_blocks_variable_deltas(tmp_path, suffix):
    reports = _real_coordinate_reports(tmp_path, suffix, "unit", relation="scalar")

    result = compare_reports(*reports)

    assert not any(item["field"] == "temperature" for item in result["statistics"]["changed"])
    assert any(
        item["field"] == "temperature" and "probe" in item["reason"]
        for item in result["statistics"]["incomparable"]
    )
