# Five-minute quickstart

This run uses deterministic synthetic data. It validates a declared crystal-plasticity curve,
writes an HDF5 file with provenance, and renders a stress-strain plot.

## 1. Install the current release

Start in a virtual environment:

```bash
python -m venv .venv
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

On POSIX shells:

```bash
source .venv/bin/activate
```

Install the current release from PyPI:

```bash
python -m pip install cpdatakit
```

For a pinned GitHub v0.7.0 release wheel, use:

```bash
python -m pip install "https://github.com/17636365690/cpdatakit/releases/download/v0.7.0/cpdatakit-0.7.0-py3-none-any.whl"
```

## 2. Generate a reproducible example

```bash
python -c "from cpdatakit.samples import generate_sample_data; generate_sample_data('cpdatakit-demo')"
```

The generator uses a fixed seed and produces reproducible synthetic data for the walkthrough.

## 3. Validate and summarize

```bash
cpdatakit validate cpdatakit-demo/synthetic_curve.csv --schema curve --json-output validation.json
cpdatakit summary cpdatakit-demo/synthetic_curve.csv --schema curve --json-output summary.json
```

The validation report records schema findings. The summary contains field-level descriptive
statistics and the validation result used to produce it.

## 4. Convert with provenance

```bash
cpdatakit convert cpdatakit-demo/synthetic_curve.csv --schema curve --output curve.h5 --source-description "Fixed-seed quickstart example"
```

The CPDataKit HDF5 file embeds the schema/profile, units, source filename and SHA-256, conversion
time, software versions, validation summary, and operation log.

## 5. Inspect and share the result

Start with the structure, then create a report you can share:

```bash
cpdatakit inspect curve.h5 --format json --output inspect.json
cpdatakit report curve.h5 --schema curve --output report.html
```

The inspection result lists the format and version, field order, dtype, shape, units, missing
values, HDF5 chunks, provenance, adapter details, and structural risks. The report adds the schema
profile/version, validation errors and warnings, descriptive statistics, and scope note. Open
`report.html` in a browser or print it in an offline environment. Existing output stays in place.
pass `--force` when replacement is intended. The report describes declared structural checks. Use
domain methods to interpret physical and scientific results.

## 6. Plot the declared curve

```bash
cpdatakit plot curve.h5 --schema curve --kind stress-strain --output stress-strain.png
```

At this point, the directory contains `validation.json`, `summary.json`, `curve.h5`, and
`stress-strain.png`. CPDataKit checks the declared data contract. Use domain-specific methods to
assess physical correctness.

## 7. Open the local workbench

For project-local uploads and service-backed workflows, run:

```bash
cpdatakit ui --workspace cpdatakit-workspace
```

The workbench binds to loopback, opens the default browser, and keeps its catalog, uploads, and
artifacts below the selected workspace. Use `--no-browser` for a headless smoke check.

## 7. Read a window or stream chunks

For bounded access, use the explicit HDF5 readers:

```python
from cpdatakit.io import iter_hdf5_chunks, load_hdf5

window = load_hdf5("curve.h5", fields=["step", "stress"], start=10, stop=20)
for chunk in iter_hdf5_chunks("curve.h5", fields=["step", "stress"], chunk_size=4096):
    consume(chunk.data)
```

`start` is inclusive and `stop` is exclusive. Field order follows the requested order, and
every chunk is a `Dataset` with the HDF5 metadata and source path preserved. Reads are sliced
along the record axis, so vector and tensor values keep their per-record shapes. Use
Choose `load_dataset()` when the existing full-read workflow is sufficient.

## 8. Opt into record-axis HDF5 storage chunks

For a larger sequential-read workload, choose the HDF5 storage layout explicitly while keeping
the same read APIs:

```python
from cpdatakit.io import load_dataset, load_hdf5, write_hdf5
from cpdatakit.schema import load_schema
from cpdatakit.validation import validate_dataset

dataset = load_dataset("cpdatakit-demo/synthetic_curve.csv")
schema = load_schema("curve")
validation = validate_dataset(dataset, schema)
write_hdf5(
    dataset,
    "curve-chunked.h5",
    schema,
    validation,
    force=True,
    hdf5_chunk_size=4096,
)
window = load_hdf5("curve-chunked.h5", fields=["step", "stress"], start=10, stop=20)
```

`hdf5_chunk_size` is an opt-in positive record count for the HDF5 storage chunks. Leave it out or
pass `None` to use the default layout. The value applies to the first record axis, so
vector and tensor trailing dimensions remain intact. It is separate from the reader-side
`iter_hdf5_chunks(..., chunk_size=...)` batch size. Use `load_hdf5()` for a selected window,
`iter_hdf5_chunks()` for bounded iteration, and `load_dataset()` for the existing full-read path.

## 9. Measure read scaling

From a repository checkout with the development environment active, run both diagnostic sizes:

```bash
python scripts/benchmark_hdf5_read.py --records 100000 --chunk-size 4096 --hdf5-chunk-size 4096
python scripts/benchmark_hdf5_read.py --records 1000000 --chunk-size 4096 --hdf5-chunk-size 4096
```

Each command prints JSON for full, selected-field, and chunked reads, including record counts,
elapsed time, peak RSS where available, and the configured storage chunk size. Compare runs on the
same machine. The benchmark provides scaling evidence, while CI checks the result structure and
record counts.

## Try invalid data

The generator also creates a deliberately malformed point dataset. A validation exit status of
`1` means findings were reported successfully:

```bash
cpdatakit validate cpdatakit-demo/intentionally_invalid_point.csv --schema point
```

To try another case, edit one of the generated CSV files and run `validate` again. For a new data
contract or input format, open an issue with a small synthetic sample and its field rules.
