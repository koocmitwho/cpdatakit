from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cpdatakit.catalog import CatalogError, SQLiteCatalog


def test_catalog_stores_workspace_relative_dataset_and_artifact_records(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "catalog.sqlite3"
    catalog = SQLiteCatalog(database, workspace)

    catalog.initialize()
    project = catalog.create_project("thermal study")
    source = workspace / "input" / "thermal.csv"
    source.parent.mkdir()
    source.write_text("time,temperature\n0,273.15\n", encoding="utf-8")
    dataset = catalog.register_dataset(project.id, source, sha256="a" * 64)
    artifact_path = workspace / "output" / "report.json"
    artifact = catalog.register_artifact(project.id, artifact_path, kind="report", sha256="b" * 64)

    assert dataset.relative_path == "input/thermal.csv"
    assert artifact.relative_path == "output/report.json"
    assert catalog.get_project(project.id).name == "thermal study"
    assert [item.id for item in catalog.list_datasets(project.id)] == [dataset.id]
    assert [item.id for item in catalog.list_artifacts(project.id)] == [artifact.id]
    with sqlite3.connect(database) as connection:
        stored_paths = [row[0] for row in connection.execute("SELECT relative_path FROM datasets")]
        stored_paths += [
            row[0] for row in connection.execute("SELECT relative_path FROM artifacts")
        ]
    assert stored_paths == ["input/thermal.csv", "output/report.json"]
    assert str(tmp_path) not in " ".join(stored_paths)


def test_catalog_rejects_paths_outside_workspace_before_persisting(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    catalog = SQLiteCatalog(tmp_path / "catalog.sqlite3", workspace)
    catalog.initialize()
    project = catalog.create_project("study")

    with pytest.raises(CatalogError, match="workspace"):
        catalog.register_dataset(project.id, tmp_path / "outside.csv", sha256="a" * 64)


def test_catalog_migration_backs_up_nonempty_database(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                workspace TEXT NOT NULL
            );
            CREATE TABLE datasets (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                sha256 TEXT NOT NULL
            );
            CREATE TABLE artifacts (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                relative_path TEXT NOT NULL,
                kind TEXT NOT NULL,
                sha256 TEXT NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    catalog = SQLiteCatalog(database, workspace)

    catalog.initialize()

    backup = database.with_name("catalog.sqlite3.bak")
    assert backup.exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        columns = {row[1] for row in connection.execute("PRAGMA table_info(datasets)")}
    assert "metadata_json" in columns


def test_catalog_stores_schema_and_job_metadata_without_absolute_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    catalog = SQLiteCatalog(tmp_path / "catalog.sqlite3", workspace)
    catalog.initialize()
    project = catalog.create_project("study")
    schema_path = workspace / "schemas" / "curve.json"
    schema_path.parent.mkdir()
    schema_path.write_text("{}", encoding="utf-8")

    schema = catalog.register_schema(
        project.id,
        name="curve",
        version="1.0",
        path=schema_path,
        sha256="c" * 64,
        metadata={"source": "bundled"},
    )
    catalog.register_job(
        project.id,
        job_id="job-1",
        operation="validate",
        status="succeeded",
        input_filename="curve.csv",
        output_filename="report.json",
        operation_log=("queued", "running", "succeeded"),
    )

    assert schema.relative_path == "schemas/curve.json"
    assert catalog.list_schemas(project.id)[0] == schema
    job = catalog.get_job("job-1")
    assert job.project_id == project.id
    assert job.operation_log == ("queued", "running", "succeeded")
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        stored = " ".join(str(row) for row in connection.execute("SELECT * FROM schemas"))
        stored += " " + " ".join(str(row) for row in connection.execute("SELECT * FROM jobs"))
    assert str(tmp_path) not in stored


def test_catalog_delete_is_non_destructive_to_source_bytes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    catalog = SQLiteCatalog(tmp_path / "catalog.sqlite3", workspace)
    catalog.initialize()
    project = catalog.create_project("study")
    dataset = catalog.register_dataset(project.id, source, sha256="a" * 64)

    catalog.delete_dataset(dataset.id)

    assert source.read_text(encoding="utf-8") == "value\n1\n"
    assert catalog.list_datasets(project.id) == ()
