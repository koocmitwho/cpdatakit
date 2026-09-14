from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_ruff_checks_the_minimum_supported_python_version():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    supported = config["project"]["requires-python"].removeprefix(">=")

    assert config["tool"]["ruff"]["target-version"] == "py" + supported.replace(".", "")


def test_codeql_initialization_and_analysis_use_the_same_pinned_revision():
    workflow = (ROOT / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
    references = dict(re.findall(r"uses: github/codeql-action/([^@\s]+)@([^\s]+)", workflow))

    assert {"init", "analyze"} <= references.keys()
    assert references["init"] == references["analyze"]
    assert re.fullmatch("[0-9a-f]{40}", references["init"])
