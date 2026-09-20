"""Inspect crash evidence without mutation; recover verified copies without overwrite."""

import html
import json
import shutil
import stat
import tempfile
import threading
import uuid
from contextvars import ContextVar
from pathlib import Path

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .._atomic import publish_directory, publish_file
from ..exceptions import CPDataKitError
from .durable import atomic_json

ACTIVE_TRANSACTION = ContextVar("output_transaction", default=None)
_RECOVERY_LOCK = threading.Lock()


def signature(path):
    from .outputs import _output_signature

    device, inode, kind, digest, entries = _output_signature(path)
    return {
        "device": device,
        "inode": inode,
        "kind": kind,
        "sha256": digest,
        "entries": list(entries),
    }


def matches(path, expected):
    try:
        return signature(path) == expected
    except (OSError, CPDataKitError):
        return False


def _validate_signature(value):
    if (
        not isinstance(value, dict)
        or any(type(value.get(key)) is not int for key in ("device", "inode", "kind"))
        or value["kind"] not in {stat.S_IFREG, stat.S_IFDIR}
        or not isinstance(value.get("sha256"), str)
        or len(value["sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in value["sha256"])
        or not isinstance(value.get("entries"), list)
        or not all(isinstance(entry, str) for entry in value["entries"])
    ):
        raise ValueError("Invalid recovery identity")


def bounded_path(workspace, root, raw):
    if not isinstance(raw, str):
        raise ValueError("Invalid recovery path")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts or relative.drive:
        raise ValueError("Recovery path escapes the project")
    path = workspace / relative
    if path == root or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Recovery path escapes the project")
    for parent in (path, *path.parents):
        if parent == workspace:
            break
        if parent.is_symlink():
            raise ValueError("Recovery paths cannot contain links")
    return path


class OutputTransaction:
    def __init__(self, request, staging, staged, backup):
        self.workspace = Path(request.workspace).resolve()
        target = request.output.resolve()
        parts = target.relative_to(self.workspace).parts
        project = (
            int(parts[1])
            if len(parts) > 2 and parts[0] == "projects" and parts[1].isdigit()
            else None
        )
        identifier = uuid.uuid4().hex

        def relative(path):
            return path.relative_to(self.workspace).as_posix()

        self.path = (
            self.workspace
            / ".output-transactions"
            / str(project or "standalone")
            / f"{identifier}.json"
        )
        for parent in (self.path.parent, self.path.parent.parent):
            if parent.is_symlink():
                raise ValueError("Transaction storage cannot contain links")
        self.data = {
            "version": 1,
            "id": identifier,
            "workspace": str(self.workspace),
            "project_id": project,
            "phase": "prepared",
            "target": relative(target),
            "directory": relative(staging),
            "candidates": {
                "new": {
                    "locations": [
                        relative(staged),
                        relative(target),
                        relative(staging / "unregistered"),
                    ],
                    "signature": signature(staged),
                }
            },
            "recovered": {},
        }
        if target.exists():
            self.data["candidates"]["previous"] = {
                "locations": [relative(backup), relative(target)],
                "signature": signature(target),
            }
        self.write()

    def write(self, phase=None):
        if phase:
            self.data["phase"] = phase
        atomic_json(self.path, self.data)

    def snapshot(self, path):
        self.data["candidates"]["snapshot"] = {
            "locations": [path.relative_to(self.workspace).as_posix()],
            "signature": signature(path),
        }
        self.write("snapshot_ready")

    def registered(self, record):
        self.data["artifact_id"] = record.id
        self.write("registered")


def _read(workspace, project, path):
    root = workspace / "projects" / str(project)
    if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
        raise ValueError("Linked transaction evidence")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        data["version"] != 1
        or data["workspace"] != str(workspace)
        or data["project_id"] != project
        or data["id"] != path.stem
        or len(data["id"]) != 32
        or any(c not in "0123456789abcdef" for c in data["id"])
    ):
        raise ValueError("Transaction identity does not match this project")
    bounded_path(workspace, root, data["target"])
    bounded_path(workspace, root, data["directory"])
    if not isinstance(data["candidates"], dict) or not data["candidates"]:
        raise ValueError("Missing candidates")
    for role, candidate in data["candidates"].items():
        if role not in {"new", "previous", "snapshot"} or not candidate["locations"]:
            raise ValueError("Unknown recovery candidate")
        for raw in candidate["locations"]:
            bounded_path(workspace, root, raw)
        _validate_signature(candidate["signature"])
    recovered = data["recovered"]
    if not isinstance(recovered, dict):
        raise ValueError("Invalid recovered output records")
    for role, entry in recovered.items():
        if role not in data["candidates"] or not isinstance(entry, dict):
            raise ValueError("Invalid recovered output record")
        path = bounded_path(workspace, root, entry["path"])
        if path != _destination(workspace, project, data, role):
            raise ValueError("Recovery destination does not match this transaction")
        _validate_signature(entry["signature"])
    return data


def _destination(workspace, project, data, role):
    name = Path(data["target"]).name
    return workspace / "projects" / str(project) / "recovered" / f"{data['id']}-{role}-{name}"


def _inspect(workspace, project, data, catalog):
    root = workspace / "projects" / str(project)
    candidates, conflict = [], False
    # A path can be a candidate location for both old and new outputs. It is
    # foreign only if none of the transaction's recorded identities matches it.
    expectations = {}
    for candidate in data["candidates"].values():
        for raw in candidate["locations"]:
            expectations.setdefault(raw, []).append(candidate["signature"])
    for raw, signatures in expectations.items():
        path = bounded_path(workspace, root, raw)
        if (path.exists() or path.is_symlink()) and not any(
            matches(path, item) for item in signatures
        ):
            conflict = True
    for role, candidate in data["candidates"].items():
        found = next(
            (
                raw
                for raw in candidate["locations"]
                if matches(bounded_path(workspace, root, raw), candidate["signature"])
            ),
            None,
        )
        candidates.append(
            {
                "role": role,
                "path": found,
                "verified": found is not None,
                "sha256": candidate["signature"]["sha256"],
                "destination": _destination(workspace, project, data, role)
                .relative_to(workspace)
                .as_posix(),
            }
        )
    registered = [
        record.id
        for record in catalog.list_artifacts(project)
        if record.metadata.get("transaction_id") == data["id"]
    ]
    return {
        "id": data["id"],
        "phase": data["phase"],
        "target": data["target"],
        "state": "conflict" if conflict else "registered" if registered else "recoverable",
        "artifact_ids": registered,
        "candidates": candidates,
    }


def recovery_inventory(workspace, project, catalog):
    catalog.get_project(project)
    root = workspace / ".output-transactions" / str(project)
    items = []
    if root.is_symlink() or root.parent.is_symlink():
        return [
            {
                "id": "unknown",
                "state": "conflict",
                "candidates": [],
                "reason": "Linked recovery storage",
            }
        ]
    for path in sorted(root.glob("*.json")):
        try:
            data = _read(workspace, project, path)
            items.append(_inspect(workspace, project, data, catalog))
        except (OSError, ValueError, KeyError, TypeError, CPDataKitError):
            items.append(
                {
                    "id": path.stem,
                    "state": "conflict",
                    "candidates": [],
                    "reason": "Unreadable or untrusted recovery evidence; all files retained",
                }
            )
    return items


def recover_copy(workspace, project, identifier, role, catalog):
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Unknown transaction")
    catalog.get_project(project)
    manifest = workspace / ".output-transactions" / str(project) / f"{identifier}.json"
    with _RECOVERY_LOCK:
        data = _read(workspace, project, manifest)
        view = _inspect(workspace, project, data, catalog)
        if view["state"] == "conflict" or role not in data["candidates"]:
            raise ValueError("Recovery evidence has changed; all files are retained")
        row = next(item for item in view["candidates"] if item["role"] == role)
        if not row["verified"]:
            raise ValueError("The recorded output is unavailable")
        root = workspace / "projects" / str(project)
        target = bounded_path(workspace, root, row["destination"])
        previous = data["recovered"].get(role)
        if target.exists():
            if (
                previous
                and previous["path"] == row["destination"]
                and matches(target, previous["signature"])
            ):
                return row["destination"]
            raise ValueError("Recovery destination already exists; no files were overwritten")
        source = bounded_path(workspace, root, row["path"])
        expected = data["candidates"][role]["signature"]
        if not matches(source, expected):
            raise ValueError("Recovery source changed")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".recovery-", dir=target.parent))
        copied = staging / target.name
        if source.is_dir():
            shutil.copytree(source, copied)
        else:
            shutil.copyfile(source, copied)
        copied_signature = signature(copied)
        if (
            copied_signature["sha256"] != expected["sha256"]
            or copied_signature["entries"] != expected["entries"]
            or not matches(source, expected)
        ):
            raise ValueError("Recovery source changed during copying; evidence retained")
        data["recovered"][role] = {"path": row["destination"], "signature": copied_signature}
        atomic_json(manifest, data)
        if copied.is_dir():
            publish_directory(copied, target)
        else:
            publish_file(copied, target)
        staging.rmdir()
        return row["destination"]


