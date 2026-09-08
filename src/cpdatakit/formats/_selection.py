"""Dimension-preserving selection, shared by disk readers and slice services."""

import numpy as np
import xarray as xr

from ..exceptions import DataReadError
from ._metadata import METADATA_KEY, decode_metadata
from .base import Selection


def describe_xarray(dataset):
    metadata = decode_metadata(dataset.attrs.get(METADATA_KEY))
    units = metadata.get("units", {})
    return {
        name: {
            "dims": list(var.dims),
            "shape": list(var.shape),
            "dtype": str(var.dtype),
            "unit": var.attrs.get("unit", var.attrs.get("units", units.get(name))),
            "role": var.attrs.get("role"),
            "kind": "coordinate" if name in dataset.coords else "variable",
        }
        for name, var in dataset.variables.items()
    }


def dimension_slices(sizes, first_dims, selection: Selection | None, *, label: str):
    if selection is None:
        return {}
    indices = dict(selection.indexers)
    if selection.start is not None or selection.stop is not None:
        if not first_dims:
            raise DataReadError(f"{label} record selection requires a non-scalar field")
        dimension = first_dims[0]
        if dimension in indices:
            raise DataReadError(f"{label} duplicate record and named selection for {dimension}")
        indices[dimension] = slice(selection.start, selection.stop)
    result = {}
    for dimension, index in indices.items():
        if dimension not in sizes:
            raise DataReadError(f"{label} unknown selection dimension: {dimension}")
        length = sizes[dimension]
        if isinstance(index, int):
            if index >= length:
                raise DataReadError(f"{label} selection bounds must fit {dimension}={length}")
            index = slice(index, index + 1)
        if index.stop is not None and index.stop > length:
            raise DataReadError(f"{label} selection bounds must fit {dimension}={length}")
        result[dimension] = index
    return result


def select_xarray(dataset, selection: Selection | None, *, label: str):
    if selection and selection.fields:
        unknown = [name for name in selection.fields if name not in dataset.variables]
        if unknown:
            raise DataReadError(f"Unknown {label} selection fields: {unknown}")
        dataset = dataset[list(selection.fields)]
    fields = tuple(selection.fields) if selection and selection.fields else tuple(dataset.data_vars)
    first_dims = dataset[fields[0]].dims if fields else ()
    indices = dimension_slices(dataset.sizes, first_dims, selection, label=label)
    return dataset.isel(indices) if indices else dataset


def materialize(dataset, context=None, *, block_bytes=8 * 1024 * 1024):
    """Load selected arrays, checking cancellation between at most 8 MiB slabs.

    One first-axis row is the minimum slab. The returned data is still eager and
    must fit memory; this bounds temporary reads and adds cooperative checkpoints.
    """
    if context is None:
        return dataset.load()
    variables = {}
    for name, variable in dataset.variables.items():
        context.checkpoint(f"read {name}")
        if not variable.dims:
            values = variable.values
        else:
            row_bytes = max(1, int(np.prod(variable.shape[1:])) * variable.dtype.itemsize)
            step = max(1, block_bytes // row_bytes)
            values = np.empty(variable.shape, dtype=variable.dtype)
            for start in range(0, variable.shape[0], step):
                stop = min(variable.shape[0], start + step)
                context.checkpoint(f"read {name} {start}:{stop}/{variable.shape[0]}")
                values[start:stop] = variable.isel({variable.dims[0]: slice(start, stop)}).values
        variables[name] = xr.Variable(
            variable.dims, values, attrs=variable.attrs, encoding=variable.encoding
        )
    context.checkpoint("read complete")
    return xr.Dataset(
        {k: v for k, v in variables.items() if k not in dataset.coords},
        coords={k: v for k, v in variables.items() if k in dataset.coords},
        attrs=dataset.attrs,
    )
