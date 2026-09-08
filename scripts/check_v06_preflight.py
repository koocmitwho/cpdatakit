"""Check that the v0.6 preflight artifacts are complete and consistent."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import tomllib

_REQUIRED = (
    "docs/superpowers/specs/2026-09-02-local-first-scientific-platform-design.md",
    "docs/superpowers/specs/2026-09-02-v0.6-preflight-design.md",
    "docs/superpowers/plans/2026-09-02-v0.6-preflight.md",
    "docs/v0.6-dependency-probe.md",
    "docs/data-model-2.0.md",
    "docs/schema-2.0.md",
    "docs/hdf5-2.0.md",
    "docs/application-services.md",
    "docs/local-ui-security.md",
    ".github/workflows/v06-dependency-matrix.yml",
    "scripts/probe_v06_dependencies.py",
    "scripts/run_v06_dependency_matrix.py",
    "scripts/v06-dependency-candidates.json",
    "scripts/generate_hdf5_v2_fixtures.py",
    "tests/compat/v0.5-public-contract.json",
    "tests/test_v05_public_contract.py",
    "tests/test_v06_project_metadata.py",
    "tests/test_v06_dependency_probe.py",
    "tests/test_v06_dependency_matrix.py",
    "tests/test_v06_reference_case.py",
    "tests/test_schema_v2_fixtures.py",
    "tests/test_hdf5_v2_fixtures.py",
    "tests/test_format_interfaces.py",
    "tests/test_v06_preflight_documents.py",
    "tests/test_v06_preflight_gate.py",
    "tests/test_application_services.py",
    "tests/test_application_report_services.py",
    "tests/test_application_plot_services.py",
    "tests/test_scientific_dataset.py",
    "examples/thermal-field-v2/reference.json",
    "examples/thermal-field-v2/malformed/ambiguous-record-axis.json",
    "examples/thermal-field-v2/malformed/object-array.json",
    "examples/thermal-field-v2/README.md",
    "tests/fixtures/schema-v2/standalone.json",
    "tests/fixtures/schema-v2/base.json",
    "tests/fixtures/schema-v2/fragment-space.json",
    "tests/fixtures/schema-v2/fragment-temperature.json",
    "tests/fixtures/schema-v2/composed.json",
    "tests/fixtures/schema-v2/expected-resolved.json",
    "tests/fixtures/schema-v2/invalid-cycle.json",
    "tests/fixtures/schema-v2/invalid-collision.json",
    "tests/fixtures/hdf5-v2/manifest.json",
    "src/cpdatakit/data/scientific.py",
    "src/cpdatakit/data/__init__.py",
    "src/cpdatakit/application/services.py",
    "src/cpdatakit/application/__init__.py",
    "src/cpdatakit/application/capabilities.py",
    "src/cpdatakit/formats/base.py",
    "src/cpdatakit/formats/__init__.py",
    "src/cpdatakit/formats/netcdf.py",
    "src/cpdatakit/formats/zarr.py",
    "src/cpdatakit/formats/parquet.py",
    "src/cpdatakit/schemas/v2.py",
    "src/cpdatakit/io/hdf5_v2.py",
    "src/cpdatakit/catalog/__init__.py",
    "src/cpdatakit/catalog/sqlite.py",
    "src/cpdatakit/jobs/__init__.py",
    "src/cpdatakit/jobs/manager.py",
    "src/cpdatakit/web/__init__.py",
    "src/cpdatakit/web/app.py",
    "src/cpdatakit/web/templates/index.html",
    "src/cpdatakit/web/static/style.css",
    "src/cpdatakit/web/static/app.js",
    "tests/test_application_capabilities.py",
    "tests/test_application_schema_services.py",
    "tests/test_schema_v2.py",
    "tests/test_hdf5_v2.py",
    "tests/test_catalog.py",
    "tests/test_jobs.py",
    "tests/test_web.py",
    "tests/test_web_workflows.py",
    "tests/test_cli_ui.py",
    "tests/test_v06_release_metadata.py",
)
_ABSENT_PRODUCTION_PATHS = ("src/cpdatakit/scientific_dataset.py",)
_BUILTIN_HASHES = {
    "curve": "6234e8cd78f0ad9f0251cd233fd7111f6c62fc17835289ab521369880977fa44",
    "field2d": "766d6ee0e1ad3b2a77d0fdffb3a5aec4274a33490a51315676fb48d57817e4b0",
    "point": "c668c4b05cf542ab4c3af8aba7b1b03ebd4a20d49186773b2a5a229f27e6c59b",
}
_SCHEMA_V2_HASH = "65e820c6f5ed729e47e52b6a6592fb56352644a635e547c5ffde88014c431619"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def check_preflight(root: Path) -> list[str]:
    """Return deterministic failures for an incomplete or inconsistent preflight."""
    root = Path(root)
    failures: list[str] = []
    for relative in _REQUIRED:
        if not (root / relative).exists():
            failures.append(f"missing preflight artifact: {relative}")
    for relative in _ABSENT_PRODUCTION_PATHS:
        if (root / relative).exists():
            failures.append(
                f"production v0.6 module is present before preflight handoff: {relative}"
            )
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        if project.get("project", {}).get("requires-python") != ">=3.12":
            failures.append("pyproject.toml does not declare requires-python >=3.12")
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        failures.append("cannot read pyproject.toml for v0.6 Python floor")
    snapshot_path = root / "tests/compat/v0.5-public-contract.json"
    if snapshot_path.is_file():
        try:
            snapshot = _json(snapshot_path)
            actual = snapshot.get("builtin_schema_hashes")
            if actual != _BUILTIN_HASHES:
                failures.append("v0.5 compatibility snapshot has unexpected built-in schema hashes")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            failures.append("v0.5 compatibility snapshot is not valid JSON")
    resolved_path = root / "tests/fixtures/schema-v2/expected-resolved.json"
    if resolved_path.is_file():
        try:
            resolved = _json(resolved_path)["resolved"]
            canonical = json.dumps(
                resolved, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != _SCHEMA_V2_HASH:
                failures.append("schema v2 resolved fixture hash does not match the pinned value")
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            failures.append("schema v2 resolved fixture is invalid")
    candidates_path = root / "scripts/v06-dependency-candidates.json"
    if candidates_path.is_file():
        try:
            candidates = _json(candidates_path)
            packages = candidates.get("packages")
            if candidates.get("python_minimum") != "3.12" or not isinstance(packages, list):
                failures.append(
                    "dependency candidate file has invalid Python floor or package list"
                )
            elif any(
                not isinstance(item, dict)
                or not all(
                    isinstance(item.get(key), str) and item[key]
                    for key in ("name", "distribution", "module")
                )
                or not isinstance(item.get("lower"), dict)
                or set(item["lower"]) != {"3.12", "3.13"}
                or not all(
                    isinstance(v, str) and v.startswith("==") for v in item["lower"].values()
                )
                for item in packages
            ):
                failures.append("dependency candidate file contains an incomplete package entry")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            failures.append("dependency candidate file is not valid JSON")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check CPDataKit v0.6 preflight artifacts")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    failures = check_preflight(args.root)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("CPDataKit v0.6 preflight check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
