"""Explicit field mapping and unit conversion."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from pint import DimensionalityError, UndefinedUnitError, UnitRegistry

from .exceptions import NormalizationError
from .model import Dataset
from .schema import ProfileSchema, load_schema

_UREG = UnitRegistry()


def _is_missing_scalar(value: object) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return isinstance(missing, (bool, np.bool_)) and bool(missing)


def _numeric_array(
    value: object, *, source: str, record: object, shape: tuple[int, ...]
) -> np.ndarray:
    try:
        array = np.asarray(value)
    except (TypeError, ValueError) as exc:
        raise NormalizationError(
            f"Field {source!r} record {record!r} is not a regular numeric array"
        ) from exc
    if tuple(array.shape) != shape:
        raise NormalizationError(
            f"Field {source!r} record {record!r} has shape {tuple(array.shape)}; "
            f"expected shape {shape}"
        )
    if array.dtype.kind not in {"i", "u", "f"}:
        raise NormalizationError(f"Field {source!r} record {record!r} is not numeric")
    return np.asarray(array, dtype=np.float64)


def _convert_series_units(
    series: pd.Series,
    *,
    source: str,
    shape: tuple[int, ...],
    input_unit: str,
    output_unit: str,
) -> pd.Series:
    converted: list[object] = []
    for record, value in series.items():
        if _is_missing_scalar(value):
            converted.append(value)
            continue
        array = _numeric_array(value, source=source, record=record, shape=shape)
        try:
            magnitude = _UREG.Quantity(array, input_unit).to(output_unit).magnitude
        except (DimensionalityError, UndefinedUnitError) as exc:
            raise NormalizationError(
                f"Cannot convert {source!r} from {input_unit!r} to {output_unit!r}: {exc}"
            ) from exc
        converted_array = np.asarray(magnitude, dtype=np.float64)
        converted.append(converted_array.item() if not shape else converted_array)
    return pd.Series(converted, index=series.index, name=series.name)


@dataclass(frozen=True, slots=True)
class FieldMapping:
    """An explicit, traceable field mapping and optional unit conversion."""

    source: str
    target: str
    input_unit: str | None = None
    output_unit: str | None = None
    source_note: str = "user supplied"


def load_mapping_file(path: str | Path) -> tuple[list[FieldMapping], bool]:
    """Load a strict JSON mapping file for explicit CLI normalization."""
    mapping_path = Path(path)
    try:
        payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NormalizationError(f"Mapping file does not exist: {mapping_path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NormalizationError(f"Cannot read mapping file {mapping_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise NormalizationError("Mapping file root must be a JSON object")
    raw_mappings = payload.get("mappings")
    if not isinstance(raw_mappings, list):
        raise NormalizationError("Mapping file 'mappings' must be a list")
    drop_unmapped = payload.get("drop_unmapped", False)
    if not isinstance(drop_unmapped, bool):
        raise NormalizationError("Mapping file 'drop_unmapped' must be boolean")

    allowed = {"source", "target", "input_unit", "output_unit", "source_note"}
    mappings: list[FieldMapping] = []
    for index, raw in enumerate(raw_mappings):
        if not isinstance(raw, dict):
            raise NormalizationError(f"Mapping {index} must be a JSON object")
        unknown = set(raw) - allowed
        if unknown:
            raise NormalizationError(
                f"Mapping {index} contains unsupported keys: {sorted(unknown)}"
            )
        source = raw.get("source")
        target = raw.get("target")
        if not isinstance(source, str) or not source.strip():
            raise NormalizationError(f"Mapping {index} source must be a non-empty string")
        if not isinstance(target, str) or not target.strip():
            raise NormalizationError(f"Mapping {index} target must be a non-empty string")
        input_unit = raw.get("input_unit")
        output_unit = raw.get("output_unit")
        source_note = raw.get("source_note", "user supplied")
        if input_unit is not None and not isinstance(input_unit, str):
            raise NormalizationError(f"Mapping {index} input_unit must be a string or null")
        if output_unit is not None and not isinstance(output_unit, str):
            raise NormalizationError(f"Mapping {index} output_unit must be a string or null")
        if not isinstance(source_note, str):
            raise NormalizationError(f"Mapping {index} source_note must be a string")
        mappings.append(
            FieldMapping(
                source=source,
                target=target,
                input_unit=input_unit,
                output_unit=output_unit,
                source_note=source_note,
            )
        )
    return mappings, drop_unmapped


def normalize_dataset(
    dataset: Dataset,
    schema: str | ProfileSchema,
    mappings: list[FieldMapping] | None = None,
    *,
    drop_unmapped: bool = False,
) -> Dataset:
    """Normalize fields from explicit mappings and schema conventions."""
    contract = load_schema(schema)
    items = list(mappings or [])
    sources = [item.source for item in items]
    targets = [item.target for item in items]
    if len(sources) != len(set(sources)) or len(targets) != len(set(targets)):
        raise NormalizationError("Mappings contain a duplicate source or target")
    unknown = set(targets) - set(contract.field_map())
    if unknown:
        raise NormalizationError(
            f"Mapping targets are not declared by the schema: {sorted(unknown)}"
        )
    missing = set(sources) - set(dataset.data.columns)
    if missing:
        raise NormalizationError(f"Mapping sources do not exist: {sorted(missing)}")
    collisions = {
        item.target for item in items if item.target in dataset.data and item.target != item.source
    }
    if collisions:
        raise NormalizationError(f"Mappings would overwrite existing fields: {sorted(collisions)}")

    result = dataset.copy()
    units = dict(result.metadata.get("units", {}))
    mapping_log: dict[str, dict[str, str | None]] = {}
    for item in items:
        series = result.data[item.source]
        if bool(item.input_unit) != bool(item.output_unit):
            raise NormalizationError("Both input_unit and output_unit are required for conversion")
        if item.input_unit and item.output_unit:
            if item.source in units:
                try:
                    reference = (
                        _UREG.Quantity(np.array([0.0, 1.0]), units[item.source])
                        .to(item.input_unit)
                        .magnitude
                    )
                    agrees = np.allclose(reference, [0.0, 1.0], rtol=1e-12, atol=0.0)
                except (DimensionalityError, UndefinedUnitError, TypeError, ValueError) as exc:
                    raise NormalizationError(
                        f"Invalid source unit declaration for {item.source!r}"
                    ) from exc
                if not agrees:
                    raise NormalizationError(
                        f"Input unit declaration conflicts for {item.source!r}: "
                        f"stored {units[item.source]!r}, mapping {item.input_unit!r}"
                    )
            spec = contract.field_map()[item.target]
            series = _convert_series_units(
                series,
                source=item.source,
                shape=spec.shape,
                input_unit=item.input_unit,
                output_unit=item.output_unit,
            )
            units[item.target] = item.output_unit
        elif item.source in units:
            units[item.target] = units[item.source]
        result.data[item.target] = series
        if item.target != item.source:
            result.data = result.data.drop(columns=[item.source])
            units.pop(item.source, None)
        mapping_log[item.source] = {
            "target": item.target,
            "input_unit": item.input_unit,
            "output_unit": item.output_unit,
            "source_note": item.source_note,
        }
    if drop_unmapped:
        keep = [item.name for item in contract.fields if item.name in result.data]
        result.data = result.data.loc[:, keep]
        units = {key: value for key, value in units.items() if key in keep}
    result.metadata.update(
        {
            "profile": contract.profile,
            "schema_version": contract.schema_version,
            "units": units,
            "field_mapping": mapping_log,
        }
    )
    return result
