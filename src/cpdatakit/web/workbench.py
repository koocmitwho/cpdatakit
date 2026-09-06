"""Project schemas, directory uploads and registered result downloads."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask

from ..application import ImportInspectRequest, ReadLimits, import_and_inspect
from ..application.data_access import contract_dict, path_sha256, resolve_contract
from ..exceptions import CatalogError, CPDataKitError, SchemaError
from ..schema import BUILTIN_PROFILES


def project_directory(app, project_id: int) -> Path:
    app.state.catalog.get_project(project_id)
    root = app.state.workspace / "projects" / str(project_id)
    if root.is_symlink() or not root.resolve().is_relative_to(app.state.workspace):
        raise CatalogError("Project directory is outside the workspace")
    return root.resolve()


def select_schema(app, project_id: int, selector: str):
    """Resolve a bundled profile or an immutable schema belonging to this project."""
    if selector in BUILTIN_PROFILES:
        return selector
    if not selector.startswith("schema:"):
        raise SchemaError("Choose a bundled profile or an uploaded project schema.")
    schemas = app.state.catalog.list_schemas(project_id)
    record = next((item for item in schemas if selector == f"schema:{item.id}"), None)
    if record is None or not record.relative_path:
        raise SchemaError("Selected schema does not belong to this project.")
    path = app.state.workspace / record.relative_path
    if not path.resolve().is_relative_to(project_directory(app, project_id)):
        raise SchemaError("Schema path is outside this project.")
    if not path.is_file() or path_sha256(path) != record.sha256:
        raise SchemaError("Stored schema has changed; upload it as a new schema.")
    return resolve_contract(path)


def install_workbench(app, templates, *, csrf_token, session_token, require_csrf):
    # Imported at installation time to keep the existing app helpers in one place.
    from .app import _json_error, _set_session_cookie

    catalog = app.state.catalog

    @app.get("/projects/{project_id}")
    async def project_page(request: Request, project_id: int) -> Response:
        try:
            project_directory(app, project_id)
            context = {
                "project": catalog.get_project(project_id),
                "csrf_token": csrf_token,
                "datasets": catalog.list_datasets(project_id),
                "schemas": catalog.list_schemas(project_id),
                "artifacts": catalog.list_artifacts(project_id),
                "jobs": catalog.list_jobs(project_id),
            }
        except CatalogError:
            return _json_error(404, "project_not_found", "Project not found.", "Choose a project.")
        response = templates.TemplateResponse(request=request, name="project.html", context=context)
        return _set_session_cookie(response, session_token)

    @app.post("/api/projects/{project_id}/schemas")
    async def upload_schema(
        request: Request,
        project_id: int,
        file: Annotated[UploadFile, File()],
        csrf_token_form: Annotated[str | None, Form(alias="csrf_token")] = None,
    ) -> Response:
        target = None
        registered = False
        try:
            error = require_csrf(request, csrf_token_form)
            if error is not None:
                return error
            root = project_directory(app, project_id)
            payload_bytes = await file.read(min(app.state.upload_limit, 1024 * 1024) + 1)
            if len(payload_bytes) > min(app.state.upload_limit, 1024 * 1024):
                return _json_error(
                    413,
                    "upload_too_large",
                    "Schema exceeds the size limit.",
                    "Upload a smaller schema JSON file.",
                )
            payload = json.loads(payload_bytes)
            if not isinstance(payload, dict):
                raise SchemaError("Schema must be a JSON object.")
            if "resolved" in payload:
                payload = payload["resolved"]
            if not isinstance(payload, dict) or "extends" in payload or "includes" in payload:
                raise SchemaError("Upload a standalone or resolved schema without file references.")
            contract = resolve_contract(payload)
            schema_dir = root / "schemas"
            if not schema_dir.resolve().is_relative_to(root):
                raise SchemaError("Schema directory is outside this project.")
            schema_dir.mkdir(exist_ok=True)
            target = schema_dir / f"{uuid.uuid4().hex}.json"
            stored = contract_dict(contract)
            target.write_text(
                json.dumps(stored.get("resolved", stored), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            record = catalog.register_schema(
                project_id,
                name=contract.profile,
                version=contract.schema_version,
                path=target,
                sha256=path_sha256(target),
            )
            registered = True
            return JSONResponse(
                {
                    "id": record.id,
                    "selector": f"schema:{record.id}",
                    "name": record.name,
                    "version": record.version,
                },
                status_code=201,
            )
        except (CPDataKitError, ValueError, OSError, UnicodeError) as exc:
            from ..inspection import sanitize_error_message

            return _json_error(
                400,
                "invalid_schema",
                sanitize_error_message(exc),
                "Upload a valid schema 1.0 or standalone schema 2.0 JSON file.",
            )
        finally:
            await file.close()
            if target is not None and not registered:
                target.unlink(missing_ok=True)

    @app.post("/api/projects/{project_id}/inspect-zarr")
    async def inspect_zarr(
        request: Request,
        project_id: int,
        files: Annotated[list[UploadFile], File()],
        schema_name: Annotated[str, Form(alias="schema")] = "curve",
        csrf_token_form: Annotated[str | None, Form(alias="csrf_token")] = None,
    ) -> Response:
        staging = None
        installed = None
        registered = False
        try:
            error = require_csrf(request, csrf_token_form)
            if error is not None:
                return error
            schema = select_schema(app, project_id, schema_name)
            root = project_directory(app, project_id)
            uploads = root / "uploads"
            if not uploads.resolve().is_relative_to(root):
                raise ValueError("Upload directory is outside this project")
            if len(files) > 1000:
                raise ValueError("Zarr uploads support at most 1000 files")
            entries = []
            seen = set()
            store_name = None
            for file in files:
                name = (file.filename or "").replace("\\", "/")
                parts = name.split("/")
                if len(parts) < 2 or any(
                    p in {"", ".", ".."} or ":" in p or "\0" in p for p in parts
                ):
                    raise ValueError("Unsafe Zarr file path")
                if not parts[0].lower().endswith(".zarr"):
                    raise ValueError("Select a directory ending in .zarr")
                if store_name is not None and parts[0] != store_name:
                    raise ValueError("Upload one Zarr directory at a time")
                store_name = parts[0]
                if name.casefold() in seen:
                    raise ValueError("Duplicate Zarr file path")
                seen.add(name.casefold())
                entries.append((file, parts[1:]))
            if not store_name:
                raise ValueError("Zarr directory is empty")
            target = uploads / store_name
            if target.exists():
                return _json_error(
                    409,
                    "upload_exists",
                    "This Zarr directory already exists.",
                    "Rename the directory and retry.",
                )
            staging = Path(tempfile.mkdtemp(prefix=".zarr-upload-", dir=uploads))
            total = 0
            for file, parts in entries:
                destination = staging.joinpath(*parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as stream:
                    while chunk := await file.read(
                        min(1024 * 1024, app.state.upload_limit - total + 1)
                    ):
                        total += len(chunk)
                        if total > app.state.upload_limit:
                            return _json_error(
                                413,
                                "upload_too_large",
                                "Directory exceeds the size limit.",
                                "Choose a smaller dataset.",
                            )
                        stream.write(chunk)
            # A named child preserves .zarr format dispatch during inspection.
            staged_store = staging / store_name
            staged_store.mkdir()
            for child in tuple(staging.iterdir()):
                if child != staged_store:
                    child.rename(staged_store / child.name)
            result = import_and_inspect(
                ImportInspectRequest(
                    staged_store,
                    schema,
                    ReadLimits(max_bytes=app.state.preview_limit),
                )
            )
            if not result.ok:
                status = 413 if result.error.code == "read_limit_exceeded" else 400
                return JSONResponse(result.to_dict(), status_code=status)
            digest = path_sha256(staged_store)
            os.replace(staged_store, target)
            installed = target
            record = catalog.register_dataset(
                project_id, target, sha256=digest, metadata={"schema": schema_name}
            )
            registered = True
            return JSONResponse({**result.to_dict(), "dataset_id": record.id})
        except (CPDataKitError, OSError, ValueError):
            return _json_error(
                400,
                "upload_rejected",
                "Zarr directory upload was rejected.",
                "Choose one valid .zarr directory and a project schema.",
            )
        finally:
            for file in files:
                await file.close()
            if staging is not None:
                shutil.rmtree(staging)
            if installed is not None and not registered:
                shutil.rmtree(installed)

    @app.get("/api/projects/{project_id}/artifacts/{artifact_id}")
    async def artifact(project_id: int, artifact_id: int, download: bool = False) -> Response:
        try:
            root = project_directory(app, project_id)
            record = next(
                (a for a in catalog.list_artifacts(project_id) if a.id == artifact_id), None
            )
            if record is None:
                raise CatalogError("Artifact not found")
            path = (app.state.workspace / record.relative_path).resolve()
            if not path.is_relative_to(root) or not path.exists():
                raise CatalogError("Artifact is outside this project or missing")
            headers = {"X-Content-Type-Options": "nosniff"}
            if path.is_dir():
                descriptor, temporary = tempfile.mkstemp(suffix=".zip")
                os.close(descriptor)
                archive = Path(temporary)
                try:
                    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
                        for entry in sorted(path.rglob("*")):
                            if entry.is_symlink() or not entry.resolve().is_relative_to(path):
                                raise CatalogError("Artifact contains an external link")
                            if entry.is_file():
                                zipped.write(
                                    entry, f"{path.name}/{entry.relative_to(path).as_posix()}"
                                )
                except BaseException:
                    archive.unlink(missing_ok=True)
                    raise
                return FileResponse(
                    archive,
                    filename=f"{path.name}.zip",
                    headers=headers,
                    background=BackgroundTask(archive.unlink, missing_ok=True),
                )
            if record.kind == "report" and record.metadata.get("format") == "html" and not download:
                headers["Content-Security-Policy"] = (
                    "sandbox; default-src 'none'; style-src 'unsafe-inline'"
                )
                return FileResponse(path, media_type="text/html", headers=headers)
            return FileResponse(path, filename=path.name, headers=headers)
        except (CatalogError, OSError):
            return _json_error(
                404,
                "artifact_not_found",
                "Result not found in this project.",
                "Refresh the project results.",
            )
