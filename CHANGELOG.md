# Changelog

All notable changes follow Keep a Changelog; versions follow Semantic Versioning.

## [Unreleased]

## [0.8.0] - 2026-09-08

### Fixed

- Apply field and slice selection before loading NetCDF, Zarr and HDF5 2.0 arrays, while preserving
  existing record selection semantics and the pandas index behavior of Parquet column selection.
  Parquet skips unrelated row groups.
- Show read and conversion progress at cancellation checkpoints. Completed, registered outputs
  keep their successful status and result reference when a cancellation request arrives later.
- Report conflicting array unit declarations. Reset storage encoding after unit conversion or
  dimension transposition so NetCDF output retains the converted values.

### Added

- Heatmaps in Python and the workbench, with variable and slice selection, adjustable color limits
  and PNG export. Images include units and slice positions.
- Editable schema drafts and mapping previews in Python, CLI and the workbench. Mapping version
  2.0 renames dimensions and orders axes according to the target schema.
- Batch conversion from a shared configuration, with validation for each file, output-conflict checks
  and an atomic progress manifest. Retries check output hashes and retain earlier successful results.
- A KupferDigital/experiment-to-CPFE example that converts existing experimental curves and FE
  training data, keeping the source records, license, transformations and case partitions.
- A reproducible benchmark that measures read time and peak memory in separate processes.

### Release preparation

- Require h5py >=3.11, Pint >=0.24.4 and netCDF4 >=1.7.2 following reproduced NumPy 2 import
  incompatibilities and a classic NetCDF read failure.
- Pin lower dependency combinations for each Python version, check installed versions and run CI
  acceptance against the installed wheel. Reports record the combinations tested and remaining
  declared-floor gaps.
- Update project links to koocmitwho/cpdatakit. Scientific snapshots retain their original URLs.

## [0.7.0] - 2026-09-06

### Added

- Connected NetCDF, Zarr 3, Parquet and HDF5 2.0 to application inspection, validation,
  conversion and reports. Added schema 2.0 array checks and summaries. HDF5 output follows
  the data model. Callers can also select NetCDF, Zarr or tabular Parquet writers.
- Added project workbench pages with schema upload/selection, file and Zarr directory uploads,
  validation findings, conversion/report jobs, cancellation, report previews and downloads.
  Custom schema 1.0 profiles also work with the plotting API.
- Preserved v0.5 public contracts and the default HDF5 1.0 output for tabular data.
  See [the v0.7 workbench guide](docs/v0.7-workbench.md).

## [0.6.1] - 2026-09-06

### Fixed

- Save and restore units, provenance, validation summaries, schema references, mappings and custom
  JSON metadata in NetCDF, Zarr 3 and Parquet. Older files remain readable. Readers report an error
  for malformed metadata envelopes.
- Restore the old Zarr output when replacement fails. If restoration also fails, the original
  remains in a named backup next to the output.
- Set failed application jobs and their catalog entries to `failed`. Job polling returns the
  service error and validation findings.

## [0.6.0] - 2026-09-03

### Added

- Added an xarray-backed `ScientificDataset`, schema 2.0 local composition, and HDF5 2.0
  N-dimensional read/write support while keeping the tabular `Dataset` and HDF5 1.0 contracts.
- Added lossless NetCDF, Zarr 3, and Parquet adapters with capability checks and explicit optional
  dependency diagnostics.
- Added typed application services, deterministic capability discovery, a transactional SQLite
  catalog, cooperative in-process jobs, and the loopback-only `cpdatakit ui` workbench.
- Added bundled Jinja templates and static assets, project uploads, bounded inspect, validation,
  conversion, report, comparison, plot, and job polling/cancellation routes.

### Changed

- Set the v0.6 runtime floor to Python 3.12 and promoted the measured lower-bound dependency matrix;
  the v0.5.x line remains the compatibility path for Python 3.10 and 3.11.
- Added clean-wheel UI smoke coverage and tag-triggered PyPI Trusted Publishing checks across the
  Python 3.12/3.13 Windows, macOS, and Linux matrix.

### v0.6 implementation slice

- Added typed application services for import/inspect, schema/mapping resolution, validation/summary,
  HDF5 1.0 conversion, report generation, report comparison, and declared plotting. Existing CLI
  commands route through the core slice without changing v0.5 command names or exit-code behavior.

## [0.5.0] - 2026-09-02

### Added

- External JSON schemas can use non-CP profile names throughout validation, normalization, HDF5,
  inspection, reports, comparisons, and CLI workflows.
- Added schema-driven x-y plotting and a complete deterministic thermal-cycle example.
- Added backward-compatible adapter descriptors, format detection, and an in-process registry.

### Changed

- Position CPDataKit as a schema-first scientific and engineering data-contract tool, with crystal
  plasticity retained as the first supported vertical.
- Separate generic scalar statistics from CP-specific grain and phase identifier summaries while
  preserving built-in profile output.
- Require verified embedded schemas for non-built-in HDF5 profiles while retaining legacy built-in
  CPDataKit HDF5 1.0 reads.

### Fixed

- Do not report a missing-unit structural risk for schema-declared string fields in HDF5 files.

## [0.4.0] - 2026-08-31

### Added

- `diff_schemas()` and `cpdatakit schema diff` compare contract compatibility deterministically.
- `compare_reports()` and `cpdatakit compare` write offline bundles with JSON, Markdown, HTML, and
  a manifest of member hashes.

### Fixed

- Reject non-canonical embedded HDF5 schema JSON even when its semantic hash matches.
- Return a structured `invalid_shape` finding for ragged shaped values instead of leaking a NumPy
  shape-conversion exception.
