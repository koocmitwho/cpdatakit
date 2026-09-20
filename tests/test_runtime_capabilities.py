"""Development clients must not determine whether the installed UI can serve."""

import json
import subprocess
import sys


def test_ui_serves_and_is_available_without_httpx(tmp_path):
    probe = r"""
import asyncio
import importlib.abc
import importlib.util
import json
import sys

class BlockHttpx(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "httpx" or fullname.startswith("httpx."):
            raise ModuleNotFoundError("httpx deliberately absent")

sys.meta_path.insert(0, BlockHttpx())
from cpdatakit.application import discover_capabilities
from cpdatakit.web import create_app

app = create_app(sys.argv[1])
messages = []
async def receive():
    return {"type": "http.request", "body": b"", "more_body": False}
async def send(message):
    messages.append(message)
async def run():
    async with app.router.lifespan_context(app):
        await app({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                   "http_version": "1.1", "method": "GET", "scheme": "http",
                   "path": "/health", "raw_path": b"/health", "query_string": b"",
                   "headers": [(b"host", b"127.0.0.1")],
                   "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80)}, receive, send)
asyncio.run(run())
app.state.jobs.shutdown()
ui = next(item for item in discover_capabilities().value.items if item.name == "local-ui")
print(json.dumps({"status": messages[0]["status"], "available": ui.available,
                  "reason": ui.reason, "loaded_httpx": "httpx" in sys.modules}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(tmp_path / "workspace")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["status"] == 200
    assert not evidence["loaded_httpx"]
    assert evidence["available"], evidence
