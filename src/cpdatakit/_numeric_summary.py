"""Aggregate finite Python scalars without narrowing integer or floating evidence."""

from __future__ import annotations

from fractions import Fraction
from numbers import Integral
from statistics import mean, pstdev

import numpy as np


def numeric_equal(before: int | float, after: int | float) -> bool:
    """Compare Python scalars so NumPy cannot promote a large integer to float."""
    left = int(before) if isinstance(before, Integral) else float(before)
    right = int(after) if isinstance(after, Integral) else float(after)
    return left == right


def finite_difference(before: int | float, after: int | float) -> int | float | None:
    """Subtract stored values before the final floating rounding, or flag overflow."""
    if isinstance(before, Integral) and isinstance(after, Integral):
        return int(after) - int(before)
    try:
        left = int(before) if isinstance(before, Integral) else float(before)
        right = int(after) if isinstance(after, Integral) else float(after)
        return float(Fraction(right) - Fraction(left))
    except OverflowError:
        return None


def finite_statistics(
    values: list[int | float], *, include_std: bool = False
) -> dict[str, int | float]:
    """Summarize nonempty finite scalars, rounding only the final mean/std.

    The standard library accumulates exact integer ratios before division. This
    avoids both floating overflow and losing small residuals after cancellation;
    simply promoting to float64 or scaling by the largest value cannot do both.
    Keep extrema in their original Python scalar types for JSON integer fidelity.
    """
    values = [
        int(value) if isinstance(value, (int, np.integer)) else float(value) for value in values
    ]
    result = {"min": min(values), "max": max(values), "mean": float(mean(values))}
    if include_std:
        result["std"] = float(pstdev(values))
    return result
