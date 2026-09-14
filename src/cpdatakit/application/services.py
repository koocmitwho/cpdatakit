"""Application use cases independent of CLI, HTTP, and template concerns."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from ..comparison import compare_reports as compare_report_values
from ..comparison import write_comparison_bundle
from ..data import ScientificDataset
from ..exceptions import (
    AdapterError,
    CPDataKitError,
    DataReadError,
    DataValidationError,
    NormalizationError,
    OutputExistsError,
    SchemaError,
)
from ..formats import NetCDFWriter, ParquetWriter, ReadLimits, ZarrWriter
from ..inspection import sanitize_error_message
from ..io import write_hdf5, write_hdf5_v2
from ..normalization import load_mapping_file
from ..plotting import (
    plot_counts,
    plot_field2d,
    plot_histogram,
    plot_stress_strain,
    plot_xy,
    save_figure,
)
from ..reporting import SCOPE_NOTE, write_report
from ..reporting import build_report as build_core_report
from ..schema import ProfileSchema
from ..schema_diff import (
    diff_schemas as diff_schema_values,
)
from ..schema_diff import (
    render_schema_diff_json,
    render_schema_diff_markdown,
    write_schema_diff,
)
from .contracts import (
    ComparisonOutcome as ComparisonOutcome,
)
from .contracts import (
    ComparisonRequest as ComparisonRequest,
)
from .contracts import (
    ConversionOutcome as ConversionOutcome,
)
from .contracts import (
    ConvertRequest as ConvertRequest,
)
from .contracts import (
    DatasetRequest as DatasetRequest,
)
from .contracts import (
    ImportInspectRequest as ImportInspectRequest,
)
from .contracts import (
    PlotOutcome as PlotOutcome,
)
from .contracts import (
    PlotRequest as PlotRequest,
)
from .contracts import (
    ReportOutcome as ReportOutcome,
)
from .contracts import (
    ReportRequest as ReportRequest,
)
from .contracts import (
    ResolvedSchemaMapping as ResolvedSchemaMapping,
)
from .contracts import (
    ResolveSchemaRequest as ResolveSchemaRequest,
)
from .contracts import (
    SchemaDiffOutcome as SchemaDiffOutcome,
)
from .contracts import (
    SchemaDiffRequest as SchemaDiffRequest,
)
from .contracts import (
    ServiceError as ServiceError,
)
from .contracts import (
    ServiceResult as ServiceResult,
)
from .contracts import (
    ValidationSummary as ValidationSummary,
)
from .data_access import (
    ReadLimitError,
    contract_dict,
    inspect_input,
    is_hdf5_v2,
    load_value,
    path_sha256,
    reader_for,
    resolve_contract,
    summarize_value,
    validate_value,
)
from .mapping import normalize_value

logger = logging.getLogger(__name__)

_ERROR_DETAILS: tuple[tuple[type[CPDataKitError], str, str], ...] = (
    (ReadLimitError, "read_limit_exceeded", "Choose a smaller input or increase the read limit."),
    (DataReadError, "data_read_error", "Check the input path and supported format."),
    (SchemaError, "schema_error", "Check the local schema contract."),
    (NormalizationError, "normalization_error", "Check the explicit field and unit mapping."),
    (DataValidationError, "data_validation_error", "Fix validation findings before writing."),
    (OutputExistsError, "output_exists", "Choose a new output or explicitly enable overwrite."),
    (AdapterError, "adapter_error", "Check the selected external format adapter."),
)


def _provenance(
    operation: str, data: Path, *, output: Path | None = None, steps: tuple[str, ...] = ()
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "operation": operation,
        "input_filename": data.name,
        "operation_log": list(steps),
    }
    if output is not None:
        value["output_filename"] = output.name
    return value


def _comparison_provenance(request: ComparisonRequest) -> dict[str, Any]:
    return {
        "operation": "compare_reports",
        "left_filename": request.left.name,
        "right_filename": request.right.name,
        "output_filename": request.output.name,
        "operation_log": ["read", "compare", "write"],
    }


def _relative_artifact(path: Path, workspace: Path | None) -> str:
    base = workspace or path.parent
    try:
        relative = path.resolve(strict=False).relative_to(base.resolve(strict=False))
    except (OSError, ValueError):
        return "[outside-workspace]"
    return relative.as_posix() or path.name


def _failure[T](
    operation: str,
    exc: Exception,
    *,
    provenance: dict[str, Any],
    value: T | None = None,
) -> ServiceResult[T]:
    if isinstance(exc, CPDataKitError):
        code = "cpdatakit_error"
        action = "Check the operation inputs and CPDataKit diagnostics."
        for exception_type, candidate_code, candidate_action in _ERROR_DETAILS:
            if isinstance(exc, exception_type):
                code = candidate_code
                action = candidate_action
                break
        error = ServiceError(code, sanitize_error_message(exc), action)
    else:
        correlation_id = uuid.uuid4().hex
        logger.exception("Unexpected application service failure correlation_id=%s", correlation_id)
        error = ServiceError(
            "internal_error",
            "Unexpected application service failure.",
            "Retry the operation or inspect the correlated application log.",
            correlation_id,
        )
    return ServiceResult(
        operation=operation,
        status="failed",
        value=value,
        error=error,
        provenance=provenance,
    )


def _resolve(
    request: DatasetRequest | ConvertRequest | PlotRequest,
) -> ResolvedSchemaMapping:
    contract = resolve_contract(request.schema)
    if request.mapping is None:
        return ResolvedSchemaMapping(contract)
    mappings, drop_unmapped = load_mapping_file(request.mapping)
    return ResolvedSchemaMapping(contract, tuple(mappings), drop_unmapped)


def _load_normalized(
    request: DatasetRequest | ConvertRequest | PlotRequest,
    resolved: ResolvedSchemaMapping,
    context=None,
):
    dataset = (
        load_value(request.data) if context is None else load_value(request.data, context=context)
    )
    return normalize_value(dataset, request, resolved)


def _read_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataReadError(f"Report input does not exist: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DataReadError(f"Cannot read report input {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DataReadError(f"Report input must be a JSON object: {path}")
    return payload


def resolve_schema_and_mapping(
    request: ResolveSchemaRequest,
) -> ServiceResult[ResolvedSchemaMapping]:
    """Load a local schema and optional explicit mapping without touching data."""

    provenance = {"operation": "resolve_schema_and_mapping"}
    try:
        contract = resolve_contract(request.schema)
        if request.mapping is None:
            resolved = ResolvedSchemaMapping(contract)
        else:
            mappings, drop_unmapped = load_mapping_file(request.mapping)
            resolved = ResolvedSchemaMapping(contract, tuple(mappings), drop_unmapped)
    except Exception as exc:
        return _failure("resolve_schema_and_mapping", exc, provenance=provenance)
    return ServiceResult(
        operation="resolve_schema_and_mapping",
        status="succeeded",
        value=resolved,
        provenance=provenance,
    )


def import_and_inspect(
    request: ImportInspectRequest,
) -> ServiceResult[dict[str, Any]]:
    """Inspect a supported input after applying byte and record bounds."""

    provenance = _provenance("import_and_inspect", request.data, steps=("inspect",))
    try:
        if request.data.is_file() and request.data.stat().st_size > request.read_limits.max_bytes:
            return ServiceResult(
                operation="import_and_inspect",
                status="failed",
                error=ServiceError(
                    "read_limit_exceeded",
                    "Input exceeds the configured maximum byte limit.",
                    "Choose a smaller input or increase the bounded read limit.",
                ),
                provenance=provenance,
            )
        result = inspect_input(request.data, request.schema, request.read_limits)
        record_count = result.get("record_count")
        if isinstance(record_count, int) and record_count > request.read_limits.max_records:
            return ServiceResult(
                operation="import_and_inspect",
                status="failed",
                error=ServiceError(
                    "read_limit_exceeded",
                    "Input exceeds the configured maximum record limit.",
                    "Choose a smaller input or increase the bounded read limit.",
                ),
                provenance=provenance,
            )
    except Exception as exc:
        return _failure("import_and_inspect", exc, provenance=provenance)
    return ServiceResult(
        operation="import_and_inspect",
        status="succeeded",
        value=result,
        provenance=provenance,
    )


def validate_and_summarize(
    request: DatasetRequest,
) -> ServiceResult[ValidationSummary]:
    """Load, explicitly normalize, validate, and summarize one dataset."""

    provenance = _provenance(
        "validate_and_summarize", request.data, steps=("load", "normalize", "validate", "summarize")
    )
    try:
        resolved = _resolve(request)
        dataset = _load_normalized(request, resolved)
        validation = validate_value(dataset, resolved.schema)
        summary = summarize_value(dataset, resolved.schema, validation)
    except Exception as exc:
        return _failure("validate_and_summarize", exc, provenance=provenance)
    return ServiceResult(
        operation="validate_and_summarize",
        status="succeeded",
        value=ValidationSummary(validation, summary),
        provenance=provenance,
    )


def convert_and_write(request: ConvertRequest, *, context=None) -> ServiceResult[ConversionOutcome]:
    """Validate and atomically write through the selected format adapter."""

    provenance = _provenance(
        "convert_and_write",
        request.data,
        output=request.output,
        steps=("load", "normalize", "validate", "convert"),
    )
    try:
        resolved = _resolve(request)
        dataset = _load_normalized(request, resolved, context=context)
        if context is not None:
            context.checkpoint("validate")
        validation = validate_value(dataset, resolved.schema)
    except Exception as exc:
        return _failure("convert_and_write", exc, provenance=provenance)

    outcome = ConversionOutcome(validation)
    if not validation.valid and not request.allow_invalid:
        return ServiceResult(
            operation="convert_and_write",
            status="failed",
            value=outcome,
            error=ServiceError(
                "validation_failed",
                "Dataset has validation errors and was not written.",
                "Fix validation findings or explicitly allow invalid output.",
            ),
            provenance=provenance,
        )
    try:
        if request.output_format == "hdf5" and not isinstance(dataset, ScientificDataset):
            if request.mapping is not None:
                dataset.metadata["mapping_sha256"] = path_sha256(request.mapping)
            if context is not None:
                context.checkpoint("write")
            write_hdf5(
                dataset,
                request.output,
                resolved.schema,
                validation,
                source_description=request.source_description,
                operation_log=list(provenance["operation_log"]),
                force=request.force,
                allow_invalid=request.allow_invalid,
            )
        else:
            if request.mapping is not None:
                dataset.metadata["mapping_sha256"] = path_sha256(request.mapping)
            dataset.metadata["provenance"] = {
                **dataset.metadata.get("provenance", {}),
                **provenance,
                "input_sha256": path_sha256(request.data),
            }
            if request.source_description is not None:
                dataset.metadata["provenance"]["source_description"] = request.source_description
            dataset.metadata["validation_summary"] = {
                "valid": validation.valid,
                "error_count": len(validation.errors),
                "warning_count": len(validation.warnings),
            }
            if context is not None:
                context.checkpoint("write")
            if request.output_format == "hdf5":
                write_hdf5_v2(
                    dataset,
                    request.output,
                    resolved.schema,
                    force=request.force,
                    allow_invalid=request.allow_invalid,
                )
            else:
                writers = {"netcdf": NetCDFWriter, "zarr": ZarrWriter, "parquet": ParquetWriter}
                if request.output_format not in writers:
                    raise DataValidationError("Unsupported output format")
                writers[request.output_format]().write(dataset, request.output, force=request.force)
    except Exception as exc:
        return _failure("convert_and_write", exc, provenance=provenance, value=outcome)
    artifact = _relative_artifact(request.output, request.workspace)
    completed = ConversionOutcome(validation, artifact)
    return ServiceResult(
        operation="convert_and_write",
        status="succeeded",
        value=completed,
        artifact=artifact,
        provenance=provenance,
    )


def _plot_figure(request: PlotRequest, dataset: Any, schema: ProfileSchema) -> Any:
    if request.kind == "stress-strain":
        return plot_stress_strain(dataset, schema)
    if request.kind == "histogram":
        if not request.field:
            raise CPDataKitError("--field is required for histogram")
        return plot_histogram(dataset, schema, request.field)
    if request.kind == "grain-count":
        return plot_counts(dataset, schema, "grain_id")
    if request.kind == "phase-count":
        return plot_counts(dataset, schema, "phase_id")
    if request.kind == "field2d":
        return plot_field2d(dataset, schema)
    if request.kind == "xy":
        if not request.x or not request.y:
            raise CPDataKitError("--x and --y are required for xy")
        return plot_xy(dataset, schema, request.x, request.y)
    raise CPDataKitError(f"Unsupported plot kind: {request.kind}")


def plot_declared_fields(request: PlotRequest) -> ServiceResult[PlotOutcome]:
    """Validate, render, and save one schema-driven plot without returning a figure handle."""

    provenance = _provenance(
        "plot_declared_fields",
        request.data,
        output=request.output,
        steps=("load", "normalize", "validate", "plot", "write"),
    )
    try:
        resolved = _resolve(request)
        dataset = _load_normalized(request, resolved)
        validation = validate_value(dataset, resolved.schema)
    except Exception as exc:
        return _failure("plot_declared_fields", exc, provenance=provenance)

    outcome = PlotOutcome(request.kind, validation)
    if not validation.valid:
        return ServiceResult(
            operation="plot_declared_fields",
            status="failed",
            value=outcome,
            error=ServiceError(
                "validation_failed",
                "Dataset has validation errors and was not plotted.",
                "Fix validation findings before creating a plot.",
            ),
            provenance=provenance,
        )

    figure = None
    try:
        figure, _ = _plot_figure(request, dataset, resolved.schema)
        save_figure(figure, request.output, force=request.force)
    except Exception as exc:
        return _failure("plot_declared_fields", exc, provenance=provenance)
    finally:
        if figure is not None:
            plt.close(figure)

    artifact = _relative_artifact(request.output, request.workspace)
    completed = PlotOutcome(request.kind, validation, artifact)
    return ServiceResult(
        operation="plot_declared_fields",
        status="succeeded",
        value=completed,
        artifact=artifact,
        provenance=provenance,
    )


def build_report(request: ReportRequest) -> ServiceResult[ReportOutcome]:
    """Build and render an offline report through the shared service boundary."""

    provenance = _provenance(
        "build_report",
        request.data,
        output=request.output,
        steps=("inspect", "load", "validate", "summarize", "render"),
    )
    try:
        if reader_for(request.data) is None and not is_hdf5_v2(request.data):
            report = build_core_report(request.data, request.schema)
        else:
            contract = resolve_contract(request.schema)
            dataset = load_value(request.data)
            validation = validate_value(dataset, contract)
            summary = summarize_value(dataset, contract, validation)
            inspection = inspect_input(request.data, None, ReadLimits(2**63 - 1, 2**63 - 1))
            report = {
                **inspection,
                "schema": contract_dict(contract),
                "validation": validation.to_dict(),
                "statistics": summary,
                "provenance": dataset.metadata.get("provenance", inspection["provenance"]),
                "scope_note": SCOPE_NOTE,
            }
            if isinstance(dataset, ScientificDataset):
                report["fields"] = [
                    {"name": name, **info} for name, info in summary["fields"].items()
                ]
        write_report(report, request.output, format=request.format, force=request.force)
    except Exception as exc:
        return _failure("build_report", exc, provenance=provenance)
    artifact = _relative_artifact(request.output, request.workspace)
    return ServiceResult(
        operation="build_report",
        status="succeeded",
        value=ReportOutcome(report, artifact),
        artifact=artifact,
        provenance=provenance,
    )


def compare_reports(request: ComparisonRequest) -> ServiceResult[ComparisonOutcome]:
    """Compare two JSON reports and atomically write the offline bundle."""

    provenance = _comparison_provenance(request)
    try:
        comparison = compare_report_values(_read_report(request.left), _read_report(request.right))
        write_comparison_bundle(comparison, request.output, force=request.force)
    except Exception as exc:
        return _failure("compare_reports", exc, provenance=provenance)
    artifact = _relative_artifact(request.output, request.workspace)
    return ServiceResult(
        operation="compare_reports",
        status="succeeded",
        value=ComparisonOutcome(comparison, artifact),
        artifact=artifact,
        provenance=provenance,
    )


def diff_schema_contracts(request: SchemaDiffRequest) -> ServiceResult[SchemaDiffOutcome]:
    """Diff two contracts of the same schema version and optionally write an artifact."""

    provenance: dict[str, Any] = {
        "operation": "diff_schema_contracts",
        "source_filename": request.source.name,
        "target_filename": request.target.name,
        "operation_log": ["read", "diff", "render"],
    }
    if request.output is not None:
        provenance["output_filename"] = request.output.name
    try:
        diff = diff_schema_values(request.source, request.target)
        rendered = (
            render_schema_diff_json(diff)
            if request.format == "json"
            else render_schema_diff_markdown(diff)
        )
        artifact = None
        if request.output is not None:
            write_schema_diff(diff, request.output, format=request.format, force=request.force)
            artifact = _relative_artifact(request.output, request.workspace)
    except Exception as exc:
        return _failure("diff_schema_contracts", exc, provenance=provenance)
    return ServiceResult(
        operation="diff_schema_contracts",
        status="succeeded",
        value=SchemaDiffOutcome(diff, rendered, artifact),
        artifact=artifact,
        provenance=provenance,
    )