def install_recovery(app, *, require_csrf, csrf_token):
    from .app import _json_error

    workspace, catalog = app.state.workspace, app.state.catalog

    @app.get("/api/projects/{project}/recovery")
    def inventory(project: int):
        try:
            return {"items": recovery_inventory(workspace, project, catalog)}
        except CPDataKitError:
            return _json_error(404, "project_not_found", "Project not found.", "Choose a project.")

    @app.get("/projects/{project}/recovery", response_class=HTMLResponse)
    def recovery_page(project: int):
        try:
            items = recovery_inventory(workspace, project, catalog)
        except CPDataKitError:
            return _json_error(404, "project_not_found", "Project not found.", "Choose a project.")
        rows = []
        labels = {
            "previous": "Previous output",
            "new": "Generated output",
            "snapshot": "Artifact snapshot",
        }
        for item in items:
            forms = ""
            for candidate in item["candidates"]:
                if candidate["verified"] and item["state"] != "conflict":
                    action = f"/api/projects/{project}/recovery/{item['id']}/{candidate['role']}"
                    forms += (
                        f'<form method="post" action="{html.escape(action, quote=True)}">'
                        '<input type="hidden" name="csrf_token" '
                        f'value="{html.escape(csrf_token, quote=True)}">'
                        f"<button>Recover {labels[candidate['role']]} "
                        "to a new location</button></form>"
                    )
            rows.append(
                f"<li>{html.escape(item.get('target', item['id']))} · {item['state']}{forms}</li>"
            )
        project_href = html.escape(f"/projects/{project}", quote=True)
        return HTMLResponse(
            '<html><head><meta charset="utf-8"><title>Recovery · CPDataKit</title></head>'
            "<body><h1>Recovery</h1><p>Verified files are copied to a new location. "
            "Existing outputs and evidence are retained. Conflicts require manual review.</p>"
            f"<ul>{''.join(rows) or '<li>No pending output recovery.</li>'}</ul>"
            f'<a href="{project_href}">Return to project</a></body></html>'
        )

    @app.post("/api/projects/{project}/recovery/{identifier}/{role}")
    def recover(
        request: Request,
        project: int,
        identifier: str,
        role: str,
        csrf_token: str | None = Form(None),
    ):
        error = require_csrf(request, csrf_token)
        if error is not None:
            return error
        try:
            path = recover_copy(workspace, project, identifier, role, catalog)
            return JSONResponse({"path": path, "status": "recovered"})
        except (OSError, ValueError, KeyError, TypeError, CPDataKitError):
            return _json_error(
                409,
                "recovery_conflict",
                "Recovery could not verify the files or destination. All evidence is retained.",
                "Review the recovery list and existing files.",
            )
