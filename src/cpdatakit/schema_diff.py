"""Compare validated schemas deterministically."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ._atomic import publish_file
from ._comparison_v2 import diff_scientific_schemas
from .exceptions import CPDataKitError, OutputExistsError, SchemaError
from .schema import ProfileSchema, schema_sha256, schema_to_canonical_json, validate_schema
from .schemas import ResolvedSchemaV2, SchemaV2

SchemaInput = str | Path | ProfileSchema | Mapping[str, Any] | SchemaV2 | ResolvedSchemaV2

_FIELD_PROPERTIES = (
    "dtype",
    "shape",
    "components",
    "unit",
    "required",
    "allow_missing",
    "minimum",
    "maximum",
    "index",
    "unique",
    "aliases",
    "role",
    "description",
)
_SAFE_FIELD_CHANGES = {"aliases", "description"}


def _field_changes(source: Any, target: Any) -> list[str]:
    return [name for name in _FIELD_PROPERTIES if getattr(source, name) != getattr(target, name)]


def _aliases_only_additive(source: Any, target: Any) -> bool:
    return set(source.aliases).issubset(target.aliases)


def _convention_changes(source: ProfileSchema, target: ProfileSchema) -> list[str]:
    missing = object()
    names = list(source.conventions)
    names.extend(name for name in target.conventions if name not in source.conventions)
    return [
        name
        for name in names
        if source.conventions.get(name, missing) != target.conventions.get(name, missing)
    ]


def _field_change_is_safe(source: Any, target: Any, changes: list[str]) -> bool:
    if not changes or not set(changes).issubset(_SAFE_FIELD_CHANGES):
        return False
    return "aliases" not in changes or _aliases_only_additive(source, target)


def _requires_mapping(
    source: ProfileSchema,
    target: ProfileSchema,
    added: list[str],
    removed: list[str],
    changed: list[dict[str, Any]],
    conventions_changed: list[str],
    extension_prefix_changed: bool,
) -> bool:
    if removed or conventions_changed or extension_prefix_changed:
        return True
    if source.profile != target.profile or source.schema_version != target.schema_version:
        return True
    if any(target.field_map()[name].required for name in added):
        return True
    return any(any(name not in _SAFE_FIELD_CHANGES for name in item["changes"]) for item in changed)


def diff_schemas(source: SchemaInput, target: SchemaInput) -> dict[str, Any]:
    """Compare two schemas and return a JSON-ready compatibility diff."""
    source_version, target_version = _schema_version(source), _schema_version(target)
    if source_version != target_version:
        raise SchemaError(
            f"Unsupported schema version combination: {source_version} and {target_version}. "
            "Cross-version comparison requires explicit schema and data mapping."
        )
    if source_version == "2.0":
        return diff_scientific_schemas(source, target)
    source_contract = validate_schema(source)
    target_contract = validate_schema(target)
    source_canonical = schema_to_canonical_json(source_contract)
    target_canonical = schema_to_canonical_json(target_contract)
    source_fields = source_contract.field_map()
    target_fields = target_contract.field_map()
    added = [name for name in target_fields if name not in source_fields]
    removed = [name for name in source_fields if name not in target_fields]
    changed: list[dict[str, Any]] = []
    for name in source_fields:
        if name not in target_fields:
            continue
        changes = _field_changes(source_fields[name], target_fields[name])
        if changes:
            changed.append({"name": name, "changes": changes})
    conventions_changed = _convention_changes(source_contract, target_contract)
    extension_prefix_changed = source_contract.extension_prefix != target_contract.extension_prefix
    field_changes_are_safe = all(
        _field_change_is_safe(
            source_fields[item["name"]], target_fields[item["name"]], item["changes"]
        )
        for item in changed
    )
    compatible = (
        source_contract.profile == target_contract.profile
        and source_contract.schema_version == target_contract.schema_version
        and not conventions_changed
        and not extension_prefix_changed
        and not removed
        and field_changes_are_safe
        and all(not target_fields[name].required for name in added)
    )
    if source_canonical == target_canonical:
        classification = "identical"
    elif compatible:
        classification = "backward-compatible"
    else:
        classification = "breaking"
    return {
        "source": {
            "profile": source_contract.profile,
            "schema_version": source_contract.schema_version,
            "sha256": schema_sha256(source_contract),
        },
        "target": {
            "profile": target_contract.profile,
            "schema_version": target_contract.schema_version,
            "sha256": schema_sha256(target_contract),
        },
        "classification": classification,
        "fields": {"added": added, "removed": removed, "changed": changed},
        "conventions_changed": conventions_changed,
        "extension_prefix_changed": extension_prefix_changed,
        "requires_explicit_data_mapping": _requires_mapping(
            source_contract,
            target_contract,
            added,
            removed,
            changed,
            conventions_changed,
            extension_prefix_changed,
        ),
    }


def _schema_version(source: SchemaInput) -> str | None:
    if isinstance(source, (ProfileSchema, SchemaV2, ResolvedSchemaV2)):
        return source.schema_version
    payload = source
    if isinstance(source, (str, Path)):
        if not Path(source).is_file():
            return validate_schema(source).schema_version
        try:
            payload = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise SchemaError(f"Cannot read schema {source}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise SchemaError("Schema root must be a JSON object")
    return payload.get("schema_version")


def render_schema_diff_json(diff: Mapping[str, Any]) -> str:
    """Render a schema diff as JSON with stable key order."""
    return json.dumps(diff, indent=2, sort_keys=True, allow_nan=False) + "\n"


def render_schema_diff_markdown(diff: Mapping[str, Any]) -> str:
    """Render a schema diff as stable Markdown."""
    source = diff.get("source", {})
    target = diff.get("target", {})
    fields = diff.get("fields", {})
    changed = fields.get("changed", []) if isinstance(fields, Mapping) else []
    conventions = diff.get("conventions_changed", [])
    lines = [
        "# CPDataKit Schema Diff",
        "",
        "## Source",
        "",
        f"- Profile: {source.get('profile')}",
        f"- Schema version: {source.get('schema_version')}",
        f"- SHA-256: {source.get('sha256')}",
        "",
        "## Target",
        "",
        f"- Profile: {target.get('profile')}",
        f"- Schema version: {target.get('schema_version')}",
        f"- SHA-256: {target.get('sha256')}",
        "",
        "## Classification",
        "",
        f"- Result: {diff.get('classification')}",
        f"- Requires explicit data mapping: {diff.get('requires_explicit_data_mapping')}",
        f"- Extension prefix changed: {diff.get('extension_prefix_changed')}",
        "",
        "## Fields",
        "",
        "| Change | Field |",
        "| --- | --- |",
    ]
    for name in fields.get("added", []) if isinstance(fields, Mapping) else []:
        lines.append(f"| Added | {name} |")
    for name in fields.get("removed", []) if isinstance(fields, Mapping) else []:
        lines.append(f"| Removed | {name} |")
    if (
        not fields.get("added") and not fields.get("removed")
        if isinstance(fields, Mapping)
        else True
    ):
        lines.append("| None | |")
    lines.extend(["", "## Changed properties", "", "| Field | Properties |", "| --- | --- |"])
    if changed:
        lines.extend(
            f"| {item.get('name')} | {', '.join(item.get('changes', []))} |" for item in changed
        )
    else:
        lines.append("| None | |")
    lines.extend(["", "## Conventions", ""])
    lines.append(", ".join(conventions) if conventions else "None")
    for section in ("dimensions", "coordinates", "variables"):
        if section not in diff:
            continue
        details = diff[section]
        lines.extend(
            [
                "",
                f"## {section.title()}",
                "",
                f"- Order changed: {details['order_changed']}",
                "",
                "| Change | Declaration | Properties |",
                "| --- | --- | --- |",
            ]
        )
        for change in ("added", "removed"):
            lines.extend(f"| {change.title()} | {name} | |" for name in details[change])
        lines.extend(
            f"| Changed | {item['name']} | {', '.join(item['changes'])} |"
            for item in details["changed"]
        )
        if not any(details[key] for key in ("added", "removed", "changed")):
            lines.append("| None | | |")
    lines.extend(
        [
            "",
            "## Scope",
            "",
            "This command compares declared schema contracts. Data migration and HDF5 rewrites are "
            "separate operations.",
            "",
        ]
    )
    return "\n".join(lines)


def write_schema_diff(
    diff: Mapping[str, Any],
    output: str | Path,
    *,
    format: str = "json",
    force: bool = False,
) -> Path:
    """Write a schema diff and protect an existing output by default."""
    if format == "json":
        rendered = render_schema_diff_json(diff)
    elif format == "markdown":
        rendered = render_schema_diff_markdown(diff)
    else:
        raise CPDataKitError(f"Unsupported schema diff format: {format!r}")
    target = Path(output)
    if target.exists() and not force:
        raise OutputExistsError(f"Output already exists: {target}; pass --force to replace it")
    staged = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.", dir=target.parent, delete=False
        ) as stream:
            staged = Path(stream.name)
        staged.write_text(rendered, encoding="utf-8")
        publish_file(staged, target, force=force)
    except OSError as exc:
        raise CPDataKitError(f"Cannot write schema diff output {target}: {exc}") from exc
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return target
