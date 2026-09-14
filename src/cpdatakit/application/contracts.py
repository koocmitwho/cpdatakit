"""Typed requests and results shared by application use cases."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..formats import ReadLimits
from ..inspection import sanitize_for_output
from ..model import ValidationResult
from ..normalization import FieldMapping
from .data_access import Contract, SchemaInput, contract_dict


@dataclass(frozen=True, slots=True)
class ServiceError:
    """Stable, edge-safe description of one expected service failure."""

    code: str
    message: str
    action: str
    correlation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ServiceResult[T]:
    """Typed service outcome with no open handles or absolute artifact paths."""

    operation: str
    status: Literal["succeeded", "failed"]
    value: T | None = None
    error: ServiceError | None = None
    artifact: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", dict(self.provenance))
        if self.status == "succeeded" and self.error is not None:
            raise ValueError("successful service results cannot carry an error")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed service results require an error")

    @property
    def ok(self) -> bool:
        """Whether the application operation completed without an expected failure."""

        return self.status == "succeeded"

    def to_dict(self) -> dict[str, Any]:
        """Return a sanitized JSON-compatible service envelope."""

        value = self.value.to_dict() if hasattr(self.value, "to_dict") else self.value
        payload: dict[str, Any] = {
            "operation": self.operation,
            "status": self.status,
            "value": sanitize_for_output(value),
            "artifact": self.artifact,
            "provenance": sanitize_for_output(self.provenance),
        }
        if self.error is not None:
            payload["error"] = asdict(self.error)
        return payload


@dataclass(frozen=True, slots=True)
class DatasetRequest:
    """Input shared by tabular import, validation, and summary use cases."""

    data: Path
    schema: SchemaInput
    mapping: Path | None = None
    workspace: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", Path(self.data))
        if self.mapping is not None:
            object.__setattr__(self, "mapping", Path(self.mapping))
        if self.workspace is not None:
            object.__setattr__(self, "workspace", Path(self.workspace))


@dataclass(frozen=True, slots=True)
class ImportInspectRequest:
    """Input for bounded import and structural inspection."""

    data: Path
    schema: SchemaInput | None = None
    read_limits: ReadLimits = field(default_factory=ReadLimits)
    workspace: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", Path(self.data))
        if not isinstance(self.read_limits, ReadLimits):
            raise TypeError("read_limits must be a ReadLimits instance")
        if self.workspace is not None:
            object.__setattr__(self, "workspace", Path(self.workspace))


@dataclass(frozen=True, slots=True)
class ResolveSchemaRequest:
    """Input for local schema and explicit mapping resolution."""

    schema: SchemaInput
    mapping: Path | None = None

    def __post_init__(self) -> None:
        if self.mapping is not None:
            object.__setattr__(self, "mapping", Path(self.mapping))


@dataclass(frozen=True, slots=True)
class ConvertRequest:
    """Input for validated conversion; default HDF5 layout follows the data model."""

    data: Path
    schema: SchemaInput
    output: Path
    mapping: Path | None = None
    workspace: Path | None = None
    source_description: str | None = None
    force: bool = False
    allow_invalid: bool = False
    output_format: Literal["hdf5", "netcdf", "zarr", "parquet"] = "hdf5"

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", Path(self.data))
        object.__setattr__(self, "output", Path(self.output))
        if self.mapping is not None:
            object.__setattr__(self, "mapping", Path(self.mapping))
        if self.workspace is not None:
            object.__setattr__(self, "workspace", Path(self.workspace))


@dataclass(frozen=True, slots=True)
class ReportRequest:
    """Input for a rendered offline validation report."""

    data: Path
    schema: SchemaInput
    output: Path
    format: Literal["html", "markdown", "json"] = "html"
    workspace: Path | None = None
    force: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", Path(self.data))
        object.__setattr__(self, "output", Path(self.output))
        if self.workspace is not None:
            object.__setattr__(self, "workspace", Path(self.workspace))


@dataclass(frozen=True, slots=True)
class ComparisonRequest:
    """Input for comparing two JSON reports into an offline bundle."""

    left: Path
    right: Path
    output: Path
    workspace: Path | None = None
    force: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "left", Path(self.left))
        object.__setattr__(self, "right", Path(self.right))
        object.__setattr__(self, "output", Path(self.output))
        if self.workspace is not None:
            object.__setattr__(self, "workspace", Path(self.workspace))


@dataclass(frozen=True, slots=True)
class PlotRequest:
    """Input for a schema-driven PNG or SVG plot."""

    data: Path
    schema: SchemaInput
    output: Path
    kind: Literal[
        "stress-strain",
        "histogram",
        "grain-count",
        "phase-count",
        "field2d",
        "xy",
    ]
    field: str | None = None
    x: str | None = None
    y: str | None = None
    mapping: Path | None = None
    workspace: Path | None = None
    force: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", Path(self.data))
        object.__setattr__(self, "output", Path(self.output))
        if self.mapping is not None:
            object.__setattr__(self, "mapping", Path(self.mapping))
        if self.workspace is not None:
            object.__setattr__(self, "workspace", Path(self.workspace))


@dataclass(frozen=True, slots=True)
class SchemaDiffRequest:
    """Input for a rendered schema compatibility diff."""

    source: Path
    target: Path
    format: Literal["json", "markdown"] = "json"
    output: Path | None = None
    workspace: Path | None = None
    force: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", Path(self.source))
        object.__setattr__(self, "target", Path(self.target))
        if self.output is not None:
            object.__setattr__(self, "output", Path(self.output))
        if self.workspace is not None:
            object.__setattr__(self, "workspace", Path(self.workspace))


@dataclass(frozen=True, slots=True)
class ResolvedSchemaMapping:
    """Validated schema plus the explicit mappings selected by an edge."""

    schema: Contract
    mappings: tuple[FieldMapping, ...] = field(default_factory=tuple)
    drop_unmapped: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "mappings", tuple(self.mappings))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": contract_dict(self.schema),
            "mappings": [asdict(item) for item in self.mappings],
            "drop_unmapped": self.drop_unmapped,
        }


@dataclass(frozen=True, slots=True)
class ValidationSummary:
    """Validation and summary values produced from the same normalized dataset."""

    validation: ValidationResult
    summary: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", dict(self.summary))

    def to_dict(self) -> dict[str, Any]:
        return {"validation": self.validation.to_dict(), "summary": self.summary}


@dataclass(frozen=True, slots=True)
class ConversionOutcome:
    """Conversion result retaining validation findings and a safe artifact reference."""

    validation: ValidationResult
    artifact: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"validation": self.validation.to_dict(), "artifact": self.artifact}


@dataclass(frozen=True, slots=True)
class ReportOutcome:
    """Rendered report payload and its safe artifact reference."""

    report: dict[str, Any]
    artifact: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "report", dict(self.report))

    def to_dict(self) -> dict[str, Any]:
        return {"report": self.report, "artifact": self.artifact}


@dataclass(frozen=True, slots=True)
class ComparisonOutcome:
    """Comparison payload and its safe bundle reference."""

    comparison: dict[str, Any]
    artifact: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparison", dict(self.comparison))

    def to_dict(self) -> dict[str, Any]:
        return {"comparison": self.comparison, "artifact": self.artifact}


@dataclass(frozen=True, slots=True)
class PlotOutcome:
    """Plot validation findings and a safe artifact reference."""

    kind: str
    validation: ValidationResult
    artifact: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "validation": self.validation.to_dict(),
            "artifact": self.artifact,
        }


@dataclass(frozen=True, slots=True)
class SchemaDiffOutcome:
    """Schema diff payload and optional rendered text/artifact."""

    diff: dict[str, Any]
    rendered: str
    artifact: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "diff", dict(self.diff))

    def to_dict(self) -> dict[str, Any]:
        return {"diff": self.diff, "rendered": self.rendered, "artifact": self.artifact}
