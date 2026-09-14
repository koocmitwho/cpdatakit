"""Compare aggregate CPDataKit reports and write offline bundles."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from collections.abc import Mapping
from numbers import Real
from pathlib import Path
from typing import Any

from ._atomic import publish_directory, publish_file
from ._comparison_v2 import compare_scientific_statistics
from .exceptions import CPDataKitError, OutputExistsError
from .inspection import sanitize_for_output
from .reporting import render_report_json
from .schema_diff import diff_schemas, render_schema_diff_markdown

_STATISTICS = ("min", "max", "mean", "std")
_SCOPE_NOTE = (
    "This comparison covers declared schema, validation, structure, and descriptive aggregates. "
    "Physical and scientific equivalence require separate analysis."
)


def _require_report(value: object, side: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CPDataKitError(f"{side} report must be a mapping")
    return value


def _field_names(report: Mapping[str, Any]) -> list[str]:
    fields = report.get("fields", [])
    if not isinstance(fields, list):
        return []
    return [
        str(item["name"])
        for item in fields
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    ]


def _schema_summary(schema: object, digest: object) -> object:
    if not isinstance(schema, Mapping):
        return "not available"
    return {
        "profile": schema.get("profile", "not available"),
        "schema_version": schema.get("schema_version", "not available"),
        "sha256": digest,
    }


def _finite_number(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(float(value))


def _safe_value(value: object) -> object:
    return sanitize_for_output(value)


def _stats_mapping(report: Mapping[str, Any]) -> Mapping[str, Any]:
    statistics = report.get("statistics", {})
    if not isinstance(statistics, Mapping):
        return {}
    numeric_fields = statistics.get("numeric_fields", {})
    return numeric_fields if isinstance(numeric_fields, Mapping) else {}


def _metric_value(numeric_fields: Mapping[str, Any], field: str, metric: str) -> object:
    field_stats = numeric_fields.get(field)
    if isinstance(field_stats, Mapping):
        return field_stats.get(metric, "not available")
    return field_stats if metric == "value" else "not available"


def _compare_statistics(
    left: Mapping[str, Any], right: Mapping[str, Any], schema_diff: Mapping[str, Any] | None = None
) -> dict[str, list[dict[str, Any]]]:
    left_stats = _stats_mapping(left)
    right_stats = _stats_mapping(right)
    field_names = list(left_stats)
    field_names.extend(name for name in right_stats if name not in left_stats)
    changed: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []

    left_record_count = left.get("record_count", "not available")
    right_record_count = right.get("record_count", "not available")
    if _finite_number(left_record_count) and _finite_number(right_record_count):
        if left_record_count != right_record_count:
            changed.append(
                {
                    "field": "records",
                    "metric": "record_count",
                    "left": _safe_value(left_record_count),
                    "right": _safe_value(right_record_count),
                    "delta": right_record_count - left_record_count,
                }
            )
    elif _safe_value(left_record_count) != _safe_value(right_record_count):
        unavailable.append(
            {
                "field": "records",
                "metric": "record_count",
                "left": _safe_value(left_record_count),
                "right": _safe_value(right_record_count),
            }
        )

    for field in field_names:
        reason = _legacy_comparability_reason(field, schema_diff)
        for metric in _STATISTICS:
            left_value = _metric_value(left_stats, field, metric)
            right_value = _metric_value(right_stats, field, metric)
            if reason:
                unavailable.append(
                    {
                        "field": field,
                        "metric": metric,
                        "left": _safe_value(left_value),
                        "right": _safe_value(right_value),
                        "reason": reason,
                    }
                )
                continue
            if _finite_number(left_value) and _finite_number(right_value):
                if left_value != right_value:
                    changed.append(
                        {
                            "field": field,
                            "metric": metric,
                            "left": _safe_value(left_value),
                            "right": _safe_value(right_value),
                            "delta": right_value - left_value,
                        }
                    )
            elif _safe_value(left_value) != _safe_value(right_value):
                unavailable.append(
                    {
                        "field": field,
                        "metric": metric,
                        "left": _safe_value(left_value),
                        "right": _safe_value(right_value),
                    }
                )
    return {"changed": changed, "unavailable": unavailable}


def _legacy_comparability_reason(field: str, schema_diff: Mapping[str, Any] | None) -> str | None:
    if schema_diff is None:
        return None
    if schema_diff["source"]["profile"] != schema_diff["target"]["profile"]:
        return "Schema profiles differ; explicit field mapping is required"
    if schema_diff["conventions_changed"]:
        return "Schema conventions differ; numeric interpretation is unavailable"
    fields = schema_diff["fields"]
    if field in fields["added"] or field in fields["removed"]:
        return "Field declaration is absent from one schema"
    for item in fields["changed"]:
        changes = [key for key in item["changes"] if key not in {"aliases", "description"}]
        if item["name"] == field and changes:
            return "Field declaration differs: " + ", ".join(changes)
    return None


def _is_scientific_report(report: Mapping[str, Any]) -> bool:
    schema, statistics = report.get("schema"), report.get("statistics")
    return (isinstance(schema, Mapping) and schema.get("schema_version") == "2.0") or (
        isinstance(statistics, Mapping) and isinstance(statistics.get("fields"), Mapping)
    )


def compare_reports(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Compare two report payloads and return JSON-ready aggregate details."""
    left_report = _require_report(left, "left")
    right_report = _require_report(right, "right")
    left_schema = left_report.get("schema")
    right_schema = right_report.get("schema")
    schema_result = None
    if isinstance(left_schema, Mapping) and isinstance(right_schema, Mapping):
        schema_result = diff_schemas(left_schema, right_schema)
        schema_summary = {
            "classification": schema_result["classification"],
            "diff": schema_result,
        }
        left_schema_summary = _schema_summary(left_schema, schema_result["source"]["sha256"])
        right_schema_summary = _schema_summary(right_schema, schema_result["target"]["sha256"])
        changed_fields = schema_result["fields"]["changed"]
    else:
        schema_summary = {
            "classification": "not available",
            "diff": {"reason": "schema definition unavailable"},
        }
        left_schema_summary = _schema_summary(left_schema, "not available")
        right_schema_summary = _schema_summary(right_schema, "not available")
        changed_fields = []

    left_names = _field_names(left_report)
    right_names = _field_names(right_report)
    result = {
        "format": "CPDataKit comparison 1.0",
        "left": {
            "file": left_report.get("file", {}),
            "schema": left_schema_summary,
            "record_count": left_report.get("record_count", "not available"),
        },
        "right": {
            "file": right_report.get("file", {}),
            "schema": right_schema_summary,
            "record_count": right_report.get("record_count", "not available"),
        },
        "schema": schema_summary,
        "structure": {
            "fields_added": [name for name in right_names if name not in left_names],
            "fields_removed": [name for name in left_names if name not in right_names],
            "fields_changed": changed_fields,
        },
        "validation": {
            "left": left_report.get("validation", {}),
            "right": right_report.get("validation", {}),
        },
        "statistics": (
            compare_scientific_statistics(left_report, right_report)
            if _is_scientific_report(left_report) or _is_scientific_report(right_report)
            else _compare_statistics(left_report, right_report, schema_result)
        ),
        "scope_note": _SCOPE_NOTE,
    }
    return dict(sanitize_for_output(result))


