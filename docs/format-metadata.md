# Metadata in NetCDF, Zarr and Parquet

Starting with v0.6.1, these adapters store the complete CPDataKit metadata dictionary in a
`cpdatakit_metadata_json` envelope. NetCDF and Zarr use a root attribute; Parquet uses UTF-8
Arrow schema metadata under the same key, alongside the existing pandas metadata.

```json
{"version":1,"metadata":{"units":{"temperature":"K"},"provenance":{"source_description":"thermal experiment"}}}
```

Metadata must be a JSON object with finite JSON values and string keys. Writers check encoding
before creating output. They preserve nested metadata, including validation findings, schema
references and field mappings, without inventing a successful validation result. Callers retain
ownership of their data and attributes; writing does not mutate them.

Readers restore the dictionary and remove the transport attribute from the returned xarray
attributes. Existing metadata keys take precedence over generated format/engine/unit defaults.
Older files without the envelope retain their previous reading behavior. Unsupported versions
and malformed envelopes raise DataReadError. The root attribute name is reserved: a writer
rejects a collision rather than overwriting a caller's attribute.

## Zarr replacement and recovery

The writer finishes a sibling temporary store before moving the existing output into
`.NAME.backup-RANDOM/previous`. It then installs the completed store at the requested path.
If installation fails, it moves the original back. If rollback also fails, the original backup
is retained and the exception note gives its path. After resolving the filesystem error, the
caller can read that backup with ZarrReader or restore it to the intended location.

Directory replacement uses multiple renames, so it does not guarantee uninterrupted visibility
to concurrent readers or automatic recovery after process termination. An interrupted operation
may leave a temporary store or backup requiring inspection. Backup contents are only removed
after successful installation.
