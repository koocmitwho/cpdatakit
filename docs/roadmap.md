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

Future work includes migration manifests, additional adapters, schema-driven plots and mesh
topology. Solver execution and physical inference need their own data and validation workflows.

Further DADF5 and ODB coverage follows the documented evidence and license review process.
