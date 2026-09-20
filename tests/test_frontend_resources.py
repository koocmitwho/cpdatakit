"""Execute shipped browser resource interactions without adding a JS dependency."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "behavior",
    [
        "historical",
        "selection",
        "load-more",
        "slice",
        "coalesce",
        "stale-page",
        "schema-selection",
        "slice-legacy",
        "active-history",
        "terminal-refresh-race",
        "stale-running",
        "pending-persistence",
        "validation-context",
        "validation-history",
        "output-conflict",
        "artifact-actions",
        "authoring-context",
        "authoring-save",
        "mapping-scope",
    ],
)
def test_frontend_resource_behavior(behavior):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for browser logic verification")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("frontend_resource_harness.cjs")), behavior],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
