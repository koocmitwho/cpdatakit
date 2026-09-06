"""NetCDF reader and writer adapters backed by xarray."""

from __future__ import annotations

import importlib
import os
import tempfile
from pathlib import Path
from typing import Any

from ..data import ScientificDataset
from ..exceptions import DataReadError, DataValidationError, OutputExistsError
from ._metadata import scientific_for_write, scientific_metadata
from .base import CapabilityResult, DetectionResult, ReaderInfo, ReadLimits, Selection, WriterInfo

_ENGINES = {"h5netcdf": "h5netcdf", "netcdf4": "netCDF4"}
_EXTENSIONS = (".nc", ".netcdf")


def _xarray() -> Any:
    try:
        return importlib.import_module("xarray")
    except Exception as exc:
        raise DataReadError(f"xarray is unavailable: {type(exc).__name__}") from exc


def _backend(engine: str) -> None:
    try:
        importlib.import_module(_ENGINES[engine])
    except Exception as exc:
        raise DataReadError(
            f"NetCDF engine {engine!r} is unavailable: {type(exc).__name__}"
        ) from exc


def _check_path(path: Path) -> None:
    if not path.exists():
        raise DataReadError(f"Input path does not exist: {path}")
    if not path.is_file():
        raise DataReadError(f"Input path is not a file: {path}")
    if path.suffix.lower() not in _EXTENSIONS:
        raise DataReadError(f"Unsupported NetCDF extension: {path.suffix}")


def _check_bytes(path: Path, limits: ReadLimits) -> None:
    if path.stat().st_size > limits.max_bytes:
        raise DataReadError("NetCDF input exceeds the configured byte limit")


def _metadata(dataset: Any, engine: str) -> dict[str, Any]:
    return scientific_metadata(dataset, format="NetCDF", engine=engine)


class NetCDFReader:
    """Read NetCDF files through one explicitly selected xarray engine."""

    def __init__(self, *, engine: str = "h5netcdf") -> None:
        if engine not in _ENGINES:
            raise ValueError(f"Unsupported NetCDF engine: {engine}")
        self.engine = engine
        self.info = ReaderInfo("netcdf", "NetCDF", frozenset({"scientific", "read"}), _EXTENSIONS)

    def detect(self, path: Path) -> DetectionResult:
        input_path = Path(path)
        if input_path.suffix.lower() not in _EXTENSIONS:
            return DetectionResult(False, "extension is not NetCDF")
        try:
            _xarray()
            _backend(self.engine)
        except DataReadError as exc:
            return DetectionResult(False, str(exc))
        return DetectionResult(True)

    def inspect(self, path: Path, *, limits: ReadLimits) -> dict[str, Any]:
        input_path = Path(path)
        _check_path(input_path)
        _check_bytes(input_path, limits)
        xarray = _xarray()
        _backend(self.engine)
        try:
            with xarray.open_dataset(input_path, engine=self.engine) as dataset:
                dimensions = {name: int(length) for name, length in dataset.sizes.items()}
                variables = {
                    name: {"dims": list(variable.dims), "shape": list(variable.shape)}
                    for name, variable in dataset.variables.items()
                }
                data_variables = tuple(dataset.data_vars)
                record_count = (
                    int(dataset[data_variables[0]].sizes[dataset[data_variables[0]].dims[0]])
                    if data_variables and dataset[data_variables[0]].dims
                    else 0
                )
                if record_count > limits.max_records:
                    raise DataReadError("NetCDF input exceeds the configured record limit")
                return {
                    "format": "NetCDF",
                    "engine": self.engine,
                    "dimensions": dimensions,
                    "variables": variables,
                    "record_count": record_count,
                }
        except DataReadError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise DataReadError(f"Cannot inspect NetCDF input {input_path}: {exc}") from exc

    def load(self, path: Path, *, selection: Selection | None = None) -> ScientificDataset:
        input_path = Path(path)
        _check_path(input_path)
        xarray = _xarray()
        _backend(self.engine)
        try:
            with xarray.open_dataset(input_path, engine=self.engine) as opened:
                dataset = opened.load()
            if selection and selection.fields:
                unknown = [name for name in selection.fields if name not in dataset.variables]
                if unknown:
                    raise DataReadError(f"Unknown NetCDF selection fields: {unknown}")
                dataset = dataset[list(selection.fields)]
            if selection and (selection.start is not None or selection.stop is not None):
                fields = (
                    tuple(selection.fields)
                    if selection and selection.fields
                    else tuple(dataset.data_vars)
                )
                if not fields or not dataset[fields[0]].dims:
                    raise DataReadError("NetCDF record selection requires a non-scalar field")
                dimension = dataset[fields[0]].dims[0]
                length = int(dataset.sizes[dimension])
                start = selection.start if selection.start is not None else 0
                stop = selection.stop if selection.stop is not None else length
                if stop > length:
                    raise DataReadError(f"NetCDF selection bounds must fit {dimension}={length}")
                dataset = dataset.isel({dimension: slice(start, stop)})
            metadata = _metadata(dataset, self.engine)
            return ScientificDataset(dataset.copy(deep=True), metadata, input_path)
        except DataReadError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise DataReadError(f"Cannot read NetCDF input {input_path}: {exc}") from exc


class NetCDFWriter:
    """Write ScientificDataset values through an explicit NetCDF engine."""

    def __init__(self, *, engine: str = "h5netcdf") -> None:
        if engine not in _ENGINES:
            raise ValueError(f"Unsupported NetCDF engine: {engine}")
        self.engine = engine
        self.info = WriterInfo("netcdf", "NetCDF", frozenset({"scientific", "write"}), _EXTENSIONS)

    def check(self, data: object) -> CapabilityResult:
        if not isinstance(data, ScientificDataset):
            return CapabilityResult(False, ("NetCDF requires a ScientificDataset",))
        try:
            _xarray()
            _backend(self.engine)
        except DataReadError as exc:
            return CapabilityResult(False, (str(exc),))
        for name, variable in data.data.variables.items():
            if variable.dtype.kind == "O":
                values = variable.values.flat
                if not all(isinstance(item, (str, bytes)) for item in values):
                    return CapabilityResult(
                        False, (f"variable {name!r} has unsupported object dtype",)
                    )
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
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=target.suffix, dir=target.parent
            )
            os.close(descriptor)
            temporary = Path(name)
            dataset.to_netcdf(temporary, engine=self.engine)
            os.replace(temporary, target)
        except BaseException:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise
        return target
