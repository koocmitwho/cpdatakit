"""Deterministic, metadata-first capability discovery."""

from __future__ import annotations

import importlib.metadata
import importlib.util
from dataclasses import asdict, dataclass, field
from typing import Any

from ..adapters import DEFAULT_ADAPTER_REGISTRY
from .services import ServiceResult


@dataclass(frozen=True, slots=True)
class CapabilityRequest:
    """Options controlling optional and third-party capability visibility."""

    include_unavailable: bool = True
    safe_mode: bool = False


@dataclass(frozen=True, slots=True)
class CapabilityItem:
    """One reader, writer, plot, UI, or adapter capability."""

    kind: str
    name: str
    format_name: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    available: bool = True
    reason: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", tuple(sorted(self.capabilities)))


@dataclass(frozen=True, slots=True)
class CapabilityDiscovery:
    """Stable collection of capabilities available to the current process."""

    items: tuple[CapabilityItem, ...]

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.items, key=lambda item: (item.kind, item.name)))
        object.__setattr__(self, "items", ordered)

    def to_dict(self) -> dict[str, Any]:
        return {"items": [asdict(item) for item in self.items]}


def _missing_modules(modules: tuple[str, ...]) -> tuple[str, ...]:
    missing: list[str] = []
    for module in modules:
        try:
            found = importlib.util.find_spec(module)
        except (ImportError, ModuleNotFoundError, ValueError):
            found = None
        if found is None:
            missing.append(module)
    return tuple(missing)


def _version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _item(
    kind: str,
    name: str,
    format_name: str,
    capabilities: tuple[str, ...],
    *,
    modules: tuple[str, ...] = (),
    distributions: tuple[str, ...] = (),
) -> CapabilityItem:
    missing = _missing_modules(modules)
    reason = f"missing dependencies: {', '.join(missing)}" if missing else None
    versions = [_version(distribution) for distribution in distributions]
    version = (
        ", ".join(
            f"{distribution}={resolved}"
            for distribution, resolved in zip(distributions, versions, strict=True)
            if resolved is not None
        )
        or None
    )
    return CapabilityItem(
        kind,
        name,
        format_name,
        capabilities,
        available=not missing,
        reason=reason,
        version=version,
    )


def _format_items(kind: str, capability: str) -> list[CapabilityItem]:
    items = [
        _item(kind, "csv", "CSV", (capability,)),
        _item(kind, "json", "JSON records", (capability,)),
        _item(kind, "hdf5-v1", "CPDataKit HDF5 1.0", (capability,)),
        _item(kind, "hdf5-v2", "CPDataKit HDF5 2.0", (capability,), modules=("xarray",)),
        _item(
            kind,
            "netcdf:h5netcdf",
            "NetCDF (h5netcdf)",
            (capability,),
            modules=("xarray", "h5netcdf"),
            distributions=("xarray", "h5netcdf"),
        ),
        _item(
            kind,
            "netcdf:netcdf4",
            "NetCDF (netCDF4)",
            (capability,),
            modules=("xarray", "netCDF4"),
            distributions=("xarray", "netCDF4"),
        ),
        _item(
            kind,
            "parquet",
            "Parquet",
            (capability,),
            modules=("pandas", "pyarrow"),
            distributions=("pandas", "pyarrow"),
        ),
        _item(
            kind,
            "zarr-v3",
            "Zarr 3",
            (capability,),
            modules=("xarray", "zarr"),
            distributions=("xarray", "zarr"),
        ),
    ]
    return items


def _plot_items() -> list[CapabilityItem]:
    return [
        _item("plot", name, "CPDataKit plot", ("plot",), modules=("matplotlib",))
        for name in ("field2d", "grain-count", "histogram", "phase-count", "stress-strain", "xy")
    ]


def discover_capabilities(
    request: CapabilityRequest | None = None,
) -> ServiceResult[CapabilityDiscovery]:
    """Return deterministic capability metadata without loading plugin implementations."""

    options = request or CapabilityRequest()
    items = [*_format_items("reader", "read"), *_format_items("writer", "write"), *_plot_items()]
    items.append(
        _item(
            "ui",
            "local-ui",
            "FastAPI local UI",
            ("serve",),
            modules=("fastapi", "uvicorn", "jinja2", "multipart"),
            distributions=("fastapi", "uvicorn", "Jinja2", "python-multipart"),
        )
    )
    if not options.safe_mode:
        for adapter in DEFAULT_ADAPTER_REGISTRY.describe():
            items.append(
                CapabilityItem(
                    "adapter",
                    adapter.name,
                    adapter.format_name,
                    tuple(adapter.capabilities),
                    version=_version("cpdatakit"),
                )
            )
    if not options.include_unavailable:
        items = [item for item in items if item.available]
    discovery = CapabilityDiscovery(tuple(items))
    return ServiceResult(
        operation="discover_capabilities",
        status="succeeded",
        value=discovery,
        provenance={"operation": "discover_capabilities"},
    )
