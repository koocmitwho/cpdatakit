from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "run_v06_dependency_matrix.py"
WORKFLOW = ROOT / ".github" / "workflows" / "v06-dependency-matrix.yml"


def _load_matrix_module():
    spec = importlib.util.spec_from_file_location("run_v06_dependency_matrix", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load dependency matrix runner: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dependency_matrix_builds_deterministic_lower_and_latest_requirements() -> None:
    module = _load_matrix_module()

    lower = module.requirements_for("lower", python_version="3.12")
    latest = module.requirements_for("latest")

    assert lower[0] == "numpy==2.0.0"
    assert "h5py==3.11.0" in lower
    assert latest[0] == "numpy"
    assert "python-multipart" in latest
    assert lower != latest
    with pytest.raises(ValueError, match="candidate set"):
        module.requirements_for("unsupported")


def test_dependency_matrix_install_command_requires_binary_wheels() -> None:
    module = _load_matrix_module()

    command = module.install_command("lower")

    assert "--only-binary=:all:" in command
    assert any(item.startswith("numpy==") for item in command)
    assert command[-1] == "."


def test_dependency_matrix_wheel_report_excludes_editable_project_install() -> None:
    module = _load_matrix_module()

    report = {
        "install": [
            {
                "metadata": {"name": "cpdatakit"},
                "download_info": {"url": "file:///workspace/cpdatakit"},
            },
            {
                "metadata": {"name": "numpy"},
                "download_info": {"url": "https://files.example/numpy.whl"},
            },
        ]
    }

    assert module.wheel_availability(report, ("numpy",)) == {"numpy": True}


def test_dependency_matrix_workflow_covers_supported_platform_matrix() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "ubuntu-latest" in text
    assert "macos-latest" in text
    assert "windows-latest" in text
    assert 'python-version: ["3.12", "3.13"]' in text
    assert "dependency-set: [lower, latest]" in text
    assert "scripts/run_v06_dependency_matrix.py" in text
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in text
    assert "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in text


@pytest.mark.parametrize("version", ["3.12", "3.13"])
def test_lower_requirements_are_exact_for_target_python(version):
    module = _load_matrix_module()
    requirements = module.requirements_for("lower", python_version=version)
    assert all("==" in item and ">" not in item and "<" not in item for item in requirements)
    assert any(item.startswith("matplotlib==") for item in requirements)
    assert any(item.startswith("pint==") for item in requirements)


def test_lower_rejects_ranges_in_candidate_input(tmp_path):
    import json

    path = tmp_path / "candidates.json"
    path.write_text(json.dumps({"packages": [{"distribution": "numpy", "lower": ">=2"}]}))
    with pytest.raises(ValueError, match="exact"):
        _load_matrix_module().requirements_for("lower", path)


def test_matrix_rejects_silent_installed_version_drift():
    module = _load_matrix_module()
    with pytest.raises(ValueError, match="numpy"):
        module.verify_installed(["numpy==2.0.0"], {"numpy": "2.1.0"})
    module.verify_installed(["Jinja2==3.1.0"], {"jinja2": "3.1.0"})


def test_matrix_rejects_failed_probe_operations():
    module = _load_matrix_module()
    with pytest.raises(ValueError, match="parquet"):
        module.verify_probe(
            {"dependencies": {}, "runtime": {}, "operations": {"parquet": {"status": "fail"}}}
        )


def test_lower_install_does_not_upgrade_and_installs_wheel():
    command = _load_matrix_module().install_command("lower", root="candidate.whl")
    assert "--upgrade" not in command
    assert "-e" not in command
    assert command[-1] == "candidate.whl"


def test_install_command_selects_pins_for_requested_interpreter():
    command = _load_matrix_module().install_command("lower", python_version="3.13")
    assert "numpy==2.1.0" in command
    assert "numpy==2.0.0" not in command


def test_source_snapshot_is_independent_of_later_checkout_edits(tmp_path):
    import tarfile

    source = tmp_path / "cpdatakit-0.8.0"
    source.mkdir()
    metadata = source / "pyproject.toml"
    metadata.write_text('version = "0.8.0"')
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(source, arcname=source.name)
    snapshot = _load_matrix_module().extract_source(archive, tmp_path / "snapshot")
    metadata.write_text('version = "0.9.0"')
    assert (snapshot / "pyproject.toml").read_text() == 'version = "0.8.0"'


@pytest.mark.parametrize("payload", [{}, {"dependencies": {}, "runtime": {}, "operations": {}}])
def test_matrix_rejects_incomplete_probe(payload):
    with pytest.raises(ValueError, match="incomplete"):
        _load_matrix_module().verify_probe(payload)


def test_probe_completeness_uses_frozen_candidate_list(tmp_path):
    import json

    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"packages": [{"name": "fixture"}]}))
    payload = {
        "dependencies": {"fixture": {"installed": True}},
        "runtime": {"cpdatakit": {"installed": True}},
        "operations": {
            name: {"status": "pass"}
            for name in ("netcdf:h5netcdf", "netcdf:netcdf4", "zarr:v3", "parquet", "fastapi:httpx")
        },
    }
    _load_matrix_module().verify_probe(payload, candidates=candidates)
