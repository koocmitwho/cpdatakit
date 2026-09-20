"""Dimension-preserving selection, shared by disk readers and slice services."""

import numpy as np
import xarray as xr

from ..exceptions import DataReadError
from ._metadata import METADATA_KEY, decode_metadata
from .base import Selection


def _inherit_cf_time_bounds(dataset):
    """Propagate CF time-bound metadata before projecting out a parent field."""
    for variable in dataset.variables.values():
        units = variable.attrs.get("units")
        bounds = variable.attrs.get("bounds")
        if isinstance(units, str) and "since" in units and bounds in dataset.variables:
            target = dataset.variables[bounds].attrs
            for name in ("units", "calendar"):
                if name in variable.attrs:
                    target.setdefault(name, variable.attrs[name])


def describe_xarray(dataset, *, decode_cf=False):
    metadata = decode_metadata(dataset.attrs.get(METADATA_KEY))
    units = metadata.get("units", {})
    variables = dataset.variables
    if decode_cf:
        _inherit_cf_time_bounds(dataset)
        # Decode variable metadata without constructing indexes. CF datetime dtype
        # detection samples endpoints, so callers must check size limits first.
        variables = {
            name: xr.conventions.decode_cf_variable(name, var, concat_characters=False)
            for name, var in variables.items()
        }
    return {
        name: {
            "dims": list(var.dims),
            "shape": list(var.shape),
            "dtype": str(var.dtype),
            "unit": var.attrs.get("unit", var.attrs.get("units", units.get(name))),
            "role": var.attrs.get("role"),
            "kind": "coordinate" if name in dataset.coords else "variable",
        }
        for name, var in variables.items()
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


def materialize_cf_selection(dataset, selection, *, label, context=None):
    """Keep packed values exact and retain CF time types known from axis endpoints.

    Time dtype detection needs bounded samples outside the selected interval. It
    cannot detect an out-of-range interior date on an arbitrary nonmonotonic axis;
    the selected values therefore retain automatic decoding unless the endpoints
    already require cftime. No full coordinate scan is performed.
    """
    _inherit_cf_time_bounds(dataset)
    selected = select_xarray(dataset, selection, label=label)
    time_coders = {}
    for name in selected.variables:
        variable = dataset.variables[name]
        units = variable.attrs.get("units")
        if isinstance(units, str) and "since" in units:
            if context is not None:
                context.checkpoint(f"inspect time dtype {name}")
            decoded = xr.conventions.decode_cf_variable(name, variable)
            time_coders[name] = xr.coders.CFDatetimeCoder(
                use_cftime=True if decoded.dtype.kind == "O" else None
            )
    loaded = materialize(selected, context)
    decoded = xr.decode_cf(loaded, decode_times=time_coders, concat_characters=False).load()
    for name, coder in time_coders.items():
        coordinate = decoded.variables[name]
        if coder.use_cftime and coordinate.dims == (name,) and coordinate.size == 0:
            # No values remain from which xarray could infer an empty CFTimeIndex.
            decoded = decoded.assign_coords(
                {
                    name: xr.IndexVariable(
                        (name,),
                        xr.CFTimeIndex([], name=name),
                        attrs=coordinate.attrs,
                        encoding=coordinate.encoding,
                    )
                }
            )
    return decoded


def materialize(dataset, context=None, *, block_bytes=8 * 1024 * 1024):
    """Load selected arrays, checking cancellation between at most 8 MiB slabs.

    One first-axis row is the minimum slab. The returned data is still eager and
    must fit memory; this bounds temporary reads and adds cooperative checkpoints.
    """
    if context is None:
        dataset = dataset.load()
        # Readers defer default indexes so opening cannot read whole coordinates.
        # Restore label-based selection only after the positional subset is eager.
        for name, coordinate in dataset.coords.items():
            if coordinate.dims == (name,) and name not in dataset.xindexes:
                dataset = dataset.set_xindex(name)
        return dataset
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
