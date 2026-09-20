"""Bind uploads to their produced bytes and retain uncertain rollback evidence.

Random staging and quarantine names are private to this operation. This does not
provide atomic compare-and-delete or protect against another process actively
modifying those private paths or writing through an already-open file handle.
Public names are detached, then checked again before private cleanup.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path

from fastapi.responses import JSONResponse

from ..application.data_access import path_sha256
from ..exceptions import CatalogError, CPDataKitError

logger = logging.getLogger(__name__)


def cleanup_upload_staging(path: Path) -> None:
    """Clean an operation's private directory without reversing a committed upload."""
    try:
        shutil.rmtree(path)
    except OSError as exc:
        logger.warning("Retained upload staging %s after cleanup failed: %s", path, exc)


def _signature(path: Path):
    identity = path.lstat()
    kind = stat.S_IFMT(identity.st_mode)
    if kind not in {stat.S_IFREG, stat.S_IFDIR}:
        raise CatalogError("Uploads must be regular files or directories")
    entries = []
    if kind == stat.S_IFDIR:
        for entry in sorted(path.rglob("*")):
            info = entry.lstat()
            entry_kind = stat.S_IFMT(info.st_mode)
            if entry_kind not in {stat.S_IFREG, stat.S_IFDIR}:
                raise CatalogError("Upload directories must not contain links or special files")
            entries.append(
                (entry.relative_to(path).as_posix(), info.st_dev, info.st_ino, entry_kind)
            )
    return identity.st_dev, identity.st_ino, kind, path_sha256(path), tuple(entries)


class UploadPublication:
    """Remember identity before publication; never infer ownership from a public name."""

    def __init__(self, staged: Path, target: Path, workspace: Path):
        self.target = target
        self.workspace = workspace
        self.signature = _signature(staged)
        self.published = False
        self.pending_record = None

    def _owned(self, path: Path) -> bool:
        try:
            return _signature(path) == self.signature
        except (OSError, CPDataKitError):
            return False

    def verify(self) -> None:
        if not self._owned(self.target):
            raise CatalogError("Upload changed during publication or registration")

    def register(self, catalog, project_id, *, metadata):
        self.verify()
        record = catalog.register_dataset(
            project_id, self.target, sha256=self.signature[3], metadata=metadata
        )
        self.pending_record = record.id
        try:
            self.verify()
        except (CPDataKitError, OSError):
            catalog.delete_dataset(record.id)
            self.pending_record = None
            raise
        self.pending_record = None
        return record

    def rollback(self):
        if not self.published:
            return None
        try:
            recovery_dir = Path(
                tempfile.mkdtemp(prefix=".upload-recovery-", dir=self.target.parent)
            )
        except OSError:
            recovery = {
                "reason": "upload_cleanup_failed",
                "target": self.target.relative_to(self.workspace).as_posix(),
                "record_written": False,
            }
            if self.pending_record is not None:
                recovery["dataset_id"] = self.pending_record
            return recovery
        quarantined = recovery_dir / "unregistered"
        try:
            if not self._owned(self.target):
                reason = "target_changed"
            else:
                # A public writer can still replace/modify the checked path here.
                os.rename(self.target, quarantined)
                if not self._owned(quarantined):
                    reason = "quarantined_target_changed"
                elif self.pending_record is not None:
                    reason = "catalog_cleanup_failed"
                else:
                    shutil.rmtree(recovery_dir)
                    return None
        except (OSError, CPDataKitError):
            reason = "upload_cleanup_failed"
        recovery = {
            "reason": reason,
            "directory": recovery_dir.relative_to(self.workspace).as_posix(),
            "target": self.target.relative_to(self.workspace).as_posix(),
        }
        if quarantined.exists() or quarantined.is_symlink():
            recovery["unregistered_upload"] = quarantined.relative_to(self.workspace).as_posix()
        if self.pending_record is not None:
            recovery["dataset_id"] = self.pending_record
        recovery["record_written"] = True
        try:
            (recovery_dir / "recovery.json").write_text(
                json.dumps(recovery, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except OSError:
            recovery["record_written"] = False
        return recovery


def upload_failure(publication: UploadPublication, *, status_code=500):
    recovery = publication.rollback()
    payload = {
        "status": "failed",
        "error": {
            "code": "upload_recovery_required" if recovery else "catalog_registration_failed",
            "message": "The upload could not be registered in the local catalog.",
            "action": (
                "Inspect the recovery record and retained upload before retrying. "
                "Restore retained content only to an unused path; do not overwrite another writer."
                if recovery
                else "Retry the upload after checking the catalog."
            ),
        },
    }
    if recovery:
        payload["recovery"] = recovery
    return JSONResponse(payload, status_code=status_code)
