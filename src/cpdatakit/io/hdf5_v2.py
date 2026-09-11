"""HDF5 2.0 storage for explicit xarray-backed scientific datasets."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import xarray as xr

from ..data import ScientificDataset
from ..data.validation import validate_scientific
from ..exceptions import DataReadError, DataValidationError, OutputExistsError
from ..formats import Selection
from ..formats._selection import dimension_slices
from ..schemas import ResolvedSchemaV2, SchemaV2, resolve_schema_v2, schema_v2_sha256

_ROOT_ATTRIBUTES = (
    "format",
    "format_version",
    "profile",
    "schema_version",
    "schema_json",
    "schema_sha256",
    "units_json",
    "provenance_json",
    "validation_summary_json",
)
_GROUPS = ("dimensions", "coordinates", "variables")

SchemaInputV2 = ResolvedSchemaV2 | SchemaV2 | Path | Mapping[str, Any]


def _text_attribute(attributes: h5py.AttributeManager, name: str, path: Path) -> str:
    if name not in attributes:
        raise DataReadError(f"HDF5 2.0 metadata attribute {name!r} is required: {path}")
    value = attributes[name]
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DataReadError(
                f"HDF5 2.0 metadata attribute {name!r} is not valid UTF-8: {path}"
            ) from exc
    if not isinstance(value, str):
        raise DataReadError(f"HDF5 2.0 metadata attribute {name!r} must be text: {path}")
    return value


def _json_attribute(
    attributes: h5py.AttributeManager, name: str, path: Path, *, object_only: bool = True
) -> Any:
    text = _text_attribute(attributes, name, path)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataReadError(f"Invalid HDF5 2.0 JSON in attribute {name!r}: {path}") from exc
    if object_only and not isinstance(value, dict):
        raise DataReadError(f"HDF5 2.0 metadata attribute {name!r} must be an object: {path}")
    return value


def _schema_input(value: SchemaInputV2) -> ResolvedSchemaV2:
    if isinstance(value, ResolvedSchemaV2):
        return value
    if isinstance(value, SchemaV2):
        resolved_order = tuple(
            [item.name for item in value.dimensions]
            + [
                item.name
                for item in value.coordinates
                if item.name not in {d.name for d in value.dimensions}
            ]
            + [item.name for item in value.variables]
        )
        return ResolvedSchemaV2(value, (), resolved_order)
    if isinstance(value, Mapping):
        return resolve_schema_v2(dict(value))
    return resolve_schema_v2(Path(value))


def _schema_snapshot(schema: ResolvedSchemaV2) -> str:
    return json.dumps(
        schema.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _json_text(value: Any, label: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"HDF5 2.0 {label} must be JSON-compatible") from exc


def _hdf5_values(values: Any, name: str) -> np.ndarray:
    array = np.asarray(values)
    if array.dtype.kind == "O":
        if not all(isinstance(item, (str, bytes, np.str_, np.bytes_)) for item in array.flat):
            raise DataValidationError(
                f"HDF5 2.0 field {name!r} contains an unsupported object array"
            )
        decoded = [
            item.decode("utf-8") if isinstance(item, (bytes, np.bytes_)) else str(item)
            for item in array.flat
        ]
        array = np.asarray(decoded, dtype=h5py.string_dtype(encoding="utf-8")).reshape(array.shape)
    elif array.dtype.kind == "U":
        array = array.astype(h5py.string_dtype(encoding="utf-8"))
    return array


def _dimension_lengths(schema: ResolvedSchemaV2) -> dict[str, int]:
    return {item.name: item.length for item in schema.schema.dimensions}


def _validate_value(value: ScientificDataset, schema: ResolvedSchemaV2) -> None:
    lengths = _dimension_lengths(schema)
    actual_lengths = dict(value.data.sizes)
    if actual_lengths != lengths:
        raise DataValidationError(
            f"HDF5 2.0 dimensions do not match schema: expected {lengths}, got {actual_lengths}"
        )
    declared_coordinates = {item.name: item for item in schema.schema.coordinates}
    for name, declaration in declared_coordinates.items():
        if name not in value.data.coords:
            raise DataValidationError(f"HDF5 2.0 coordinate is missing: {name}")
        coordinate = value.data.coords[name]
        if tuple(coordinate.dims) != declaration.dims or tuple(coordinate.shape) != tuple(
            lengths[dimension] for dimension in declaration.dims
        ):
            raise DataValidationError(
                f"HDF5 2.0 coordinate shape or dimensions are invalid: {name}"
            )
    declared_variables = {item.name: item for item in schema.schema.variables}
    for name, declaration in declared_variables.items():
        if name not in value.data.data_vars:
            raise DataValidationError(f"HDF5 2.0 variable is missing: {name}")
        variable = value.data.data_vars[name]
        expected_shape = tuple(lengths[dimension] for dimension in declaration.dims)
        if tuple(variable.dims) != declaration.dims or tuple(variable.shape) != expected_shape:
            raise DataValidationError(f"HDF5 2.0 variable shape or dimensions are invalid: {name}")
    extras = (set(value.data.coords) - set(declared_coordinates)) | (
        set(value.data.data_vars) - set(declared_variables)
    )
    if extras:
        raise DataValidationError(f"HDF5 2.0 value contains undeclared fields: {sorted(extras)}")


def _write_array_dataset(
    group: h5py.Group,
    name: str,
    variable: xr.DataArray,
    dimensions: tuple[str, ...],
    *,
    unit: str | None,
    role: str | None = None,
    chunks: tuple[int, ...] | None = None,
) -> None:
    if unit is not None and not isinstance(unit, str):
        raise DataValidationError(f"HDF5 2.0 field {name!r} unit must be text or null")
    data = _hdf5_values(variable.values, name)
    dataset = group.create_dataset(name, data=data, chunks=chunks)
    dataset.attrs["dims_json"] = json.dumps(list(dimensions), separators=(",", ":"))
    dataset.attrs["unit"] = unit or ""
    dataset.attrs["dtype"] = str(variable.dtype)
    if role is not None:
        dataset.attrs["role"] = role
    if chunks is not None:
        dataset.attrs["chunks_json"] = json.dumps(list(chunks), separators=(",", ":"))
    if variable.attrs:
        dataset.attrs["attributes_json"] = _json_text(dict(variable.attrs), "variable attributes")


def write_hdf5_v2(
    value: ScientificDataset,
    output: str | Path,
    schema: SchemaInputV2,
    *,
    force: bool = False,
    allow_invalid: bool = False,
) -> Path:
    """Validate and write HDF5 2.0, recording fresh results even for allowed invalid data."""

    if not isinstance(value, ScientificDataset):
        raise TypeError("write_hdf5_v2 expects a ScientificDataset")
    resolved_schema = _schema_input(schema)
    _validate_value(value, resolved_schema)
    target = Path(output)
    if target.exists() and not force:
        raise OutputExistsError(f"Output already exists: {target}; pass force=True to replace it")
    metadata = dict(value.metadata)
    units = metadata.get("units", {})
    if not isinstance(units, dict):
        raise DataValidationError("HDF5 2.0 units metadata must be an object")
    provenance = metadata.get("provenance", {})
    if not isinstance(provenance, dict):
        raise DataValidationError("HDF5 2.0 provenance metadata must be an object")
    if not isinstance(metadata.get("validation_summary", {}), dict):
        raise DataValidationError("HDF5 2.0 validation summary metadata must be an object")
    validation = validate_scientific(value, resolved_schema)
    if not validation.valid and not allow_invalid:
        codes = ", ".join(sorted({issue.code for issue in validation.errors}))
        raise DataValidationError(f"HDF5 2.0 validation failed: {codes}")
    validation_summary = {
        "valid": validation.valid,
        "error_count": len(validation.errors),
        "warning_count": len(validation.warnings),
    }
    metadata["validation_summary"] = validation_summary
    schema_snapshot = _schema_snapshot(resolved_schema)
    global_attributes = dict(value.data.attrs)
    attributes_json = _json_text(global_attributes, "global attributes")
    if json.loads(attributes_json) != global_attributes:
        raise DataValidationError("HDF5 2.0 global attributes must round-trip as JSON")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=target.suffix, dir=target.parent
        )
        os.close(descriptor)
        temp_path = Path(temporary_name)
        with h5py.File(temp_path, "w", libver="earliest") as handle:
            handle.attrs["format"] = "CPDataKit"
            handle.attrs["format_version"] = "2.0"
            handle.attrs["profile"] = resolved_schema.profile
            handle.attrs["schema_version"] = resolved_schema.schema_version
            handle.attrs["schema_json"] = schema_snapshot
            handle.attrs["schema_sha256"] = schema_v2_sha256(resolved_schema)
            handle.attrs["units_json"] = _json_text(units, "units metadata")
            handle.attrs["provenance_json"] = _json_text(provenance, "provenance metadata")
            handle.attrs["validation_summary_json"] = _json_text(
                validation_summary, "validation summary metadata"
            )
            dimensions_group = handle.create_group("dimensions")
            for declaration in resolved_schema.schema.dimensions:
                dimension = dimensions_group.create_group(declaration.name)
                dimension.attrs["length"] = declaration.length
            coordinates_group = handle.create_group("coordinates")
            for declaration in resolved_schema.schema.coordinates:
                coordinate = value.data.coords[declaration.name]
                chunks = declaration.chunks or coordinate.encoding.get("chunks")
                _write_array_dataset(
                    coordinates_group,
                    declaration.name,
                    coordinate,
                    declaration.dims,
                    unit=coordinate.attrs.get(
                        "unit", coordinate.attrs.get("units", units.get(declaration.name))
                    ),
                    chunks=chunks,
                )
            variables_group = handle.create_group("variables")
            for declaration in resolved_schema.schema.variables:
                variable = value.data.data_vars[declaration.name]
                chunks = declaration.chunks or variable.encoding.get("chunks")
                _write_array_dataset(
                    variables_group,
                    declaration.name,
                    variable,
                    declaration.dims,
                    unit=variable.attrs.get(
                        "unit", variable.attrs.get("units", units.get(declaration.name))
                    ),
                    role=declaration.role,
                    chunks=chunks,
                )
            metadata_group = handle.create_group("metadata")
            metadata_group.attrs["metadata_json"] = _json_text(metadata, "dataset metadata")
            metadata_group.attrs["attributes_json"] = attributes_json
        os.replace(temp_path, target)
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    return target


def _decode_array(value: Any) -> Any:
    if isinstance(value, (bytes, np.bytes_)):
        return value.decode("utf-8")
    if isinstance(value, np.ndarray) and value.dtype.kind in {"S", "O"}:
        decoded = [
            item.decode("utf-8") if isinstance(item, (bytes, np.bytes_)) else item
            for item in value.flat
        ]
        return np.asarray(decoded, dtype=object).reshape(value.shape)
    return value


def _read_group_array(
    group: h5py.Group,
    name: str,
    lengths: dict[str, int],
    path: Path,
    *,
    selected: bool,
    indices: dict[str, slice],
) -> tuple[tuple[str, ...], Any, dict[str, Any]] | None:
    item = group.get(name)
    if not isinstance(item, h5py.Dataset):
        raise DataReadError(f"HDF5 2.0 field is missing or not a dataset: {name}")
    dims = _json_attribute(item.attrs, "dims_json", path, object_only=False)
    if not isinstance(dims, list) or any(not isinstance(dimension, str) for dimension in dims):
        raise DataReadError(f"HDF5 2.0 field {name!r} has invalid dimension references")
    dimensions = tuple(dims)
    if any(dimension not in lengths for dimension in dimensions):
        raise DataReadError(f"HDF5 2.0 field {name!r} references a missing dimension")
    expected_shape = tuple(lengths[dimension] for dimension in dimensions)
    if tuple(item.shape) != expected_shape:
        raise DataReadError(f"HDF5 2.0 field {name!r} shape does not match dimensions")
    if not selected:
        return None
    values = item[tuple(indices.get(d, slice(None)) for d in dimensions)]
    values = _decode_array(values)
    attributes: dict[str, Any] = {}
    unit = _text_attribute(item.attrs, "unit", path)
    if unit:
        attributes["unit"] = unit
    if "role" in item.attrs:
        attributes["role"] = _text_attribute(item.attrs, "role", path)
    if "attributes_json" in item.attrs:
        attributes.update(_json_attribute(item.attrs, "attributes_json", path))
    return dimensions, values, attributes


def _schema_hash(schema_payload: dict[str, Any]) -> str:
    if "resolved" not in schema_payload and not any(
        key in schema_payload for key in ("dimensions", "coordinates", "variables")
    ):
        canonical = json.dumps(
            schema_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    try:
        return schema_v2_sha256(schema_payload)
    except TypeError:
        canonical = json.dumps(
            schema_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_hdf5_v2(path: str | Path, *, selection: Selection | None = None) -> ScientificDataset:
    """Read an HDF5 2.0 ScientificDataset with optional bounded selection."""

    input_path = Path(path)
    if not input_path.exists():
        raise DataReadError(f"Input path does not exist: {input_path}")
    if not input_path.is_file():
        raise DataReadError(f"Input path is not a file: {input_path}")
    try:
        with h5py.File(input_path, "r") as handle:
            for name in _ROOT_ATTRIBUTES:
                _text_attribute(handle.attrs, name, input_path)
            if _text_attribute(handle.attrs, "format", input_path) != "CPDataKit":
                raise DataReadError(f"HDF5 2.0 format marker is invalid: {input_path}")
            if _text_attribute(handle.attrs, "format_version", input_path) != "2.0":
                raise DataReadError(f"Unsupported HDF5 format_version for v2 reader: {input_path}")
            profile = _text_attribute(handle.attrs, "profile", input_path)
            schema_version = _text_attribute(handle.attrs, "schema_version", input_path)
            schema_text = _text_attribute(handle.attrs, "schema_json", input_path)
            try:
                schema_payload = json.loads(schema_text)
            except json.JSONDecodeError as exc:
                raise DataReadError(f"HDF5 2.0 schema_json is invalid: {input_path}") from exc
            if not isinstance(schema_payload, dict):
                raise DataReadError(f"HDF5 2.0 schema_json must be an object: {input_path}")
            stored_hash = _text_attribute(handle.attrs, "schema_sha256", input_path)
            if _schema_hash(schema_payload) != stored_hash.lower():
                raise DataReadError(
                    f"HDF5 2.0 schema_sha256 does not match schema_json: {input_path}"
                )
            if (
                schema_payload.get("profile") != profile
                or schema_payload.get("schema_version") != schema_version
            ):
                raise DataReadError(
                    f"HDF5 2.0 schema metadata does not match root attributes: {input_path}"
                )
            units = _json_attribute(handle.attrs, "units_json", input_path)
            provenance = _json_attribute(handle.attrs, "provenance_json", input_path)
            validation_summary = _json_attribute(
                handle.attrs, "validation_summary_json", input_path
            )
            metadata_group = handle.get("metadata")
            global_attributes = {}
            if isinstance(metadata_group, h5py.Group) and "attributes_json" in metadata_group.attrs:
                global_attributes = _json_attribute(
                    metadata_group.attrs, "attributes_json", input_path
                )
            for group_name in _GROUPS:
                if not isinstance(handle.get(group_name), h5py.Group):
                    raise DataReadError(f"HDF5 2.0 group is missing: {group_name}")
            dimensions_group = handle["dimensions"]
            lengths: dict[str, int] = {}
            for name, item in dimensions_group.items():
                if not isinstance(item, h5py.Group) or "length" not in item.attrs:
                    raise DataReadError(f"HDF5 2.0 dimension metadata is invalid: {name}")
                length = item.attrs["length"]
                if (
                    isinstance(length, bool)
                    or not isinstance(length, (int, np.integer))
                    or int(length) <= 0
                ):
                    raise DataReadError(f"HDF5 2.0 dimension length is invalid: {name}")
                lengths[name] = int(length)

            coordinates_group = handle["coordinates"]
            variables_group = handle["variables"]
            all_variables = tuple(
                name for name, item in variables_group.items() if isinstance(item, h5py.Dataset)
            )
            all_coordinates = tuple(
                name for name, item in coordinates_group.items() if isinstance(item, h5py.Dataset)
            )
            known_fields = set(all_variables) | set(all_coordinates)
            has_field_filter = bool(selection and selection.fields)
            selected_fields = tuple(selection.fields) if has_field_filter else all_variables
            unknown = [name for name in selected_fields if name not in known_fields]
            if unknown:
                raise DataReadError(f"Unknown HDF5 2.0 selection fields: {unknown}")
            selected_variables = [name for name in selected_fields if name in all_variables]
            selected_coordinates = (
                set(all_coordinates)
                if not has_field_filter
                else set(name for name in all_coordinates if name in selected_fields)
            )
            selected_dimensions: set[str] = set()
            for name in selected_fields:
                group = variables_group if name in all_variables else coordinates_group
                dims = _json_attribute(
                    group[name].attrs, "dims_json", input_path, object_only=False
                )
                selected_dimensions.update(dims)
            for name in all_coordinates:
                dims = _json_attribute(
                    coordinates_group[name].attrs, "dims_json", input_path, object_only=False
                )
                if set(dims).issubset(selected_dimensions):
                    selected_coordinates.add(name)
            first_field = (
                selected_fields[0] if selected_fields else next(iter(all_coordinates), None)
            )
            field_group = variables_group if first_field in variables_group else coordinates_group
            first_dims = (
                _json_attribute(
                    field_group[first_field].attrs, "dims_json", input_path, object_only=False
                )
                if first_field is not None
                else ()
            )
            indices = dimension_slices(lengths, first_dims, selection, label="HDF5 2.0")
            data_vars: dict[str, Any] = {}
            for name in selected_variables:
                result = _read_group_array(
                    variables_group,
                    name,
                    lengths,
                    input_path,
                    selected=True,
                    indices=indices,
                )
                if result is None:
                    continue
                dims, values, attributes = result
                data_vars[name] = (dims, values, attributes)
            coords: dict[str, Any] = {}
            for name in sorted(selected_coordinates, key=lambda item: all_coordinates.index(item)):
                result = _read_group_array(
                    coordinates_group,
                    name,
                    lengths,
                    input_path,
                    selected=True,
                    indices=indices,
                )
                if result is None:
                    continue
                dims, values, attributes = result
                coords[name] = (dims, values, attributes)
            metadata: dict[str, Any] = {
                "units": units,
                "provenance": provenance,
                "validation_summary": validation_summary,
                "schema": schema_payload,
            }
            metadata_group = handle.get("metadata")
            if isinstance(metadata_group, h5py.Group) and "metadata_json" in metadata_group.attrs:
                extra = _json_attribute(metadata_group.attrs, "metadata_json", input_path)
                metadata.update(extra)
                metadata["units"] = units
                metadata["provenance"] = provenance
                metadata["validation_summary"] = validation_summary
                metadata["schema"] = schema_payload
            dataset = xr.Dataset(data_vars=data_vars, coords=coords, attrs=global_attributes)
    except DataReadError:
        raise
    except (OSError, KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise DataReadError(f"Cannot read HDF5 2.0 {input_path}: {exc}") from exc
    return ScientificDataset(dataset, metadata, input_path)
