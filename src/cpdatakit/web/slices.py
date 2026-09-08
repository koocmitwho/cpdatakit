"""HTTP edges for the shared scientific slice service."""

import json
import uuid
from typing import Annotated

from fastapi import Form, Request
from fastapi.responses import JSONResponse

from ..application import (
    ImportInspectRequest,
    ReadLimits,
    SliceRequest,
    import_and_inspect,
    plot_scientific_slice,
)
from ..exceptions import CatalogError


def install_slices(
    app, *, dataset_path, output_path, require_csrf, queue_job, artifact_registration
):
    from .app import _json_error

    @app.get("/api/projects/{project_id}/datasets/{dataset_id}/structure")
    async def structure(project_id: int, dataset_id: int):
        try:
            source = dataset_path(project_id, dataset_id)
        except (CatalogError, ValueError):
            return _json_error(
                404,
                "dataset_not_found",
                "Dataset not found in this project.",
                "Choose an uploaded dataset.",
            )
        result = import_and_inspect(
            ImportInspectRequest(
                source, read_limits=ReadLimits(max_records=10**9, max_bytes=app.state.preview_limit)
            )
        )
        return JSONResponse(result.to_dict(), status_code=200 if result.ok else 400)

    @app.post("/api/projects/{project_id}/slice")
    async def slice_plot(
        request: Request,
        project_id: int,
        dataset_id: Annotated[int, Form()],
        variable: Annotated[str, Form()],
        x: Annotated[str, Form()],
        y: Annotated[str, Form()],
        indices: Annotated[str, Form()] = "{}",
        cmap: Annotated[str, Form()] = "viridis",
        vmin: Annotated[str, Form()] = "",
        vmax: Annotated[str, Form()] = "",
    ):
        error = require_csrf(request, None)
        if error is not None:
            return error
        try:
            source = dataset_path(project_id, dataset_id)
            target = output_path(project_id, f"results/slice-{uuid.uuid4().hex}.png")
            positions = json.loads(indices)
            if not isinstance(positions, dict):
                raise ValueError("indices must be an object")
            operation = SliceRequest(
                source,
                variable,
                x,
                y,
                positions,
                target,
                workspace=app.state.workspace,
                cmap=cmap,
                vmin=float(vmin) if vmin else None,
                vmax=float(vmax) if vmax else None,
                read_limits=ReadLimits(max_records=10**9, max_bytes=app.state.preview_limit),
            )
        except (CatalogError, ValueError):
            return _json_error(
                400,
                "invalid_slice",
                "Check the dataset, indices and color limits.",
                "Select a variable and two display dimensions.",
            )

        def work(context):
            result = plot_scientific_slice(operation, context=context)
            if result.ok:
                try:
                    artifact_registration(project_id, target, kind="slice", metadata=result.value)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
            return result.to_dict()

        return queue_job(
            "slice", work, project_id=project_id, input_path=source, output_path_value=target
        )
