"""Observed schema drafts and previews of the actual conversion mapping."""

from math import prod

import numpy as np

from ..data import ScientificDataset
from ..formats import ReadLimits
from .data_access import ReadLimitError, inspect_input, load_value, validate_value
from .mapping import normalize_value
from .services import ServiceResult, _failure, _resolve


def _dtype(raw):
    if not isinstance(raw, str):
        return None
    if raw in {"string", "large_string"}:
        return "string"
    try:
        kind = np.dtype(raw).kind
    except (TypeError, ValueError):
        return None
    return {
        "f": "float",
        "i": "integer",
        "u": "integer",
        "b": "boolean",
        "S": "string",
        "U": "string",
    }.get(kind)


def draft_schema(request):
    """Return an editable draft plus observations and unresolved scientific decisions."""
    provenance = {"operation": "draft_schema", "input_filename": request.data.name}
    try:
        observed = inspect_input(request.data, None, request.read_limits)
        scientific = any(f.get("kind") in {"coordinate", "variable"} for f in observed["fields"])
        schema = {"profile": request.data.stem, "schema_version": "2.0" if scientific else "1.0"}
        review = [
            {
                "field": None,
                "property": "conventions",
                "observed": None,
                "question": (
                    "Confirm physical definitions, coordinate frame, "
                    "and tensor conventions where applicable."
                ),
            }
        ]
        if scientific:
            schema.update(
                dimensions=[{"name": n, "length": v} for n, v in observed["dimensions"].items()],
                coordinates=[],
                variables=[],
            )
        else:
            schema["fields"] = []
        for field in observed["fields"]:
            name = field["name"]
            unit = field.get("unit")
            if unit in {"", "not available"}:
                unit = None
            entry = {"name": name, "dtype": _dtype(field.get("dtype")), "unit": unit}
            coordinate = scientific and field.get("kind") == "coordinate"
            if scientific:
                entry["dims"] = field.get("dims", [])
            else:
                entry.update(required=True, shape=field.get("record_shape", []))
            if not coordinate:
                entry["role"] = field.get("role")
            for key in ("unit",) if coordinate else ("unit", "role"):
                review.append(
                    {
                        "field": name,
                        "property": key,
                        "observed": entry[key],
                        "question": f"Confirm {key} against the source documentation.",
                    }
                )
            if entry["dtype"] is None:
                review.append(
                    {
                        "field": name,
                        "property": "dtype",
                        "observed": field.get("dtype"),
                        "question": "Choose a supported dtype after checking these values.",
                    }
                )
            schema["coordinates" if coordinate else "variables" if scientific else "fields"].append(
                entry
            )
        return ServiceResult(
            "draft_schema",
            "succeeded",
            {
                "draft_version": 1,
                "ready": False,
                "schema": schema,
                "observations": observed,
                "review": review,
            },
            provenance=provenance,
        )
    except Exception as exc:
        return _failure("draft_schema", exc, provenance=provenance)


def _field(value, name):
    if isinstance(value, ScientificDataset):
        array = value.data[name]
        return {
            "dims": list(array.dims),
            "shape": list(array.shape),
            "unit": array.attrs.get(
                "unit", array.attrs.get("units", value.metadata.get("units", {}).get(name))
            ),
            "sample": array.values.reshape(-1)[:5].tolist(),
        }
    series = value.data[name]
    return {
        "dims": ["record"],
        "shape": [len(series)],
        "unit": value.metadata.get("units", {}).get(name),
        "sample": series.head(5).tolist(),
    }


def preview_mapping(request, *, read_limits=None):
    """Run conversion's normalization and validation without writing output.

    Inputs must fit the explicit preview limits. Full validation is performed;
    only the first five flattened values of each field are returned for display.
    """
    read_limits = read_limits or ReadLimits()
    provenance = {"operation": "preview_mapping", "input_filename": request.data.name}
    try:
        observed = inspect_input(request.data, None, read_limits)
        decoded_bytes = 0
        for field in observed["fields"]:
            if field.get("kind") not in {"coordinate", "variable"}:
                continue
            dtype = np.dtype(field["dtype"])
            if dtype.kind == "O":
                raise ReadLimitError("Variable-length arrays require a bounded numeric preview")
            decoded_bytes += prod(field["shape"]) * dtype.itemsize
        if decoded_bytes > read_limits.max_bytes:
            raise ReadLimitError("Decoded arrays exceed the configured preview byte limit")
        resolved = _resolve(request)
        before = load_value(request.data)
        after = normalize_value(before, request, resolved)
        validation = validate_value(after, resolved.schema)
        renames = {item.source: item.target for item in resolved.mappings}
        renames.update(after.metadata.get("dimension_mapping", {}))
        before_names = (
            before.data.variables if isinstance(before, ScientificDataset) else before.data.columns
        )
        after_names = (
            after.data.variables if isinstance(after, ScientificDataset) else after.data.columns
        )
        changes = []
        for name in before_names:
            target = renames.get(name, name)
            original = _field(before, name)
            current = _field(after, target) if target in after_names else None
            changes.append(
                {
                    "source": name,
                    "target": target if current else None,
                    "source_dims": original["dims"],
                    "target_dims": current["dims"] if current else None,
                    "source_shape": original["shape"],
                    "target_shape": current["shape"] if current else None,
                    "source_unit": original["unit"]
                    or next((m.input_unit for m in resolved.mappings if m.source == name), None),
                    "target_unit": current["unit"] if current else None,
                    "before": original["sample"],
                    "after": current["sample"] if current else None,
                }
            )
        return ServiceResult(
            "preview_mapping",
            "succeeded",
            {"fields": changes, "validation": validation.to_dict(), "sample_limit": 5},
            provenance=provenance,
        )
    except Exception as exc:
        return _failure("preview_mapping", exc, provenance=provenance)
