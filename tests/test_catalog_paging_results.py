import sqlite3

import pytest

from cpdatakit.catalog import CatalogError, SQLiteCatalog


def make_catalog(tmp_path):
    catalog = SQLiteCatalog(tmp_path / "catalog.sqlite3", tmp_path)
    catalog.initialize()
    return catalog, catalog.create_project("study")


def test_job_results_survive_reopen_and_omitted_updates_but_can_be_explicitly_cleared(tmp_path):
    catalog, project = make_catalog(tmp_path)
    payload = {"validation": {"valid": False, "errors": ["missing field"]}, "count": 2}
    job = catalog.register_job(
        project.id, job_id="result", operation="validate", status="failed", result=payload
    )
    assert job.result == payload
    reopened = SQLiteCatalog(catalog.database, tmp_path)
    assert reopened.get_job(job.id).result == payload
    assert reopened.list_jobs(project.id)[0].result == payload
    assert reopened.update_job(job.id, status="failed", error="details retained").result == payload
    assert reopened.update_job(job.id, status="succeeded", result=[1, 2, None]).result == [
        1,
        2,
        None,
    ]
    assert reopened.update_job(job.id, status="succeeded", result=None).result is None


@pytest.mark.parametrize("invalid", [{"value": float("nan")}, {"opaque": object()}])
def test_invalid_job_result_does_not_replace_durable_result(tmp_path, invalid):
    catalog, project = make_catalog(tmp_path)
    catalog.register_job(
        project.id,
        job_id="result",
        operation="validate",
        status="succeeded",
        result={"valid": True},
    )
    with pytest.raises(CatalogError, match="JSON"):
        catalog.update_job("result", status="failed", result=invalid)
    assert catalog.get_job("result").result == {"valid": True}
    assert catalog.get_job("result").status == "succeeded"


def test_job_summary_pages_do_not_load_or_decode_durable_results(tmp_path):
    catalog, project = make_catalog(tmp_path)
    catalog.register_job(
        project.id,
        job_id="large-result",
        operation="validate",
        status="succeeded",
        result={"payload": "x" * 10000},
    )
    with sqlite3.connect(catalog.database) as connection:
        connection.execute("UPDATE jobs SET result_json = 'invalid JSON sentinel'")
    summary = catalog.list_jobs(project.id, limit=1, include_result=False)
    assert summary[0].id == "large-result"
    assert summary[0].result is None
    with pytest.raises(ValueError):
        catalog.get_job("large-result")
    with pytest.raises(ValueError):
        catalog.list_jobs(project.id)
    with pytest.raises(CatalogError, match="include_result"):
        catalog.list_jobs(project.id, include_result="false")


def test_resource_pages_counts_and_direct_lookups_preserve_project_ownership(tmp_path):
    catalog, project = make_catalog(tmp_path)
    other = catalog.create_project("other")
    for current in (project, other):
        for index in range(4):
            catalog.register_dataset(current.id, f"data-{index}.csv", sha256="a" * 64)
            catalog.register_artifact(current.id, f"plot-{index}.svg", sha256="b" * 64, kind="plot")
            catalog.register_schema(
                current.id, name=f"schema-{index}", version="1", sha256="c" * 64
            )
            catalog.register_job(
                current.id,
                job_id=f"{current.id}-{4 - index}",
                operation=f"job-{index}",
                status="succeeded",
                result={"index": index},
            )
    assert catalog.count_resources(project.id) == {
        "datasets": 4,
        "artifacts": 4,
        "schemas": 4,
        "jobs": 4,
    }
    for resource in ("datasets", "artifacts", "schemas", "jobs"):
        listing = getattr(catalog, "list_" + resource)
        all_records = listing(project.id)
        assert listing(project.id, limit=2, offset=1) == all_records[1:3]
        assert listing(project.id, limit=2, newest_first=True) == all_records[-1:-3:-1]
        assert listing(project.id, offset=3) == all_records[3:]
        assert listing(project.id, offset=20, limit=2) == ()
        assert all(record.project_id == project.id for record in listing(project.id, limit=2))
    assert [job.operation for job in catalog.list_jobs(project.id, limit=2, newest_first=True)] == [
        "job-3",
        "job-2",
    ]
    for resource in ("dataset", "artifact", "schema"):
        record = getattr(catalog, "list_" + resource + "s")(other.id)[1]
        assert getattr(catalog, "get_" + resource)(record.id) == record
        with pytest.raises(CatalogError, match="does not exist"):
            getattr(catalog, "get_" + resource)(99999)
    with pytest.raises(CatalogError, match="Project does not exist"):
        catalog.count_resources(99999)


@pytest.mark.parametrize("resource", ["datasets", "artifacts", "schemas", "jobs"])
@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": True},
        {"limit": -1},
        {"offset": -1},
        {"offset": 1.5},
        {"newest_first": 1},
    ],
)
def test_pagination_rejects_invalid_bounds(tmp_path, resource, kwargs):
    catalog, project = make_catalog(tmp_path)
    with pytest.raises(CatalogError):
        getattr(catalog, "list_" + resource)(project.id, **kwargs)


