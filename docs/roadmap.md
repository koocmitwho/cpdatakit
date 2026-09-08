# Roadmap

- **v0.2.0 (released 2026-08-24):** schema authoring helpers, clearer tensor-valued tabular
  encodings, explicit CLI mapping files, and richer nested-field validation coverage.
- **v0.3.0 (released 2026-08-30):** strict HDF5 metadata validation, validation-aware atomic
  writes, explicit field/range and lazy chunk reads, immutable schema state, expanded regression
  coverage, deterministic HDF5 scaling benchmarks, and a documented adapter acceptance checklist.
  It also includes the read-only DAMASK DADF5 selection reader, the hash-verified Surfalex HF
  Workflow 7A reference case, an 85% CI coverage gate, lower-bound dependency tests, and clean-wheel
  HDF5 API smoke checks.
- **v0.4.0 (released 2026-08-31):** schema diff, the first pieces of explicit migration support,
  comparison/report bundles, and compatibility tests. The schema command compares contracts. The
  comparison command reads JSON reports and compares their schema, validation, structure, and
  scalar statistics.
- **v0.5.0 (released 2026-09-02):** scientific/engineering contract-core positioning, external
  non-CP profile names, self-describing custom-profile HDF5 1.0 files, generic x-y plots, explicit
  separation of CP identifier statistics, a lightweight adapter registry, and a complete
  thermal-cycle example.
- **v0.6.0 (released 2026-09-03):** Python 3.12 floor, v0.5 compatibility snapshot,
  N-dimensional `ScientificDataset`, schema/HDF5 2.0, NetCDF/Zarr 3/Parquet adapters, shared typed
  application services, deterministic capability discovery, SQLite catalog, cooperative jobs, and
  a loopback-only FastAPI/Jinja workbench with bundled assets. The supported CI matrix covers
  Python 3.12/3.13 on Ubuntu, macOS, and Windows.

- **v0.7.0 (released 2026-09-06):** application and workbench integration for NetCDF, Zarr 3,
  Parquet and HDF5 2.0. Project pages support custom schemas, uploads, validation, conversion,
  reports, job status and result downloads. Scientific arrays use schema 2.0 validation and
  HDF5 2.0 output, while tabular workflows retain their v0.5 contracts.

## v0.8.0 (released 2026-09-08)

v0.8.0 adds selective reads, multidimensional heatmaps and batch conversion,
along with schema drafts and mapping previews that help users check fields before conversion.
Jobs expose checkpoints for progress and cancellation.
The KupferDigital/FE example keeps source hashes and case splits from existing experiment-derived
and training data in the 0–0.8% strain window.

See [usage](post-v07-workflows.md), [dependency combinations](v0.8-dependencies.md) and
[candidate verification](verification/2026-09-08-v080-p0.md) for usage and release status.

## Follow-up milestones

- **Mesh topology:** use a licensed mesh and matching scalar or tensor field to define node/element
  IDs, connectivity, element types, coordinate frame, units and field association (node, cell or
  integration point). Round trips preserve IDs and connectivity. Tests catch dangling references.
  Establish the example's field-to-mesh association before choosing interpolation or projection rules.
- **Schema migration:** use paired schema versions and a real dataset with an agreed transformation,
  then record each rename, unit conversion, reshape, dropped field and loss policy in a migration
  manifest. Include source/target schema hashes and a dry-run report. Tests cover forward conversion
  and recovery after a failed step, and check that source bytes are preserved.
  Schema diff compares contracts.
- **Additional adapters:** document the format and version range, provide licensed fixtures,
  and specify selection behavior, units and scientific conventions. DADF5 and ODB extensions use
  the same acceptance process. Solver execution and physical inference remain separate workflows
  with their own data and validation.
