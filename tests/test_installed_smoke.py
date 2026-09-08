"""Exercise the installed-package release smoke, including an actual HTTP server."""

import json
import subprocess
import sys
from pathlib import Path


def test_release_smoke_runs_legacy_cli_and_serves_packaged_assets(tmp_path):
    root = Path(__file__).parents[1]
    output = tmp_path / "smoke.json"
    result = subprocess.run(
        [sys.executable, str(root / "scripts/smoke_installed.py"), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report["http"]["/health"] == 200
    assert report["http"]["/static/authoring.js"] == 200
    assert report["legacy_hdf5_rows"] > 0
