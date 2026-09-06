"""Tabular Parquet reader and writer adapters backed by PyArrow."""

from __future__ import annotations

import importlib
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from ..data import ScientificDataset
from ..exceptions import DataReadError, DataValidationError, OutputExistsError
from ..model import Dataset
from ._metadata import METADATA_KEY, decode_metadata, encode_metadata
from .base import CapabilityResult, DetectionResult, ReaderInfo, ReadLimits, Selection, WriterInfo


def _pyarrow() -> Any:
    try:
        return importlib.import_module("pyarrow")
    except Exception as exc:
        raise DataReadError(f"pyarrow is unavailable: {type(exc).__name__}") from exc


def _check_path(path: Path) -> None:
    if not path.exists():
        raise DataReadError(f"Input path does not exist: {path}")
    if not path.is_file():
        raise DataReadError(f"Input path is not a file: {path}")
    if path.suffix.lower() != ".parquet":
        raise DataReadError(f"Unsupported Parquet extension: {path.suffix}")


class ParquetReader:
    """Read Parquet as the existing tabular Dataset type."""

    def __init__(self) -> None:
        self.info = ReaderInfo("parquet", "Parquet", frozenset({"tabular", "read"}), (".parquet",))

    def detect(self, path: Path) -> DetectionResult:
        input_path = Path(path)
        if input_path.suffix.lower() != ".parquet":
            return DetectionResult(False, "extension is not Parquet")
        try:
            _pyarrow()
        except DataReadError as exc:
            return DetectionResult(False, str(exc))
        return DetectionResult(True)

    def inspect(self, path: Path, *, limits: ReadLimits) -> dict[str, Any]:
        input_path = Path(path)
        _check_path(input_path)
        if input_path.stat().st_size > limits.max_bytes:
            raise DataReadError("Parquet input exceeds the configured byte limit")
        parquet = importlib.import_module("pyarrow.parquet")
        try:
            metadata = parquet.ParquetFile(input_path).metadata
            rows = int(metadata.num_rows)
            if rows > limits.max_records:
                raise DataReadError("Parquet input exceeds the configured record limit")
            return {
                "format": "Parquet",
                "record_count": rows,
                "fields": [
                    metadata.schema.column(index).name for index in range(metadata.num_columns)
                ],
            }
        except DataReadError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise DataReadError(f"Cannot inspect Parquet input {input_path}: {exc}") from exc

    def load(self, path: Path, *, selection: Selection | None = None) -> Dataset:
        input_path = Path(path)
        _check_path(input_path)
        _pyarrow()
        try:
            fields = list(selection.fields) if selection and selection.fields else None
            parquet = importlib.import_module("pyarrow.parquet")
            table = parquet.read_table(input_path, columns=fields, use_pandas_metadata=True)
            metadata = decode_metadata((table.schema.metadata or {}).get(METADATA_KEY.encode()))
            metadata.setdefault("format", "Parquet")
            frame = table.to_pandas()
            if selection and (selection.start is not None or selection.stop is not None):
                start = selection.start if selection.start is not None else 0
                stop = selection.stop if selection.stop is not None else len(frame)
                if stop > len(frame):
                    raise DataReadError(
                        f"Parquet selection bounds must fit record_count={len(frame)}"
                    )
                frame = frame.iloc[start:stop].reset_index(drop=True)
            return Dataset(frame, metadata, input_path)
        except DataReadError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise DataReadError(f"Cannot read Parquet input {input_path}: {exc}") from exc


class ParquetWriter:
    """Write only tabular Dataset values to Parquet."""

    def __init__(self) -> None:
        self.info = WriterInfo("parquet", "Parquet", frozenset({"tabular", "write"}), (".parquet",))

    def check(self, data: object) -> CapabilityResult:
        if isinstance(data, ScientificDataset):
            return CapabilityResult(
                False, ("Parquet does not accept N-dimensional ScientificDataset values",)
            )
        if not isinstance(data, Dataset):
            return CapabilityResult(False, ("Parquet requires a Dataset",))
        try:
            _pyarrow()
        except DataReadError as exc:
            return CapabilityResult(False, (str(exc),))
        for name, series in data.data.items():
            if series.dtype.kind == "O" and any(
                isinstance(item, (list, tuple, np.ndarray, dict, set, frozenset)) for item in series
            ):
                return CapabilityResult(False, (f"field {name!r} contains nested object values",))
        try:
            encode_metadata(data.metadata)
        except DataValidationError as exc:
            return CapabilityResult(False, (str(exc),))
        return CapabilityResult(True)

    def write(self, data: object, output: Path, *, force: bool = False) -> Path:
        capability = self.check(data)
        if not capability.supported:
            raise DataValidationError("; ".join(capability.messages))
        payload = encode_metadata(data.metadata)
        arrow = _pyarrow()
        table = arrow.Table.from_pandas(data.data, preserve_index=False)
        table = table.replace_schema_metadata(
            {**(table.schema.metadata or {}), METADATA_KEY.encode(): payload.encode("utf-8")}
        )
        target = Path(output)
        if target.exists() and not force:
            raise OutputExistsError(
                f"Output already exists: {target}; pass force=True to replace it"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=target.suffix, dir=target.parent
            )
            os.close(descriptor)
            temporary = Path(name)
            importlib.import_module("pyarrow.parquet").write_table(table, temporary)
            os.replace(temporary, target)
        except BaseException:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise
        return target
