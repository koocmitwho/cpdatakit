from __future__ import annotations

import re
from pathlib import Path

import tomllib

from cpdatakit import __version__

ROOT = Path(__file__).parents[1]


def test_v06_python_floor_and_release_version_are_declared() -> None:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert payload["project"]["requires-python"] == ">=3.12"
    assert payload["project"]["version"] == __version__
    assert "Programming Language :: Python :: 3.10" not in payload["project"]["classifiers"]
    assert "Programming Language :: Python :: 3.11" not in payload["project"]["classifiers"]
    assert "Programming Language :: Python :: 3.12" in payload["project"]["classifiers"]
    assert "Programming Language :: Python :: 3.13" in payload["project"]["classifiers"]


def test_v06_ci_matrix_targets_python_312_and_313() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert 'python-version: ["3.12", "3.13"]' in workflow
    assert 'python-version: ["3.10", "3.11", "3.12", "3.13"]' not in workflow
    assert re.search(r"on:\s*\n(?:  [^\n]+\n)*  pull_request:", workflow)
    assert re.search(r"on:\s*\n  push:\s*\n    branches:\s*\[main\]", workflow)


def test_v06_ci_runs_clean_wheel_smoke_on_macos() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "os: [ubuntu-latest, macos-latest, windows-latest]" in workflow
    assert "macos-wheel-smoke:" in workflow
    assert "python -m build --wheel" in workflow
    assert "wheel-env/bin/python -m pip install dist/*.whl" in workflow


def test_v06_docs_keep_v05_older_python_support_note() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    assert "v0.5.x" in english
    assert "for Python 3.10 and 3.11" in english
    assert "v0.5.x" in chinese


def test_v06_publish_workflow_uses_reviewed_artifact_action_pins() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-pypi.yml").read_text(encoding="utf-8")

    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1" in workflow
    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1" in workflow
    assert "actions/upload-artifact@330a01c490aca151604b8cf639adc76d48f6c5d4" not in workflow
    assert "actions/download-artifact@018cc2cf5baa6db3ef3c5f8a56943fffe632ef53" not in workflow
