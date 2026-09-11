# Application service boundary

The v0.6 application service boundary is additive to the v0.5 package surface and does not change
schema 1.0, HDF5 1.0, or the existing command names.

## Implemented core contract

`cpdatakit.application` exposes immutable request values and a generic result envelope:

```python
import_and_inspect(ImportInspectRequest) -> ServiceResult[dict[str, Any]]
resolve_schema_and_mapping(ResolveSchemaRequest) -> ServiceResult[ResolvedSchemaMapping]
validate_and_summarize(DatasetRequest) -> ServiceResult[ValidationSummary]
convert_and_write(ConvertRequest) -> ServiceResult[ConversionOutcome]
build_report(ReportRequest) -> ServiceResult[ReportOutcome]
compare_reports(ComparisonRequest) -> ServiceResult[ComparisonOutcome]
plot_declared_fields(PlotRequest) -> ServiceResult[PlotOutcome]
```

`ServiceResult.status` is either `"succeeded"` or `"failed"`. A successful validation operation may
still contain `ValidationSummary.validation.valid == False`: invalid declared data is a completed
validation operation, not an application crash. A conversion with validation errors is a failed
operation and never creates an artifact unless `allow_invalid=True` was explicit.

`ImportInspectRequest.read_limits` accepts positive `ReadLimits` values. The service checks byte and
record limits before returning an inspection payload. `convert_and_write` defaults to HDF5 1.0
for tabular data and HDF5 2.0 for scientific data. Artifact paths are relative to `workspace`.
Paths outside it are shown as `[outside-workspace]`.

Tabular plots use the units stored in the dataset. For legacy inputs with no unit metadata, labels
use the selected schema's declarations. Plotting does not change numeric values; use an explicit
mapping when a different output unit is needed. A mapping's input unit must agree with the stored
source unit, including scale and offset; equivalent unit aliases are accepted.

v0.7 services read NetCDF, Zarr and Parquet through the format adapters. Requests accept schema 1.0
and schema 2.0, and `output_format` selects the conversion writer. See the
[workbench guide](v0.7-workbench.md) for supported formats and the page workflow.

Expected CPDataKit exceptions become stable error codes, sanitized messages, and suggested actions.
Unexpected exceptions receive a correlation ID in the log and a generic message at the edge. Service
results contain no open handles, HTTP objects, templates, browser state, or absolute artifact paths.

The v0.6 Web UI and Python API call the same application services as the migrated CLI core.
Each service accepts typed values and returns a typed result. `argparse`, HTTP request objects,
templates, and browser state stay at their respective edges.

## Shared use cases

The service layer owns these operations:

- **import and inspect:** detect a format, apply bounded `ReadLimits`, and return fields, dimensions,
  provenance, and capability candidates.
- **resolve schema and mapping:** load a local contract, validate composition, and prepare explicit
  field and unit mappings.
- **validate and summarize:** run structural validation and generic or domain-specific summaries,
  preserving the existing scope note.
- **convert and write:** check the target writer's capability, require overwrite intent, and write an
  atomic artifact with provenance.
- **build reports and comparisons:** build JSON/Markdown/HTML reports and compare declared schemas,
  structure, validation findings, and scalar aggregates.
- **plot declared fields:** select a schema-driven plot and return a figure/artifact description.
- **capability discovery:** list readers, writers, plots, domain checks, solvers, and providers that
  are installed and available on this host.

Capability discovery is metadata-first and can report unavailable optional packages without importing
plugin implementations. Every service result includes a stable operation name, status, sanitized
message, provenance, and any artifact path relative to the active project workspace. A service never
returns an open file handle.

## Edge adapters

The CLI maps parsed arguments into service requests and owns exit codes and terminal output. The Web
layer maps forms and JSON into the same requests, owns sessions and CSRF checks, and renders or queues
the returned result. The Python API may call services directly or keep using the existing functional
helpers during the migration. All current data, report, comparison, and plot CLI commands use the
service boundary; the schema-diff command remains on its existing functional path.

No service imports `argparse`, Starlette/FastAPI request types, browser JavaScript, or template
objects. This keeps CLI, Web, and Python tests independent and prevents a UI concern from changing a
data contract.

## Error ownership

Expected data, schema, format, capability, and output errors remain typed CPDataKit exceptions. The
service converts them to a stable error result with a code, safe message, and suggested action. The
CLI maps that result to the v0.5 exit-code contract. The Web layer displays the message and keeps
the job/project state. Unexpected exceptions are logged with a correlation ID and shown as a generic
failure without credentials or absolute paths.

## Data and artifact flow

```text
edge request -> service request -> reader/contract/validation -> service result -> edge response
                                      |
                                      +-> artifact + provenance + catalog reference
```

The service may stream bounded previews. An operation that materializes a full dataset declares that
fact in its result before execution when the caller can know it. The existing direct-path APIs keep
their current materialization behavior.

## Migration order

Characterization tests protect the current CLI/Python behavior first. Each existing workflow then
gets a service adapter and the same test vectors. The Web UI is added only after service results are
stable. No v0.6 format implementation is hidden behind a service name before its reader/writer
capability contract is tested.
