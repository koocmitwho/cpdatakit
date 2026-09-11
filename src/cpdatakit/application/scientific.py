"""Schema 2.0 array validation and descriptive statistics."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..data import ScientificDataset
from ..data.validation import validate_scientific as validate_scientific
from ..model import ValidationResult


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