def legacy_database(path, *, version=3):
    connection = sqlite3.connect(path)
    connection.executescript("""
CREATE TABLE projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL, workspace TEXT NOT NULL);
CREATE TABLE datasets(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL,
relative_path TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE artifacts(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL,
relative_path TEXT NOT NULL, kind TEXT NOT NULL, sha256 TEXT NOT NULL);
INSERT INTO projects VALUES (1, 'legacy', '.');
""")
    if version >= 2:
        connection.execute(
            "ALTER TABLE datasets ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
        )
        connection.execute(
            "ALTER TABLE artifacts ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
        )
    if version >= 3:
        connection.executescript("""
CREATE TABLE schemas(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL,
name TEXT NOT NULL, version TEXT NOT NULL, relative_path TEXT, sha256 TEXT NOT NULL,
metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE jobs(id TEXT PRIMARY KEY, project_id INTEGER NOT NULL, operation TEXT NOT NULL,
status TEXT NOT NULL, started_at TEXT, finished_at TEXT, input_filename TEXT, output_filename TEXT,
operation_log_json TEXT NOT NULL DEFAULT '[]', error TEXT);
INSERT INTO jobs(id, project_id, operation, status) VALUES('old', 1, 'inspect', 'succeeded');
""")
    connection.execute(f"PRAGMA user_version = {version}")
    connection.commit()
    return connection


def test_version_three_migration_preserves_old_jobs_and_backups_include_wal_commits(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    writer = legacy_database(path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute(
            "INSERT INTO jobs(id, project_id, operation, status) "
            "VALUES ('wal', 1, 'convert', 'succeeded')"
        )
        writer.commit()
        catalog = SQLiteCatalog(path, tmp_path)
        catalog.initialize()
        assert [job.id for job in catalog.list_jobs(1)] == ["old", "wal"]
        assert all(job.result is None for job in catalog.list_jobs(1))
        catalog.update_job("wal", status="succeeded", result={"artifact": "result.h5"})
        backups = list(tmp_path.glob("catalog.sqlite3.bak*"))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 3
            assert backup.execute("SELECT id FROM jobs ORDER BY rowid").fetchall() == [
                ("old",),
                ("wal",),
            ]
        with sqlite3.connect(path) as upgraded:
            assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 4
            for table in ("datasets", "artifacts", "schemas", "jobs"):
                plan = upgraded.execute(
                    f"EXPLAIN QUERY PLAN SELECT id FROM {table} "
                    "WHERE project_id = 1 ORDER BY rowid LIMIT 2"
                ).fetchall()
                assert any("INDEX" in row[3] for row in plan)
    finally:
        writer.close()


@pytest.mark.parametrize("version", [1, 3])
def test_failed_migration_rolls_back_every_schema_change_and_preserves_backup(
    tmp_path, monkeypatch, version
):
    path = tmp_path / "catalog.sqlite3"
    legacy_database(path, version=version).close()
    catalog = SQLiteCatalog(path, tmp_path)

    class FailIndex(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.lstrip().startswith("CREATE INDEX"):
                raise sqlite3.OperationalError("injected index failure")
            return super().execute(sql, parameters)

    monkeypatch.setattr(catalog, "_connect", lambda: sqlite3.connect(path, factory=FailIndex))
    with pytest.raises(CatalogError, match="injected index failure"):
        catalog.initialize()
    with sqlite3.connect(path) as unchanged:
        assert unchanged.execute("PRAGMA user_version").fetchone()[0] == version
        if version == 1:
            assert "metadata_json" not in {
                row[1] for row in unchanged.execute("PRAGMA table_info(datasets)")
            }
            assert (
                unchanged.execute("SELECT name FROM sqlite_master WHERE name='jobs'").fetchone()
                is None
            )
        else:
            assert "result_json" not in {
                row[1] for row in unchanged.execute("PRAGMA table_info(jobs)")
            }
        assert unchanged.execute("SELECT name FROM projects WHERE id=1").fetchone()[0] == "legacy"
    assert list(tmp_path.glob("catalog.sqlite3.bak*"))


def test_backup_failure_leaves_original_schema_untouched(tmp_path, monkeypatch):
    path = tmp_path / "catalog.sqlite3"
    legacy_database(path).close()
    catalog = SQLiteCatalog(path, tmp_path)
    occupied = tmp_path / "existing-backup"
    occupied.write_bytes(b"user backup")
    monkeypatch.setattr(catalog, "_backup_path", lambda: occupied)
    with pytest.raises(CatalogError, match="back up"):
        catalog.initialize()
    assert occupied.read_bytes() == b"user backup"
    with sqlite3.connect(path) as unchanged:
        assert unchanged.execute("PRAGMA user_version").fetchone()[0] == 3
        assert "result_json" not in {row[1] for row in unchanged.execute("PRAGMA table_info(jobs)")}


def test_omitted_update_result_does_not_revert_a_concurrent_result_write(tmp_path, monkeypatch):
    catalog, project = make_catalog(tmp_path)
    catalog.register_job(
        project.id, job_id="result", operation="validate", status="running", result={"old": True}
    )
    original = catalog.get_job
    concurrent = True

    def interleaved_read(job_id):
        nonlocal concurrent
        record = original(job_id)
        if concurrent:
            concurrent = False
            with sqlite3.connect(catalog.database) as writer:
                writer.execute("UPDATE jobs SET result_json=? WHERE id=?", ('{"new":true}', job_id))
        return record

    monkeypatch.setattr(catalog, "get_job", interleaved_read)
    assert catalog.update_job("result", status="succeeded").result == {"new": True}
