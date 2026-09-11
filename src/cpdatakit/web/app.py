"""Minimal local workbench built on the application service boundary."""

from __future__ import annotations

import html
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Final

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..application import (
    CapabilityRequest,
    ComparisonRequest,
    ConvertRequest,
    DatasetRequest,
    ImportInspectRequest,
    PlotRequest,
    ReadLimits,
    ReportRequest,
    build_report,
    compare_reports,
    convert_and_write,
    discover_capabilities,
    import_and_inspect,
    plot_declared_fields,
    validate_and_summarize,
)
from ..catalog import ProjectRecord, SQLiteCatalog
from ..catalog.sqlite import ArtifactRecord
from ..exceptions import CatalogError, CPDataKitError, JobError, SchemaError
from ..jobs import JobManager
from ..jobs.manager import CommittedResult, JobFailure
from ..provenance import sha256_file
from ..schema import ProfileSchema
from .artifacts import register_snapshot, with_registered_artifact
from .authoring import install_authoring, store_mapping
from .outputs import convert_registered
from .slices import install_slices
from .workbench import install_workbench, select_schema

_SESSION_COOKIE: Final = "cpdatakit_session"
_CSRF_HEADER: Final = "X-CSRF-Token"
_LOCAL_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "::1"})
_ARCHIVE_SUFFIXES: Final = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
_DEFAULT_UPLOAD_BYTES: Final = 64 * 1024 * 1024
_DEFAULT_PREVIEW_BYTES: Final = 64 * 1024 * 1024


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _host_name(raw_host: str) -> str:
    value = raw_host.strip().lower()
    if value.startswith("["):
        closing = value.find("]")
        return value[1:closing] if closing > 0 else value
    if value.count(":") == 1:
        return value.rsplit(":", 1)[0]
    return value


def _safe_upload_name(raw_name: str | None) -> str:
    if not raw_name:
        raise ValueError("Uploaded file must have a name")
    if "\x00" in raw_name:
        raise ValueError("Uploaded file name is invalid")
    normalized = raw_name.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    if name in {"", ".", ".."}:
        raise ValueError("Uploaded file must have a valid name")
    return name


def _within(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("Path escapes the workspace") from exc
    return resolved_candidate


def _safe_project_path(workspace: Path, project_root: Path, raw_name: str) -> Path:
    if not isinstance(raw_name, str) or not raw_name.strip() or "\x00" in raw_name:
        raise ValueError("A non-empty relative path is required")
    normalized = raw_name.strip().replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part)
    if (
        not parts
        or normalized.startswith("/")
        or any(part in {".", ".."} for part in parts)
        or ":" in parts[0]
    ):
        raise ValueError("Path must remain inside the project workspace")
    return _within(project_root, _within(workspace, project_root.joinpath(*parts)))


def _job_payload(record) -> dict[str, object]:
    status = record.status.value if hasattr(record.status, "value") else record.status
    return {
        "id": record.id,
        "operation": record.operation,
        "status": status,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "input_filename": record.input_filename,
        "output_filename": record.output_filename,
        "operation_log": list(record.operation_log),
        "result": getattr(record, "result", None),
        "error": record.error,
    }


def _json_error(status_code: int, code: str, message: str, action: str) -> JSONResponse:
    return JSONResponse(
        {
            "status": "failed",
            "detail": message,
            "error": {"code": code, "message": message, "action": action},
        },
        status_code=status_code,
    )


def _set_session_cookie(response: Response, session_token: str) -> Response:
    response.set_cookie(
        _SESSION_COOKIE,
        session_token,
        httponly=True,
        samesite="lax",
    )
    return response