def render_comparison_markdown(comparison: Mapping[str, Any]) -> str:
    """Render a comparison mapping as stable Markdown."""
    value = dict(sanitize_for_output(comparison))
    structure = value.get("structure", {})
    statistics = value.get("statistics", {})
    scientific = "incomparable" in statistics
    lines = [
        "# CPDataKit Comparison",
        "",
        "## Classification",
        "",
        f"- Schema: {value.get('schema', {}).get('classification', 'not available')}",
        "",
        "## Structure",
        "",
        "| Change | Fields |",
        "| --- | --- |",
        f"| Added | {', '.join(structure.get('fields_added', [])) or 'None'} |",
        f"| Removed | {', '.join(structure.get('fields_removed', [])) or 'None'} |",
        "",
        "## Validation",
        "",
        "| Side | Valid | Errors | Warnings |",
        "| --- | --- | ---: | ---: |",
    ]
    validation = value.get("validation", {})
    for side in ("left", "right"):
        item = validation.get(side, {}) if isinstance(validation, Mapping) else {}
        lines.append(
            f"| {side} | {item.get('valid', 'not available')} | "
            f"{len(item.get('errors', []))} | {len(item.get('warnings', []))} |"
        )
    lines.extend(
        [
            "",
            "## Statistics",
            "",
            "| Field | Metric | Left | Right | Delta |" + (" Unit |" if scientific else ""),
            "| --- | --- | ---: | ---: | ---: |" + (" --- |" if scientific else ""),
        ]
    )
    changed = statistics.get("changed", []) if isinstance(statistics, Mapping) else []
    for item in changed:
        lines.append(
            f"| {item.get('field')} | {item.get('metric')} | {item.get('left')} | "
            f"{item.get('right')} | {item.get('delta')} |"
            + (f" {item.get('unit')} |" if scientific else "")
        )
    if not changed:
        lines.append("| None | | | | |" + (" |" if scientific else ""))
    unavailable = statistics.get("unavailable", []) if isinstance(statistics, Mapping) else []
    lines.extend(
        [
            "",
            "## Unavailable statistics",
            "",
            "| Field | Metric | Left | Right |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in unavailable:
        lines.append(
            f"| {item.get('field')} | {item.get('metric')} | {item.get('left')} | "
            f"{item.get('right')} |"
        )
    if not unavailable:
        lines.append("| None | | | |")
    if "incomparable" in statistics:
        lines.extend(["", "## Incomparable statistics", "", "| Field | Reason |", "| --- | --- |"])
        for item in statistics["incomparable"]:
            lines.append(f"| {item.get('field')} | {item.get('reason')} |")
        if not statistics["incomparable"]:
            lines.append("| None | |")
    schema_diff = value.get("schema", {}).get("diff", {})
    if "dimensions" in schema_diff:
        lines.extend(
            [
                "",
                render_schema_diff_markdown(schema_diff).replace(
                    "# CPDataKit Schema Diff", "## Scientific schema details", 1
                ),
            ]
        )
    lines.extend(
        [
            "",
            "## Scope",
            "",
            str(value.get("scope_note", _SCOPE_NOTE)),
            "",
        ]
    )
    return "\n".join(lines)


def render_comparison_html(comparison: Mapping[str, Any]) -> str:
    """Render a comparison mapping as static, escaped HTML."""
    safe = dict(sanitize_for_output(comparison))
    payload = json.dumps(safe, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    from html import escape

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">',
            "<title>CPDataKit Comparison</title>",
            "<style>body{font-family:Arial,sans-serif;color:#222;margin:2rem;}"
            "pre{white-space:pre-wrap;background:#f6f8fa;padding:1rem;}"
            ".scope{border-left:.35rem solid #17365d;padding:.7rem 1rem;"
            "background:#f2f6fa;}</style>",
            "</head><body>",
            "<h1>CPDataKit Comparison</h1>",
            f"<pre>{escape(payload, quote=True)}</pre>",
            f'<p class="scope">{escape(str(safe.get("scope_note", _SCOPE_NOTE)), quote=True)}</p>',
            "</body></html>",
            "",
        ]
    )


def _member_payloads(comparison: Mapping[str, Any]) -> dict[str, str]:
    return {
        "comparison.json": render_report_json(comparison),
        "comparison.md": render_comparison_markdown(comparison),
        "comparison.html": render_comparison_html(comparison),
    }


def _publish_bundle(staged: Path, target: Path, *, force: bool) -> None:
    backup: Path | None = None
    try:
        if target.is_symlink():
            raise CPDataKitError("Comparison bundle output must not be a symbolic link")
        if target.exists():
            if not force:
                raise OutputExistsError(f"Output already exists: {target}")
            backup = Path(tempfile.mkdtemp(prefix=f".{target.name}.backup-", dir=target.parent))
            os.replace(target, backup / "previous")
        try:
            publish_directory(staged, target)
        except BaseException as failure:
            if backup is not None and (backup / "previous").exists():
                previous = backup / "previous"
                try:
                    if previous.is_dir():
                        publish_directory(previous, target)
                    else:
                        publish_file(previous, target)
                except BaseException as recovery_error:
                    raise CPDataKitError(
                        f"Cannot publish comparison bundle {target}: {failure}. "
                        f"Original output retained at {previous}; recovery failed: {recovery_error}"
                    ) from failure
            raise
    except BaseException:
        if backup is not None and not (backup / "previous").exists():
            backup.rmdir()
        raise
    else:
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)


