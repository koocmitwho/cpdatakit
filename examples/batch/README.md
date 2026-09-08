# Batch conversion

These two synthetic curves use a shared schema. The values are software fixtures.

```bash
cpdatakit batch examples/batch/batch.json --manifest .artifacts/batch-example.json
cpdatakit batch examples/batch/batch.json --manifest .artifacts/batch-example.json --retry
```

The first run writes two HDF5 1.0 files under `examples/batch/results/`.
Retry checks their hashes and skips both files. To try a partial failure, copy this
example, remove `stress` from the second CSV, then run with a new manifest to see
its validation errors alongside the first file's successful result.
Restore the column and retry that manifest. Only the failed input is processed.
