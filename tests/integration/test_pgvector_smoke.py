"""Smoke test ONLY: the server can load the pgvector extension. No vector tables, no HNSW/IVFFlat indexes, no embeddings
(those belong to the retrieval phase; no index type is claimed optimal here)."""

from __future__ import annotations

import psycopg
import pytest

pytestmark = pytest.mark.postgres


def test_vector_extension_can_be_created_in_a_throwaway_database(pg_empty_dsn: str, record_property: pytest.RecordProperty) -> None:
    with psycopg.connect(pg_empty_dsn, autocommit=True) as c:
        available = c.execute("SELECT default_version FROM pg_available_extensions WHERE name = 'vector'").fetchone()
        if available is None:
            pytest.fail("pgvector is not installed on this PostgreSQL server (prerequisite failed; CI must use an image that ships it)")
        c.execute("CREATE EXTENSION vector")
        version = c.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
        assert version is not None
        record_property("pgvector_extversion", str(version[0]))  # observed fact, recorded in the report; not asserted against a number
        assert c.execute("SELECT '[1,2,3]'::vector(3)::text").fetchone() == ("[1,2,3]",)
        assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'").fetchone() == (
            0,
        )  # nothing was created for it


def test_the_project_migrations_never_create_the_extension_or_vector_objects(pg_conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    assert pg_conn.execute("SELECT count(*) FROM pg_extension WHERE extname = 'vector'").fetchone() == (0,)
    assert pg_conn.execute(
        "SELECT count(*) FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid WHERE t.typname = 'vector'"
    ).fetchone() == (0,)
