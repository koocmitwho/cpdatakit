"""Command-line interface for CPDataKit."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import webbrowser
from pathlib import Path
from typing import Any

from . import __version__
from .application import (
    ComparisonRequest,
    ConvertRequest,
    DatasetRequest,
    ImportInspectRequest,
    PlotRequest,
    ReportRequest,
    SchemaDiffRequest,
    convert_and_write,
    diff_schema_contracts,
    draft_schema,
    import_and_inspect,
    plot_declared_fields,
    preview_mapping,
    run_batch,
    validate_and_summarize,
)
from .application import (
    build_report as build_report_service,
)
from .application import (
    compare_reports as compare_reports_service,
)
from .exceptions import CPDataKitError
from .inspection import (
    render_inspection_json,
    render_inspection_text,
    sanitize_error_message,
    write_inspection,
)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("data", type=Path, help="Input CSV, JSON records, or CPDataKit HDF5")
    parser.add_argument("--schema", required=True, help="Built-in profile or JSON schema path")
    parser.add_argument(
        "--mapping",
        type=Path,
        help="JSON file declaring explicit source/target fields and optional unit conversions",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cpdatakit",
        description="Validate and process declared scientific and engineering data contracts.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--debug", action="store_true", help="Show tracebacks for unexpected failures"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    batch = commands.add_parser("batch", help="Run reproducible per-file conversions from JSON")
    batch.add_argument("config", type=Path)
    batch.add_argument("--manifest", type=Path, required=True)
    batch.add_argument(
        "--retry", action="store_true", help="Resume using verified results in this manifest"
    )
    validate = commands.add_parser("validate", help="Validate schema conformance")
    _common(validate)
    validate.add_argument("--json-output", type=Path, help="Write the validation report as JSON")
    validate.add_argument("--force", action="store_true", help="Replace an existing report")
    summary = commands.add_parser("summary", help="Print a descriptive JSON summary")
    _common(summary)
    summary.add_argument("--json-output", type=Path, help="Write the summary as JSON")
    summary.add_argument("--force", action="store_true", help="Replace an existing report")
    convert = commands.add_parser("convert", help="Convert to CPDataKit HDF5")
    _common(convert)
    convert.add_argument("--output", required=True, type=Path)
    convert.add_argument("--source-description")
    convert.add_argument("--force", action="store_true")
    plot = commands.add_parser("plot", help="Create a PNG or SVG plot")
    _common(plot)
    plot.add_argument(
        "--kind",
        required=True,
        choices=["stress-strain", "histogram", "grain-count", "phase-count", "field2d", "xy"],
    )
    plot.add_argument("--field", help="Declared numeric field for histogram")
    plot.add_argument("--x", help="Declared scalar numeric x-axis field for xy")
    plot.add_argument("--y", help="Declared scalar numeric y-axis field for xy")
    plot.add_argument("--output", required=True, type=Path)
    plot.add_argument("--force", action="store_true")
    inspect = commands.add_parser("inspect", help="Inspect a file and optionally check a schema")
    inspect.add_argument("data", type=Path, help="Input CSV, JSON records, or HDF5")
    inspect.add_argument("--schema", help="Optional built-in profile or JSON schema path")
    inspect.add_argument("--format", choices=["text", "json"], default="text")
    inspect.add_argument("--output", type=Path, help="Write the inspection result to a file")
    inspect.add_argument("--force", action="store_true", help="Replace an existing output")
    report = commands.add_parser("report", help="Write an offline validation report")
    report.add_argument("data", type=Path, help="Input CSV, JSON records, or HDF5")
    report.add_argument("--schema", required=True, help="Built-in profile or JSON schema path")
    report.add_argument("--format", choices=["html", "markdown", "json"], default="html")
    report.add_argument("--output", required=True, type=Path)
    report.add_argument("--force", action="store_true", help="Replace an existing output")
    compare = commands.add_parser("compare", help="Compare two JSON validation reports")
    compare.add_argument("left", type=Path, help="Left JSON report")
    compare.add_argument("right", type=Path, help="Right JSON report")
    compare.add_argument("--output", required=True, type=Path)
    compare.add_argument("--force", action="store_true", help="Replace an existing bundle")
    schema = commands.add_parser("schema", help="Compare schema contracts")
    schema_commands = schema.add_subparsers(dest="schema_command", required=True)
    draft = schema_commands.add_parser(
        "draft", help="Draft a schema from observable file structure"
    )
    draft.add_argument("data", type=Path)
    draft.add_argument("--output", type=Path)
    draft.add_argument("--force", action="store_true")
    mapping = commands.add_parser("mapping", help="Preview declared field and unit mappings")
    mapping_commands = mapping.add_subparsers(dest="mapping_command", required=True)
    preview = mapping_commands.add_parser(
        "preview", help="Normalize and validate without writing data"
    )
    _common(preview)
    preview.add_argument("--output", type=Path)
    preview.add_argument("--force", action="store_true")
    schema_diff = schema_commands.add_parser("diff", help="Compare two schema contracts")
    schema_diff.add_argument("source", type=Path)
    schema_diff.add_argument("target", type=Path)
    schema_diff.add_argument("--format", choices=["json", "markdown"], default="json")
    schema_diff.add_argument("--output", type=Path)
    schema_diff.add_argument("--force", action="store_true", help="Replace an existing output")
    ui = commands.add_parser("ui", help="Open the local web workbench")
    ui.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd() / ".cpdatakit",
        help="Workspace directory for the local catalog and project files",
    )
    ui.add_argument(
        "--host",
        choices=["127.0.0.1", "localhost", "::1"],
        default="127.0.0.1",
        help="Loopback interface to bind",
    )
    ui.add_argument(
        "--port",
        type=int,
        default=0,
        help="TCP port; 0 selects an available port",
    )
    ui.add_argument("--no-browser", action="store_true", help="Do not open a browser window")
    return parser


def _write_json(payload: dict[str, Any], target: Path | None, force: bool) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)
    if target is None:
        print(rendered)
        return
    if target.exists() and not force:
        raise CPDataKitError(f"Output already exists: {target}; pass --force to replace it")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered + "\n", encoding="utf-8")


def _inspection_status(result: dict[str, Any]) -> int:
    schema = result.get("schema", {})
    validation = schema.get("validation", {}) if isinstance(schema, dict) else {}
    risks = result.get("risks", {})
    has_validation_errors = isinstance(validation, dict) and bool(validation.get("errors"))
    has_risks = isinstance(risks, dict) and bool(
        risks.get("missing_values") or risks.get("structural")
    )
    return 1 if has_validation_errors or has_risks else 0


def _run_inspect(args: argparse.Namespace) -> int:
    service_result = import_and_inspect(ImportInspectRequest(data=args.data, schema=args.schema))
    if not service_result.ok or service_result.value is None:
        if service_result.error is None:  # pragma: no cover - ServiceResult enforces this
            raise CPDataKitError("Inspection service failed without an error")
        raise CPDataKitError(service_result.error.message)
    result = service_result.value
    if args.output is None:
        rendered = (
            render_inspection_json(result)
            if args.format == "json"
            else render_inspection_text(result)
        )
        print(rendered, end="")
    else:
        write_inspection(result, args.output, format=args.format, force=args.force)
        print(args.output.name)
    return _inspection_status(result)


def _run_report(args: argparse.Namespace) -> int:
    service_result = build_report_service(
        ReportRequest(
            data=args.data,
            schema=args.schema,
            output=args.output,
            format=args.format,
            force=args.force,
        )
    )
    if not service_result.ok or service_result.value is None:
        if service_result.error is None:  # pragma: no cover - ServiceResult enforces this
            raise CPDataKitError("Report service failed without an error")
        raise CPDataKitError(service_result.error.message)
    report = service_result.value.report
    print(args.output.name)
    return 0 if report["validation"]["valid"] else 1


def _run_schema_diff(args: argparse.Namespace) -> int:
    service_result = diff_schema_contracts(
        SchemaDiffRequest(
            source=args.source,
            target=args.target,
            format=args.format,
            output=args.output,
            force=args.force,
        )
    )
    if not service_result.ok or service_result.value is None:
        if service_result.error is None:  # pragma: no cover - ServiceResult enforces this
            raise CPDataKitError("Schema diff service failed without an error")
        raise CPDataKitError(service_result.error.message)
    if args.output is None:
        print(service_result.value.rendered, end="")
    else:
        print(args.output.name)
    return 0


def _run_compare(args: argparse.Namespace) -> int:
    service_result = compare_reports_service(
        ComparisonRequest(
            left=args.left,
            right=args.right,
            output=args.output,
            force=args.force,
        )
    )
    if not service_result.ok:
        if service_result.error is None:  # pragma: no cover - ServiceResult enforces this
            raise CPDataKitError("Comparison service failed without an error")
        raise CPDataKitError(service_result.error.message)
    print(args.output)
    return 0


def _available_port(host: str) -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def _run_ui(args: argparse.Namespace) -> int:
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise CPDataKitError("The local UI can bind only to a loopback host")
    if args.port < 0 or args.port > 65_535:
        raise CPDataKitError("UI port must be between 0 and 65535")
    try:
        import uvicorn
    except ImportError as exc:
        raise CPDataKitError(
            "The local UI dependencies are unavailable; install the CPDataKit UI dependencies."
        ) from exc
    from .web import create_app

    port = args.port or _available_port(args.host)
    app = create_app(args.workspace)
    display_host = f"[{args.host}]" if ":" in args.host else args.host
    url = f"http://{display_host}:{port}"
    print(url)
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=port, log_level="debug" if args.debug else "info")
    return 0


def _run(args: argparse.Namespace) -> int:
    if args.command == "batch":
        result = run_batch(args.config, args.manifest, retry=args.retry)
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.ok else 1 if result.value is not None else 2
    if args.command == "mapping" or (args.command == "schema" and args.schema_command == "draft"):
        result = (
            draft_schema(ImportInspectRequest(args.data))
            if args.command == "schema"
            else preview_mapping(DatasetRequest(args.data, args.schema, args.mapping))
        )
        if not result.ok:
            raise CPDataKitError(result.error.message)
        _write_json(result.value, args.output, args.force)
        return 0 if args.command == "schema" or result.value["validation"]["valid"] else 1
    if args.command == "ui":
        return _run_ui(args)
    if args.command == "compare":
        return _run_compare(args)
    if args.command == "schema":
        return _run_schema_diff(args)
    if args.command == "inspect":
        return _run_inspect(args)
    if args.command == "report":
        return _run_report(args)
    if args.command == "plot" and args.kind == "xy" and (not args.x or not args.y):
        raise CPDataKitError("--x and --y are required for xy")
    if args.command in {"validate", "summary"}:
        service_result = validate_and_summarize(
            DatasetRequest(data=args.data, schema=args.schema, mapping=args.mapping)
        )
        if not service_result.ok or service_result.value is None:
            if service_result.error is None:  # pragma: no cover - ServiceResult enforces this
                raise CPDataKitError("Validation service failed without an error")
            raise CPDataKitError(service_result.error.message)
        validation = service_result.value.validation
        if args.command == "validate":
            _write_json(validation.to_dict(), args.json_output, args.force)
        else:
            _write_json(service_result.value.summary, args.json_output, args.force)
        return 0 if validation.valid else 1
    if args.command == "convert":
        service_result = convert_and_write(
            ConvertRequest(
                data=args.data,
                schema=args.schema,
                output=args.output,
                mapping=args.mapping,
                source_description=args.source_description,
                force=args.force,
            )
        )
        if service_result.value is not None and not service_result.value.validation.valid:
            print(json.dumps(service_result.value.validation.to_dict(), indent=2), file=sys.stderr)
            return 1
        if not service_result.ok:
            if service_result.error is None:  # pragma: no cover - ServiceResult enforces this
                raise CPDataKitError("Conversion service failed without an error")
            raise CPDataKitError(service_result.error.message)
        print(args.output)
        return 0
    service_result = plot_declared_fields(
        PlotRequest(
            data=args.data,
            schema=args.schema,
            output=args.output,
            kind=args.kind,
            field=args.field,
            x=args.x,
            y=args.y,
            mapping=args.mapping,
            force=args.force,
        )
    )
    if service_result.value is not None and not service_result.value.validation.valid:
        print(json.dumps(service_result.value.validation.to_dict(), indent=2), file=sys.stderr)
        return 1
    if not service_result.ok:
        if service_result.error is None:  # pragma: no cover - ServiceResult enforces this
            raise CPDataKitError("Plot service failed without an error")
        raise CPDataKitError(service_result.error.message)
    print(args.output)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit status."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except CPDataKitError as exc:
        if args.debug:
            raise
        parser.print_usage(sys.stderr)
        message = (
            sanitize_error_message(exc)
            if args.command in {"inspect", "report", "schema", "compare"}
            else str(exc)
        )
        print(f"{parser.prog}: error: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
