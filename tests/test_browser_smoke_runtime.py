"""Keep a virtual environment's launcher identity across the smoke process boundary."""

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest


def test_browser_smoke_does_not_dereference_the_requested_runtime(tmp_path, monkeypatch):
    """Resolving a POSIX venv's Python symlink would launch the base interpreter."""
    installed = tmp_path / "installed-runtime"
    installed.mkdir()
    (installed / "python").write_text("launcher identity fixture", encoding="utf-8")
    runtime = tmp_path / "browser-runtime"
    if os.name == "nt":
        # Junctions exercise real Path.resolve behavior without Windows symlink privileges.
        import _winapi

        _winapi.CreateJunction(str(installed), str(runtime))
    else:
        runtime.symlink_to(installed, target_is_directory=True)
    launcher = runtime / "python"
    assert launcher.resolve() != launcher.absolute()

    # Browser tools belong to another environment. This test stops at preparation,
    # before any browser/server API is called, and needs no Playwright installation.
    browser_api = ModuleType("playwright.sync_api")
    browser_api.expect = None
    browser_api.sync_playwright = None
    monkeypatch.setitem(sys.modules, "playwright.sync_api", browser_api)
    script = Path(__file__).parents[1] / "scripts/smoke_browser_installed.py"
    spec = importlib.util.spec_from_file_location("browser_smoke_runtime", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class PreparationReached(Exception):
        pass

    def stop_at_preparation(python, code, root, environment):
        raise PreparationReached(python)

    monkeypatch.setattr(module, "run_child", stop_at_preparation)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(script), "--python", "browser-runtime/python", "--output-dir", "evidence"],
    )
    with pytest.raises(PreparationReached) as reached:
        module.main()
    assert reached.value.args[0] == launcher.absolute(), (
        "The subprocess must receive the venv launcher, not its base-interpreter target"
    )
