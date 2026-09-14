"""Stage, promote and register workbench outputs with a shared recovery boundary."""

import json
import os
import shutil
import stat
import tempfile
import threading
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from .._atomic import publish_directory, publish_file
from ..application import ComparisonRequest
from ..application.data_access import path_sha256
from ..application.services import _comparison_provenance, _failure, _relative_artifact
from ..exceptions import CPDataKitError, OutputExistsError
from .artifacts import with_registered_artifact

_PROMOTION_LOCK = threading.Lock()


def _output_signature(path):
    identity = path.lstat()
    kind = stat.S_IFMT(identity.st_mode)
    if kind not in {stat.S_IFREG, stat.S_IFDIR}:
        raise OSError("Published outputs must be regular files or directories")
    entries = (
        tuple(sorted(entry.relative_to(path).as_posix() for entry in path.rglob("*")))
        if kind == stat.S_IFDIR
        else ()
    )
    return identity.st_dev, identity.st_ino, kind, path_sha256(path), entries


def _still_owned(path, signature):
    try:
        return _output_signature(path) == signature
    except (OSError, CPDataKitError):
        return False


def _restore_entry(source, target):
    if source.is_dir() or source.is_symlink():
        publish_directory(source, target)
    else:
        publish_file(source, target)


def _rollback(target, staging, backup, signature):
    if signature is not None:
        if not _still_owned(target, signature):
            return "target_changed"
        quarantined = staging / "unregistered"
        # Detach atomically, then verify again before cleanup. A replacement in
        # the check/rename interval must be restored or retained, never deleted.
        os.rename(target, quarantined)
        if not _still_owned(quarantined, signature):
            _restore_entry(quarantined, target)
            return "target_changed"
    if backup.exists() or backup.is_symlink():
        _restore_entry(backup, target)
    return None


def _recovery_evidence(request, staging, backup, reason):
    recovery = {
        "reason": reason,
        "directory": _relative_artifact(staging, request.workspace),
        "target": _relative_artifact(request.output, request.workspace),
    }
    for name, path in (
        ("previous_output", backup),
        ("unregistered_output", staging / "unregistered"),
    ):
        if path.exists() or path.is_symlink():
            recovery[name] = _relative_artifact(path, request.workspace)
    # The returned result still carries the recovery locations if disk I/O fails.
    with suppress(OSError):
        (staging / "recovery.json").write_text(
            json.dumps(recovery, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return recovery


def convert_registered(request, context, *, convert, register):
    return produce_registered(
        request,
        context,
        produce=lambda staged: convert(staged, context=context),
        register=register,
        operation="convert_and_write",
    )


def produce_registered(request, context, *, produce, register, operation):
    target = request.output
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.conversion-", dir=target.parent))
    (staging / "new").mkdir()
    staged = staging / "new" / target.name
    backup = staging / "previous"
    promoted = False
    committed = False
    promotion_locked = False
    recovery = None
    try:
        context.checkpoint("produce " + operation)
        result = produce(replace(request, output=staged, force=False))
        if not result.ok:
            return result
        # Reading and staging remain concurrent; promotion/registration/rollback are serialized.
        _PROMOTION_LOCK.acquire()
        promotion_locked = True
        context.checkpoint("register output")
        signature = _output_signature(staged)
        if target.exists():
            if not request.force:
                raise OutputExistsError("Output appeared while conversion was running")
            os.replace(target, backup)
        try:
            if staged.is_dir():
                publish_directory(staged, target)
            else:
                publish_file(staged, target)
            promoted = True
            record = register(target, expected_sha256=signature[3])
        except BaseException as exc:
            try:
                reason = _rollback(target, staging, backup, signature if promoted else None)
            except (OSError, CPDataKitError) as recovery_error:
                reason = "restore_failed"
                exc.add_note(f"Output recovery retained in {staging}: {recovery_error}")
            if reason:
                recovery = _recovery_evidence(request, staging, backup, reason)
            raise
        committed = True
        if record is not None:
            return with_registered_artifact(result, record)
        artifact = _relative_artifact(target, request.workspace)
        return replace(result, artifact=artifact, value=replace(result.value, artifact=artifact))
    except Exception as exc:
        provenance = (
            _comparison_provenance(request)
            if isinstance(request, ComparisonRequest)
            else {"input_filename": request.data.name, "output_filename": target.name}
        )
        if recovery is not None:
            provenance["recovery"] = recovery
        return _failure(
            operation,
            exc,
            provenance=provenance,
        )
    finally:
        # Preserve a previous output if filesystem recovery itself fails.
        try:
            if committed or (recovery is None and not backup.exists()):
                shutil.rmtree(staging, ignore_errors=True)
        finally:
            if promotion_locked:
                _PROMOTION_LOCK.release()
