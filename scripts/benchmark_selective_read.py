"""Reproduce v0.7 eager reads and compare selection in fresh subprocesses."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import xarray as xr

from cpdatakit.formats import NetCDFReader, ParquetReader, Selection, ZarrReader


def peak_rss_mib():
    if sys.platform == "win32":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        info = Counters()
        info.cb = ctypes.sizeof(info)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(info), info.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.PeakWorkingSetSize / 2**20
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (2**20 if sys.platform == "darwin" else 1024)


def measure(args):
    path = (
        args.output_dir
        / {"netcdf": "field.nc", "zarr": "field.zarr", "parquet": "table.parquet"}[args.format]
    )
    start = args.rows // 2 if args.format == "parquet" else args.time // 2
    stop = min(args.rows, start + 100) if args.format == "parquet" else start + 1
    field = "value0" if args.format == "parquet" else "temperature"
    selection = Selection((field,), start, stop)
    begun = time.perf_counter()
    if args.mode == "selective":
        reader = {"netcdf": NetCDFReader, "zarr": ZarrReader, "parquet": ParquetReader}[
            args.format
        ]()
        loaded = reader.load(path, selection=selection)
        values = (
            loaded.data[field].to_numpy() if args.format == "parquet" else loaded.data[field].values
        )
    elif args.format == "parquet":
        # v0.7 projects columns but reads all their rows before iloc.
        table = pq.read_table(path, columns=[field], use_pandas_metadata=True)
        frame = table.to_pandas()
        values = frame.iloc[start:stop].reset_index(drop=True)[field].to_numpy()
    else:
        opener = (
            xr.open_dataset(path, engine="h5netcdf")
            if args.format == "netcdf"
            else xr.open_zarr(path, consolidated=False, chunks=None)
        )
        with opener as opened:
            dataset = opened.load()
        dataset = dataset[[field]].isel(time=slice(start, stop)).copy(deep=True)
        values = dataset[field].values
    elapsed = time.perf_counter() - begun
    return {
        "elapsed_seconds": elapsed,
        "peak_rss_mib": peak_rss_mib(),
        "sha256": hashlib.sha256(np.ascontiguousarray(values).tobytes()).hexdigest(),
        "shape": list(values.shape),
        "pid": os.getpid(),
    }


def generate(args):
    rng = np.random.default_rng(20260907)
    shape = (args.time, args.side, args.side)
    data = xr.Dataset(
        {
            name: (("time", "y", "x"), rng.random(shape, dtype=np.float32))
            for name in ("temperature", "other1", "other2")
        },
        coords={"time": np.arange(args.time), "x": np.arange(args.side), "y": np.arange(args.side)},
    )
    chunks = (min(4, args.time), min(128, args.side), min(128, args.side))
    data.to_netcdf(
        args.output_dir / "field.nc",
        engine="h5netcdf",
        encoding={name: {"chunksizes": chunks} for name in data.data_vars},
    )
    data.to_zarr(
        args.output_dir / "field.zarr",
        zarr_format=3,
        consolidated=False,
        encoding={name: {"chunks": chunks} for name in data.data_vars},
    )
    schema = pa.schema([(f"value{i}", pa.float64()) for i in range(8)])
    with pq.ParquetWriter(args.output_dir / "table.parquet", schema) as writer:
        for start in range(0, args.rows, 65536):
            count = min(65536, args.rows - start)
            writer.write_table(pa.table({f"value{i}": rng.random(count) for i in range(8)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--time", type=int, default=128)
    parser.add_argument("--side", type=int, default=512)
    parser.add_argument("--rows", type=int, default=2_000_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--mode", choices=["eager", "selective"])
    parser.add_argument("--format", choices=["netcdf", "zarr", "parquet"])
    args = parser.parse_args()
    if min(args.time, args.side, args.rows, args.repeats) <= 0:
        parser.error("sizes and repeat count must be positive")
    if args.mode:
        print(json.dumps(measure(args)))
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if any(args.output_dir.iterdir()):
        parser.error("use a new empty output directory")
    generate(args)
    report = {
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "packages": {
                name: importlib.metadata.version(name)
                for name in ("numpy", "pandas", "xarray", "h5py", "h5netcdf", "zarr", "pyarrow")
            },
        },
        "input": {
            "array_shape": [args.time, args.side, args.side],
            "array_variables": 3,
            "array_dtype": "float32",
            "rows": args.rows,
            "columns": 8,
            "seed": 20260907,
            "array_bytes": args.time * args.side**2 * 4 * 3,
        },
        "method": (
            "fresh processes; elapsed excludes imports; process peak RSS includes imports; "
            "OS cache uncontrolled; alternating order"
        ),
        "formats": {},
    }
    for format_name, filename in (
        ("netcdf", "field.nc"),
        ("zarr", "field.zarr"),
        ("parquet", "table.parquet"),
    ):
        source = args.output_dir / filename
        size = (
            sum(p.stat().st_size for p in source.rglob("*") if p.is_file())
            if source.is_dir()
            else source.stat().st_size
        )
        item = {"input_bytes": size, "eager": [], "selective": []}
        for repeat in range(args.repeats):
            for mode in ["eager", "selective"] if repeat % 2 == 0 else ["selective", "eager"]:
                result = subprocess.run(
                    [
                        sys.executable,
                        __file__,
                        "--output-dir",
                        str(args.output_dir),
                        "--time",
                        str(args.time),
                        "--side",
                        str(args.side),
                        "--rows",
                        str(args.rows),
                        "--mode",
                        mode,
                        "--format",
                        format_name,
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                item[mode].append(json.loads(result.stdout))
        if len({run["sha256"] for mode in ("eager", "selective") for run in item[mode]}) != 1:
            raise RuntimeError(f"{format_name}: selected output differs from eager output")
        report["formats"][format_name] = item
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
