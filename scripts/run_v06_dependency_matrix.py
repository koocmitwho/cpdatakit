"""Install and run one v0.6 dependency matrix cell in an isolated environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

_CANDIDATES = Path(__file__).with_name("v06-dependency-candidates.json")
_SETS = {"lower", "latest"}


def _read_candidates(path: Path = _CANDIDATES) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("packages"), list):
        raise ValueError("Dependency candidates must contain a packages list")
    return payload


def requirements_for(
    candidate_set: str,
    candidates: Path = _CANDIDATES,
    *,
    python_version: str | None = None,
) -> list[str]:
    """Return deterministic pip requirements for one named candidate set."""
    if candidate_set not in _SETS:
        raise ValueError("candidate set must be 'lower' or 'latest'")
    payload = _read_candidates(candidates)
    python_version = python_version or f"{sys.version_info.major}.{sys.version_info.minor}"
    requirements = []
    for item in payload["packages"]:
        requirement = item[candidate_set]
        if isinstance(requirement, dict):
            requirement = requirement.get(python_version)
        if candidate_set == "lower" and (
            not isinstance(requirement, str)
            or not requirement.startswith("==")
            or any(c in requirement for c in "*<>,;")
        ):
            raise ValueError(
                f"lower requires an exact pin for {item['distribution']} on {python_version}"
            )
        requirements.append(
            item["distribution"]
            if requirement == "latest"
            else f"{item['distribution']}{requirement}"
        )
    return requirements


def install_command(
    candidate_set: str,
    *,
    python_executable: str | Path | None = None,
    root: str | Path = ".",
    report: str | Path | None = None,
    python_version: str | None = None,
    candidates: Path = _CANDIDATES,
) -> list[str]:
    """Build a wheel-only pip command for one isolated matrix cell."""
    executable = str(python_executable or sys.executable)
    command = [
        executable,
        "-m",
        "pip",
        "install",
        "--only-binary=:all:",
    ]
    if report is not None:
        command.extend(["--report", str(report)])
    if candidate_set == "latest":
        command.append("--upgrade")
    command.extend(
        [*requirements_for(candidate_set, candidates, python_version=python_version), str(root)]
    )
    return command


def verify_installed(requirements: list[str], installed: dict[str, str]) -> None:
    """Reject missing or drifted exact pins, including normalized package names."""
    actual = {_normalise_distribution_name(k): v for k, v in installed.items()}
    for requirement in requirements:
        if "==" in requirement:
            name, version = requirement.split("==", 1)
            if actual.get(_normalise_distribution_name(name)) != version:
                raise ValueError(f"Installed version does not match {requirement}")


def verify_probe(payload: dict[str, Any], *, candidates: Path = _CANDIDATES) -> None:
    """A report with failed imports/operations must fail the CI cell."""
    for group in ("dependencies", "runtime"):
        for name, item in payload.get(group, {}).items():
            if not item.get("installed"):
                raise ValueError(f"Probe import failed: {name}")
    for name, item in payload.get("operations", {}).items():
        if item.get("status") != "pass":
            raise ValueError(f"Probe operation failed: {name}")
    expected = {item["name"] for item in _read_candidates(candidates)["packages"]}
    operations = {"netcdf:h5netcdf", "netcdf:netcdf4", "zarr:v3", "parquet", "fastapi:httpx"}
    if (
        not expected <= payload.get("dependencies", {}).keys()
        or "cpdatakit" not in payload.get("runtime", {})
        or not operations <= payload.get("operations", {}).keys()
    ):
        raise ValueError("Dependency probe is incomplete")


def verify_environment_module(module: str | Path, environment: Path) -> None:
    """Resolve filesystem aliases before checking the installed module boundary."""
    if not Path(module).resolve().is_relative_to(environment.resolve()):
        raise ValueError(f"Tests would import outside installed environment: {module}")


def extract_source(archive: Path, destination: Path) -> Path:
    """Use the sdist snapshot for tests, metadata and examples."""
    with tarfile.open(archive) as handle:
        handle.extractall(destination, filter="data")
    (source,) = destination.iterdir()
    if not (source / "pyproject.toml").is_file():
        raise ValueError("Source distribution has no project metadata")
    return source


def _venv_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _normalise_distribution_name(value: str) -> str:
    return value.replace("-", "_").replace(".", "_").lower()


def wheel_availability(
    report: dict[str, Any], distributions: tuple[str, ...] | None = None
) -> dict[str, bool]:
    """Return wheel availability for selected distributions in a pip report."""
    expected = (
        {_normalise_distribution_name(name): name for name in distributions}
        if distributions is not None
        else None
    )
    result: dict[str, bool] = {}
    for item in report.get("install", []):
        metadata = item.get("metadata", {})
        name = metadata.get("name")
        url = item.get("download_info", {}).get("url", "")
        if isinstance(name, str):
            normalised = _normalise_distribution_name(name)
            if expected is not None and normalised not in expected:
                continue
            output_name = expected.get(normalised, name) if expected is not None else name
            result[output_name] = isinstance(url, str) and url.split("?", 1)[0].lower().endswith(
                ".whl"
            )
    return dict(sorted(result.items()))


def _write_report(payload: dict[str, Any], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return output


def run_matrix_cell(
    candidate_set: str,
    *,
    root: Path,
    output: Path,
    python_executable: str | Path | None = None,
) -> Path:
    """Create an isolated venv, install candidates, and persist its probe report."""
    root = root.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"Matrix output already exists: {output}")
    evidence = output.with_suffix("")
    evidence.mkdir(parents=True, exist_ok=False)
    payload: dict[str, Any] = {
        "status": "running",
        "candidate_set": candidate_set,
    }
    _write_report(payload, output)
    try:
        with tempfile.TemporaryDirectory(prefix="cpdatakit-matrix-") as directory:
            environment = Path(directory) / "venv"
            subprocess.run(
                [str(python_executable or sys.executable), "-m", "venv", str(environment)],
                check=True,
            )
            executable = _venv_python(environment)

            def run(name: str, command: list[str]) -> None:
                with (evidence / f"{name}.log").open("w", encoding="utf-8") as log:
                    subprocess.run(
                        command, cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True
                    )

            identity = json.loads(
                subprocess.check_output(
                    [
                        str(executable),
                        "-c",
                        "import json, platform, sys; "
                        "print(json.dumps({'python': platform.python_version(), "
                        "'platform': platform.platform(), "
                        "'key': f'{sys.version_info.major}.{sys.version_info.minor}'}))",
                    ],
                    text=True,
                )
            )
            payload.update(identity)
            version_key = identity["key"]
            builder = Path(directory) / "builder"
            subprocess.run([str(executable), "-m", "venv", str(builder)], check=True)
            builder_python = _venv_python(builder)
            run(
                "build-tools",
                [
                    str(builder_python),
                    "-m",
                    "pip",
                    "install",
                    "--only-binary=:all:",
                    "--report",
                    str(evidence / "build-tools-report.json"),
                    "build>=1.2",
                ],
            )
            distributions = evidence / "dist"
            run(
                "build",
                [str(builder_python), "-m", "build", "--outdir", str(distributions), str(root)],
            )
            (wheel,) = distributions.glob("cpdatakit-*.whl")
            (sdist,) = distributions.glob("cpdatakit-*.tar.gz")
            payload["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
            payload["sdist_sha256"] = hashlib.sha256(sdist.read_bytes()).hexdigest()
            root = extract_source(sdist, Path(directory) / "source")
            candidates = root / "scripts/v06-dependency-candidates.json"
            pip_report = evidence / "pip-report.json"
            command = install_command(
                candidate_set,
                python_executable=executable,
                root=wheel,
                report=pip_report,
                python_version=version_key,
                candidates=candidates,
            )
            if candidate_set == "lower":
                key = version_key.replace(".", "")
                command[4:4] = [
                    "--constraint",
                    str(root / "scripts/constraints" / f"lower-py{key}.txt"),
                ]
            command[-1:-1] = ["pytest>=7.4", "pytest-cov>=4.1", "hypothesis>=6.100"]
            run("install", command)
            run("pip-check", [str(executable), "-m", "pip", "check"])
            installed = json.loads(
                subprocess.check_output(
                    [str(executable), "-m", "pip", "list", "--format=json"], text=True
                )
            )
            payload["installed"] = {item["name"]: item["version"] for item in installed}
            required = requirements_for(candidate_set, candidates, python_version=version_key)
            verify_installed(required, payload["installed"])
            pip_payload = json.loads(pip_report.read_text(encoding="utf-8"))
            payload["matrix"] = {
                "requirements": required,
                "wheel_only_install": True,
                "wheel_availability": wheel_availability(pip_payload),
            }
            probe_output = evidence / "probe.json"
            run(
                "probe",
                [
                    str(executable),
                    str(root / "scripts/probe_v06_dependencies.py"),
                    "--candidate-set",
                    candidate_set,
                    "--operations",
                    "--output",
                    str(probe_output),
                ],
            )
            probe = json.loads(probe_output.read_text(encoding="utf-8"))
            payload["probe"] = probe
            verify_probe(probe, candidates=candidates)
            module = subprocess.check_output(
                [str(executable), "-c", "import cpdatakit; print(cpdatakit.__file__)"], text=True
            ).strip()
            verify_environment_module(module, environment)
            payload["installed_module"] = module
            run(
                "tests",
                [
                    str(executable),
                    "-m",
                    "pytest",
                    "--cov=cpdatakit",
                    "--cov-report=term-missing",
                    f"--cov-report=json:{evidence / 'coverage.json'}",
                    "--cov-fail-under=85",
                    f"--junitxml={evidence / 'tests.xml'}",
                ],
            )
            run(
                "smoke",
                [
                    str(executable),
                    str(root / "scripts/smoke_installed.py"),
                    "--output",
                    str(evidence / "smoke.json"),
                ],
            )
            payload["status"] = "passed"
    except Exception as exc:
        payload["status"] = "failed"
        payload["error"] = str(exc)
        raise
    finally:
        _write_report(payload, output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a CPDataKit v0.6 dependency matrix cell")
    parser.add_argument("--candidate-set", choices=sorted(_SETS), required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(
        run_matrix_cell(
            args.candidate_set,
            root=args.root,
            output=args.output,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
