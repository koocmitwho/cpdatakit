"""xarray-backed scientific data and explicit lossless tabular conversions."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from numbers import Real
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from ..exceptions import (
    AmbiguousRecordAxisError,
    LossyConversionError,
    RaggedDataError,
    UnsupportedDataError,
)
from ..model import Dataset

_INTERNAL_RECORD_DIM = "record_dim"


def _json_metadata(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("ScientificDataset metadata must be a dictionary")
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TypeError("ScientificDataset metadata must be JSON-compatible") from exc
    return deepcopy(value)


def _is_missing(value: object) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    if isinstance(value, (list, tuple, np.ndarray, dict, set, frozenset)):
        return False
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return isinstance(missing, (bool, np.bool_)) and bool(missing)


def _is_nested(value: object) -> bool:
    return isinstance(value, (list, tuple, np.ndarray)) and not isinstance(value, (str, bytes))


def _is_scalar_value(value: object) -> bool:
    return _is_missing(value) or isinstance(value, (str, bool, np.bool_, Real, np.number))


def _variable_attributes(metadata: dict[str, Any], name: str) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for source_key, attribute_key in (
        ("units", "unit"),
        ("roles", "role"),
        ("components", "components"),
    ):
        source = metadata.get(source_key)
        if isinstance(source, dict) and name in source:
            attributes[attribute_key] = deepcopy(source[name])
    return attributes


def _scalar_values(values: list[object], name: str) -> np.ndarray:
    non_missing = [value for value in values if not _is_missing(value)]
    supported = (str, bool, np.bool_, Real, np.number)
    if any(not isinstance(value, supported) for value in non_missing):
        raise UnsupportedDataError(f"Field {name!r} contains unsupported object values")
    if all(isinstance(value, str) for value in non_missing):
        dtype = object if len(non_missing) != len(values) else str
        return np.asarray(values, dtype=dtype)
    if all(isinstance(value, (bool, np.bool_)) for value in non_missing):
        return np.asarray(values, dtype=object if len(non_missing) != len(values) else bool)
    if all(isinstance(value, (Real, np.number)) for value in non_missing):
        dtype = float if len(non_missing) != len(values) else None
        return np.asarray(values, dtype=dtype)
    return np.asarray(values, dtype=object)


def _array_values(values: list[object], name: str) -> tuple[np.ndarray, tuple[int, ...]]:
    arrays: list[np.ndarray] = []
    expected_shape: tuple[int, ...] | None = None
    for value in values:
        if _is_missing(value):
            raise UnsupportedDataError(f"Field {name!r} contains missing array values")
        try:
            array = np.asarray(value)
        except (TypeError, ValueError) as exc:
            raise UnsupportedDataError(f"Field {name!r} is not a regular array") from exc
        if array.ndim == 0:
            raise RaggedDataError(f"Field {name!r} contains scalar and array values")
        if array.dtype.kind == "O":
            raise UnsupportedDataError(f"Field {name!r} contains an unsupported object array")
        shape = tuple(array.shape)
        if expected_shape is None:
            expected_shape = shape
        elif shape != expected_shape:
            raise RaggedDataError(
                f"Field {name!r} has inconsistent array shapes: {expected_shape} and {shape}"
            )
        arrays.append(array)
    if expected_shape is None:
        raise UnsupportedDataError(f"Field {name!r} has no array values")
    try:
        stacked = np.stack(arrays, axis=0)
    except ValueError as exc:
        raise RaggedDataError(f"Field {name!r} has inconsistent array shapes") from exc
    if stacked.dtype.kind == "O":
        raise UnsupportedDataError(f"Field {name!r} contains an unsupported object array")
    return stacked, expected_shape


def _axis_names(name: str, shape: tuple[int, ...], used: set[str]) -> tuple[str, ...]:
    result: list[str] = []
    for index in range(len(shape)):
        candidate = f"{name}__axis_{index}"
        suffix = 1
        while candidate in used:
            candidate = f"{name}__axis_{index}_{suffix}"
            suffix += 1
        result.append(candidate)
        used.add(candidate)
    return tuple(result)


@dataclass(slots=True)
class ScientificDataset:
    """An xarray dataset with JSON metadata and an optional source path."""

    data: xr.Dataset
    metadata: dict[str, Any] = field(default_factory=dict)
    source: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.data, xr.Dataset):
            raise TypeError("ScientificDataset data must be an xarray.Dataset")
        self.metadata = _json_metadata(self.metadata)
        if self.source is not None:
            self.source = Path(self.source)

    def copy(self) -> ScientificDataset:
        """Return a deep copy of arrays, metadata, and source identity."""

        return ScientificDataset(self.data.copy(deep=True), deepcopy(self.metadata), self.source)


def dataset_to_scientific(dataset: Dataset, *, record_dim: str | None = None) -> ScientificDataset:
    """Convert a tabular dataset into an explicitly dimensioned xarray value."""

    if not isinstance(dataset, Dataset):
        raise TypeError("dataset_to_scientific expects a Dataset")
    resolved_record_dim = record_dim or "record"
    if not isinstance(resolved_record_dim, str) or not resolved_record_dim.strip():
        raise ValueError("record_dim must be a non-empty string or None")
    metadata = _json_metadata(dataset.metadata)
    metadata[_INTERNAL_RECORD_DIM] = resolved_record_dim
    data_vars: dict[str, Any] = {}
    used_dimensions = {resolved_record_dim}
    for name in dataset.data.columns:
        values = dataset.data[name].tolist()
        nested = any(_is_nested(value) for value in values if not _is_missing(value))
        attributes = _variable_attributes(metadata, str(name))
        if nested:
            stacked, shape = _array_values(values, str(name))
            dimensions = (resolved_record_dim, *_axis_names(str(name), shape, used_dimensions))
            data_vars[str(name)] = (dimensions, stacked, attributes)
        else:
            data_vars[str(name)] = (
                (resolved_record_dim,),
                _scalar_values(values, str(name)),
                attributes,
            )
    return ScientificDataset(
        xr.Dataset(data_vars=data_vars),
        metadata,
        dataset.source,
    )


def _record_dimension(value: ScientificDataset, requested: str | None) -> str:
    dimensions = tuple(value.data.dims)
    if requested is not None:
        if not isinstance(requested, str) or not requested.strip():
            raise ValueError("record_dim must be a non-empty string or None")
        if requested not in dimensions:
            raise AmbiguousRecordAxisError(f"Requested record dimension {requested!r} is absent")
        return requested
    stored = value.metadata.get(_INTERNAL_RECORD_DIM)
    if isinstance(stored, str) and stored in dimensions:
        return stored
    candidates = [
        dimension
        for dimension in dimensions
        if any(dimension in variable.dims for variable in value.data.data_vars.values())
    ]
    if len(candidates) != 1:
        choices = ", ".join(candidates) or "none"
        raise AmbiguousRecordAxisError(
            f"ScientificDataset requires one explicit record dimension; candidates: {choices}"
        )
    return candidates[0]


def _coordinate_columns(value: ScientificDataset, record_dim: str) -> dict[str, Any]:
    columns: dict[str, Any] = {}
    for name, coordinate in value.data.coords.items():
        if coordinate.dims == (record_dim,):
            columns[name] = np.asarray(coordinate.values).copy()
        else:
            raise LossyConversionError(
                f"Coordinate {name!r} cannot be represented as a record column without loss"
            )
    return columns


def _data_columns(value: ScientificDataset, record_dim: str) -> dict[str, Any]:
    columns: dict[str, Any] = {}
    for name, variable in value.data.data_vars.items():
        if record_dim not in variable.dims:
            raise LossyConversionError(
                f"Variable {name!r} does not use record dimension {record_dim!r}"
            )
        other_dimensions = tuple(
            dimension for dimension in variable.dims if dimension != record_dim
        )
        ordered = variable.transpose(record_dim, *other_dimensions)
        array = np.asarray(ordered.values)
        if array.dtype.kind == "O" and any(not _is_scalar_value(item) for item in array.flat):
            raise UnsupportedDataError(f"Variable {name!r} contains an unsupported object array")
        if not other_dimensions:
            columns[name] = array.copy()
        else:
            columns[name] = [np.asarray(row).copy() for row in array]
    return columns


def scientific_to_dataset(value: ScientificDataset, *, record_dim: str | None = None) -> Dataset:
    """Convert only values that can be represented losslessly as tabular records."""

    if not isinstance(value, ScientificDataset):
        raise TypeError("scientific_to_dataset expects a ScientificDataset")
    resolved_record_dim = _record_dimension(value, record_dim)
    columns = _data_columns(value, resolved_record_dim)
    coordinate_columns = _coordinate_columns(value, resolved_record_dim)
    columns.update(coordinate_columns)
    frame = pd.DataFrame(columns)
    metadata = deepcopy(value.metadata)
    metadata.pop(_INTERNAL_RECORD_DIM, None)
    units = dict(metadata.get("units", {})) if isinstance(metadata.get("units"), dict) else {}
    for name, variable in value.data.data_vars.items():
        if "unit" in variable.attrs:
            units[name] = variable.attrs["unit"]
    for name, coordinate in value.data.coords.items():
        if "unit" in coordinate.attrs:
            units[name] = coordinate.attrs["unit"]
    if units:
        metadata["units"] = units
    return Dataset(frame, metadata, value.source)
