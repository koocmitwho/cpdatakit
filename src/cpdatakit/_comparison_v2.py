"""Scientific schema and aggregate comparison without implicit unit or axis mapping."""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Real
from pathlib import Path
from typing import Any

from .exceptions import SchemaError
from .schemas import ResolvedSchemaV2, SchemaV2, resolve_schema_v2, schema_v2_sha256

_MISSING = "not available"
_ARRAY_PROPERTIES = ("dims", "dtype", "unit", "role", "components", "attributes")


def scientific_contract(value: Any) -> SchemaV2:
    """Validate raw, resolved or file-backed schema 2.0 declarations."""
    if isinstance(value, ResolvedSchemaV2):
        value = value.schema
    if isinstance(value, SchemaV2):
        value = value.to_dict()
    if isinstance(value, Mapping):
        if "resolved" in value:
            resolved = value["resolved"]
            if not isinstance(resolved, Mapping):
                raise SchemaError("Resolved scientific schema must be an object")
            for key in ("profile", "schema_version"):
                if value.get(key) != resolved.get(key):
                    raise SchemaError(
                        f"Resolved scientific schema {key} does not match its wrapper"
                    )
            value = resolved
        value = dict(value)
    elif isinstance(value, str):
        value = Path(value)
    return resolve_schema_v2(value).schema


def _named(values: Any) -> dict[str, Any]:
    return {item.name: item.to_dict() for item in values}


