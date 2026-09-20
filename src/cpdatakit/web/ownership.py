"""Operating-system ownership, released by close or process death (never by PID)."""

import os
import threading
from contextvars import ContextVar
from pathlib import Path

from ..exceptions import CPDataKitError

_IN_REQUEST = ContextVar("cpdatakit_in_request", default=False)


class RequestDrain:
    """Keep ownership through complete ASGI responses, including their cleanup."""

    def __init__(self, accepting):
        self._accepting = accepting
        self._condition = threading.Condition()
        self._active = 0
        self._closed = False

    def enter(self):
        with self._condition:
            if self._closed or not self._accepting():
                return False
            self._active += 1
            return True

    def leave(self):
        with self._condition:
            self._active -= 1
            self._condition.notify_all()

    def can_wait(self):
        return not _IN_REQUEST.get()

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.wait_for(lambda: self._active == 0)


class OwnershipMiddleware:
    def __init__(self, app, *, drain):
        self.app = app
        self.drain = drain

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if not self.drain.enter():
            from fastapi.responses import JSONResponse

            response = JSONResponse(
                {"status": "failed", "detail": "This workbench is closed."}, status_code=503
            )
            return await response(scope, receive, send)
        token = _IN_REQUEST.set(True)
        try:
            return await self.app(scope, receive, send)
        finally:
            _IN_REQUEST.reset(token)
            self.drain.leave()


class WorkspaceOwnership:
    """Hold a kernel lock on a stable file; the file itself is never unlinked."""

    def __init__(self, workspace: Path):
        workspace.mkdir(parents=True, exist_ok=True)
        path = workspace / ".cpdatakit.lock"
        if path.is_symlink():
            raise CPDataKitError("The workspace lock cannot be a symbolic link")
        self._guard = threading.Lock()
        self._file = path.open("a+b")
        try:
            self._file.seek(0, os.SEEK_END)
            if self._file.tell() == 0:
                self._file.write(b"\0")
                self._file.flush()
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._file.close()
            raise CPDataKitError("This workspace is already in use by another workbench") from exc

    def close(self):
        with self._guard:
            if not self._file.closed:
                # Closing releases the kernel lock, including on abnormal process exit.
                self._file.close()