def write_comparison_bundle(
    comparison: Mapping[str, Any],
    output: str | Path,
    *,
    force: bool = False,
) -> Path:
    """Write the comparison bundle to a directory."""
    target = Path(output)
    if target.exists() and not force:
        raise OutputExistsError(f"Output already exists: {target}; pass force=True to replace it")
    target.parent.mkdir(parents=True, exist_ok=True)
    members = _member_payloads(comparison)
    safe_comparison = dict(sanitize_for_output(comparison))
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        digests = []
        for name, content in members.items():
            path = temporary / name
            payload = content.encode("utf-8")
            path.write_bytes(payload)
            digests.append({"name": name, "sha256": hashlib.sha256(payload).hexdigest()})
        manifest = {
            "format": "CPDataKit comparison bundle 1.0",
            "members": digests,
            "left": safe_comparison.get("left", {}),
            "right": safe_comparison.get("right", {}),
            "schema": safe_comparison.get("schema", {}),
        }
        manifest_text = json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n"
        (temporary / "manifest.json").write_bytes(manifest_text.encode("utf-8"))
        _publish_bundle(temporary, target, force=force)
    except BaseException as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        if isinstance(exc, CPDataKitError):
            raise
        if isinstance(exc, OSError):
            raise CPDataKitError(f"Cannot write comparison bundle {target}: {exc}") from exc
        raise
    return target
