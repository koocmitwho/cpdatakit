"""Bounded multidimensional heatmaps shared by Python and the workbench."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from ..exceptions import DataValidationError, OutputExistsError
from ..formats import ReadLimits, Selection
from .data_access import inspect_input, load_value
from .services import ServiceResult, _failure, _relative_artifact
from .units import declared_unit

_PLOT_LOCK = threading.Lock()


@dataclass(frozen=True)
class SliceRequest:
    data: Path
    variable: str
    x: str
    y: str
    indices: dict[str, int]
    output: Path
    workspace: Path | None = None
    vmin: float | None = None
    vmax: float | None = None
    cmap: str = "viridis"
    force: bool = False
    max_pixels: int = 1_000_000
    read_limits: ReadLimits = field(
        default_factory=lambda: ReadLimits(max_records=10**9, max_bytes=2**40)
    )


def _unit(value, name):
    return declared_unit(value.data[name], value.metadata, name)


def _axis(value, name):
    if name in value.data.coords and value.data[name].dims == (name,):
        values = value.data[name].values
        if values.dtype.kind in "iuf" and np.isfinite(values).all():
            if len(values) > 1 and not (np.all(np.diff(values) > 0) or np.all(np.diff(values) < 0)):
                raise DataValidationError(f"Heatmap coordinate {name} must be strictly monotonic")
            return values, f"{name} [{_unit(value, name) or 'unit unknown'}]"
    return np.arange(value.data.sizes[name]), f"{name} [index]"


def plot_scientific_slice(request: SliceRequest, *, context=None):
    """Export one plane, preserving fixed dimensions in the reader selection.

    This checks the selected plane's representation, not the physical validity of
    the entire dataset. Units come from input declarations and are never inferred.
    """
    source, target = Path(request.data), Path(request.output)
    provenance = {"operation": "plot_scientific_slice", "input_filename": source.name}
    temporary = None
    try:
        if target.suffix.lower() != ".png":
            raise DataValidationError("Scientific slice exports require a .png output")
        if target.exists() and not request.force:
            raise OutputExistsError(f"Output already exists: {target}")
        if target.resolve() == source.resolve() or target.resolve().is_relative_to(
            source.resolve()
        ):
            raise DataValidationError("Slice output cannot replace its input")
        info = inspect_input(source, None, request.read_limits)
        fields = {f["name"]: f for f in info["fields"]}
        spec = fields.get(request.variable)
        if spec is None or spec.get("kind") != "variable":
            raise DataValidationError("Choose an existing multidimensional variable")
        dims = spec.get("dims", [])
        if request.x == request.y or not {request.x, request.y}.issubset(dims):
            raise DataValidationError("Choose two distinct dimensions of the selected variable")
        if set(request.indices) != set(dims) - {request.x, request.y}:
            raise DataValidationError(
                "Supply an index for every dimension outside the display plane"
            )
        if any(isinstance(v, bool) or not isinstance(v, int) for v in request.indices.values()):
            raise DataValidationError("Slice positions must be integer indices")
        sizes = info["dimensions"]
        pixels = sizes[request.x] * sizes[request.y]
        if (
            not isinstance(request.max_pixels, int)
            or request.max_pixels <= 0
            or pixels > request.max_pixels
        ):
            raise DataValidationError("Selected plane exceeds the configured pixel limit")
        if any(v is not None and not np.isfinite(v) for v in (request.vmin, request.vmax)):
            raise DataValidationError("Color limits must be finite")
        if request.vmin is not None and request.vmax is not None and request.vmin >= request.vmax:
            raise DataValidationError("Color minimum must be below maximum")
        if request.cmap not in matplotlib.colormaps:
            raise DataValidationError("Unknown color map")
        value = load_value(
            source,
            selection=Selection((request.variable,), indexers=request.indices),
            context=context,
        )
        array = value.data[request.variable]
        plane = (
            array.isel({name: 0 for name in request.indices}).transpose(request.y, request.x).values
        )
        if plane.dtype.kind not in "iuf" or not np.isfinite(plane).any():
            raise DataValidationError(
                "Heatmaps require finite numeric values in the selected plane"
            )
        unit = _unit(value, request.variable)
        positions = {}
        for name, index in request.indices.items():
            coord = value.data.coords.get(name)
            position = None if coord is None or coord.dims != (name,) else coord.values[0]
            time_encoding = None
            if isinstance(position, np.datetime64):
                position = np.datetime_as_string(position)
                time_encoding = coord.encoding.get("units")
            elif isinstance(position, np.generic):
                position = position.item()
            if position is not None and not isinstance(position, (int, float, str, bool)):
                position = str(position)
            positions[name] = {
                "index": index,
                "value": position,
                "unit": _unit(value, name) if coord is not None else None,
            }
            if time_encoding is not None:
                positions[name]["encoding_unit"] = time_encoding
        xs, xlabel = _axis(value, request.x)
        ys, ylabel = _axis(value, request.y)
        metadata = {
            "variable": request.variable,
            "unit": unit,
            "x": request.x,
            "y": request.y,
            "slice": positions,
            "shape": list(plane.shape),
            "color_limits": [request.vmin, request.vmax],
            "cmap": request.cmap,
            "data_range": [
                float(plane[np.isfinite(plane)].min()),
                float(plane[np.isfinite(plane)].max()),
            ],
            "masked_pixels": int(np.count_nonzero(~np.isfinite(plane))),
            "input_filename": source.name,
            "source": value.metadata.get("provenance", {}),
            "validation_scope": "selected plane representation",
        }
        if context is not None:
            context.checkpoint("render slice")
        with _PLOT_LOCK:
            figure = Figure(figsize=(7.2, 5.2), layout="constrained")
            FigureCanvasAgg(figure)
            axes = figure.subplots()
            mesh = axes.pcolormesh(
                xs,
                ys,
                np.ma.masked_invalid(plane),
                shading="nearest",
                cmap=request.cmap,
                vmin=request.vmin,
                vmax=request.vmax,
            )
            figure.colorbar(mesh, ax=axes, label=f"{request.variable} [{unit or 'unit unknown'}]")
            position_text = "; ".join(
                f"{n}[{p['index']}] = {p['value']} {p['unit'] or ''}" for n, p in positions.items()
            )
            axes.set(
                title=f"{request.variable} [{unit or 'unit unknown'}]\n{position_text}",
                xlabel=xlabel,
                ylabel=ylabel,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".png", dir=target.parent
            )
            os.close(descriptor)
            temporary = Path(name)
            figure.savefig(
                temporary,
                dpi=160,
                metadata={
                    "Software": "CPDataKit",
                    "Description": json.dumps(metadata, ensure_ascii=False, allow_nan=False),
                },
            )
            if context is not None:
                context.checkpoint("write slice")
            if target.exists() and not request.force:
                raise OutputExistsError(f"Output already exists: {target}")
            os.replace(temporary, target)
        artifact = _relative_artifact(target, request.workspace)
        return ServiceResult(
            "plot_scientific_slice", "succeeded", metadata, artifact=artifact, provenance=provenance
        )
    except Exception as exc:
        return _failure("plot_scientific_slice", exc, provenance=provenance)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
