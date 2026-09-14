# P1 Data Integrity Implementation Plan

**Goal:** Complete the user-approved P1 findings from the 2026-09-11 review, including auxiliary coordinates included in the chat's P1 data-fidelity row.

**Architecture:** Preserve existing public contracts. Plot stored units; reject lossy scalar-coordinate flattening; share scientific validation below the application layer; extend HDF5 metadata additively; snapshot registered web artifacts while retaining caller-selected output paths.

**Tech Stack:** Python >=3.12, pandas, xarray, h5py, Pint, Matplotlib, FastAPI, SQLite, pytest.

**Spec:** User-approved review F01-F05 plus auxiliary-coordinate preservation from F06.

**Execution:** Inline in this task, as explicitly chosen by the user. Each component follows a failing regression, implementation, and focused verification. A local Git commit was authorized after validation; unrelated worktrees remain unchanged.

## Task 1: Unit labels and explicit mappings

Files: tests/test_plot_unit_integrity.py, tests/test_plotting.py; src/cpdatakit/plotting.py, src/cpdatakit/normalization.py.

Consumes Dataset metadata.units and ProfileSchema declarations. Existing plot signatures and normalization signatures remain unchanged.

- [x] Add regressions asserting actual Pa/mm/degC labels and unchanged numeric values, plus mapped-unit conflict rejection and alias compatibility.
- [x] Run the focused tests and confirm current labels/conflict handling fail.
- [x] Make plot labels resolve dataset metadata first, falling back to the schema for legacy unannotated inputs; use a neutral curve legend. Reject a mapping input_unit that contradicts the stored unit before conversion.
- [x] Verify direct plotting, real HDF5-to-SVG service output, explicit affine conversion, and unchanged input metadata.

Acceptance example:
~~~python
assert ax.get_ylabel() == "Stress [Pa]"
assert ax.lines[0].get_ydata().tolist() == [0.0, 100_000_000.0]
~~~

## Task 2: Scalar coordinates and HDF5 fidelity

Files: tests/test_scientific_integrity.py; src/cpdatakit/data/scientific.py, src/cpdatakit/io/hdf5_v2.py.

Consumes ScientificDataset, Selection, and resolved schema 2.0. Scalar non-record coordinates are rejected with the existing LossyConversionError. Optional global attributes in the HDF5 metadata group remain backward readable when absent.

- [x] Add regressions for scalar coordinates, nested JSON global attributes, old HDF5 envelopes, malformed global metadata, auxiliary/scalar coordinates, and requested field order.
- [x] Observe current failures.
- [x] Reject every non-record coordinate in tabular conversion; store and restore global attributes separately from dataset metadata; retain coordinates by dimensional association and preserve requested field order.
- [x] Verify full and selected round trips against xarray and preserve original values.

Acceptance example:
~~~python
xr.testing.assert_identical(
    selected.data,
    full.data[["temperature"]].isel(time=slice(1, 3)),
)
~~~

## Task 3: Truthful HDF5 validation

Files: tests/test_hdf5_validation_integrity.py, tests/test_selective_reads.py; src/cpdatakit/data/validation.py, src/cpdatakit/data/units.py, src/cpdatakit/application/scientific.py, src/cpdatakit/application/units.py, src/cpdatakit/io/hdf5_v2.py, src/cpdatakit/io/__init__.py, src/cpdatakit/application/services.py.

Produces shared validate_scientific(value, schema) and declared_unit(array, metadata, name), retaining application imports as compatible re-exports. The writer gains keyword-only allow_invalid=False and computes fresh validation summaries, with explicit invalid writing recording the actual failure.

- [x] Add regressions for NaN, wrong dtype, missing/conflicting units, forged/stale validation metadata, explicit invalid writes, and application forwarding.
- [x] Confirm the existing writer incorrectly accepts invalid data or stores a false summary.
- [x] Share validation in the data layer; validate before output mutation, reject invalid values by default, preserve actual unit declarations, and forward allow_invalid from the service.
- [x] Verify old valid fixtures, schema/HDF5 compatibility, and source immutability.

Acceptance example:
~~~python
with pytest.raises(DataValidationError):
    write_hdf5_v2(invalid, output, schema)
assert not output.exists()
~~~

## Task 4: Artifact identity

Files: tests/test_artifact_versions.py; src/cpdatakit/web/artifacts.py, src/cpdatakit/web/app.py, src/cpdatakit/web/outputs.py, src/cpdatakit/web/slices.py, src/cpdatakit/web/workbench.py.

Consumes the existing catalog registration API. Stores each managed artifact under a unique project-local snapshot path and returns that record's path in web job results. The requested output remains available as the caller-selected file. Legacy records with mismatching content are rejected at download, without fabricating missing history.

- [x] Add HTTP regressions for two successful overwrites, independent old/new downloads, restart, directory snapshots, registration cleanup, and changed legacy artifacts.
- [x] Confirm old records currently return replacement bytes.
- [x] Add one snapshot-registration helper with rollback cleanup, bind web results to registered paths, protect snapshot storage from output forms, and verify content against its recorded digest before serving.
- [x] Verify conversion concurrency, cancellation, reports/plots/slices, and existing catalog tests without changing SQLite's public record-only contract.

Acceptance example:
~~~python
assert sha256(first_download.content).hexdigest() == first_record.sha256
assert first_download.content != second_download.content
~~~

## Completion

- [x] Update data model, HDF5, units/plot, and workbench documentation for the implemented contracts.
- [x] Run focused regressions, full pytest with >=85% coverage, Ruff check/format, pip check, and git diff --check.
- [x] Inspect the final diff for scope and preserve unrelated uncommitted documents.
- [x] Record exact verification results without claiming unrun platform or publication checks.

Final evidence: [P1 verification](../../verification/2026-09-11-p1-data-integrity.md). Source suite: 615 passed, 87.66% coverage. Installed-wheel P1 suite: 49 passed; real HTTP smoke passed.
