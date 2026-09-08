"""Project-scoped schema drafting and conversion mapping previews."""

import uuid
from typing import Annotated

from fastapi import Form, Request
from fastapi.responses import JSONResponse

from ..application import (
    DatasetRequest,
    ImportInspectRequest,
    ReadLimits,
    draft_schema,
    preview_mapping,
)
from ..exceptions import CPDataKitError, NormalizationError
from ..normalization import load_mapping_file
from .workbench import project_directory, select_schema


def store_mapping(app, project_id, text):
    if not text.strip():
        return None
    if len(text.encode("utf-8")) > 1024 * 1024:
        raise NormalizationError("Mapping JSON exceeds 1 MiB")
    root = project_directory(app, project_id)
    directory = root / "schemas"
    if not directory.resolve().is_relative_to(root):
        raise NormalizationError("Mapping storage is outside this project")
    directory.mkdir(exist_ok=True)
    path = directory / f"mapping-{uuid.uuid4().hex}.json"
    try:
        path.write_text(text, encoding="utf-8")
        load_mapping_file(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def install_authoring(app, *, dataset_path, require_csrf):
    from .app import _json_error

    @app.get("/api/projects/{project_id}/datasets/{dataset_id}/schema-draft")
    async def schema_draft(project_id: int, dataset_id: int):
        try:
            source = dataset_path(project_id, dataset_id)
            result = draft_schema(
                ImportInspectRequest(
                    source, read_limits=ReadLimits(max_bytes=app.state.preview_limit)
                )
            )
            return JSONResponse(result.to_dict(), status_code=200 if result.ok else 400)
        except (CPDataKitError, ValueError):
            return _json_error(
                404,
                "dataset_not_found",
                "Dataset not found in this project.",
                "Choose an uploaded dataset.",
            )

    @app.post("/api/projects/{project_id}/mapping-preview")
    async def mapping_preview(
        request: Request,
        project_id: int,
        dataset_id: Annotated[int, Form()],
        schema_name: Annotated[str, Form(alias="schema")],
        mapping_json: Annotated[str, Form()],
    ):
        error = require_csrf(request, None)
        if error is not None:
            return error
        path = None
        try:
            source = dataset_path(project_id, dataset_id)
            schema = select_schema(app, project_id, schema_name)
            path = store_mapping(app, project_id, mapping_json)
            result = preview_mapping(
                DatasetRequest(source, schema, path),
                read_limits=ReadLimits(max_bytes=app.state.preview_limit),
            )
            return JSONResponse(result.to_dict(), status_code=200 if result.ok else 400)
        except (CPDataKitError, ValueError):
            return _json_error(
                400,
                "invalid_mapping",
                "Check the dataset, schema and mapping JSON.",
                "Choose project inputs and explicit mappings.",
            )
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
