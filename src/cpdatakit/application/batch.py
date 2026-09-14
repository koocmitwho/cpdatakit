"""Reproducible per-file conversion with atomic progress manifests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from .._atomic import cleanup_staged_file, publish_file
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
        cleanup_staged_file(temporary)


def _overlap(left, right):
    left, right = left.resolve(), right.resolve()
    if left == right or left.is_relative_to(right) or right.is_relative_to(left):
        return True
    try:
        return left.samefile(right)
    except OSError:
        return False


def _schema_inputs(schema):
    """Find declared local composition inputs without changing item validation."""
    if not isinstance(schema, Path):
        return []
    pending, visited = [schema], set()
    while pending:
        path = pending.pop().resolve()
        if path in visited:
            continue
        visited.add(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue  # The existing per-item validation reports invalid sources.
        if not isinstance(payload, dict) or payload.get("schema_version") != "2.0":
            continue
        references = []
        if isinstance(payload.get("extends"), str):
            references.append(payload["extends"])
        if isinstance(payload.get("includes"), list):
            references.extend(item for item in payload["includes"] if isinstance(item, str))
        pending.extend(
            path.parent / reference
            for reference in references
            if reference and not reference.startswith(("http://", "https://"))
        )
    return visited


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
    protected = [config]
    lock = manifest.with_name(manifest.name + ".lock")
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
            protected.extend(_schema_inputs(schema))
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
        if any(_overlap(request.output, path) for path in (*protected, manifest, lock)):
            raise DataValidationError(
                "Batch outputs cannot replace inputs, schemas, mappings or the manifest"
            )
        if any(_overlap(request.output, other.output) for other in result[:index]):
            raise DataValidationError("Batch outputs collide or contain one another")
    # The manifest is also an output. Check it against every configured input.
    if any(_overlap(manifest, path) for path in protected):
        raise DataValidationError("Batch manifest cannot replace an input")
    if any(_overlap(lock, path) for path in (*protected, manifest)):
        raise DataValidationError("Batch lock conflicts with an input or manifest")
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


def _acquire_lock(path):
    """Keep a kernel lock for the run; crashes release it without stale-PID guessing.

    The small lock file is retained. Removing it would let another process lock
    a new inode while an existing contender still holds the previous one.
    """
    stream = path.open("a+b")
    try:
        if path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise DataValidationError("This batch manifest is already in use") from exc
    return stream


def _changed(kind):
    return ServiceResult(
        "convert_and_write",
        "failed",
        error=ServiceError(
            kind + "_changed",
            f"Previously recorded {kind} has changed.",
            "Restore the matching data or choose a new manifest and output path.",
        ),
    )


def _staged_path(record, target):
    value = record.get("staged_output")
    if not isinstance(value, str):
        raise DataValidationError("Interrupted batch is missing its staging path")
    staged = Path(value)
    if (
        not staged.is_absolute()
        or staged.name != target.name
        or staged.parent.parent != target.parent
        or not staged.parent.name.startswith(f".{target.name}.batch-")
        or staged.is_symlink()
        or staged.parent.is_symlink()
    ):
        raise DataValidationError("Interrupted batch staging path is invalid")
    return staged


def _publish_output(staged, target):
    if staged.is_dir():
        # A populated Zarr directory is never replaced; the destination must not
        # already exist, including empty directories on POSIX systems.
        from .._atomic import publish_directory

        publish_directory(staged, target)
    else:
        publish_file(staged, target)


def _recover_prepared(request, record, prior, fingerprint):
    if prior.get("fingerprint") != fingerprint:
        return _changed("input")
    staged = _staged_path(prior, request.output)
    expected = prior.get("output_sha256")
    if not isinstance(expected, str) or prior.get("result", {}).get("status") != "succeeded":
        raise DataValidationError("Interrupted batch has no verified conversion result")
    if request.output.exists():
        if path_sha256(request.output) != expected:
            return _changed("output")
        status = "skipped"
    elif staged.exists():
        if path_sha256(staged) != expected:
            return _changed("output")
        if _digest(_parameters(request)) != fingerprint:
            return _changed("input")
        _publish_output(staged, request.output)
        status = "succeeded"
    else:
        raise DataReadError("Interrupted batch output and staging file are missing")
    record.update(
        status=status, result=prior["result"], output_sha256=expected, staged_output=str(staged)
    )
    return None


def _cleanup_staging(record, target):
    """Best-effort cleanup after durable success, never removing unverified data."""
    if "staged_output" not in record:
        return
    with suppress(OSError, DataValidationError):
        staged = _staged_path(record, target)
        if staged.is_file() and path_sha256(staged) == record["output_sha256"]:
            staged.unlink()
        # A successful directory rename removes the staged directory itself.
        # Leave any unexpected content for inspection instead of recursive deletion.
        staged.parent.rmdir()


def _convert_staged(request, record, report, index, manifest, fingerprint):
    request.output.parent.mkdir(parents=True, exist_ok=True)
    directory = Path(
        tempfile.mkdtemp(prefix=f".{request.output.name}.batch-", dir=request.output.parent)
    )
    staged = directory / request.output.name
    record.update(status="running", staged_output=str(staged))
    report["items"][index] = record
    _json_write(manifest, report)
    result = convert_and_write(replace(request, output=staged))
    if not result.ok:
        record["status"] = "failed"
        shutil.rmtree(directory)
        return result
    if _digest(_parameters(request)) != fingerprint:
        record["status"] = "failed"
        shutil.rmtree(directory)
        return _changed("input")
    artifact = request.output.relative_to(request.workspace).as_posix()
    payload = result.to_dict()
    payload["artifact"] = payload["value"]["artifact"] = artifact
    record.update(status="prepared", result=payload, output_sha256=path_sha256(staged))
    _json_write(manifest, report)
    _publish_output(staged, request.output)
    record["status"] = "succeeded"
    # Completion is durable before cleaning the directory; a crash before this
    # write leaves the prepared hash/result available for a verified retry.
    try:
        _json_write(manifest, report)
    except BaseException:
        record["status"] = "prepared"
        raise
    return None


def run_batch(config: Path, manifest: Path, *, retry: bool = False):
    """Convert config-relative inputs; retry only skips verified matching successes.

    A failed item does not stop later files. Existing outputs are never replaced.
    The manifest is atomically updated after every item and can resume an interrupted run.
    """
    config, manifest = Path(config).resolve(), Path(manifest).resolve()
    provenance = {"operation": "run_batch", "input_filename": config.name}
    lock = manifest.with_name(manifest.name + ".lock")
    lock_stream = None
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
        manifest.parent.mkdir(parents=True, exist_ok=True)
        lock_stream = _acquire_lock(lock)
        # Check again under the kernel lock: another run could have completed
        # between the initial preflight and acquiring the lock.
        if manifest.exists() and not retry:
            raise OutputExistsError("Batch manifest exists; pass retry=True")
        previous = {}
        if manifest.exists():
            old = json.loads(manifest.read_text(encoding="utf-8"))
            if old.get("manifest_version") != 1 or old.get("config_path") != str(config):
                raise DataValidationError("Retry manifest belongs to a different batch")
            previous = {item["output"]: item for item in old["items"]}
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
            recovery = (
                previous_record
                if previous_record.get("status") in {"prepared", "running"}
                else previous_record.get("recovery")
            )
            if recovery:
                record["recovery"] = recovery
            try:
                parameters = _parameters(request)
                fingerprint = _digest(parameters)
                record.update(parameters=parameters, fingerprint=fingerprint)
                prior = last_success
                if recovery and recovery.get("status") == "prepared":
                    result = _recover_prepared(request, record, recovery, fingerprint)
                elif recovery and recovery.get("fingerprint") != fingerprint:
                    result = _changed("input")
                elif (
                    prior
                    and prior["status"] in {"succeeded", "skipped"}
                    and prior.get("fingerprint") == fingerprint
                ):
                    if not request.output.exists():
                        result = _convert_staged(
                            request, record, report, index, manifest, fingerprint
                        )
                    elif path_sha256(request.output) != prior.get("output_sha256"):
                        result = _changed("output")
                    else:
                        record.update(
                            status="skipped",
                            result=prior["result"],
                            output_sha256=prior["output_sha256"],
                        )
                        result = None
                else:
                    if request.output.exists():
                        raise OutputExistsError(f"Output already exists: {request.output}")
                    result = _convert_staged(request, record, report, index, manifest, fingerprint)
                if result is not None:
                    record["result"] = result.to_dict()
                    if result.ok:
                        record.update(status="succeeded", output_sha256=path_sha256(request.output))
            except Exception as exc:
                if record.get("status") == "prepared":
                    record["recovery"] = {
                        key: record[key]
                        for key in (
                            "status",
                            "fingerprint",
                            "staged_output",
                            "result",
                            "output_sha256",
                        )
                    }
                record["status"] = "failed"
                record["result"] = _failure(
                    "convert_and_write", exc, provenance={"input_filename": request.data.name}
                ).to_dict()
            report["items"][index] = record
            report["counts"][record["status"]] += 1
            _json_write(manifest, report)
            if record["status"] in {"succeeded", "skipped"}:
                _cleanup_staging(record, request.output)
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
        if lock_stream is not None:
            lock_stream.close()