- Refuse empty HDF5 output before creating a temporary file, keeping writer and reader contracts
  closed.

## [0.3.0] - 2026-08-30

### Added

- Explicit `load_hdf5()` field/range reads and `iter_hdf5_chunks()` lazy chunk iteration, with
  metadata-preserving `Dataset` results and record-axis slicing.
- Opt-in record-axis HDF5 storage chunking through `write_hdf5(..., hdf5_chunk_size=N)`; the
  default layout remains unchanged when the option is omitted.
- A deterministic HDF5 read benchmark covering full, selected-field, and chunked reads.
- 100k- and 1M-record benchmark commands that report storage chunk size, exact record counts,
  elapsed time, and peak RSS where available.
- An adapter contribution acceptance checklist covering format evidence, licensing, fixtures,
  conventions, offline tests, ambiguity handling, and dependency boundaries.
- A read-only DAMASK DADF5 adapter for explicit increment/branch/dataset selections, with source
  metadata and no DAMASK runtime dependency.
- Added `inspect` for CSV, JSON, CPDataKit HDF5, and clear DAMASK DADF5 selections. It reads HDF5
  metadata and missing values in bounded slices, then shows chunks, provenance, adapter details, and
  optional schema findings.
- Added offline `report` output in HTML, Markdown, and canonical JSON. Reports include the schema,
  validation errors and warnings, descriptive statistics, sanitized provenance, and overwrite
  protection. The CPDataKit HDF5 1.0 format and existing CLI commands stay unchanged.
- Added helpers for canonical schema JSON and SHA-256 hashes. New HDF5 files now carry the schema
  snapshot, with an optional `schema_uri` that is recorded but never fetched.

### Fixed

- Preserve duplicate index and duplicate record findings when native HDF5 inspection crosses chunk
  boundaries.
- Keep duplicate normalization compatible with the pandas 2.0 dependency floor.
- Require all eight CPDataKit HDF5 root attributes, exact supported version markers, and JSON
  metadata objects; reject missing, unsupported, malformed, or inconsistent envelopes.
- Refuse invalid HDF5 validation results unless `allow_invalid=True` is explicit, and make writes
  atomic with temporary-file cleanup after serialization failures.
- Fixed unit conversion for declared vector, matrix, and tensor fields. Values keep their
  per-record shape, and malformed shaped values report the record that failed.
- Check embedded HDF5 schema JSON, profile/version matches, and hashes while keeping legacy
  format-1.0 files without snapshots readable.

### Changed

- Make `Dataset.copy()` isolate nested metadata so copied working datasets cannot mutate the
  original metadata tree.
- Make `ProfileSchema.conventions` recursively immutable in memory while keeping
  `schema_to_dict()` and `schema_to_json()` output as JSON objects and lists.
- Keep `FieldSchema` collection fields immutable in memory while preserving list-shaped schema JSON
  output.
- Enforce an 85% project coverage gate in CI and smoke-test `load_hdf5` and `iter_hdf5_chunks` from
  a clean wheel installation.
- Add a Python 3.10 CI job that exercises the lower bounds of the declared runtime dependencies.
- Refresh project documentation with capability-first descriptions for core workflows, adapters,
  reference cases, and release notes.
- Expand regression coverage for HDF5 metadata, bounded reads, safe writes, schema immutability,
  nested fields, CLI failures, and the adapter abstraction.

## [0.2.0] - 2026-08-24

### Added

- Public schema-authoring helpers for constructing, validating, serializing, writing, and
  documenting external JSON contracts.
- Optional tensor component-order declarations for vector and matrix fields.
- Strict JSON mapping files for CLI validation, summaries, conversion, and plots.
- Hypothesis property coverage for malformed nested shapes, non-finite values, dtype boundaries,
  and tensor HDF5 round trips.
- Add CodeQL v4 scanning for Python code on pushes, pull requests, and a weekly schedule.

### Fixed

- Keep explicit field mappings and unit conversions auditable in CLI-produced HDF5 metadata.
- Reject unsupported mapping keys and malformed schema component declarations before processing data.

### Changed

- Pin Hatchling and normalize the tagged commit timestamp for reproducible distributions.
- Build distributions twice in CI and reject byte-level differences before packaging.

## [0.1.1] - 2026-08-17

### Fixed

- Enforce custom-schema dtype, shape, bounds, alias, and option declarations at load time.
- Validate boolean and shaped numeric fields without accepting unrelated coercible values.
- Handle nested custom values safely during duplicate-record detection.
- Apply affine unit conversions with both scale and offset.
- Reject malformed CPDataKit HDF5 tables with consistent `DataReadError` failures.
- Reject shaped or non-numeric histogram fields with a concise domain error.

### Added

- PyPI Trusted Publishing workflow with tagged, inspected distributions and OIDC authentication.
- Five-minute synthetic-data quickstart and a scientifically scoped repository social-preview asset.
- Release metadata and semantic-version tag consistency checks.

### Changed

- The README now offers a one-command GitHub Release installation for non-contributors.
- Repository-relative README links are absolute so they work correctly on PyPI.
- Publishing documentation and workflow now target the complete v0.1.1 distribution.

## [0.1.0] - 2026-08-12

### Added

- Versioned `curve`, `point`, and `field2d` schemas.
- CSV, JSON records, and CPDataKit HDF5 I/O with provenance.
- Structured validation, explicit normalization, descriptive summaries, plotting, CLI, and API.
- Deterministic synthetic datasets, tests, documentation, and cross-platform CI.
