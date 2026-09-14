"""Typed application services shared by CPDataKit edges."""

from importlib import import_module as _import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .authoring import draft_schema, preview_mapping
    from .batch import run_batch
    from .capabilities import (
        CapabilityDiscovery,
        CapabilityItem,
        CapabilityRequest,
        discover_capabilities,
    )
    from .services import (
        ComparisonOutcome,
        ComparisonRequest,
        ConversionOutcome,
        ConvertRequest,
        DatasetRequest,
        ImportInspectRequest,
        PlotOutcome,
        PlotRequest,
        ReadLimits,
        ReportOutcome,
        ReportRequest,
        ResolvedSchemaMapping,
        ResolveSchemaRequest,
        SchemaDiffOutcome,
        SchemaDiffRequest,
        ServiceError,
        ServiceResult,
        ValidationSummary,
        build_report,
        compare_reports,
        convert_and_write,
        diff_schema_contracts,
        import_and_inspect,
        plot_declared_fields,
        resolve_schema_and_mapping,
        validate_and_summarize,
    )
    from .slices import SliceRequest, plot_scientific_slice
    from .tensile import integrate_tensile_bundle

__all__ = [
    "CapabilityDiscovery",
    "CapabilityItem",
    "CapabilityRequest",
    "ComparisonOutcome",
    "ComparisonRequest",
    "ConversionOutcome",
    "ConvertRequest",
    "DatasetRequest",
    "ImportInspectRequest",
    "PlotOutcome",
    "PlotRequest",
    "ReadLimits",
    "ReportOutcome",
    "ReportRequest",
    "ResolveSchemaRequest",
    "ResolvedSchemaMapping",
    "SchemaDiffOutcome",
    "SchemaDiffRequest",
    "ServiceError",
    "ServiceResult",
    "SliceRequest",
    "ValidationSummary",
    "build_report",
    "compare_reports",
    "convert_and_write",
    "diff_schema_contracts",
    "discover_capabilities",
    "draft_schema",
    "import_and_inspect",
    "integrate_tensile_bundle",
    "plot_declared_fields",
    "plot_scientific_slice",
    "preview_mapping",
    "resolve_schema_and_mapping",
    "run_batch",
    "validate_and_summarize",
]

_EXPORT_MODULES = dict.fromkeys(__all__, ".services")
_EXPORT_MODULES.update(
    dict.fromkeys(
        [
            "ServiceError",
            "ServiceResult",
            "DatasetRequest",
            "ImportInspectRequest",
            "ResolveSchemaRequest",
            "ConvertRequest",
            "ReportRequest",
            "ComparisonRequest",
            "PlotRequest",
            "SchemaDiffRequest",
            "ResolvedSchemaMapping",
            "ValidationSummary",
            "ConversionOutcome",
            "ReportOutcome",
            "ComparisonOutcome",
            "PlotOutcome",
            "SchemaDiffOutcome",
            "ReadLimits",
        ],
        ".contracts",
    )
)
_EXPORT_MODULES.update(
    {
        "CapabilityDiscovery": ".capabilities",
        "CapabilityItem": ".capabilities",
        "CapabilityRequest": ".capabilities",
        "SliceRequest": ".slices",
        "discover_capabilities": ".capabilities",
        "draft_schema": ".authoring",
        "integrate_tensile_bundle": ".tensile",
        "plot_scientific_slice": ".slices",
        "preview_mapping": ".authoring",
        "run_batch": ".batch",
    }
)


def __getattr__(name: str):
    """Resolve an application service only when its public name is requested."""
    if name not in _EXPORT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_import_module(_EXPORT_MODULES[name], __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
