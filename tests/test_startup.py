from __future__ import annotations

import json
import subprocess
import sys

import pytest

HEAVY_MODULES = {"numpy", "pandas", "xarray", "h5py", "matplotlib", "fastapi"}


def _fresh_process(source: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("module_name", ["cpdatakit", "cpdatakit.application"])
def test_import_and_public_name_discovery_do_not_load_data_dependencies(module_name):
    result = _fresh_process(
        "import importlib, json, sys; "
        f"module = importlib.import_module({module_name!r}); "
        "print(json.dumps({'exports': module.__all__, 'names': dir(module), "
        "'modules': list(sys.modules)}))"
    )

    assert set(result["exports"]) <= set(result["names"])
    assert not HEAVY_MODULES.intersection(result["modules"])


@pytest.mark.parametrize(
    ("arguments", "exit_code", "expected_output"),
    [
        (["--version"], 0, "cpdatakit "),
        (["--help"], 0, "Validate and process declared"),
        (["validate", "--help"], 0, "--schema"),
        (["ui", "--help"], 0, "--no-browser"),
        (["unknown-command"], 2, "invalid choice"),
    ],
)
def test_cli_metadata_and_argument_errors_do_not_load_data_dependencies(
    arguments, exit_code, expected_output
):
    result = _fresh_process(
        "import contextlib, io, json, sys\n"
        "from cpdatakit.cli import main\n"
        "output = io.StringIO()\n"
        "with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):\n"
        "    try:\n"
        f"        status = main({arguments!r})\n"
        "    except SystemExit as exc:\n"
        "        status = exc.code\n"
        "print(json.dumps({'status': status, 'output': output.getvalue(), "
        "'modules': list(sys.modules)}))\n"
    )

    assert result["status"] == exit_code
    assert expected_output in result["output"]
    assert not HEAVY_MODULES.intersection(result["modules"])


@pytest.mark.parametrize("module_name", ["cpdatakit", "cpdatakit.application"])
def test_public_exports_resolve_to_original_objects(module_name):
    result = _fresh_process(
        "import importlib, json\n"
        f"module = importlib.import_module({module_name!r})\n"
        "namespace = {}\n"
        f"exec('from {module_name} import *', namespace)\n"
        "for name in module.__all__:\n"
        "    value = getattr(module, name)\n"
        "    assert namespace[name] is value\n"
        "    if hasattr(value, '__module__'):\n"
        "        origin = importlib.import_module(value.__module__)\n"
        "        assert value is getattr(origin, value.__name__)\n"
        "try:\n"
        "    getattr(module, 'missing_public_name')\n"
        "except AttributeError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('unknown exports must raise AttributeError')\n"
        "print(json.dumps({'resolved': True}))\n"
    )

    assert result["resolved"]
