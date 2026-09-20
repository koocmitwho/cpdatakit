"""Probe v0.6 dependency availability without changing the environment."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.metadata
import json
import platform
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_CANDIDATES = Path(__file__).with_name("v06-dependency-candidates.json")


def _read_candidates(path: Path = _CANDIDATES) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("packages"), list):
        raise ValueError("Dependency candidates must contain a packages list")
    return payload


def _license(distribution: importlib.metadata.Distribution) -> str | None:
    for value in distribution.metadata.get_all("Classifier") or []:
        if value.startswith("License ::"):
            return value.removeprefix("License :: ").strip()[:256]
    values = distribution.metadata.get_all("License") or []
    for value in values:
        first_line = value.strip().splitlines()[0] if value and value.strip() else ""
        if first_line and first_line.lower() not in {"unknown", "none"}:
            return first_line[:256]
    return None


def _installed_size(distribution: importlib.metadata.Distribution) -> int | None:
    files = distribution.files
    if files is None:
        return None
    total = 0
    for item in files:
        try:
            total += item.locate().stat().st_size
        except OSError:
            continue
    return total


def _probe_package(candidate: dict[str, str]) -> dict[str, Any]:
    distribution_name = candidate["distribution"]
    module_name = candidate["module"]
    started = time.perf_counter()
    try:
        distribution = importlib.metadata.distribution(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return {
            "distribution": distribution_name,
            "module": module_name,
            "installed": False,
            "version": None,
            "license": None,
            "installed_bytes": None,
            "import_seconds": round(time.perf_counter() - started, 6),
            "error": "distribution is not installed",
        }

    error: str | None = None
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - exercised by environment-specific probes
        error = f"module import failed: {type(exc).__name__}"
    return {
        "distribution": distribution_name,
        "module": module_name,
        "installed": error is None,
        "version": distribution.version,
        "license": _license(distribution),
        "installed_bytes": _installed_size(distribution),
        "import_seconds": round(time.perf_counter() - started, 6),
        "error": error,
    }


def _probe_runtime_module(module_name: str, distribution_name: str) -> dict[str, Any]:
    started = time.perf_counter()
    error: str | None = None
    try:
        importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - environment-specific import failures
        error = f"module import failed: {type(exc).__name__}"
    try:
        version = importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {
        "distribution": distribution_name,
        "module": module_name,
        "installed": error is None,
        "version": version,
        "import_seconds": round(time.perf_counter() - started, 6),
        "error": error,
    }


def _operation_unavailable(packages: list[str], reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "packages": packages, "metrics": {}, "error": reason}


def _operation_failure(packages: list[str], exc: Exception) -> dict[str, Any]:
    return {
        "status": "fail",
        "packages": packages,
        "metrics": {},
        "error": f"{type(exc).__name__}: operation failed",
    }


def _reference_dataset(xarray: Any) -> Any:
    reference_path = Path(__file__).parents[1] / "examples" / "thermal-field-v2" / "reference.json"
    payload = json.loads(reference_path.read_text(encoding="utf-8"))
    coordinates = {
        name: (tuple(item["dims"]), item["values"]) for name, item in payload["coordinates"].items()
    }
    variables = {
        name: (
            tuple(item["dims"]),
            item["values"],
            {"units": item["unit"], "role": item.get("role", "")},
        )
        for name, item in payload["variables"].items()
    }
    return xarray.Dataset(
        data_vars=variables, coords=coordinates, attrs=payload.get("metadata", {})
    )


def _run_netcdf_operation(xarray: Any, engine: str, package: str) -> dict[str, Any]:
    packages = ["xarray", package]
    try:
        with tempfile.TemporaryDirectory(prefix="cpdatakit-v06-netcdf-") as directory:
            path = Path(directory) / "reference.nc"
            data = _reference_dataset(xarray)
            data.to_netcdf(path, engine=engine)
            with xarray.open_dataset(path, engine=engine) as loaded:
                shape = list(loaded["temperature"].shape)
            return {
                "status": "pass",
                "packages": packages,
                "metrics": {"temperature_shape": shape, "output_bytes": path.stat().st_size},
                "error": None,
            }
    except Exception as exc:  # pragma: no cover - backend-specific behavior
        return _operation_failure(packages, exc)


def _run_zarr_operation(xarray: Any) -> dict[str, Any]:
    packages = ["xarray", "zarr"]
    try:
        with tempfile.TemporaryDirectory(prefix="cpdatakit-v06-zarr-") as directory:
            store = Path(directory) / "reference.zarr"
            data = _reference_dataset(xarray)
            data.to_zarr(store, mode="w", consolidated=False, zarr_format=3)
            with xarray.open_zarr(store, consolidated=False) as loaded:
                shape = list(loaded["temperature"].shape)
            entries = sum(path.is_file() for path in store.rglob("*"))
            return {
                "status": "pass",
                "packages": packages,
                "metrics": {"temperature_shape": shape, "store_entries": entries},
                "error": None,
            }
    except Exception as exc:  # pragma: no cover - backend-specific behavior
        return _operation_failure(packages, exc)


def _run_parquet_operation(pyarrow: Any) -> dict[str, Any]:
    packages = ["pyarrow"]
    try:
        import pyarrow.parquet as parquet

        with tempfile.TemporaryDirectory(prefix="cpdatakit-v06-parquet-") as directory:
            path = Path(directory) / "reference.parquet"
            table = pyarrow.table(
                {
                    "time": [0.0, 10.0, 20.0, 30.0],
                    "temperature": [273.15, 283.15, 293.15, 303.15],
                }
            )
            parquet.write_table(table, path)
            rows = parquet.read_table(path).num_rows
            return {
                "status": "pass",
                "packages": packages,
                "metrics": {"rows": rows, "output_bytes": path.stat().st_size},
                "error": None,
            }
    except Exception as exc:  # pragma: no cover - backend-specific behavior
        return _operation_failure(packages, exc)


def _run_fastapi_operation(fastapi: Any, httpx: Any) -> dict[str, Any]:
    packages = ["fastapi", "httpx"]
    try:
        from cpdatakit.web import create_app

        async def request() -> tuple[int, dict[str, str]]:
            with tempfile.TemporaryDirectory(prefix="cpdatakit-v06-ui-") as directory:
                app = create_app(Path(directory))
                async with app.router.lifespan_context(app):
                    transport = httpx.ASGITransport(app=app)
                    async with httpx.AsyncClient(
                        transport=transport, base_url="http://127.0.0.1"
                    ) as client:
                        health = await client.get("/health")
                        home = await client.get("/")
                        style = await client.get("/static/style.css")
                        script = await client.get("/static/app.js")
                    if health.json() != {"status": "ok"}:
                        raise RuntimeError("health response did not match the expected payload")
                    return (
                        health.status_code,
                        {
                            "ui_home_status": home.status_code,
                            "ui_static_statuses": [style.status_code, script.status_code],
                            "ui_has_external_asset": "https://" in home.text.lower(),
                        },
                    )

        status_code, ui_metrics = asyncio.run(request())
        if status_code != 200 or ui_metrics["ui_home_status"] != 200:
            raise RuntimeError("CPDataKit UI response did not match the expected payload")
        return {
            "status": "pass",
            "packages": packages,
            "metrics": {"status_code": status_code, **ui_metrics},
            "error": None,
        }
    except Exception as exc:  # pragma: no cover - environment-specific behavior
        return _operation_failure(packages, exc)


def _probe_operations() -> dict[str, dict[str, Any]]:
    operations: dict[str, dict[str, Any]] = {}
    try:
        xarray = importlib.import_module("xarray")
    except Exception as exc:
        xarray = None
        xarray_error = f"xarray unavailable: {type(exc).__name__}"
    if xarray is None:
        operations["netcdf:h5netcdf"] = _operation_unavailable(["xarray", "h5netcdf"], xarray_error)
        operations["netcdf:netcdf4"] = _operation_unavailable(["xarray", "netCDF4"], xarray_error)
        operations["zarr:v3"] = _operation_unavailable(["xarray", "zarr"], xarray_error)
    else:
        for key, engine, package in (
            ("netcdf:h5netcdf", "h5netcdf", "h5netcdf"),
            ("netcdf:netcdf4", "netcdf4", "netCDF4"),
        ):
            try:
                importlib.import_module(package)
            except Exception as exc:
                operations[key] = _operation_unavailable(
                    ["xarray", package], f"{package} unavailable: {type(exc).__name__}"
                )
            else:
                operations[key] = _run_netcdf_operation(xarray, engine, package)
        try:
            importlib.import_module("zarr")
        except Exception as exc:
            operations["zarr:v3"] = _operation_unavailable(
                ["xarray", "zarr"], f"zarr unavailable: {type(exc).__name__}"
            )
        else:
            operations["zarr:v3"] = _run_zarr_operation(xarray)

    try:
        pyarrow = importlib.import_module("pyarrow")
    except Exception as exc:
        operations["parquet"] = _operation_unavailable(
            ["pyarrow"], f"pyarrow unavailable: {type(exc).__name__}"
        )
    else:
        operations["parquet"] = _run_parquet_operation(pyarrow)

    try:
        fastapi = importlib.import_module("fastapi")
        httpx = importlib.import_module("httpx")
    except Exception as exc:
        operations["fastapi:httpx"] = _operation_unavailable(
            ["fastapi", "httpx"], f"web packages unavailable: {type(exc).__name__}"
        )
    else:
        operations["fastapi:httpx"] = _run_fastapi_operation(fastapi, httpx)
    return operations


def probe_environment(
    candidates: Path = _CANDIDATES,
    *,
    candidate_set: str | None = None,
    operations: bool = True,
) -> dict[str, Any]:
    """Return a JSON-ready dependency and runtime availability report."""
    if candidate_set is not None and candidate_set not in {"lower", "latest"}:
        raise ValueError("candidate set must be 'lower', 'latest', or None")
    candidate_payload = _read_candidates(candidates)
    dependencies = {
        candidate["name"]: _probe_package(candidate) for candidate in candidate_payload["packages"]
    }
    return {
        "format": "CPDataKit v0.6 dependency probe 1.0",
        "candidate_file": candidates.name,
        "python": {
            "version": platform.python_version(),
            "major": sys.version_info.major,
            "minor": sys.version_info.minor,
            "implementation": platform.python_implementation(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "dependencies": dependencies,
        "candidate_set": candidate_set,
        "runtime": {"cpdatakit": _probe_runtime_module("cpdatakit", "cpdatakit")},
        "operations": _probe_operations() if operations else {},
    }


def write_probe(payload: dict[str, Any], output: Path, *, force: bool = False) -> Path:
    """Write a sorted probe JSON file, preserving an existing output by default."""
    if output.exists() and not force:
        raise FileExistsError(
            f"Probe output already exists: {output}; pass force=True to replace it"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe v0.6 dependency availability")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--candidates", type=Path, default=_CANDIDATES)
    parser.add_argument("--candidate-set", choices=["lower", "latest"])
    parser.add_argument(
        "--operations",
        action="store_true",
        help="Run local NetCDF, Zarr, Parquet, and FastAPI smoke operations",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    payload = probe_environment(
        args.candidates,
        candidate_set=args.candidate_set,
        operations=args.operations,
    )
    if args.output is None:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    else:
        write_probe(payload, args.output, force=args.force)
        print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
