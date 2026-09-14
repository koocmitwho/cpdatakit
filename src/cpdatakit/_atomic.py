"""Atomic publication of completed regular files on the same filesystem."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .exceptions import CPDataKitError, OutputExistsError

logger = logging.getLogger(__name__)


def cleanup_staged_file(staged: str | Path) -> None:
    """Remove a staging name when possible, retaining and logging cleanup failures."""
    try:
        Path(staged).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Retained staging file %s after cleanup failed: %s", staged, exc)


def publish_file(staged: str | Path, target: str | Path, *, force: bool = False) -> Path:
    """Publish a staged file, replacing an existing path only with explicit force.

    Without force, a hard link creates the destination atomically; a concurrent
    destination therefore cannot be overwritten. Both paths must be on the same
    filesystem with hard-link support. Publication failure retains the staged
    file; unsupported filesystems fail without copying. After publication,
    cleanup failure is logged and the extra staging name is retained.
    """
    staged, target = Path(staged), Path(target)
    if force:
        os.replace(staged, target)
    else:
        try:
            os.link(staged, target)
        except FileExistsError as exc:
            raise OutputExistsError(
                f"Output already exists: {target}; pass force=True to replace it"
            ) from exc
        except OSError as exc:
            raise CPDataKitError(
                "Cannot publish without overwriting: use a local filesystem with hard-link "
                "support and stage the file on the same filesystem as its output. "
                f"Original error: {exc}"
            ) from exc
        cleanup_staged_file(staged)
    return target


def publish_directory(staged: str | Path, target: str | Path) -> Path:
    """Move a directory without replacing even an empty concurrent target.

    Uses Windows rename, Linux renameat2(RENAME_NOREPLACE), or macOS
    renamex_np(RENAME_EXCL). Other platforms fail safely instead of using POSIX
    rename, which could replace a user's empty destination directory.
    """
    staged, target = Path(staged), Path(target)
    try:
        if sys.platform == "win32":
            os.rename(staged, target)
        elif sys.platform.startswith("linux") or sys.platform == "darwin":
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            function = "renamex_np" if sys.platform == "darwin" else "renameat2"
            rename = getattr(libc, function, None)
            if rename is None:
                raise CPDataKitError(f"Atomic directory publication requires {function} support")
            if sys.platform == "darwin":
                # Apple xnu bsd/sys/stdio.h declares RENAME_EXCL = 0x00000004.
                rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
                arguments = (os.fsencode(staged), os.fsencode(target), 4)
            else:
                rename.argtypes = [
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_int,
                    ctypes.c_char_p,
                    ctypes.c_uint,
                ]
                arguments = (-100, os.fsencode(staged), -100, os.fsencode(target), 1)
            rename.restype = ctypes.c_int
            if rename(*arguments):
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error), str(target))
        else:
            raise CPDataKitError(
                "Atomic directory publication requires Windows, Linux renameat2, or "
                "macOS renamex_np support; use a file output on this platform."
            )
    except FileExistsError as exc:
        raise OutputExistsError(f"Output already exists: {target}") from exc
    return target