def _declaration_diff(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    changed = []
    for name in left:
        if name not in right:
            continue
        properties = list(left[name])
        properties.extend(key for key in right[name] if key not in left[name])
        changes = [key for key in properties if left[name].get(key) != right[name].get(key)]
        if changes:
            changed.append({"name": name, "changes": changes})
    return {
        "added": [name for name in right if name not in left],
        "removed": [name for name in left if name not in right],
        "changed": changed,
        "order_changed": [name for name in left if name in right]
        != [name for name in right if name in left],
    }


def diff_scientific_schemas(source: Any, target: Any) -> dict[str, Any]:
    """Compare resolved scientific contracts, including their ordered declarations."""
    left, right = scientific_contract(source), scientific_contract(target)
    sections = {
        name: _declaration_diff(_named(getattr(left, name)), _named(getattr(right, name)))
        for name in ("dimensions", "coordinates", "variables")
    }
    fields = _declaration_diff(
        {**_named(left.coordinates), **_named(left.variables)},
        {**_named(right.coordinates), **_named(right.variables)},
    )
    # A coordinate becoming a data variable changes its interpretation even when
    # its shared declaration properties are unchanged.
    left_coordinates, right_coordinates = _named(left.coordinates), _named(right.coordinates)
    for name in (*left_coordinates, *(item.name for item in left.variables)):
        if (name in left_coordinates) == (name in right_coordinates):
            continue
        if name in fields["removed"]:
            continue
        change = next((item for item in fields["changed"] if item["name"] == name), None)
        if change is None:
            fields["changed"].append({"name": name, "changes": ["declaration_kind"]})
        else:
            change["changes"].append("declaration_kind")
    fields.pop("order_changed")
    names = list(left.conventions)
    names.extend(name for name in right.conventions if name not in left.conventions)
    missing = object()
    conventions = [
        name
        for name in names
        if left.conventions.get(name, missing) != right.conventions.get(name, missing)
    ]
    identical = left.to_dict() == right.to_dict()
    return {
        "source": {
            "profile": left.profile,
            "schema_version": "2.0",
            "sha256": schema_v2_sha256(left),
        },
        "target": {
            "profile": right.profile,
            "schema_version": "2.0",
            "sha256": schema_v2_sha256(right),
        },
        "classification": "identical" if identical else "breaking",
        "fields": fields,
        **sections,
        "conventions_changed": conventions,
        "extension_prefix_changed": False,
        "requires_explicit_data_mapping": not identical,
    }


def _statistics(report: Mapping[str, Any]) -> Mapping[str, Any]:
    statistics = report.get("statistics")
    if not isinstance(statistics, Mapping):
        return {}
    fields = statistics.get("fields")
    return fields if isinstance(fields, Mapping) else {}


def _unit_conflicts(report: Mapping[str, Any]) -> set[str]:
    """Distinguish a conflict's unknown unit from a legitimate null string unit."""
    validation = report.get("validation")
    errors = validation.get("errors") if isinstance(validation, Mapping) else None
    if not isinstance(errors, list):
        return set()
    return {
        item["field"]
        for item in errors
        if isinstance(item, Mapping)
        and item.get("code") == "unit_conflict"
        and isinstance(item.get("field"), str)
    }


def _finite(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(float(value))


def _semantic_changes(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    return [key for key in _ARRAY_PROPERTIES if left.get(key) != right.get(key)]


def _comparability_reason(name: str, left: SchemaV2, right: SchemaV2) -> str | None:
    left_fields = {**_named(left.coordinates), **_named(left.variables)}
    right_fields = {**_named(right.coordinates), **_named(right.variables)}
    if name not in left_fields or name not in right_fields:
        return "Field declaration is absent from one schema"
    if left.profile != right.profile:
        return "Schema profiles differ; explicit field mapping is required"
    if left.conventions != right.conventions:
        return "Schema conventions differ; explicit interpretation is required"
    left_coordinates, right_coordinates = _named(left.coordinates), _named(right.coordinates)
    if (name in left_coordinates) != (name in right_coordinates):
        return "Coordinate and variable declarations have different meanings"
    changes = _semantic_changes(left_fields[name], right_fields[name])
    if changes:
        return "Field declaration differs: " + ", ".join(changes)
    dims = left_fields[name]["dims"]
    left_dimensions, right_dimensions = _named(left.dimensions), _named(right.dimensions)
    if any(left_dimensions.get(dim) != right_dimensions.get(dim) for dim in dims):
        return "Declared dimension lengths differ"
    for coordinate in dict.fromkeys((*left_coordinates, *right_coordinates)):
        before = left_coordinates.get(coordinate)
        after = right_coordinates.get(coordinate)
        coordinate_dims = set((before or after)["dims"])
        if coordinate_dims and not coordinate_dims.intersection(dims):
            continue
        if before is None or after is None or _semantic_changes(before, after):
            return f"Coordinate declaration differs: {coordinate}"
    return None


def _observed_reason(
    name: str, statistics: Mapping[str, Any], schema: SchemaV2, unit_conflicts: set[str]
) -> str | None:
    if name in unit_conflicts:
        return "Observed unit declarations conflict or are invalid"
    item = statistics.get(name)
    if not isinstance(item, Mapping):
        return None  # Missing aggregates are reported as unavailable below.
    declaration = {**_named(schema.coordinates), **_named(schema.variables)}[name]
    expected = {
        "unit": declaration["unit"],
        "dims": declaration["dims"],
        "shape": [_named(schema.dimensions)[dim]["length"] for dim in declaration["dims"]],
    }
    for key, value in expected.items():
        if item.get(key) != value:
            return f"Observed {key} is missing or conflicts with the declared schema"
    return None


def _observed_coordinate_reason(
    name: str, statistics: Mapping[str, Any], schema: SchemaV2, unit_conflicts: set[str]
) -> str | None:
    declaration = {**_named(schema.coordinates), **_named(schema.variables)}[name]
    dimensions = set(declaration["dims"])
    for coordinate in schema.coordinates:
        if coordinate.name == name:
            continue
        if coordinate.dims and not dimensions.intersection(coordinate.dims):
            continue
        reason = _observed_reason(coordinate.name, statistics, schema, unit_conflicts)
        if reason:
            return f"Coordinate observation differs: {coordinate.name} ({reason})"
    return None


def compare_scientific_statistics(
    left_report: Mapping[str, Any], right_report: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """Subtract only compatible numeric aggregates; retain all missing-value evidence."""
    left_stats, right_stats = _statistics(left_report), _statistics(right_report)
    left_unit_conflicts, right_unit_conflicts = (
        _unit_conflicts(left_report),
        _unit_conflicts(right_report),
    )
    if not all(isinstance(report.get("schema"), Mapping) for report in (left_report, right_report)):
        names = list(dict.fromkeys((*left_stats, *right_stats))) or ["all fields"]
        return {
            "changed": [],
            "unavailable": [],
            "incomparable": [
                {
                    "field": name,
                    "reason": "Schema definition unavailable; "
                    "numeric comparability cannot be established",
                }
                for name in names
            ],
        }
    left = scientific_contract(left_report["schema"])
    right = scientific_contract(right_report["schema"])
    left_declarations = {**_named(left.coordinates), **_named(left.variables)}
    right_declarations = {**_named(right.coordinates), **_named(right.variables)}
    declarations = {**left_declarations, **right_declarations}
    names = list(declarations)
    names.extend(name for name in (*left_stats, *right_stats) if name not in declarations)
    result: dict[str, list[dict[str, Any]]] = {"changed": [], "unavailable": [], "incomparable": []}
    for name in dict.fromkeys(names):
        declared_types = {
            side[name]["dtype"] for side in (left_declarations, right_declarations) if name in side
        }
        if declared_types and not declared_types.intersection({"float", "integer"}):
            continue
        reason = _comparability_reason(name, left, right)
        if reason is None:
            reason = _observed_reason(
                name, left_stats, left, left_unit_conflicts
            ) or _observed_reason(name, right_stats, right, right_unit_conflicts)
        if reason is None:
            reason = _observed_coordinate_reason(
                name, left_stats, left, left_unit_conflicts
            ) or _observed_coordinate_reason(name, right_stats, right, right_unit_conflicts)
        if reason:
            result["incomparable"].append({"field": name, "reason": reason})
            continue
        before, after = left_stats.get(name), right_stats.get(name)
        before = before if isinstance(before, Mapping) else {}
        after = after if isinstance(after, Mapping) else {}
        metrics = ["min", "max", "mean"]
        metrics.extend(
            key for key in ("std", "count", "missing_count") if key in before or key in after
        )
        for metric in metrics:
            left_value, right_value = before.get(metric, _MISSING), after.get(metric, _MISSING)
            item = {
                "field": name,
                "metric": metric,
                "left": left_value,
                "right": right_value,
                "unit": "1" if metric in {"count", "missing_count"} else declarations[name]["unit"],
            }
            if not (_finite(left_value) and _finite(right_value)):
                result["unavailable"].append(item)
            elif left_value != right_value:
                result["changed"].append({**item, "delta": right_value - left_value})
    return result
