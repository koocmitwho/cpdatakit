"""CPDataKit metadata stored in NetCDF, Zarr and Parquet files."""

from __future__ import annotations

import json
from typing import Any

from ..exceptions import DataReadError, DataValidationError

METADATA_KEY = "cpdatakit_metadata_json"


def encode_metadata(metadata: dict[str, Any]) -> str:
    """Encode JSON metadata before any output is created."""
    try:
        if not isinstance(metadata, dict):
            raise TypeError("metadata must be an object")
        payload = json.dumps(
            {"version": 1, "metadata": metadata},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if json.loads(payload)["metadata"] != metadata:
            raise ValueError("metadata must round-trip as JSON without type changes")
        return payload
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"Invalid CPDataKit metadata: {exc}") from exc


def decode_metadata(payload: str | bytes | None) -> dict[str, Any]:
    """Decode an envelope, or return an empty dict for a legacy file."""
    if payload is None:
        return {}
    try:
        envelope = json.loads(payload)
        if (
            not isinstance(envelope, dict)
            or type(envelope.get("version")) is not int
            or envelope["version"] != 1
        ):
            raise ValueError("unsupported metadata envelope version")
        metadata = envelope["metadata"]
        encode_metadata(metadata)
        return metadata
    except (TypeError, ValueError, KeyError, DataValidationError) as exc:
        raise DataReadError(f"Invalid CPDataKit metadata envelope: {exc}") from exc


def scientific_for_write(value: Any) -> Any:
    """Attach encoded metadata to a shallow copy of the xarray dataset."""
    if METADATA_KEY in value.data.attrs:
        raise DataValidationError(f"Attribute {METADATA_KEY!r} is reserved for CPDataKit metadata")
    payload = encode_metadata(value.metadata)
    dataset = value.data.copy(deep=False)
    dataset.attrs = {**value.data.attrs, METADATA_KEY: payload}
    return dataset


def scientific_metadata(dataset: Any, **defaults: Any) -> dict[str, Any]:
    """Restore dataset metadata and remove its storage attribute."""
    metadata = decode_metadata(dataset.attrs.pop(METADATA_KEY, None))
    units = {}
    for name, variable in dataset.variables.items():
        unit = variable.attrs.get("unit", variable.attrs.get("units"))
        if unit is not None:
            units[name] = unit
    for key, default in {**defaults, "units": units}.items():
        metadata.setdefault(key, default)
    return metadata
