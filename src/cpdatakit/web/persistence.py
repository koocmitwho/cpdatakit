"""Bounded independent catalog retries backed by terminal completion evidence."""

import json
import logging
import sqlite3
import threading
import time
from contextlib import suppress
from types import SimpleNamespace

from ..exceptions import CatalogError
from .durable import atomic_json

logger = logging.getLogger(__name__)
TERMINAL = {"succeeded", "failed", "cancelled"}


def _validate_completion(payload):
    required = {
        "id",
        "operation",
        "status",
        "started_at",
        "finished_at",
        "input_filename",
        "output_filename",
        "operation_log",
        "result",
        "error",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Incomplete completion evidence")
    for key in ("id", "operation", "status"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise ValueError("Invalid completion identity")
    for key in ("started_at", "finished_at", "input_filename", "output_filename", "error"):
        if payload[key] is not None and not isinstance(payload[key], str):
            raise ValueError("Invalid completion fields")
    if not isinstance(payload["operation_log"], list) or not all(
        isinstance(item, str) for item in payload["operation_log"]
    ):
        raise ValueError("Invalid completion log")
    if payload["status"] not in TERMINAL:
        raise ValueError("Completion is not terminal")
    json.dumps(payload, allow_nan=False)


class JobPersistence:
    def __init__(self, workspace, catalog, lock, on_saved):
        self.root = workspace / ".job-completions"
        if self.root.is_symlink():
            raise CatalogError("Completion storage cannot be a symbolic link")
        self.catalog = catalog
        self.workspace = workspace
        self.lock = lock
        self.on_saved = on_saved
        self.pending = {}
        self._stop = threading.Event()
        self._thread = None
        self.recovery_errors = []
        self.drained = False

    def replay(self):
        for path in self.root.glob("*.json"):
            try:
                if path.is_symlink():
                    raise ValueError("linked completion record")
                entry = json.loads(path.read_text(encoding="utf-8"))
                payload = entry["record"]
                _validate_completion(payload)
                current = self.catalog.get_job(payload["id"])
                if (
                    entry["version"] != 1
                    or entry["workspace"] != str(self.workspace)
                    or path.stem != payload["id"]
                    or current.project_id != entry["project_id"]
                    or current.operation != payload["operation"]
                    or type(entry["project_id"]) is not int
                    or (current.status in TERMINAL and current.status != payload["status"])
                    or payload["status"] not in TERMINAL
                ):
                    raise ValueError("completion ownership mismatch")
                self.save(payload, entry["project_id"])
            except (OSError, ValueError, KeyError, TypeError, CatalogError) as exc:
                self.recovery_errors.append(path.name)
                logger.warning(
                    "Completion evidence retained: %s (%s)", path.name, type(exc).__name__
                )

    def status(self, job_id):
        with self.lock:
            entry = self.pending.get(job_id)
            if entry is None:
                return {"state": "saved"}
            return {
                "state": "pending",
                "attempts": entry["attempts"],
                "evidence_saved": entry["evidence_saved"],
                "error": "Job result is waiting to be saved to the local catalog.",
            }

    def record(self, job_id):
        with self.lock:
            entry = self.pending.get(job_id)
            return SimpleNamespace(**entry["record"]) if entry else None

    def save(self, payload, project_id):
        with self.lock:
            if self._stop.is_set():
                raise CatalogError("Job persistence is closed")
            job_id = payload["id"]
            entry = self.pending.setdefault(
                job_id,
                {
                    "record": payload,
                    "project_id": project_id,
                    "attempts": 0,
                    "evidence_saved": False,
                    "retry_at": 0.0,
                },
            )
            # HTTP access never resets the bounded retry budget.
            if entry["attempts"] == 0:
                self._attempt(job_id, entry)
            if (
                not self._stop.is_set()
                and any(item["attempts"] < 6 for item in self.pending.values())
                and self._thread is None
            ):
                self._thread = threading.Thread(
                    target=self._retry, name="cpdatakit-save", daemon=True
                )
                self._thread.start()

    def _attempt(self, job_id, entry):
        entry["attempts"] += 1
        entry["retry_at"] = time.monotonic() + 0.05 * (2 ** (entry["attempts"] - 1))
        payload = entry["record"]
        if not entry["evidence_saved"]:
            try:
                atomic_json(
                    self.root / f"{job_id}.json",
                    {
                        "version": 1,
                        "workspace": str(self.workspace),
                        "project_id": entry["project_id"],
                        "record": payload,
                    },
                )
                entry["evidence_saved"] = True
            except (OSError, ValueError):
                logger.warning("Could not save completion evidence for job %s", job_id)
        try:
            self.catalog.update_job(
                job_id,
                **{
                    key: payload[key]
                    for key in (
                        "status",
                        "started_at",
                        "finished_at",
                        "operation_log",
                        "error",
                        "result",
                    )
                },
            )
        except (CatalogError, OSError, sqlite3.DatabaseError):
            logger.warning(
                "Job %s awaiting catalog persistence (attempt %s)", job_id, entry["attempts"]
            )
            return
        self.pending.pop(job_id, None)
        with suppress(OSError):
            (self.root / f"{job_id}.json").unlink(missing_ok=True)
        self.on_saved(job_id)

    def _retry(self):
        while not self._stop.wait(0.025):
            with self.lock:
                for job_id, entry in list(self.pending.items()):
                    if entry["attempts"] < 6 and entry["retry_at"] <= time.monotonic():
                        self._attempt(job_id, entry)
                if not any(entry["attempts"] < 6 for entry in self.pending.values()):
                    # Clearing under the save lock lets an arriving completion
                    # start a successor even before this thread has returned.
                    self._thread = None
                    return

    def close(self):
        self._stop.set()
        # The retry loop can clear its shared reference while it exits. Keep
        # the captured thread alive for join; the stop flag prevents successors
        # from doing work, and the final lock drains any concurrent save.
        worker = self._thread
        if worker is not None:
            worker.join()
        with self.lock:
            try:
                for job_id, entry in list(self.pending.items()):
                    self._attempt(job_id, entry)
            finally:
                self.drained = True
