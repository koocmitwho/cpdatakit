from __future__ import annotations

import json
import subprocess
import sys


def test_benchmark_reports_storage_chunk_size(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_hdf5_read.py",
            "--records",
            "100",
            "--chunk-size",
            "16",
            "--hdf5-chunk-size",
            "8",
            "--output-dir",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["records"] == 100
    assert payload["chunk_size"] == 16
    assert payload["hdf5_chunk_size"] == 8
    assert payload["full"]["record_count"] == 100
    assert payload["selected_fields"]["record_count"] == 100
    assert payload["chunked"]["record_count"] == 100


def test_selective_benchmark_compares_equivalent_outputs_in_separate_processes(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_selective_read.py",
            "--output-dir",
            str(tmp_path),
            "--time",
            "8",
            "--side",
            "8",
            "--rows",
            "100",
            "--repeats",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "report.json").read_text())
    assert set(report["formats"]) == {"netcdf", "zarr", "parquet"}
    for item in report["formats"].values():
        assert item["input_bytes"] > 0
        assert item["eager"][0]["sha256"] == item["selective"][0]["sha256"]
        assert item["eager"][0]["pid"] != item["selective"][0]["pid"]
        assert item["selective"][0]["peak_rss_mib"] > 0
