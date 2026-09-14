from __future__ import annotations

import json
import subprocess
import sys


def test_startup_benchmark_runs_each_operation_in_fresh_processes():
    result = subprocess.run(
        [sys.executable, "scripts/benchmark_startup.py", "--repeats", "2"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["python_executable"] == sys.executable
    assert set(report["operations"]) == {"import", "version", "help"}
    for operation in report["operations"].values():
        assert len(operation["seconds"]) == 2
        assert all(elapsed > 0 for elapsed in operation["seconds"])
        assert min(operation["seconds"]) <= operation["median_seconds"]
        assert operation["median_seconds"] <= max(operation["seconds"])
        assert len(set(operation["pids"])) == 2
    assert report["operations"]["import"]["stdout"] == ""
    assert report["operations"]["version"]["stdout"].startswith("cpdatakit ")
    assert "usage: cpdatakit" in report["operations"]["help"]["stdout"]


def test_startup_benchmark_rejects_zero_repeats():
    result = subprocess.run(
        [sys.executable, "scripts/benchmark_startup.py", "--repeats", "0"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "must be positive" in result.stderr
