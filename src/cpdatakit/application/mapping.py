"""Mapping dispatch; array mappings explicitly opt in to version 2.0."""

import json

import numpy as np

from ..data import ScientificDataset
from ..exceptions import DataValidationError, NormalizationError
from ..normalization import _UREG, normalize_dataset
from ..schemas import ResolvedSchemaV2
from .units import declared_unit


def normalize_value(value, request, resolved):
    if request.mapping is None:
        return value
    if not isinstance(value, ScientificDataset):
        return normalize_dataset(
            value, resolved.schema, list(resolved.mappings), drop_unmapped=resolved.drop_unmapped
        )
    payload = json.loads(request.mapping.read_text(encoding="utf-8"))
    if payload.get("mapping_version") != "2.0":
        raise DataValidationError(
            "Tabular mappings do not apply to scientific data; use mapping_version 2.0"
        )
    if not isinstance(resolved.schema, ResolvedSchemaV2):
        raise NormalizationError("Scientific mappings require schema 2.0")
    dimensions = payload.get("dimensions", {})
    if not isinstance(dimensions, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or not v for k, v in dimensions.items()
    ):
        raise NormalizationError("Dimension mappings must contain source and target names")
    if set(dimensions) - set(value.data.dims) or len(set(dimensions.values())) != len(dimensions):
        raise NormalizationError("Unknown or colliding dimension mappings")
    if any(target in value.data.dims and target != source for source, target in dimensions.items()):
        raise NormalizationError("Dimension mapping would overwrite an existing dimension")
    declared = {
        v.name: v for v in (*resolved.schema.schema.coordinates, *resolved.schema.schema.variables)
    }
    items = list(resolved.mappings)
    if len({m.source for m in items}) != len(items) or len({m.target for m in items}) != len(items):
        raise NormalizationError("Mappings contain a duplicate source or target")
    renames = dict(dimensions)
    for item in items:
        if item.source not in value.data.variables or item.target not in declared:
            raise NormalizationError("Mapping fields must exist in the input and target schema")
        if item.target in value.data.variables and item.target != item.source:
            raise NormalizationError("Field mapping would overwrite an existing field")
        if item.source in renames and renames[item.source] != item.target:
            raise NormalizationError("Coordinate and dimension renames disagree")
        renames[item.source] = item.target
    result = value.copy()
    try:
        result.data = result.data.rename(renames)
    except ValueError as exc:
        raise NormalizationError(str(exc)) from exc
    units = {renames.get(k, k): v for k, v in result.metadata.get("units", {}).items()}
    log = {}
    for item in items:
        array = result.data[item.target]
        actual = declared_unit(value.data[item.source], value.metadata, item.source)
        if bool(item.input_unit) != bool(item.output_unit):
            raise NormalizationError("Both input_unit and output_unit are required for conversion")
        if item.input_unit:
            if actual is not None and actual != item.input_unit:
                raise NormalizationError(f"Input unit declaration conflicts for {item.source}")
            if units.get(item.target) is not None and units[item.target] != item.input_unit:
                raise NormalizationError(f"Metadata unit conflicts for {item.source}")
            if array.dtype.kind not in "iuf":
                raise NormalizationError("Unit conversion requires numeric arrays")
            try:
                converted = (
                    _UREG.Quantity(array.values, item.input_unit).to(item.output_unit).magnitude
                )
            except (ValueError, TypeError) as exc:
                raise NormalizationError(f"Cannot convert units for {item.source}: {exc}") from exc
            result.data[item.target] = array.copy(data=np.asarray(converted, dtype=float))
            result.data[item.target].encoding = {}
            result.data[item.target].attrs.pop("units", None)
            result.data[item.target].attrs["unit"] = item.output_unit
            units[item.target] = item.output_unit
        elif actual is not None:
            units[item.target] = actual
        log[item.source] = {
            "target": item.target,
            "input_unit": item.input_unit,
            "output_unit": item.output_unit,
            "source_note": item.source_note,
        }
    if resolved.drop_unmapped:
        result.data = result.data.drop_vars(
            [name for name in result.data.variables if name not in declared]
        )
    for name, array in tuple(result.data.variables.items()):
        if name in declared and set(array.dims) == set(declared[name].dims):
            result.data[name] = result.data[name].transpose(*declared[name].dims)
            if array.dims != declared[name].dims:
                result.data[name].encoding = {}
    result.metadata.update(
        units={n: u for n, u in units.items() if n in result.data.variables},
        field_mapping=log,
        dimension_mapping=dimensions,
        profile=resolved.schema.profile,
        schema_version="2.0",
    )
    return result
