from __future__ import annotations

import importlib.util
from pathlib import Path

import tomllib

from cpdatakit import __version__

ROOT = Path(__file__).parents[1]


def _load_release_checker():
    path = ROOT / "scripts" / "check_release.py"
    spec = importlib.util.spec_from_file_location("check_release", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load release checker: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v06_version_and_runtime_dependency_metadata() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["version"] == __version__
    assert project["requires-python"] == ">=3.12"
    dependencies = set(project["dependencies"])
    assert {
        "numpy>=2.0,<3",
        "pandas>=2.2,<3",
        "xarray>=2026.7,<2027",
        "zarr>=3.1,<4",
        "pyarrow>=25,<26",
        "h5py>=3.8,<4",
        "h5netcdf>=1.5,<2",
        "netCDF4>=1.7,<2",
        "fastapi>=0.141,<1",
        "uvicorn>=0.35,<1",
        "Jinja2>=3.1,<4",
        "python-multipart>=0.0.20,<1",
    } <= dependencies
    assert "httpx>=0.28,<1" in set(project["optional-dependencies"]["dev"])


def test_v06_ci_covers_supported_matrix_and_clean_wheel_ui_smoke() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "os: [ubuntu-latest, macos-latest, windows-latest]" in workflow
    assert 'python-version: ["3.12", "3.13"]' in workflow
    assert "macos-wheel-smoke:" in workflow
    assert "cpdatakit ui" in workflow
    assert "--no-browser" in workflow


def test_v06_publish_is_tag_triggered_and_uses_trusted_publishing() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-pypi.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch" not in workflow
    assert 'tags: ["v*.*.*"]' in workflow
    assert "RELEASE_TAG: ${{ github.ref_name }}" in workflow
    assert "GITHUB_REF_TYPE" in workflow
    assert "id-token: write" in workflow
    assert "pypa/gh-action-pypi-publish@" in workflow


def test_v06_release_metadata_is_synchronized_and_v05_compatibility_is_documented() -> None:
    checker = _load_release_checker()

    assert checker.verify_release(f"v{__version__}") == __version__
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "v0.6.0" in english
    assert "v0.6.0" in chinese
    assert "v0.5.x" in english
    assert "Python 3.10 and 3.11" in english
    assert "v0.5.x" in chinese


def test_v06_release_notes_exist_for_the_tag() -> None:
    notes = ROOT / ".github" / "release-notes" / "v0.6.0.md"
    assert notes.is_file()
    assert "CPDataKit v0.6.0" in notes.read_text(encoding="utf-8")


def test_v06_mainline_docs_close_preflight_claims_after_release() -> None:
    roadmap = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
    probe = (ROOT / "docs" / "v0.6-dependency-probe.md").read_text(encoding="utf-8")
    schemas = (ROOT / "docs" / "schema-2.0.md").read_text(encoding="utf-8")
    hdf5 = (ROOT / "docs" / "hdf5-2.0.md").read_text(encoding="utf-8")

    assert "v0.6.0 (released 2026-09-03)" in roadmap
    assert "12/12" in probe
    assert "runtime dependencies are promoted in v0.6.0" in probe
    assert "Next probe gate" not in probe
    assert schemas.startswith("# CPDataKit schema 2.0\n")
    assert hdf5.startswith("# CPDataKit HDF5 2.0\n")
