"""Schema 2.0 array validation and descriptive statistics."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..data import ScientificDataset
from ..model import ValidationIssue, ValidationResult
from ..schemas import ResolvedSchemaV2


def validate_scientific(value: ScientificDataset, schema: ResolvedSchemaV2) -> ValidationResult:
    """Check array declarations and compare units as stored."""
    result = ValidationResult()

    def issue(code: str, name: str | None, message: str, count: int = 1) -> None:
        result.errors.append(ValidationIssue(code, name, message, count))

    expected = {item.name: item.length for item in schema.schema.dimensions}
    if dict(value.data.sizes) != expected:
        issue(
            "dimension_mismatch",
            None,
            f"Expected dimensions {expected}; found {dict(value.data.sizes)}.",
        )
    for declarations, arrays in (
        (schema.schema.coordinates, value.data.coords),
        (schema.schema.variables, value.data.data_vars),
    ):
        declared = {item.name for item in declarations}
        for name in sorted(set(arrays) - declared):
            issue("undeclared_field", name, "Field is not declared in the selected schema.")
        for item in declarations:
            if item.name not in arrays:
                issue("missing_field", item.name, "Required field is missing.")
                continue
            array = arrays[item.name]
            if tuple(array.dims) != item.dims or tuple(array.shape) != tuple(
                expected[dim] for dim in item.dims
            ):
                issue(
                    "dimension_mismatch",
                    item.name,
                    f"Expected dimensions {item.dims}; found {tuple(array.dims)} / {array.shape}.",
                )
            values = np.asarray(array.values)
            kinds = {"float": "f", "integer": "iu", "boolean": "b", "string": "US"}
            matches = values.dtype.kind in kinds[item.dtype]
            if item.dtype == "string" and values.dtype.kind == "O":
                matches = all(isinstance(x, (str, bytes)) for x in values.flat)
            if not matches:
                issue("invalid_dtype", item.name, f"Expected {item.dtype}; found {values.dtype}.")
            unit = array.attrs.get("unit", array.attrs.get("units"))
            metadata_unit = value.metadata.get("units", {}).get(item.name)
            actual_unit = unit if unit is not None else metadata_unit
            if actual_unit != item.unit or (
                metadata_unit is not None and metadata_unit != item.unit
            ):
                issue(
                    "unit_mismatch",
                    item.name,
                    f"Expected unit {item.unit!r}; found {actual_unit!r}.",
                )
            if values.dtype.kind in "fciub":
                count = int(np.count_nonzero(~np.isfinite(values)))
                if count:
                    issue("non_finite", item.name, "Field contains non-finite values.", count)
            else:
                count = int(np.count_nonzero(pd.isna(values)))
                if count:
                    issue("missing_values", item.name, "Field contains missing values.", count)
    return result


def summarize_scientific(value: ScientificDataset, validation: ValidationResult) -> dict[str, Any]:
    """Describe each array in its own dimensions, using finite numeric values."""
    fields: dict[str, Any] = {}
    for name, array in value.data.variables.items():
        values = np.asarray(array.values)
        info: dict[str, Any] = {
            "dims": list(array.dims),
            "shape": list(array.shape),
            "dtype": str(values.dtype),
            "unit": array.attrs.get("unit", array.attrs.get("units")),
            "count": int(values.size),
            "missing_count": int(np.count_nonzero(pd.isna(values))),
        }
        if values.dtype.kind in "fiu":
            finite = values[np.isfinite(values)]
            info.update(
                {
                    "min": float(finite.min()) if finite.size else None,
                    "max": float(finite.max()) if finite.size else None,
                    "mean": float(finite.mean()) if finite.size else None,
                }
            )
        fields[name] = info
    return {
        "quality_status": "valid" if validation.valid else "invalid",
        "dimensions": dict(value.data.sizes),
        "fields": fields,
    }
