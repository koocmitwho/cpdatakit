"""Format and schema dispatch for application services."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import h5py

from ..data import ScientificDataset
from ..exceptions import DataReadError, DataValidationError, SchemaError
from ..formats import NetCDFReader, ParquetReader, ReadLimits, Selection, ZarrReader
from ..inspection import inspect_dataset
from ..io import load_dataset, load_hdf5_v2
from ..model import Dataset
from ..provenance import sha256_file
from ..schema import ProfileSchema, load_schema, schema_to_dict
from ..schemas import ResolvedSchemaV2, SchemaV2, resolve_schema_v2
from ..statistics import summarize_dataset
from ..validation import validate_dataset
from .scientific import summarize_scientific, validate_scientific

SchemaInput = str | Path | ProfileSchema | Mapping[str, Any] | SchemaV2 | ResolvedSchemaV2
Contract = ProfileSchema | ResolvedSchemaV2


class ReadLimitError(DataReadError):
    """A structural preview exceeded an explicit byte or record bound."""


def resolve_contract(source: SchemaInput) -> Contract:
    if isinstance(source, ResolvedSchemaV2):
        return source
    if isinstance(source, SchemaV2):
        return resolve_schema_v2(source.to_dict())
    payload = source
    if isinstance(source, (str, Path)) and Path(source).is_file():
        try:
            payload = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise SchemaError(f"Cannot read schema: {source}") from exc
    if isinstance(payload, Mapping) and payload.get("schema_version") == "2.0":
        if isinstance(source, (str, Path)):
            return resolve_schema_v2(Path(source))
        return resolve_schema_v2(dict(payload))
    return load_schema(source)


def contract_dict(contract: Contract) -> dict[str, Any]:
    return (
        contract.to_dict() if isinstance(contract, ResolvedSchemaV2) else schema_to_dict(contract)
    )


def path_sha256(path: Path) -> str:
    """Hash files directly; hash directories by sorted relative name and file digest."""
    if path.is_file():
        return sha256_file(path)
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise DataReadError("Dataset directories must not contain symbolic links")
        if item.is_file():
            digest.update(item.relative_to(path).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(sha256_file(item).encode("ascii"))
            digest.update(b"\n")
    return digest.hexdigest()


def reader_for(path: Path):
    suffix = path.suffix.lower()
    if suffix in {".nc", ".netcdf"}:
        if path.is_file():
            with path.open("rb") as stream:
                if stream.read(3) == b"CDF":
                    return NetCDFReader(engine="netcdf4")
        return NetCDFReader()
    if suffix == ".zarr":
        return ZarrReader()
    if suffix == ".parquet":
        return ParquetReader()
    return None


def is_hdf5_v2(path: Path) -> bool:
    if path.suffix.lower() not in {".h5", ".hdf5"} or not path.is_file():
        return False
    try:
        with h5py.File(path, "r") as handle:
            version = handle.attrs.get("format_version")
            return version in {"2.0", b"2.0"}
    except OSError as exc:
        raise DataReadError(f"Cannot read HDF5 input: {path}") from exc


def load_value(
    path: Path, *, selection: Selection | None = None, context=None
) -> Dataset | ScientificDataset:
    if context is not None:
        context.checkpoint("load")
    reader = reader_for(path)
    if reader is not None:
        return reader.load(path, selection=selection, context=context)
    if is_hdf5_v2(path):
        return load_hdf5_v2(path, selection=selection)
    if selection is not None:
        raise DataReadError("Selective application reads require NetCDF, Zarr, Parquet or HDF5 2.0")
    return load_dataset(path)


def validate_value(value, contract: Contract):
    if isinstance(value, ScientificDataset) and isinstance(contract, ResolvedSchemaV2):
        return validate_scientific(value, contract)
    if isinstance(value, Dataset) and isinstance(contract, ProfileSchema):
        return validate_dataset(value, contract)
    raise SchemaError("Scientific data requires schema 2.0; tabular data requires schema 1.0.")


def summarize_value(value, contract: Contract, validation):
    if isinstance(value, ScientificDataset):
        return summarize_scientific(value, validation)
    return summarize_dataset(value, contract, validation=validation)


def inspect_input(path: Path, schema: SchemaInput | None, limits: ReadLimits) -> dict[str, Any]:
    size = 0
    if path.is_dir():
        for item in path.rglob("*"):
            if item.is_symlink():
                raise DataReadError("Dataset directories must not contain symbolic links")
            if item.is_file():
                size += item.stat().st_size
                if size > limits.max_bytes:
                    raise ReadLimitError("Input exceeds the configured byte limit")
    elif path.is_file():
        size = path.stat().st_size
    if size > limits.max_bytes:
        raise ReadLimitError("Input exceeds the configured byte limit")
    reader = reader_for(path)
    if reader is None and not is_hdf5_v2(path):
        result = inspect_dataset(path, schema=schema)
        if result.get("record_count", 0) > limits.max_records:
            raise ReadLimitError("Input exceeds the configured record limit")
        return result
    if reader is not None:
        try:
            info = reader.inspect(path, limits=limits)
        except DataReadError as exc:
            if "exceeds the configured" in str(exc):
                raise ReadLimitError(str(exc)) from exc
            raise
        fields = info.get("field_details", info.get("fields", info.get("variables", [])))
        fields = [
            {"name": name, **(fields[name] if isinstance(fields, dict) else {})} for name in fields
        ]
        version = "3" if isinstance(reader, ZarrReader) else "not applicable"
    else:
        with h5py.File(path, "r") as handle:
            if handle.attrs.get("format") not in {"CPDataKit", b"CPDataKit"}:
                raise DataReadError("HDF5 is not a CPDataKit file")
            try:
                dimensions = {
                    name: int(item.attrs["length"]) for name, item in handle["dimensions"].items()
                }
                fields = [
                    {
                        "name": name,
                        "dims": json.loads(array.attrs["dims_json"]),
                        "shape": list(array.shape),
                        "dtype": str(array.dtype),
                        "unit": array.attrs.get("unit", ""),
                        "role": array.attrs.get("role"),
                        "kind": "coordinate" if group == "coordinates" else "variable",
                    }
                    for group in ("coordinates", "variables")
                    for name, array in handle[group].items()
                ]
            except (KeyError, ValueError, TypeError) as exc:
                raise DataReadError("Invalid HDF5 2.0 structure") from exc
            info = {
                "format": "CPDataKit",
                "dimensions": dimensions,
                "record_count": next(
                    (array.shape[0] for array in handle["variables"].values() if array.shape), 0
                ),
            }
            version = "2.0"
    if info["record_count"] > limits.max_records:
        raise ReadLimitError("Input exceeds the configured record limit")
    result = {
        "file": {
            "filename": path.name,
            "file_type": path.suffix.lstrip("."),
            "format": info["format"],
            "format_version": version,
        },
        "record_count": info["record_count"],
        "dimensions": info.get("dimensions", {}),
        "fields": fields,
        "adapter": {"format": info["format"]},
        "provenance": {"input_filename": path.name},
    }
    if schema is not None:
        result["schema"] = contract_dict(resolve_contract(schema))
    return result


def require_tabular_mapping(value, mapping) -> None:
    if isinstance(value, ScientificDataset) and mapping is not None:
        raise DataValidationError("Tabular field mappings do not apply to multidimensional data.")
