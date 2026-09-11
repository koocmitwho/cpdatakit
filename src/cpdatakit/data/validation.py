"""Validation of explicit scientific array contracts."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..exceptions import DataValidationError
from ..model import ValidationIssue, ValidationResult
from ..schemas import ResolvedSchemaV2
from .scientific import ScientificDataset
from .units import declared_unit


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
            try:
                actual_unit = declared_unit(array, value.metadata, item.name)
            except DataValidationError as exc:
                issue("unit_conflict", item.name, str(exc))
                actual_unit = None
            if actual_unit != item.unit:
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
