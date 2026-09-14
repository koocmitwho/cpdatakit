# Architecture

CPDataKit has a scientific-data contract core and a crystal-plasticity compatibility layer.
It represents tables as Dataset and named multidimensional arrays as ScientificDataset. Units,
coordinates, tensor order and other scientific meanings come from explicit declarations.

| Area | Responsibility |
| --- | --- |
| schema.py, schemas/v2.py | Schema 1.0 fields and schema 2.0 dimensions, coordinates, variables and local composition |
| model.py, data/ | Data containers and shared scientific validation/unit checks |
| normalization.py, application/mapping.py | Explicit field, dimension and unit mapping |
| io/, formats/ | HDF5 1.0/2.0, NetCDF, Zarr and Parquet representations |
| _atomic.py | Same-filesystem file publication and exclusive directory promotion |
| inspection.py, statistics.py, reporting.py | Structural inspection, aggregate statistics and offline reports |
| schema_diff.py, comparison.py, _comparison_v2.py | Version-aware schema and report comparison |
| application/contracts.py | Typed request and result values, re-exported through application and services |
| application/services.py | Shared import, validation, conversion, reporting and plotting use cases |
| application/batch.py | Per-item intent, prepared-result proof and interrupted batch recovery |
| application/slices.py | Explicit bounded planes and multidimensional heatmaps |
| catalog/sqlite.py, jobs/manager.py | Durable project/job records, optional paging and bounded in-memory execution |
| web/app.py | Workspace/session setup, core resource routes and job admission/history |
| web/operations.py, web/outputs.py | Output forms and shared production/promotion/registration/recovery |
| web/workbench.py, web/authoring.py, web/slices.py | Project pages, uploads/downloads, drafting, previews and field controls |
| web/artifacts.py | Independent result versions and digest-bound registration |

The dependency direction is inward. Contracts and data validation have no HTTP, template or
browser dependency. Readers close backend handles before returning materialized selections.
Application services translate expected exceptions into structured results; the CLI and Web
consume the same service and schema comparison boundaries.

The top-level package and application namespace resolve public objects on demand. Public names,
function signatures and legacy API identities remain available. The CLI parses help, version and
usage errors before importing scientific or visualization libraries. The contract module can be
used without importing plotting execution.

Tables keep schema/HDF5 1.0 and the legacy CSV/JSON workflow. NetCDF, Zarr and HDF5 2.0 keep named
arrays and use schema 2.0. Parquet is tabular. Scientific HDF5 writing recomputes validation and
preserves JSON global attributes; selection retains associated coordinates. Report comparison is
descriptive and checks representation compatibility, including relevant observed coordinates.

Output producers write private staging paths. Workbench operations serialize promotion and
registration, bind snapshots to the produced digest, and restore only outputs still owned by the
failing operation. Concurrent changes and failed restoration retain their bytes and recovery
metadata. Batch execution records intent and prepared output proof before publishing; kernel locks
release on process exit. These are process-interruption recovery contracts, not a claim of
power-loss durability on arbitrary filesystems.

Synchronous Web handlers run in FastAPI's worker pool so inspection, validation, mapping previews,
hashes and archive construction do not occupy the event loop. Jobs wait for successful catalog
admission before executing. Completed results are persisted before memory retirement; the
workbench applies queue/log/history limits while standalone JobManager defaults remain compatible.
Catalog schema v4 stores JSON results, adds project indexes, and creates consistent SQLite backups
before transactional migrations.

Resource pages request bounded recent summaries. Full job details are fetched when selected;
historical completed jobs are not polled. Optional API/catalog pagination preserves the previous
unbounded query defaults for existing callers.

Built-in curve, point and field2d schemas, identifier enrichment, selected plots and the read-only
DAMASK DADF5 adapter form the CP compatibility layer. Generic schemas do not acquire CP fields.
External adapters retain DatasetAdapter.load(path), explicit scientific selections, licensed
fixtures and provenance. Solver execution, new mesh contracts and physical inference are separate
workflows.

Current workflows and recovery behavior are documented in [post-v07-workflows.md](post-v07-workflows.md).
Historical design documents remain under superpowers/; they do not replace the current contracts.
