"""Schema 2.0 array validation and descriptive statistics."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .._numeric_summary import finite_statistics
from ..data import ScientificDataset
from ..data.units import declared_unit
from ..data.validation import validate_scientific as validate_scientific
from ..exceptions import DataValidationError
from ..model import ValidationResult


def summarize_scientific(value: ScientificDataset, validation: ValidationResult) -> dict[str, Any]:
    """Describe each array in its own dimensions, using finite numeric values."""
    fields: dict[str, Any] = {}
    for name, array in value.data.variables.items():
        values = np.asarray(array.values)
        try:
            unit = declared_unit(array, value.metadata, name)
        except DataValidationError:
            # Validation retains the conflicting declarations. The report must
            # still be usable without choosing one of those declarations.
            unit = None
        info: dict[str, Any] = {
            "dims": list(array.dims),
            "shape": list(array.shape),
            "dtype": str(values.dtype),
            "unit": unit,
            "count": int(values.size),
            "missing_count": int(np.count_nonzero(pd.isna(values))),
        }
        present = values[~pd.isna(values)]
        numeric_object = values.dtype.kind == "O" and all(
            isinstance(item, (int, float, np.integer, np.floating))
            and not isinstance(item, (bool, np.bool_))
            for item in present
        )
        if values.dtype.kind in "fiu" or numeric_object:
            finite = [item for item in present.tolist() if math.isfinite(item)]
            info.update(
                finite_statistics(finite) if finite else {"min": None, "max": None, "mean": None}
            )
        fields[name] = info
    return {
        "quality_status": "valid" if validation.valid else "invalid",
        "dimensions": dict(value.data.sizes),
        "fields": fields,
    }
