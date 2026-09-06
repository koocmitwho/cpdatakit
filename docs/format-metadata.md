# Metadata in NetCDF, Zarr and Parquet

From v0.6.1, NetCDF, Zarr and Parquet files carry the CPDataKit metadata dictionary under
`cpdatakit_metadata_json`. NetCDF and Zarr store it as a root attribute. Parquet stores the UTF-8
JSON in Arrow schema metadata, alongside pandas metadata.

```json
{"version":1,"metadata":{"units":{"temperature":"K"},"provenance":{"source_description":"thermal experiment"}}}
```

Writers check that metadata is a JSON object with string keys and finite values before creating
output. The stored dictionary includes nested fields, schema references, mappings and the supplied
validation findings. Writing leaves the caller's data and attributes unchanged.

On read, the dictionary becomes dataset metadata and the storage attribute is removed from the
returned xarray attributes. Stored keys take precedence over the reader's format, engine and unit
defaults. Older files without the envelope use the existing read path.

Malformed envelopes and unsupported versions raise DataReadError. The root attribute name is
reserved, so a writer reports a collision if the caller already uses it.

## Zarr replacement and recovery

The writer first completes a temporary store next to the output. It moves the old output to
`.NAME.backup-RANDOM/previous`, then renames the new store to the requested path. The backup is
removed only after the new store is installed.

If the final rename fails, the writer moves the original back. A second filesystem error can
prevent that move. In that case the backup is kept, and its path appears in the exception note.
Once the filesystem error is resolved, use ZarrReader to read the backup or move it back to the
output path.

Replacement takes several renames. Concurrent readers can briefly lose access to the output path.
If the process stops partway through, inspect any temporary store or backup left behind and
restore the output manually.
