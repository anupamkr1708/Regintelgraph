"""Migration runner: forward-only, one transaction per file, checksum-pinned, concurrency-safe."""

from __future__ import annotations

import threading
from pathlib import Path

import psycopg
import pytest

from scripts import migrate
from scripts.migrate import ChecksumMismatchError, MigrationError, OrderingError, apply_migrations, discover

pytestmark = pytest.mark.postgres
REPO_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def tables(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as c:
        return {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()}


def recorded(dsn: str) -> list[tuple[int, str, str]]:
    with psycopg.connect(dsn) as c:
        return [
            (int(v), str(n), str(s).strip())
            for v, n, s in c.execute("SELECT version, name, checksum FROM schema_migration ORDER BY version").fetchall()
        ]


def test_empty_database_gets_all_repository_migrations(pg_empty_dsn: str) -> None:
    applied = apply_migrations(pg_empty_dsn)
    assert applied == [m.version for m in discover(REPO_MIGRATIONS)] and applied
    assert {
        "source",
        "ingest_run",
        "raw_artifact",
        "regulatory_document",
        "document_version",
        "document_version_location",
        "ingest_result",
        "quarantine_record",
        "job",
        "job_event",
        "schema_migration",
    } <= tables(pg_empty_dsn)


def test_no_tables_from_later_phases_exist(pg_empty_dsn: str) -> None:
    apply_migrations(pg_empty_dsn)
    allowed = {
        "source",
        "ingest_run",
        "raw_artifact",
        "regulatory_document",
        "document_version",
        "document_version_location",
        "ingest_result",
        "quarantine_record",
        "job",
        "job_event",
        "schema_migration",
    }
    assert tables(pg_empty_dsn) == allowed  # no embeddings / graph / retrieval / claim / agent / MCP tables
    with psycopg.connect(pg_empty_dsn) as c:
        assert c.execute("SELECT count(*) FROM pg_extension WHERE extname='vector'").fetchone() == (0,)  # extension not created
        assert c.execute("SELECT count(*) FROM pg_indexes WHERE indexdef ILIKE '%hnsw%' OR indexdef ILIKE '%ivfflat%'").fetchone() == (0,)


def test_rerun_is_a_noop(pg_empty_dsn: str) -> None:
    apply_migrations(pg_empty_dsn)
    before = recorded(pg_empty_dsn)
    assert apply_migrations(pg_empty_dsn) == []
    assert recorded(pg_empty_dsn) == before


def test_checksums_are_recorded_and_match_the_files(pg_empty_dsn: str) -> None:
    apply_migrations(pg_empty_dsn)
    assert [(m.version, m.name, m.checksum) for m in discover(REPO_MIGRATIONS)] == recorded(pg_empty_dsn)


def test_editing_an_applied_migration_is_a_hard_failure_and_is_never_repaired(pg_empty_dsn: str, tmp_path: Path) -> None:
    write(tmp_path, "0001_a.sql", "CREATE TABLE t1 (id int);")
    apply_migrations(pg_empty_dsn, tmp_path)
    before = recorded(pg_empty_dsn)
    write(tmp_path, "0001_a.sql", "CREATE TABLE t1 (id int, extra int);")  # tampered after apply
    for _ in range(2):  # fails every time: nothing "heals" the mismatch
        with pytest.raises(ChecksumMismatchError, match="edited after"):
            apply_migrations(pg_empty_dsn, tmp_path)
    assert recorded(pg_empty_dsn) == before


def test_whitespace_only_edits_also_fail(pg_empty_dsn: str, tmp_path: Path) -> None:
    write(tmp_path, "0001_a.sql", "CREATE TABLE t1 (id int);")
    apply_migrations(pg_empty_dsn, tmp_path)
    write(tmp_path, "0001_a.sql", "CREATE TABLE t1 (id int); ")
    with pytest.raises(ChecksumMismatchError):
        apply_migrations(pg_empty_dsn, tmp_path)


def test_a_missing_applied_file_is_refused(pg_empty_dsn: str, tmp_path: Path) -> None:
    write(tmp_path, "0001_a.sql", "CREATE TABLE t1 (id int);")
    write(tmp_path, "0002_b.sql", "CREATE TABLE t2 (id int);")
    apply_migrations(pg_empty_dsn, tmp_path)
    (tmp_path / "0002_b.sql").unlink()
    with pytest.raises(ChecksumMismatchError, match="no file"):
        apply_migrations(pg_empty_dsn, tmp_path)


def test_a_failing_migration_rolls_back_entirely_and_is_not_recorded(pg_empty_dsn: str, tmp_path: Path) -> None:
    write(tmp_path, "0001_ok.sql", "CREATE TABLE good (id int);")
    write(tmp_path, "0002_bad.sql", "CREATE TABLE half_done (id int);\nINSERT INTO half_done VALUES (1);\nSELECT 1/0;")
    write(tmp_path, "0003_never.sql", "CREATE TABLE never (id int);")
    with pytest.raises(psycopg.errors.DivisionByZero):
        apply_migrations(pg_empty_dsn, tmp_path)
    assert "half_done" not in tables(pg_empty_dsn) and "never" not in tables(pg_empty_dsn)  # the failed file left nothing behind
    assert "good" in tables(pg_empty_dsn) and [v for v, _, _ in recorded(pg_empty_dsn)] == [1]  # earlier migration stays applied
    write(tmp_path, "0002_bad.sql", "CREATE TABLE half_done (id int);")  # fixing a NEVER-applied file is allowed
    assert apply_migrations(pg_empty_dsn, tmp_path) == [2, 3]


def test_a_migration_and_its_bookkeeping_row_commit_atomically(pg_empty_dsn: str, tmp_path: Path) -> None:
    """If recording the migration fails, the migration's own effects must roll back with it (never applied-but-unrecorded)."""
    write(tmp_path, "0001_a.sql", "CREATE TABLE applied_but_unrecorded (id int);")
    with psycopg.connect(pg_empty_dsn, autocommit=True) as c:
        c.execute(migrate._BOOTSTRAP)
        c.execute(
            "CREATE FUNCTION refuse_record() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'bookkeeping refused'; END $$"
        )
        c.execute("CREATE TRIGGER refuse BEFORE INSERT ON schema_migration FOR EACH ROW EXECUTE FUNCTION refuse_record()")
    with pytest.raises(psycopg.errors.RaiseException, match="bookkeeping refused"):
        apply_migrations(pg_empty_dsn, tmp_path)
    assert "applied_but_unrecorded" not in tables(pg_empty_dsn)  # the DDL was rolled back together with the failed record
    with psycopg.connect(pg_empty_dsn, autocommit=True) as c:
        c.execute("DROP TRIGGER refuse ON schema_migration")
    assert apply_migrations(pg_empty_dsn, tmp_path) == [1]  # and the migration then applies normally


def test_ordering_is_deterministic_and_numeric(pg_empty_dsn: str, tmp_path: Path) -> None:
    for n in (3, 1, 2):
        write(tmp_path, f"000{n}_m{n}.sql", f"CREATE TABLE m{n} (id int);\nINSERT INTO m{n} VALUES ({n});")
    write(
        tmp_path,
        "0004_dep.sql",
        "CREATE TABLE dep AS SELECT (SELECT count(*) FROM m1) + (SELECT count(*) FROM m2) + (SELECT count(*) FROM m3) AS n;",
    )
    assert apply_migrations(pg_empty_dsn, tmp_path) == [1, 2, 3, 4]
    assert [v for v, _, _ in recorded(pg_empty_dsn)] == [1, 2, 3, 4]


@pytest.mark.parametrize(
    "names",
    [
        ["0001_a.sql", "0003_c.sql"],
        ["0001_a.sql", "0001_dup.sql"],
        ["0002_a.sql"],
        ["001_a.sql"],
        ["0001_A.sql"],
        ["0001-a.sql"],
        ["init.sql"],
    ],
)
def test_bad_numbering_or_names_are_rejected(names: list[str], tmp_path: Path) -> None:
    for n in names:
        write(tmp_path, n, "SELECT 1;")
    with pytest.raises(OrderingError):
        discover(tmp_path)


def test_gaps_are_rejected_before_the_database_is_touched(pg_empty_dsn: str, tmp_path: Path) -> None:
    write(tmp_path, "0001_a.sql", "CREATE TABLE a (id int);")
    write(tmp_path, "0003_c.sql", "CREATE TABLE c (id int);")
    with pytest.raises(OrderingError):
        apply_migrations(pg_empty_dsn, tmp_path)
    assert "schema_migration" not in tables(pg_empty_dsn)


def test_an_unapplied_migration_sorting_before_an_applied_one_is_rejected(pg_empty_dsn: str, tmp_path: Path) -> None:
    write(tmp_path, "0001_a.sql", "CREATE TABLE a (id int);")
    write(tmp_path, "0002_b.sql", "CREATE TABLE b (id int);")
    migrations = {m.version: m for m in discover(tmp_path)}
    with psycopg.connect(pg_empty_dsn, autocommit=True) as c:  # simulate a database where 0002 was applied but 0001 was not
        c.execute(migrate._BOOTSTRAP)
        c.execute("INSERT INTO schema_migration (version, name, checksum) VALUES (2, %s, %s)", (migrations[2].name, migrations[2].checksum))
    with pytest.raises(OrderingError, match="sorts before"):
        apply_migrations(pg_empty_dsn, tmp_path)
    assert "a" not in tables(pg_empty_dsn)


@pytest.mark.parametrize("stmt", ["BEGIN;", "COMMIT;", "ROLLBACK;", "start transaction;", "  begin ;", "END;"])
def test_transaction_control_in_a_migration_is_refused(stmt: str, tmp_path: Path) -> None:
    write(tmp_path, "0001_a.sql", f"CREATE TABLE a (id int);\n{stmt}\n")
    with pytest.raises(MigrationError, match="transaction control"):
        discover(tmp_path)


def test_plpgsql_begin_end_inside_function_bodies_is_not_transaction_control(tmp_path: Path) -> None:
    write(tmp_path, "0001_f.sql", "CREATE FUNCTION f() RETURNS int LANGUAGE plpgsql AS $$\nBEGIN\n  RETURN 1;\nEND\n$$;")
    assert len(discover(tmp_path)) == 1


def test_concurrent_runners_serialise_and_apply_each_migration_exactly_once(pg_empty_dsn: str, tmp_path: Path) -> None:
    write(tmp_path, "0001_slow.sql", "CREATE TABLE a (id int);\nSELECT pg_sleep(0.3);")
    write(tmp_path, "0002_b.sql", "CREATE TABLE b (id int);")
    results: list[list[int] | BaseException] = []

    def run() -> None:
        try:
            results.append(apply_migrations(pg_empty_dsn, tmp_path))
        except BaseException as exc:
            results.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not [r for r in results if isinstance(r, BaseException)], results
    assert sorted(map(tuple, results), key=len) == [(), (), (), (1, 2)]  # exactly one runner did the work; the rest were no-ops
    assert [v for v, _, _ in recorded(pg_empty_dsn)] == [1, 2]


def test_cli_status_and_apply(pg_empty_dsn: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert migrate.main(["apply", "--dsn", pg_empty_dsn]) == 0
    assert "0001" in capsys.readouterr().out
    assert migrate.main(["status", "--dsn", pg_empty_dsn]) == 0
    assert migrate.main(["apply", "--dsn", pg_empty_dsn]) == 0 and "up to date" in capsys.readouterr().out


def test_cli_without_a_dsn_is_a_usage_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("RIG_DATABASE_URL", raising=False)
    assert migrate.main(["apply"]) == 2 and "no DSN" in capsys.readouterr().err


def test_database_settings_the_project_relies_on(pg_conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    assert pg_conn.execute("SHOW server_encoding").fetchone() == ("UTF8",)
    assert pg_conn.execute("SHOW timezone").fetchone() == ("UTC",)
    cols = pg_conn.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema='public'"
        " AND (column_name LIKE '%\\_at' OR column_name IN ('first_seen','last_seen','run_after'))"
    ).fetchall()
    assert cols and all(t == "timestamp with time zone" for _, _, t in cols), cols  # timestamptz everywhere, never naive timestamps
