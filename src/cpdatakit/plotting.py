"""Headless-safe scientific plotting helpers."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import rcParams
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from .exceptions import CPDataKitError, OutputExistsError
from .model import Dataset
from .schema import ProfileSchema, load_schema

_BLUE = "#3B6FB6"
_INK = "#263238"
rcParams["svg.hashsalt"] = "cpdatakit-0.1"


def _unit(dataset: Dataset, schema: ProfileSchema, field: str) -> str:
    units = dataset.metadata.get("units", {})
    if not isinstance(units, dict):
        raise CPDataKitError("Dataset units metadata must be an object")
    if field in units:
        unit = units[field]
        if not isinstance(unit, str) or not unit.strip():
            raise CPDataKitError(f"Invalid stored unit for {field!r}")
        return unit
    spec = schema.field_map().get(field)
    return spec.unit if spec and spec.unit else "unit not declared"


def _finish(fig: Figure, ax: Axes) -> tuple[Figure, Axes]:
    ax.tick_params(colors=_INK)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color="#D9DEE3", linewidth=0.7, alpha=0.7)
    fig.tight_layout()
    return fig, ax


def plot_stress_strain(dataset: Dataset, schema: str | ProfileSchema) -> tuple[Figure, Axes]:
    """Plot an explicitly declared scalar stress-strain curve."""
    contract = load_schema(schema)
    if "strain" not in dataset.data or "stress" not in dataset.data:
        raise CPDataKitError("stress-strain plot requires declared 'strain' and 'stress' fields")
    strain_unit = _unit(dataset, contract, "strain")
    stress_unit = _unit(dataset, contract, "stress")
    fig, ax = plt.subplots(figsize=(7.2, 4.5))
    ax.plot(dataset.data["strain"], dataset.data["stress"], color=_BLUE, label="stress")
    ax.set(
        title="Stress-strain curve",
        xlabel=f"Strain [{strain_unit}]",
        ylabel=f"Stress [{stress_unit}]",
    )
    ax.legend(frameon=False)
    return _finish(fig, ax)


def plot_histogram(
    dataset: Dataset, schema: str | ProfileSchema, field: str
) -> tuple[Figure, Axes]:
    """Plot a finite-value histogram for a declared numeric field."""
    contract = load_schema(schema)
    if field not in dataset.data or field not in contract.field_map():
        raise CPDataKitError(f"Histogram field is absent or undeclared: {field}")
    spec = contract.field_map()[field]
    if spec.dtype not in {"float", "integer"} or spec.shape:
        raise CPDataKitError(f"Histogram field must be a declared scalar numeric field: {field}")
    try:
        values = np.asarray(dataset.data[field], dtype=float)
    except (TypeError, ValueError) as exc:
        raise CPDataKitError(f"Histogram field is not numeric: {field}") from exc
    values = values[np.isfinite(values)]
    if not len(values):
        raise CPDataKitError(f"Histogram field has no finite data: {field}")
    unit = _unit(dataset, contract, field)
    fig, ax = plt.subplots(figsize=(7.2, 4.5))
    ax.hist(values, bins="auto", color=_BLUE, edgecolor="white", label=field)
    ax.set(
        title=f"Distribution of {field}",
        xlabel=f"{field} [{unit}]",
        ylabel="Count [records]",
    )
    ax.legend(frameon=False)
    return _finish(fig, ax)


def plot_xy(
    dataset: Dataset,
    schema: str | ProfileSchema,
    x: str,
    y: str,
) -> tuple[Figure, Axes]:
    """Plot declared scalar fields with stored units or legacy schema defaults."""
    contract = load_schema(schema)
    declared = contract.field_map()
    for field in (x, y):
        if field not in dataset.data or field not in declared:
            raise CPDataKitError(f"XY plot field is absent or undeclared: {field}")
        spec = declared[field]
        if spec.dtype not in {"float", "integer"} or spec.shape:
            raise CPDataKitError(f"XY plot field must be a declared scalar numeric field: {field}")
    try:
        x_values = np.asarray(dataset.data[x], dtype=float)
        y_values = np.asarray(dataset.data[y], dtype=float)
    except (TypeError, ValueError) as exc:
        raise CPDataKitError("XY plot fields must contain numeric values") from exc
    finite = np.isfinite(x_values) & np.isfinite(y_values)
    if not finite.any():
        raise CPDataKitError("XY plot fields have no paired finite data")
    x_unit = _unit(dataset, contract, x)
    y_unit = _unit(dataset, contract, y)
    fig, ax = plt.subplots(figsize=(7.2, 4.5))
    ax.plot(x_values[finite], y_values[finite], color=_BLUE, label=y)
    ax.set(
        title=f"{y} vs {x}",
        xlabel=f"{x} [{x_unit}]",
        ylabel=f"{y} [{y_unit}]",
    )
    ax.legend(frameon=False)
    return _finish(fig, ax)


def plot_counts(dataset: Dataset, schema: str | ProfileSchema, field: str) -> tuple[Figure, Axes]:
    """Plot record counts for grain_id or phase_id."""
    load_schema(schema)
    if field not in {"grain_id", "phase_id"} or field not in dataset.data:
        raise CPDataKitError("Count plot field must be present grain_id or phase_id")
    counts = dataset.data[field].value_counts().sort_index()
    fig, ax = plt.subplots(figsize=(7.2, 4.5))
    ax.bar(counts.index.astype(str), counts.values, color=_BLUE, label="Record count")
    ax.set(title=f"Records by {field}", xlabel=f"{field} [identifier]", ylabel="Count [records]")
    ax.legend(frameon=False)
    return _finish(fig, ax)


def plot_field2d(dataset: Dataset, schema: str | ProfileSchema) -> tuple[Figure, Axes]:
    """Plot a 2D scalar field using its declared sample coordinates."""
    contract = load_schema(schema)
    needed = {"x", "y", "value"}
    if not needed.issubset(dataset.data):
        raise CPDataKitError("field2d plot requires x, y, and value")
    x_unit = _unit(dataset, contract, "x")
    y_unit = _unit(dataset, contract, "y")
    value_unit = _unit(dataset, contract, "value")
    fig, ax = plt.subplots(figsize=(6.2, 5.2))
    points = ax.scatter(
        dataset.data["x"],
        dataset.data["y"],
        c=dataset.data["value"],
        cmap="viridis",
        s=35,
        label="Samples",
    )
    colorbar = fig.colorbar(points, ax=ax)
    colorbar.set_label(f"Value [{value_unit}]")
    ax.set(
        title="Two-dimensional scalar field",
        xlabel=f"x [{x_unit}]",
        ylabel=f"y [{y_unit}]",
    )
    ax.legend(frameon=False)
    return _finish(fig, ax)


def save_figure(fig: Figure, output: str | Path, *, force: bool = False) -> Path:
    """Save a PNG or SVG and preserve existing files unless replacement is requested."""
    target = Path(output)
    if target.suffix.lower() not in {".png", ".svg"}:
        raise CPDataKitError("Plot output extension must be .png or .svg")
    if target.exists() and not force:
        raise OutputExistsError(f"Output already exists: {target}; pass force=True to replace it")
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = {"Date": None} if target.suffix.lower() == ".svg" else {"Software": "CPDataKit"}
    fig.savefig(target, dpi=180, bbox_inches="tight", metadata=metadata)
    return target
