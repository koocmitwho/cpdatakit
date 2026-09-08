"""Reproducible per-file conversion with atomic progress manifests."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from ..exceptions import DataReadError, DataValidationError, OutputExistsError
from ..schema import BUILTIN_PROFILES
from .data_access import contract_dict, path_sha256, resolve_contract
from .services import ConvertRequest, ServiceError, ServiceResult, _failure, convert_and_write


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _json_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _overlap(left, right):
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _requests(config, manifest, payload):
    if not isinstance(payload, dict) or payload.get("batch_version") != 1:
        raise DataValidationError("Batch configuration requires batch_version 1")
    if set(payload) - {"batch_version", "defaults", "items"}:
        raise DataValidationError("Unknown batch configuration keys")
    defaults = payload.get("defaults", {})
    entries = payload.get("items")
    if not isinstance(defaults, dict) or not isinstance(entries, list) or not entries:
        raise DataValidationError("Batch defaults must be an object and items a nonempty list")
    base = config.parent
    protected = [config, manifest]
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise DataValidationError("Each batch item must be an object")
        item = {**defaults, **entry}
        if set(item) - {
            "input",
            "output",
            "schema",
            "mapping",
            "output_format",
            "source_description",
        }:
            raise DataValidationError("Unknown batch item keys")
        if any(not isinstance(item.get(key), str) or not item[key] for key in ("input", "output")):
            raise DataValidationError("Each batch item requires input and output paths")
        if "schema" not in item:
            raise DataValidationError("Each batch item requires a schema")
        source = (base / item["input"]).resolve()
        target = (base / item["output"]).resolve()
        schema = item["schema"]
        if isinstance(schema, str) and schema not in BUILTIN_PROFILES:
            schema = (base / schema).resolve()
            protected.append(schema)
        if not isinstance(schema, (str, Path, dict)):
            raise DataValidationError("Schema must be a built-in name, JSON path or object")
        mapping = item.get("mapping")
        if mapping is not None:
            if not isinstance(mapping, str):
                raise DataValidationError("Mapping must be a JSON path")
            mapping = (base / mapping).resolve()
            protected.append(mapping)
        protected.append(source)
        output_format = item.get("output_format", "hdf5")
        suffixes = {
            "hdf5": {".h5", ".hdf5"},
            "netcdf": {".nc", ".netcdf"},
            "zarr": {".zarr"},
            "parquet": {".parquet"},
        }
        if output_format not in suffixes or target.suffix.lower() not in suffixes[output_format]:
            raise DataValidationError("Batch output format and extension must agree")
        result.append(
            ConvertRequest(
                source,
                schema,
                target,
                mapping,
                workspace=base,
                output_format=output_format,
                source_description=item.get("source_description"),
            )
        )
    for index, request in enumerate(result):
        if any(_overlap(request.output, path) for path in protected):
            raise DataValidationError(
                "Batch outputs cannot replace inputs, schemas, mappings or the manifest"
            )
        if any(_overlap(request.output, other.output) for other in result[:index]):
            raise DataValidationError("Batch outputs collide or contain one another")
    # The manifest is also an output. Check it against every configured input.
    if any(_overlap(manifest, path) for path in protected if path != manifest):
        raise DataValidationError("Batch manifest cannot replace an input")
    return result


def _parameters(request):
    if not request.data.exists():
        raise DataReadError("Batch input does not exist: " + request.data.name)
    contract = resolve_contract(request.schema)
    return {
        "input": str(request.data),
        "output": str(request.output),
        "input_sha256": path_sha256(request.data),
        "schema_sha256": _digest(contract_dict(contract)),
        "mapping_sha256": path_sha256(request.mapping) if request.mapping else None,
        "output_format": request.output_format,
        "source_description": request.source_description,
    }


def run_batch(config: Path, manifest: Path, *, retry: bool = False):
    """Convert config-relative inputs; retry only skips verified matching successes.

    A failed item does not stop later files. Existing outputs are never replaced.
    The manifest is atomically updated after every item and can resume an interrupted run.
    """
    config, manifest = Path(config).resolve(), Path(manifest).resolve()
    provenance = {"operation": "run_batch", "input_filename": config.name}
    lock = manifest.with_name(manifest.name + ".lock")
    owned_lock = False
    try:
        if manifest.exists() and not retry:
            raise OutputExistsError(
                "Batch manifest exists; pass retry=True or choose a new manifest"
            )
        payload = json.loads(config.read_text(encoding="utf-8"))
        requests = _requests(config, manifest, payload)
        if any(
            _overlap(lock, request.data) or _overlap(lock, request.output) for request in requests
        ):
            raise DataValidationError("Batch lock conflicts with an input or output")
        previous = {}
        if manifest.exists():
            old = json.loads(manifest.read_text(encoding="utf-8"))
            if old.get("manifest_version") != 1 or old.get("config_path") != str(config):
                raise DataValidationError("Retry manifest belongs to a different batch")
            previous = {item["output"]: item for item in old["items"]}
        manifest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with lock.open("x", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
            owned_lock = True
        except FileExistsError as exc:
            raise DataValidationError(
                "This batch manifest is already in use; inspect its lock before retrying"
            ) from exc
        report = {
            "manifest_version": 1,
            "config_path": str(config),
            "config_sha256": path_sha256(config),
            "state": "running",
            "items": [
                previous.get(
                    str(r.output),
                    {"input": str(r.data), "output": str(r.output), "status": "pending"},
                )
                for r in requests
            ],
            "counts": {"succeeded": 0, "failed": 0, "skipped": 0},
        }
        _json_write(manifest, report)
        for index, request in enumerate(requests):
            record = {"input": str(request.data), "output": str(request.output), "status": "failed"}
            previous_record = previous.get(str(request.output), {})
            last_success = (
                previous_record
                if previous_record.get("status") in {"succeeded", "skipped"}
                else previous_record.get("last_success")
            )
            if last_success:
                record["last_success"] = {
                    key: last_success[key]
                    for key in ("status", "fingerprint", "result", "output_sha256")
                }
            try:
                parameters = _parameters(request)
                fingerprint = _digest(parameters)
                record.update(parameters=parameters, fingerprint=fingerprint)
                prior = last_success
                if (
                    prior
                    and prior["status"] in {"succeeded", "skipped"}
                    and prior.get("fingerprint") == fingerprint
                ):
                    if not request.output.exists():
                        result = convert_and_write(request)
                    elif path_sha256(request.output) != prior.get("output_sha256"):
                        result = ServiceResult(
                            "convert_and_write",
                            "failed",
                            error=ServiceError(
                                "output_changed",
                                "Previously successful output has changed.",
                                "Keep it or choose a new output path.",
                            ),
                        )
                    else:
                        record.update(
                            status="skipped",
                            result=prior["result"],
                            output_sha256=prior["output_sha256"],
                        )
                        result = None
                else:
                    result = convert_and_write(request)
                if result is not None:
                    record["result"] = result.to_dict()
                    if result.ok:
                        record.update(status="succeeded", output_sha256=path_sha256(request.output))
            except Exception as exc:
                record["result"] = _failure(
                    "convert_and_write", exc, provenance={"input_filename": request.data.name}
                ).to_dict()
            report["items"][index] = record
            report["counts"][record["status"]] += 1
            _json_write(manifest, report)
        report["state"] = "partial_failure" if report["counts"]["failed"] else "completed"
        _json_write(manifest, report)
        error = (
            ServiceError(
                "batch_partial_failure",
                "Some inputs failed; see per-file validation and errors.",
                "Fix failed inputs and retry with this manifest.",
            )
            if report["counts"]["failed"]
            else None
        )
        return ServiceResult(
            "run_batch",
            "failed" if error else "succeeded",
            report,
            error=error,
            artifact=manifest.name,
            provenance=provenance,
        )
    except Exception as exc:
        return _failure("run_batch", exc, provenance=provenance)
    finally:
        if owned_lock:
            lock.unlink(missing_ok=True)
