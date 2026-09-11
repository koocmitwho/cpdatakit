"""Versioned workbench artifacts, separate from caller-selected output names."""

import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..application.data_access import path_sha256
from ..catalog.sqlite import ArtifactRecord, SQLiteCatalog
from ..exceptions import CatalogError


def register_snapshot(
    catalog: SQLiteCatalog,
    workspace: Path,
    project_id: int,
    path: Path,
    *,
    kind: str,
    metadata: dict[str, Any],
) -> ArtifactRecord:
    """Copy one completed output before registering its immutable version path."""
    root = (workspace / "projects" / str(project_id)).resolve()
    source = path.resolve()
    versions = root / ".artifacts"
    if source == root or not source.is_relative_to(root):
        raise CatalogError("Artifact output must be a project entry")
    if versions.is_symlink() or not versions.resolve().is_relative_to(root):
        raise CatalogError("Artifact storage is outside this project")
    if source.is_relative_to(versions.resolve()):
        raise CatalogError("Artifact storage cannot be used as an output")
    if source.is_dir():
        for entry in source.rglob("*"):
            if entry.is_symlink() or not entry.resolve().is_relative_to(source):
                raise CatalogError("Artifact contains an external link")
    expected = path_sha256(source)
    versions.mkdir(exist_ok=True)
    version = Path(tempfile.mkdtemp(prefix="version-", dir=versions))
    snapshot = version / source.name
    try:
        if source.is_dir():
            shutil.copytree(source, snapshot)
        else:
            shutil.copyfile(source, snapshot)
        digest = path_sha256(snapshot)
        if digest != expected:
            raise CatalogError("Output changed while its artifact snapshot was being created")
        return catalog.register_artifact(
            project_id,
            snapshot,
            kind=kind,
            sha256=digest,
            metadata={
                **metadata,
                "output_path": source.relative_to(workspace).as_posix(),
                "hash_scope": "tree" if snapshot.is_dir() else "file",
            },
        )
    except BaseException:
        shutil.rmtree(version)
        raise


def with_registered_artifact(result, record: ArtifactRecord):
    """Bind a web job's result to the exact version stored in its catalog record."""
    value = result.value
    if hasattr(value, "artifact"):
        value = replace(value, artifact=record.relative_path)
    return replace(result, artifact=record.relative_path, value=value)


def artifact_digest(path: Path, record: ArtifactRecord) -> str:
    """Honor the original manifest-only digest of legacy comparison records."""
    if path.is_dir() and record.kind == "compare" and record.metadata.get("hash_scope") != "tree":
        return path_sha256(path / "manifest.json")
    return path_sha256(path)
