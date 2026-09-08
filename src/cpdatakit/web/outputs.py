"""Keep a conversion's promoted output consistent with catalog registration."""

import os
import shutil
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

from ..application.services import _failure, _relative_artifact
from ..exceptions import OutputExistsError

_PROMOTION_LOCK = threading.Lock()


def convert_registered(request, context, *, convert, register):
    target = request.output
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.conversion-", dir=target.parent))
    (staging / "new").mkdir()
    staged = staging / "new" / target.name
    backup = staging / "previous"
    promoted = False
    committed = False
    promotion_locked = False
    try:
        result = convert(replace(request, output=staged, force=False), context=context)
        if not result.ok:
            return result
        # Reading and staging remain concurrent; promotion/registration/rollback are serialized.
        _PROMOTION_LOCK.acquire()
        promotion_locked = True
        context.checkpoint("register output")
        if target.exists():
            if not request.force:
                raise OutputExistsError("Output appeared while conversion was running")
            os.replace(target, backup)
        try:
            os.replace(staged, target)
            promoted = True
            register(target)
        except BaseException as exc:
            try:
                if promoted:
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                if backup.exists():
                    os.replace(backup, target)
            except OSError as recovery_error:
                exc.add_note(f"Previous output retained in {backup}: {recovery_error}")
            raise
        committed = True
        artifact = _relative_artifact(target, request.workspace)
        return replace(result, artifact=artifact, value=replace(result.value, artifact=artifact))
    except Exception as exc:
        return _failure(
            "convert_and_write",
            exc,
            provenance={"input_filename": request.data.name, "output_filename": target.name},
        )
    finally:
        # Preserve a previous output if filesystem recovery itself fails.
        try:
            if committed or not backup.exists():
                shutil.rmtree(staging, ignore_errors=True)
        finally:
            if promotion_locked:
                _PROMOTION_LOCK.release()
