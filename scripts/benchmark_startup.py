"""Measure fresh-process package import and CLI metadata startup latency."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time

_PROBE = """
import contextlib
import io
import json
import os
import sys

operation = sys.argv[1]
output = io.StringIO()
with contextlib.redirect_stdout(output):
    if operation == 'import':
        import cpdatakit
    else:
        from cpdatakit.cli import main
        try:
            main(['--' + operation])
        except SystemExit as exc:
            if exc.code != 0:
                raise
print(json.dumps({'pid': os.getpid(), 'stdout': output.getvalue()}))
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    operations = {}
    for operation in ("import", "version", "help"):
        seconds = []
        pids = []
        for _ in range(args.repeats):
            start = time.perf_counter()
            completed = subprocess.run(
                [sys.executable, "-c", _PROBE, operation],
                capture_output=True,
                text=True,
                check=True,
            )
            seconds.append(time.perf_counter() - start)
            probe = json.loads(completed.stdout)
            pids.append(probe["pid"])
        operations[operation] = {
            "seconds": seconds,
            "median_seconds": statistics.median(seconds),
            "pids": pids,
            "stdout": probe["stdout"],
        }
    print(
        json.dumps(
            {
                "python_executable": sys.executable,
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "timing": "wall time including process creation and interpreter shutdown",
                "operations": operations,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
