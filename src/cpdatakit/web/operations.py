"""HTTP output operations using the shared staged artifact transaction."""

from typing import Annotated

from fastapi import Form, Request
from fastapi.responses import Response

from ..application.contracts import ComparisonRequest, ConvertRequest, PlotRequest, ReportRequest
from ..exceptions import CatalogError, CPDataKitError, SchemaError
from ..schema import ProfileSchema
from .outputs import convert_registered, produce_registered


def install_output_operations(
    app,
    *,
    workspace_path,
    dataset_path,
    output_path,
    existing_project_file,
    require_csrf,
    queue_job,
    artifact_registration,
    select_schema,
    store_mapping,
    convert_and_write,
    build_report,
    plot_declared_fields,
    compare_reports,
):
    from .app import _json_error

    @app.post("/api/projects/{project_id}/convert")
    def convert_project(
        request: Request,
        project_id: int,
        dataset_id: Annotated[int, Form(...)],
        output_name: Annotated[str, Form(alias="output")],
        schema_name: Annotated[str, Form(alias="schema")] = "curve",
        force: Annotated[bool, Form()] = False,
        output_format: Annotated[str, Form()] = "hdf5",
        mapping_json: Annotated[str, Form()] = "",
        csrf_token_form: Annotated[str | None, Form(alias="csrf_token")] = None,
    ) -> Response:
        csrf_error = require_csrf(request, csrf_token_form)
        if csrf_error is not None:
            return csrf_error
        try:
            source = dataset_path(project_id, dataset_id)
            target = output_path(project_id, output_name)
        except (CatalogError, ValueError):
            return _json_error(
                400,
                "path_rejected",
                "The dataset or output path is outside the project workspace.",
                "Choose an existing dataset and a relative output path.",
            )
        try:
            schema = select_schema(app, project_id, schema_name)
        except (SchemaError, CatalogError):
            return _json_error(
                400,
                "unsupported_schema",
                "The selected schema is unavailable in this project.",
                "Choose a bundled profile or upload a project schema.",
            )
        if output_format not in {"hdf5", "netcdf", "zarr", "parquet"}:
            return _json_error(
                400,
                "unsupported_format",
                "Unknown output format.",
                "Choose HDF5, NetCDF, Zarr or Parquet.",
            )
        if target.exists() and not force:
            return _json_error(
                409,
                "overwrite_confirmation",
                "The requested output already exists.",
                "Confirm overwrite explicitly before retrying.",
            )

        try:
            mapping = store_mapping(app, project_id, mapping_json)
        except (CPDataKitError, ValueError):
            return _json_error(
                400,
                "invalid_mapping",
                "Mapping JSON is invalid.",
                "Preview the mapping before conversion.",
            )

        def work(cancel) -> dict[str, object]:
            if cancel.is_set():
                return {"status": "cancelled"}
            result = convert_registered(
                ConvertRequest(
                    data=source,
                    schema=schema,
                    output=target,
                    workspace=workspace_path,
                    force=force,
                    output_format=output_format,
                    mapping=mapping,
                ),
                cancel,
                convert=convert_and_write,
                register=lambda path, expected_sha256=None: artifact_registration(
                    project_id,
                    path,
                    expected_sha256=expected_sha256,
                    kind="convert",
                    metadata={"operation": "convert_and_write", "schema": schema_name},
                ),
            )
            return result.to_dict()

        return queue_job(
            "convert",
            work,
            project_id=project_id,
            input_path=source,
            output_path_value=target,
        )

    @app.post("/api/projects/{project_id}/report")
    def report_project(
        request: Request,
        project_id: int,
        dataset_id: Annotated[int, Form(...)],
        output_name: Annotated[str, Form(alias="output")],
        schema_name: Annotated[str, Form(alias="schema")] = "curve",
        format_name: Annotated[str, Form(alias="format")] = "html",
        force: Annotated[bool, Form()] = False,
        csrf_token_form: Annotated[str | None, Form(alias="csrf_token")] = None,
    ) -> Response:
        csrf_error = require_csrf(request, csrf_token_form)
        if csrf_error is not None:
            return csrf_error
        if format_name not in {"html", "markdown", "json"}:
            return _json_error(
                400,
                "invalid_report_request",
                "The report schema or format is not supported.",
                "Choose html, markdown, or json output.",
            )
        try:
            schema = select_schema(app, project_id, schema_name)
        except (SchemaError, CatalogError):
            return _json_error(
                400,
                "unsupported_schema",
                "Selected schema is unavailable.",
                "Choose a project schema or bundled profile.",
            )
        try:
            source = dataset_path(project_id, dataset_id)
            target = output_path(project_id, output_name)
        except (CatalogError, ValueError):
            return _json_error(
                400,
                "path_rejected",
                "The dataset or output path is outside the project workspace.",
                "Choose an existing dataset and a relative output path.",
            )
        if target.exists() and not force:
            return _json_error(
                409,
                "overwrite_confirmation",
                "The requested output already exists.",
                "Confirm overwrite explicitly before retrying.",
            )

        def work(cancel) -> dict[str, object]:
            result = produce_registered(
                ReportRequest(
                    data=source,
                    schema=schema,
                    output=target,
                    format=format_name,
                    workspace=workspace_path,
                    force=force,
                ),
                cancel,
                produce=build_report,
                operation="build_report",
                register=lambda path, expected_sha256=None: artifact_registration(
                    project_id,
                    path,
                    expected_sha256=expected_sha256,
                    kind="report",
                    metadata={"operation": "build_report", "format": format_name},
                ),
            )
            return result.to_dict()

        return queue_job(
            "report",
            work,
            project_id=project_id,
            input_path=source,
            output_path_value=target,
        )

    @app.post("/api/projects/{project_id}/plot")
    def plot_project(
        request: Request,
        project_id: int,
        dataset_id: Annotated[int, Form(...)],
        kind: Annotated[str, Form(...)],
        output_name: Annotated[str, Form(alias="output")],
        schema_name: Annotated[str, Form(alias="schema")] = "curve",
        field: Annotated[str | None, Form()] = None,
        x: Annotated[str | None, Form()] = None,
        y: Annotated[str | None, Form()] = None,
        force: Annotated[bool, Form()] = False,
        csrf_token_form: Annotated[str | None, Form(alias="csrf_token")] = None,
    ) -> Response:
        csrf_error = require_csrf(request, csrf_token_form)
        if csrf_error is not None:
            return csrf_error
        if kind not in {
            "stress-strain",
            "histogram",
            "grain-count",
            "phase-count",
            "field2d",
            "xy",
        }:
            return _json_error(
                400,
                "invalid_plot_request",
                "The plot schema or kind is not supported.",
                "Choose a declared plot kind.",
            )
        try:
            schema = select_schema(app, project_id, schema_name)
            if not isinstance(schema, (str, ProfileSchema)):
                raise SchemaError("Plots currently require tabular data and schema 1.0.")
        except (SchemaError, CatalogError):
            return _json_error(
                400,
                "unsupported_schema",
                "Plot requires a tabular schema.",
                "Choose a bundled profile or a custom schema 1.0.",
            )
        try:
            source = dataset_path(project_id, dataset_id)
            target = output_path(project_id, output_name)
        except (CatalogError, ValueError):
            return _json_error(
                400,
                "path_rejected",
                "The dataset or output path is outside the project workspace.",
                "Choose an existing dataset and a relative output path.",
            )
        if target.exists() and not force:
            return _json_error(
                409,
                "overwrite_confirmation",
                "The requested output already exists.",
                "Confirm overwrite explicitly before retrying.",
            )

        def work(cancel) -> dict[str, object]:
            result = produce_registered(
                PlotRequest(
                    data=source,
                    schema=schema,
                    output=target,
                    kind=kind,
                    field=field,
                    x=x,
                    y=y,
                    workspace=workspace_path,
                    force=force,
                ),
                cancel,
                produce=plot_declared_fields,
                operation="plot_declared_fields",
                register=lambda path, expected_sha256=None: artifact_registration(
                    project_id,
                    path,
                    expected_sha256=expected_sha256,
                    kind="plot",
                    metadata={"operation": "plot_declared_fields", "kind": kind},
                ),
            )
            return result.to_dict()

        return queue_job(
            "plot",
            work,
            project_id=project_id,
            input_path=source,
            output_path_value=target,
        )

    @app.post("/api/projects/{project_id}/compare")
    def compare_project(
        request: Request,
        project_id: int,
        left_name: Annotated[str, Form(alias="left")],
        right_name: Annotated[str, Form(alias="right")],
        output_name: Annotated[str, Form(alias="output")],
        force: Annotated[bool, Form()] = False,
        csrf_token_form: Annotated[str | None, Form(alias="csrf_token")] = None,
    ) -> Response:
        csrf_error = require_csrf(request, csrf_token_form)
        if csrf_error is not None:
            return csrf_error
        try:
            left = existing_project_file(project_id, left_name)
            right = existing_project_file(project_id, right_name)
            target = output_path(project_id, output_name)
        except (CatalogError, ValueError):
            return _json_error(
                400,
                "path_rejected",
                "The comparison inputs or output path is outside the project workspace.",
                "Choose existing project files and a relative output path.",
            )
        if target.exists() and not force:
            return _json_error(
                409,
                "overwrite_confirmation",
                "The requested comparison bundle already exists.",
                "Confirm overwrite explicitly before retrying.",
            )

        def work(cancel) -> dict[str, object]:
            result = produce_registered(
                ComparisonRequest(
                    left=left,
                    right=right,
                    output=target,
                    workspace=workspace_path,
                    force=force,
                ),
                cancel,
                produce=compare_reports,
                operation="compare_reports",
                register=lambda path, expected_sha256=None: artifact_registration(
                    project_id,
                    path,
                    expected_sha256=expected_sha256,
                    kind="compare",
                    metadata={"operation": "compare_reports"},
                ),
            )
            return result.to_dict()

        return queue_job(
            "compare",
            work,
            project_id=project_id,
            input_path=left,
            output_path_value=target,
        )
