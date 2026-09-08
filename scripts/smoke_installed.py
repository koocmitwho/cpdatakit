"""Smoke an installed CPDataKit using the legacy CLI and a real Uvicorn server."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


def smoke() -> dict:
    import cpdatakit
    from cpdatakit import load_hdf5
    from cpdatakit.samples import generate_sample_data

    with tempfile.TemporaryDirectory(prefix="cpdatakit-smoke-") as directory:
        root = Path(directory)
        generate_sample_data(root)
        cli = [sys.executable, "-c", "from cpdatakit.cli import main; raise SystemExit(main())"]
        for args in [
            ["--version"],
            ["ui", "--no-browser", "--help"],
            ["validate", str(root / "synthetic_curve.csv"), "--schema", "curve"],
            ["summary", str(root / "synthetic_curve.csv"), "--schema", "curve"],
            [
                "convert",
                str(root / "synthetic_curve.csv"),
                "--schema",
                "curve",
                "--output",
                str(root / "curve.h5"),
            ],
            [
                "plot",
                str(root / "curve.h5"),
                "--schema",
                "curve",
                "--kind",
                "stress-strain",
                "--output",
                str(root / "curve.png"),
            ],
        ]:
            subprocess.run(cli + args, check=True, capture_output=True, timeout=60)
        rows = len(load_hdf5(root / "curve.h5").data)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        import uvicorn

        from cpdatakit.web import create_app

        server = uvicorn.Server(
            uvicorn.Config(
                create_app(root / "workspace"), host="127.0.0.1", port=port, log_level="error"
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        http = {}
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/health", timeout=2
                    ) as response:
                        assert json.load(response) == {"status": "ok"}
                    break
                except (urllib.error.URLError, TimeoutError):
                    if not thread.is_alive() or time.monotonic() >= deadline:
                        raise RuntimeError("Uvicorn did not become healthy") from None
                    time.sleep(0.1)
            for path in [
                "/health",
                "/",
                "/static/app.js",
                "/static/fields.js",
                "/static/authoring.js",
                "/static/style.css",
            ]:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}{path}", timeout=5
                ) as response:
                    assert response.read()
                    http[path] = response.status
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Uvicorn did not stop")
        return {
            "version": cpdatakit.__version__,
            "module": cpdatakit.__file__,
            "legacy_hdf5_rows": rows,
            "http": http,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(smoke(), indent=2) + "\n", encoding="utf-8")
