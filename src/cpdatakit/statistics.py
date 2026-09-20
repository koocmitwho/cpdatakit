"""Descriptive summaries bounded by a declared schema."""

from __future__ import annotations

from numbers import Real
from typing import Any

import numpy as np
import pandas as pd

from ._numeric_summary import finite_statistics
from .domains.crystal_plasticity import summarize_cp_identifiers
from .model import Dataset, ValidationResult
from .schema import BUILTIN_PROFILES, ProfileSchema, load_schema
from .validation import validate_dataset


def summarize_dataset(
    dataset: Dataset | pd.DataFrame,
    schema: str | ProfileSchema,
    *,
    validation: ValidationResult | None = None,
) -> dict[str, Any]:
    """Summarize the quantities declared by the schema."""
    value = dataset if isinstance(dataset, Dataset) else Dataset(dataset)
    contract = load_schema(schema)
    report = validation or validate_dataset(value, contract)
    numeric: dict[str, Any] = {}
    missing: dict[str, int | str] = {}
    infinite: dict[str, int | str] = {}
    for spec in contract.fields:
        if spec.name not in value.data:
            missing[spec.name] = "not available"
            infinite[spec.name] = "not available"
            if spec.dtype in {"float", "integer"}:
                numeric[spec.name] = "not available"
            continue
        series = value.data[spec.name]
        missing[spec.name] = int(series.isna().sum())
        if spec.dtype in {"float", "integer"}:
            if spec.shape:
                infinite[spec.name] = "not available"
                numeric[spec.name] = "not available"
                continue
            # Coercing a whole object column promotes its integers to floats if
            # any record is missing or fractional. Coerce scalar records first.
            values = (
                [
                    pd.to_numeric(item, errors="coerce") if pd.api.types.is_scalar(item) else np.nan
                    for item in series
                ]
                if not pd.api.types.is_numeric_dtype(series.dtype)
                else pd.to_numeric(series, errors="coerce").tolist()
            )
            present = [item for item in values if isinstance(item, Real) and not pd.isna(item)]
            finite = [item for item in present if isinstance(item, int) or np.isfinite(item)]
            infinite[spec.name] = len(present) - len(finite)
            numeric[spec.name] = (
                finite_statistics(finite, include_std=True) if finite else "not available"
            )
    summary = {
        "record_count": len(value.data),
        "field_count": len(value.data.columns),
        "numeric_fields": numeric,
        "missing_values": missing,
        "infinite_values": infinite,
        "quality_status": "valid" if report.valid else "invalid",
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "scope_note": (
            "Validation reports declared format constraints; physical or scientific "
            "interpretation remains part of the domain workflow."
        ),
    }
    if contract.profile in BUILTIN_PROFILES:
        summary.update(summarize_cp_identifiers(value))
    return summary
