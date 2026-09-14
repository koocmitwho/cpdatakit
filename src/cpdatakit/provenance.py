"""Reproducible provenance helpers."""

from __future__ import annotations

import hashlib
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._version import __version__


def sha256_file(path: str | Path) -> str:
    """Compute a file's SHA-256 digest in streaming chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_provenance(
    source: Path | None,
    *,
    source_description: str | None = None,
    operation_log: list[str] | None = None,
) -> dict[str, Any]:
    """Build portable metadata for a derived artifact."""
    result: dict[str, Any] = {
        "source_description": source_description or "not provided",
        "converted_at_utc": datetime.now(UTC).isoformat(),
        "cpdatakit_version": __version__,
        "python_version": platform.python_version(),
        "operation_log": list(operation_log or []),
    }
    if source is not None:
        result["input_filename"] = source.name
        result["input_sha256"] = sha256_file(source)
    else:
        result["input_filename"] = "not available"
        result["input_sha256"] = "not available"
    return result
