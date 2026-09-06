"""Zarr format 3 reader and writer adapters backed by xarray."""

from __future__ import annotations

import importlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..data import ScientificDataset
from ..exceptions import DataReadError, DataValidationError, OutputExistsError
from ._metadata import scientific_for_write, scientific_metadata
from .base import CapabilityResult, DetectionResult, ReaderInfo, ReadLimits, Selection, WriterInfo


def _xarray() -> Any:
    try:
        return importlib.import_module("xarray")
    except Exception as exc:
        raise DataReadError(f"xarray is unavailable: {type(exc).__name__}") from exc


def _zarr() -> None:
    try:
        importlib.import_module("zarr")
    except Exception as exc:
        raise DataReadError(f"zarr is unavailable: {type(exc).__name__}") from exc


def _check_path(path: Path) -> None:
    if not path.exists():
        raise DataReadError(f"Input path does not exist: {path}")
    if not path.is_dir():
        raise DataReadError(f"Zarr input is not a directory: {path}")


def _store_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _metadata(dataset: Any) -> dict[str, Any]:
    return scientific_metadata(dataset, format="Zarr 3")


def _selected(dataset: Any, selection: Selection | None) -> Any:
    if selection and selection.fields:
        unknown = [name for name in selection.fields if name not in dataset.variables]
        if unknown:
            raise DataReadError(f"Unknown Zarr selection fields: {unknown}")
        dataset = dataset[list(selection.fields)]
    if selection and (selection.start is not None or selection.stop is not None):
        fields = (
            tuple(selection.fields) if selection and selection.fields else tuple(dataset.data_vars)
        )
        if not fields or not dataset[fields[0]].dims:
            raise DataReadError("Zarr record selection requires a non-scalar field")
        dimension = dataset[fields[0]].dims[0]
        length = int(dataset.sizes[dimension])
        start = selection.start if selection.start is not None else 0
        stop = selection.stop if selection.stop is not None else length
        if stop > length:
            raise DataReadError(f"Zarr selection bounds must fit {dimension}={length}")
        dataset = dataset.isel({dimension: slice(start, stop)})
    return dataset


class ZarrReader:
    """Read local Zarr format 3 stores without consolidated-metadata assumptions."""

    def __init__(self) -> None:
        self.info = ReaderInfo("zarr-v3", "Zarr 3", frozenset({"scientific", "read"}), (".zarr",))

    def detect(self, path: Path) -> DetectionResult:
        input_path = Path(path)
        if not input_path.is_dir() and input_path.suffix.lower() != ".zarr":
            return DetectionResult(False, "path is not a Zarr store")
        try:
            _xarray()
            _zarr()
        except DataReadError as exc:
            return DetectionResult(False, str(exc))
        return DetectionResult(True)

    def inspect(self, path: Path, *, limits: ReadLimits) -> dict[str, Any]:
        input_path = Path(path)
        _check_path(input_path)
        if _store_bytes(input_path) > limits.max_bytes:
            raise DataReadError("Zarr input exceeds the configured byte limit")
        xarray = _xarray()
        _zarr()
        try:
            with xarray.open_zarr(input_path, consolidated=False) as dataset:
                data_variables = tuple(dataset.data_vars)
                record_count = (
                    int(dataset[data_variables[0]].sizes[dataset[data_variables[0]].dims[0]])
                    if data_variables and dataset[data_variables[0]].dims
                    else 0
                )
                if record_count > limits.max_records:
                    raise DataReadError("Zarr input exceeds the configured record limit")
                return {
                    "format": "Zarr 3",
                    "dimensions": {name: int(length) for name, length in dataset.sizes.items()},
                    "variables": list(dataset.variables),
                    "record_count": record_count,
                    "store_entries": sum(1 for item in input_path.rglob("*") if item.is_file()),
                }
        except DataReadError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise DataReadError(f"Cannot inspect Zarr input {input_path}: {exc}") from exc

    def load(self, path: Path, *, selection: Selection | None = None) -> ScientificDataset:
        input_path = Path(path)
        _check_path(input_path)
        xarray = _xarray()
        _zarr()
        try:
            with xarray.open_zarr(input_path, consolidated=False) as opened:
                dataset = opened.load()
            dataset = _selected(dataset, selection)
            metadata = _metadata(dataset)
            return ScientificDataset(dataset.copy(deep=True), metadata, input_path)
        except DataReadError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise DataReadError(f"Cannot read Zarr input {input_path}: {exc}") from exc


class ZarrWriter:
    """Write ScientificDataset values as local Zarr format 3 stores."""

    def __init__(self) -> None:
        self.info = WriterInfo("zarr-v3", "Zarr 3", frozenset({"scientific", "write"}), (".zarr",))

    def check(self, data: object) -> CapabilityResult:
        if not isinstance(data, ScientificDataset):
            return CapabilityResult(False, ("Zarr requires a ScientificDataset",))
        try:
            _xarray()
            _zarr()
        except DataReadError as exc:
            return CapabilityResult(False, (str(exc),))
        try:
            scientific_for_write(data)
        except DataValidationError as exc:
            return CapabilityResult(False, (str(exc),))
        return CapabilityResult(True)

    def write(self, data: object, output: Path, *, force: bool = False) -> Path:
        capability = self.check(data)
        if not capability.supported:
            raise DataValidationError("; ".join(capability.messages))
        dataset = scientific_for_write(data)
        target = Path(output)
        if target.exists() and not force:
            raise OutputExistsError(
                f"Output already exists: {target}; pass force=True to replace it"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        backup: Path | None = None
        try:
            dataset.to_zarr(temporary, mode="w", consolidated=False, zarr_format=3)
            if target.is_symlink():
                raise DataValidationError("Zarr output must not be a symbolic link")
            if target.exists():
                if not force:
                    raise OutputExistsError(f"Output already exists: {target}")
                backup = Path(tempfile.mkdtemp(prefix=f".{target.name}.backup-", dir=target.parent))
                os.replace(target, backup / "previous")
            try:
                os.replace(temporary, target)
            except BaseException as failure:
                if backup is not None:
                    try:
                        os.replace(backup / "previous", target)
                    except OSError as recovery_error:
                        failure.add_note(
                            f"Original Zarr output retained at {backup / 'previous'}: "
                            f"{recovery_error}"
                        )
                    else:
                        backup.rmdir()
                raise
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            # Never remove a backup containing the original output on a failure.
            if backup is not None and backup.exists() and not (backup / "previous").exists():
                backup.rmdir()
            raise
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
        return target
