# v0.6.1 Reliability Release Implementation Plan

**Goal:** Release fixes for metadata loss, failed Zarr replacement, and misreported service jobs.
**Architecture:** Keep existing adapter APIs. Store versioned JSON metadata inside native files; stage Zarr replacement with rollback; explicitly carry failed service outcomes into job state.
**Tech Stack:** Python 3.12+, xarray, PyArrow, Zarr 3, FastAPI, pytest, GitHub Actions OIDC.
**Spec:** User-approved findings in the current project review and release request.

## Constraints
- Preserve v0.5 public API/CLI/schema/HDF5 compatibility and current dependency bounds.
- Preserve the original checkout's 15 uncommitted documentation edits.
- Work in codex/v061-release; publish v0.6.1 after a green release PR.
- CLI/UI format integration and selection performance remain outside this patch.

## Execution
- [x] Add adapter round-trip tests asserting units, provenance, validation, schema and mapping metadata, input immutability, legacy reads and corrupt-envelope rejection. Run them red, then implement shared metadata encoding for NetCDF/Zarr/Parquet.
- [x] Add real-store overwrite tests with a final rename failure; assert old values remain readable, successful replacement works, and rollback failure retains a recoverable backup. Run red, then implement replacement rollback.
- [x] Add an HTTP conversion test that submits a curve against point schema and asserts failed job/catalog state, retained validation findings and no output. Run red, then adapt service outcomes at the queue boundary without changing arbitrary job result semantics.
- [x] Synchronize 0.6.1 version, citation, changelog, release notes and current installation examples. Keep historical release evidence.
- [ ] Run targeted tests, complete coverage suite, Ruff, metadata checks, reproducible wheel/sdist builds, twine, and a clean-wheel CLI/UI smoke.
- [ ] Review the diff, commit/push the release branch, open and merge a green PR, tag the merged commit, publish through existing OIDC workflow, verify PyPI installation, and publish matching GitHub Release artifacts.

## Validation commands
```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& ../cpdatakit/.venv/Scripts/python.exe -m pytest tests/test_format_metadata.py tests/test_format_adapters_v06.py tests/test_web_workflows.py tests/test_jobs.py -q
& ../cpdatakit/.venv/Scripts/python.exe -m pytest --cov=cpdatakit --cov-report=term-missing --cov-fail-under=85
& ../cpdatakit/.venv/Scripts/python.exe -m ruff check .
& ../cpdatakit/.venv/Scripts/python.exe -m ruff format --check .
& ../cpdatakit/.venv/Scripts/python.exe scripts/check_release.py v0.6.1 --dist-dir dist
```
