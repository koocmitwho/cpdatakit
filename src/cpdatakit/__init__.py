"""CPDataKit public API."""

from importlib import import_module as _import_module
from typing import TYPE_CHECKING

from ._version import __version__

if TYPE_CHECKING:
    from .comparison import compare_reports
    from .inspection import inspect_dataset, inspect_hdf5_structure
    from .io import iter_hdf5_chunks, load_dataset, load_hdf5
    from .normalization import FieldMapping, load_mapping_file, normalize_dataset
    from .reporting import (
        build_report,
        render_report_html,
        render_report_json,
        render_report_markdown,
    )
    from .schema import (
        FieldSchema,
        ProfileSchema,
        describe_schema,
        load_schema,
        make_field_schema,
        make_profile_schema,
        schema_sha256,
        schema_to_canonical_json,
        schema_to_dict,
        schema_to_json,
        validate_schema,
        write_schema,
    )
    from .schema_diff import diff_schemas
    from .statistics import summarize_dataset
    from .validation import validate_dataset

__all__ = [
    "FieldMapping",
    "FieldSchema",
    "ProfileSchema",
    "__version__",
    "build_report",
    "compare_reports",
    "describe_schema",
    "diff_schemas",
    "inspect_dataset",
    "inspect_hdf5_structure",
    "iter_hdf5_chunks",
    "load_dataset",
    "load_hdf5",
    "load_mapping_file",
    "load_schema",
    "make_field_schema",
    "make_profile_schema",
    "normalize_dataset",
    "render_report_html",
    "render_report_json",
    "render_report_markdown",
    "schema_sha256",
    "schema_to_canonical_json",
    "schema_to_dict",
    "schema_to_json",
    "summarize_dataset",
    "validate_dataset",
    "validate_schema",
    "write_schema",
]

_EXPORT_MODULES = {
    "FieldMapping": ".normalization",
    "FieldSchema": ".schema",
    "ProfileSchema": ".schema",
    "build_report": ".reporting",
    "compare_reports": ".comparison",
    "describe_schema": ".schema",
    "diff_schemas": ".schema_diff",
    "inspect_dataset": ".inspection",
    "inspect_hdf5_structure": ".inspection",
    "iter_hdf5_chunks": ".io",
    "load_dataset": ".io",
    "load_hdf5": ".io",
    "load_mapping_file": ".normalization",
    "load_schema": ".schema",
    "make_field_schema": ".schema",
    "make_profile_schema": ".schema",
    "normalize_dataset": ".normalization",
    "render_report_html": ".reporting",
    "render_report_json": ".reporting",
    "render_report_markdown": ".reporting",
    "schema_sha256": ".schema",
    "schema_to_canonical_json": ".schema",
    "schema_to_dict": ".schema",
    "schema_to_json": ".schema",
    "summarize_dataset": ".statistics",
    "validate_dataset": ".validation",
    "validate_schema": ".schema",
    "write_schema": ".schema",
}


def __getattr__(name: str):
    """Load public objects on first use without wrapping their signatures."""
    if name not in _EXPORT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_import_module(_EXPORT_MODULES[name], __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