def _render_home(projects: tuple[ProjectRecord, ...], csrf_token: str) -> str:
    project_items = "".join(
        f'<li data-project-id="{project.id}">{html.escape(project.name)}</li>'
        for project in projects
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>CPDataKit local workbench</title>
    <style>
      :root {{ color-scheme: light; font-family: system-ui, sans-serif; }}
      body {{ margin: 2rem auto; max-width: 56rem; padding: 0 1rem; color: #202124; }}
      section {{ border: 1px solid #d8dbe0; border-radius: .5rem; margin: 1rem 0; padding: 1rem; }}
      label {{ display: block; margin-bottom: .35rem; }}
      input, button {{ font: inherit; padding: .45rem .6rem; }}
      button {{ cursor: pointer; }}
    </style>
  </head>
  <body>
    <h1>CPDataKit local workbench</h1>
    <p>Local-only project workspace for bounded inspection and validation.</p>
    <section>
      <h2>Create project</h2>
      <form method="post" action="/api/projects">
        <input type="hidden" name="csrf_token" value="{html.escape(csrf_token, quote=True)}">
        <label for="project-name">Project name</label>
        <input id="project-name" name="name" required maxlength="200">
        <button type="submit">Create project</button>
      </form>
    </section>
    <section>
      <h2>Projects</h2>
      <ul>{project_items or "<li>No projects yet.</li>"}</ul>
    </section>
  </body>
</html>"""


def create_app(
    workspace: str | Path,
    *,
    max_upload_bytes: int = _DEFAULT_UPLOAD_BYTES,
    max_preview_bytes: int = _DEFAULT_PREVIEW_BYTES,
) -> FastAPI:
    """Create the local UI application rooted at one explicit workspace."""

    workspace_path = Path(workspace).expanduser().resolve(strict=False)
    workspace_path.mkdir(parents=True, exist_ok=True)
    upload_limit = _positive_int(max_upload_bytes, "max_upload_bytes")
    preview_limit = _positive_int(max_preview_bytes, "max_preview_bytes")
    catalog = SQLiteCatalog(workspace_path / "catalog.sqlite3", workspace_path)
    catalog.initialize()
    jobs = JobManager()
    job_projects: dict[str, int] = {}
    catalog_job_lock = threading.Lock()
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)

    app = FastAPI(title="CPDataKit local workbench", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).with_name("static"))),
        name="static",
    )
    app.state.catalog = catalog
    app.state.jobs = jobs
    app.state.workspace = workspace_path
    app.state.upload_limit = upload_limit
    app.state.preview_limit = preview_limit

    @app.middleware("http")
    async def local_host_guard(request: Request, call_next):
        host = _host_name(request.headers.get("host", ""))
        if host not in _LOCAL_HOSTS:
            return _json_error(
                400,
                "invalid_host",
                "The request Host header is not allowed for the local UI.",
                "Use the loopback URL opened by CPDataKit.",
            )
        return await call_next(request)

    def require_csrf(request: Request, form_token: str | None = None) -> Response | None:
        if request.cookies.get(_SESSION_COOKIE) != session_token:
            return _json_error(
                403,
                "invalid_session",
                "The local UI session is missing or invalid.",
                "Reload the local UI and retry the operation.",
            )
        supplied = request.headers.get(_CSRF_HEADER) or form_token
        if not supplied or not secrets.compare_digest(supplied, csrf_token):
            return _json_error(
                403,
                "csrf_required",
                "A valid CSRF token is required for this operation.",
                "Reload the local UI and submit the operation from its form.",
            )
        return None

    def project_root(project_id: int) -> Path:
        catalog.get_project(project_id)
        return _within(workspace_path, workspace_path / "projects" / str(project_id))

    def dataset_path(project_id: int, dataset_id: int) -> Path:
        root = project_root(project_id)
        record = next(
            (item for item in catalog.list_datasets(project_id) if item.id == dataset_id),
            None,
        )
        if record is None:
            raise CatalogError(f"Dataset does not exist: {dataset_id}")
        path = _within(workspace_path, workspace_path / record.relative_path)
        if not (
            path.is_file() or (path.is_dir() and path.suffix.lower() == ".zarr")
        ) or not path.is_relative_to(root):
            raise CatalogError("Dataset source is not a regular project file")
        return path

    def existing_project_file(project_id: int, raw_name: str) -> Path:
        root = project_root(project_id)
        path = _safe_project_path(workspace_path, root, raw_name)
        if not path.is_file():
            raise CatalogError("Project input is not a regular file")
        return path

    def output_path(project_id: int, raw_name: str) -> Path:
        root = project_root(project_id)
        target = _safe_project_path(workspace_path, root, raw_name)
        if target == root or any(
            target.is_relative_to(root / folder) for folder in ("uploads", "schemas", ".artifacts")
        ):
            raise ValueError("Outputs cannot replace inputs, schemas or registered versions")
        return target

    def queue_job(
        operation: str,
        function,
        *,
        project_id: int,
        input_path: Path | None = None,
        output_path_value: Path | None = None,
    ) -> Response:
        def run_service(cancel):
            result = function(cancel)
            if result.get("status") == "failed":
                raise JobFailure(result["error"]["message"], result=result)
            if result.get("artifact"):
                return CommittedResult(result)
            return result

        try:
            handle = jobs.submit(
                operation,
                run_service,
                input_path=input_path,
                output_path=output_path_value,
            )
        except JobError:
            return _json_error(
                503,
                "job_unavailable",
                "The local job manager is unavailable.",
                "Retry the operation after restarting the local UI.",
            )
        record = jobs.get(handle.id)
        try:
            catalog.register_job(
                project_id,
                job_id=record.id,
                operation=record.operation,
                status=record.status.value,
                started_at=record.started_at,
                finished_at=record.finished_at,
                input_filename=record.input_filename,
                output_filename=record.output_filename,
                operation_log=record.operation_log,
                error=record.error,
            )
        except CatalogError:
            jobs.cancel(handle.id)
            return _json_error(
                500,
                "job_registration_failed",
                "The job could not be registered in the local catalog.",
                "Inspect the catalog and retry the operation.",
            )
        job_projects[handle.id] = project_id
        jobs.add_done_callback(handle.id, sync_catalog_job)
        response = JSONResponse(
            {"job_id": handle.id, "operation": operation, "status": "queued"},
            status_code=202,
        )
        return _set_session_cookie(response, session_token)

    def sync_catalog_job(record) -> None:
        with catalog_job_lock:
            if record.id not in job_projects:
                return
            # Polling may carry an older snapshot than the completion callback.
            current = jobs.get(record.id)
            try:
                catalog.update_job(
                    current.id,
                    status=current.status.value,
                    started_at=current.started_at,
                    finished_at=current.finished_at,
                    operation_log=current.operation_log,
                    error=current.error,
                )
            except CatalogError:
                return

    def artifact_registration(
        project_id: int,
        path: Path,
        *,
        kind: str,
        metadata: dict[str, object],
    ) -> ArtifactRecord:
        return register_snapshot(
            catalog,
            workspace_path,
            project_id,
            path,
            kind=kind,
            metadata=metadata,
        )

    @app.get("/health")
    async def health() -> Response:
        return _set_session_cookie(JSONResponse({"status": "ok"}), session_token)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> Response:
        projects = catalog.list_projects()
        response = templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"projects": projects, "csrf_token": csrf_token},
        )
        return _set_session_cookie(response, session_token)

    @app.get("/api/capabilities")
    async def capabilities() -> Response:
        result = discover_capabilities(CapabilityRequest())
        response = JSONResponse(result.to_dict())
        return _set_session_cookie(response, session_token)

    @app.get("/api/projects/{project_id}")
    async def project_detail(project_id: int) -> Response:
        try:
            project = catalog.get_project(project_id)
            datasets = catalog.list_datasets(project_id)
            artifacts = catalog.list_artifacts(project_id)
            schemas = catalog.list_schemas(project_id)
            project_jobs = catalog.list_jobs(project_id)
        except CatalogError:
            return _json_error(
                404,
                "project_not_found",
                "The requested project does not exist.",
                "Return to the project list and choose an existing project.",
            )
        response = JSONResponse(
            {
                "project": {
                    "id": project.id,
                    "name": project.name,
                    "workspace": project.workspace,
                },
                "datasets": [
                    {
                        "id": item.id,
                        "project_id": item.project_id,
                        "relative_path": item.relative_path,
                        "sha256": item.sha256,
                        "metadata": item.metadata,
                    }
                    for item in datasets
                ],
                "artifacts": [
                    {
                        "id": item.id,
                        "project_id": item.project_id,
                        "relative_path": item.relative_path,
                        "kind": item.kind,
                        "sha256": item.sha256,
                        "metadata": item.metadata,
                    }
                    for item in artifacts
                ],
                "schemas": [
                    {
                        "id": item.id,
                        "project_id": item.project_id,
                        "name": item.name,
                        "version": item.version,
                        "relative_path": item.relative_path,
                        "sha256": item.sha256,
                        "metadata": item.metadata,
                    }
                    for item in schemas
                ],
                "jobs": [_job_payload(item) for item in project_jobs],
            }
        )
        return _set_session_cookie(response, session_token)

    @app.post("/api/projects")
    async def create_project(
        request: Request,
        name: Annotated[str, Form(...)],
        csrf_token_form: Annotated[str | None, Form(alias="csrf_token")] = None,
    ) -> Response:
        csrf_error = require_csrf(request, csrf_token_form)
        if csrf_error is not None:
            return csrf_error
        try:
            project = catalog.create_project(name)
            project_root = _within(workspace_path, workspace_path / "projects" / str(project.id))
            (project_root / "uploads").mkdir(parents=True, exist_ok=True)
        except (CatalogError, OSError, ValueError):
            return _json_error(
                400,
                "project_creation_failed",
                "The project could not be created in the local workspace.",
                "Choose a valid project name and retry.",
            )
        response = JSONResponse(
            {"id": project.id, "name": project.name, "workspace": project.workspace},
            status_code=201,
        )
        return _set_session_cookie(response, session_token)

    @app.post("/api/projects/{project_id}/inspect")
    async def inspect_upload(
        request: Request,
        project_id: int,
        file: Annotated[UploadFile, File()],
        schema_name: Annotated[str, Form(alias="schema")] = "curve",
        csrf_token_form: Annotated[str | None, Form(alias="csrf_token")] = None,
    ) -> Response:
        csrf_error = require_csrf(request, csrf_token_form)
        if csrf_error is not None:
            return csrf_error
        try:
            schema = select_schema(app, project_id, schema_name)
        except (SchemaError, CatalogError):
            return _json_error(
                400,
                "unsupported_schema",
                "The selected schema is unavailable in this project.",
                "Choose a bundled profile or upload a project schema.",
            )
        temporary_path: Path | None = None
        try:
            catalog.get_project(project_id)
            upload_name = _safe_upload_name(file.filename)
            if upload_name.lower().endswith(_ARCHIVE_SUFFIXES):
                return _json_error(
                    400,
                    "archive_rejected",
                    "Archive uploads are disabled for the local inspect flow.",
                    "Upload a supported data file rather than an archive.",
                )
            project_root = _within(workspace_path, workspace_path / "projects" / str(project_id))
            upload_dir = _within(workspace_path, project_root / "uploads")
            upload_dir.mkdir(parents=True, exist_ok=True)
            upload_path = _within(workspace_path, upload_dir / upload_name)
            if upload_path.exists():
                return _json_error(
                    409,
                    "upload_exists",
                    "A file with this name already exists in the project uploads.",
                    "Rename the upload and retry.",
                )
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{upload_name}.",
                suffix=".upload",
                dir=upload_dir,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                total = 0
                while True:
                    chunk = await file.read(min(1024 * 1024, upload_limit - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > upload_limit:
                        return _json_error(
                            413,
                            "upload_too_large",
                            "The uploaded file exceeds the configured size limit.",
                            "Choose a smaller file or increase the local upload limit.",
                        )
                    temporary.write(chunk)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, upload_path)
        except CatalogError:
            return _json_error(
                404,
                "project_not_found",
                "The requested project does not exist.",
                "Return to the project list and choose an existing project.",
            )
        except (OSError, ValueError):
            return _json_error(
                400,
                "upload_rejected",
                "The uploaded file was rejected by the local workspace policy.",
                "Use a regular file with a safe name and retry.",
            )
        finally:
            await file.close()
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        result = import_and_inspect(
            ImportInspectRequest(
                data=upload_path,
                schema=schema,
                read_limits=ReadLimits(max_records=10_000, max_bytes=preview_limit),
                workspace=workspace_path,
            )
        )
        if not result.ok:
            upload_path.unlink(missing_ok=True)
            status_code = (
                413 if result.error and result.error.code == "read_limit_exceeded" else 400
            )
            response = JSONResponse(result.to_dict(), status_code=status_code)
            return _set_session_cookie(response, session_token)
        try:
            dataset_record = catalog.register_dataset(
                project_id,
                upload_path,
                sha256=sha256_file(upload_path),
                metadata={"schema": schema_name, "operation": "import_and_inspect"},
            )
        except (CatalogError, OSError):
            return _json_error(
                500,
                "catalog_registration_failed",
                "The file was stored but could not be registered in the local catalog.",
                "Inspect the catalog and retry the operation.",
            )
        response = JSONResponse({**result.to_dict(), "dataset_id": dataset_record.id})
        return _set_session_cookie(response, session_token)

    @app.post("/api/projects/{project_id}/validate")
    async def validate_project(
        request: Request,
        project_id: int,
        dataset_id: Annotated[int, Form(...)],
        schema_name: Annotated[str, Form(alias="schema")] = "curve",
        csrf_token_form: Annotated[str | None, Form(alias="csrf_token")] = None,
    ) -> Response:
        csrf_error = require_csrf(request, csrf_token_form)
        if csrf_error is not None:
            return csrf_error
        try:
            schema = select_schema(app, project_id, schema_name)
        except (SchemaError, CatalogError):
            return _json_error(
                400,
                "unsupported_schema",
                "The selected schema is unavailable in this project.",
                "Choose a bundled profile or upload a project schema.",
            )
        try:
            source = dataset_path(project_id, dataset_id)
        except CatalogError:
            return _json_error(
                404,
                "dataset_not_found",
                "The requested dataset does not exist in this project.",
                "Return to the project home and choose an uploaded dataset.",
            )
        result = validate_and_summarize(
            DatasetRequest(data=source, schema=schema, workspace=workspace_path)
        )
        response = JSONResponse(result.to_dict(), status_code=200 if result.ok else 400)
        return _set_session_cookie(response, session_token)

    @app.post("/api/projects/{project_id}/convert")
    async def convert_project(
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
                register=lambda path: artifact_registration(
                    project_id,
                    path,
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
    async def report_project(
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
            if cancel.is_set():
                return {"status": "cancelled"}
            result = build_report(
                ReportRequest(
                    data=source,
                    schema=schema,
                    output=target,
                    format=format_name,
                    workspace=workspace_path,
                    force=force,
                )
            )
            if result.ok and target.exists():
                record = artifact_registration(
                    project_id,
                    target,
                    kind="report",
                    metadata={"operation": result.operation, "format": format_name},
                )
                result = with_registered_artifact(result, record)
            return result.to_dict()

        return queue_job(
            "report",
            work,
            project_id=project_id,
            input_path=source,
            output_path_value=target,
        )

    @app.post("/api/projects/{project_id}/plot")
    async def plot_project(
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
            if cancel.is_set():
                return {"status": "cancelled"}
            result = plot_declared_fields(
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
                )
            )
            if result.ok and target.exists():
                record = artifact_registration(
                    project_id,
                    target,
                    kind="plot",
                    metadata={"operation": result.operation, "kind": kind},
                )
                result = with_registered_artifact(result, record)
            return result.to_dict()

        return queue_job(
            "plot",
            work,
            project_id=project_id,
            input_path=source,
            output_path_value=target,
        )

    @app.post("/api/projects/{project_id}/compare")
    async def compare_project(
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
            if cancel.is_set():
                return {"status": "cancelled"}
            result = compare_reports(
                ComparisonRequest(
                    left=left,
                    right=right,
                    output=target,
                    workspace=workspace_path,
                    force=force,
                )
            )
            if result.ok and target.exists():
                record = artifact_registration(
                    project_id,
                    target,
                    kind="compare",
                    metadata={"operation": result.operation},
                )
                result = with_registered_artifact(result, record)
            return result.to_dict()

        return queue_job(
            "compare",
            work,
            project_id=project_id,
            input_path=left,
            output_path_value=target,
        )

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> Response:
        try:
            record = jobs.get(job_id)
        except JobError:
            try:
                record = catalog.get_job(job_id)
            except CatalogError:
                return _json_error(
                    404,
                    "job_not_found",
                    "The requested job does not exist.",
                    "Refresh the project and choose a known job.",
                )
            if record.status in {"running", "queued"}:
                record = catalog.update_job(
                    job_id,
                    status="failed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    operation_log=(*record.operation_log, "interrupted"),
                    error="Job interrupted by a server restart. Submit the operation again.",
                )
            return _set_session_cookie(JSONResponse(_job_payload(record)), session_token)
        sync_catalog_job(record)
        response = JSONResponse(_job_payload(record))
        return _set_session_cookie(response, session_token)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(
        request: Request,
        job_id: str,
        csrf_token_form: Annotated[str | None, Form(alias="csrf_token")] = None,
    ) -> Response:
        csrf_error = require_csrf(request, csrf_token_form)
        if csrf_error is not None:
            return csrf_error
        try:
            jobs.cancel(job_id)
            record = jobs.get(job_id)
        except JobError:
            return _json_error(
                404,
                "job_not_found",
                "The requested job does not exist.",
                "Refresh the project and choose a known job.",
            )
        sync_catalog_job(record)
        response = JSONResponse(_job_payload(record))
        return _set_session_cookie(response, session_token)

    install_authoring(app, dataset_path=dataset_path, require_csrf=require_csrf)
    install_slices(
        app,
        dataset_path=dataset_path,
        output_path=output_path,
        require_csrf=require_csrf,
        queue_job=queue_job,
        artifact_registration=artifact_registration,
    )
    install_workbench(
        app,
        templates,
        csrf_token=csrf_token,
        session_token=session_token,
        require_csrf=require_csrf,
    )
    return app


__all__ = ["create_app"]
