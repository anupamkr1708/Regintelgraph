"""Database invariants of `fetch_request` (migration 0002) and the quarantine `requested_url` provenance policy."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import errors as pgerr
from psycopg.types.json import Jsonb

from packages.ingestion.logsafe import LOGGED_HEADERS
from packages.ingestion.pgrepo import PgRepository
from tests.integration.test_source_layer_db import new_run
from tests.support.builders import HOST, pdf
from tests.support.env import Env, one

pytestmark = pytest.mark.postgres
Conn = psycopg.Connection[Any]
T = datetime(2026, 1, 1, tzinfo=UTC)
URL = f"https://{HOST}/files/a.pdf"
HASH = "a" * 64

_COLUMNS = (
    "fetch_request_id, ingest_run_id, candidate_key, attempt, purpose, requested_url, final_url, redirect_chain, status, outcome,"
    " selected_headers, retrieved_at, elapsed_seconds, policy_version, content_hash, trust_class"
)


def row(run: uuid.UUID, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        fetch_request_id=uuid.uuid4(),
        ingest_run_id=run,
        candidate_key="k",
        attempt=1,
        purpose="ATTACHMENT",
        requested_url=URL,
        final_url=URL,
        redirect_chain=Jsonb([URL]),
        status=200,
        outcome="OK",
        selected_headers=Jsonb({"content-type": "application/pdf"}),
        retrieved_at=T,
        elapsed_seconds=0.5,
        policy_version="p",
        content_hash=HASH,
        trust_class="RUNTIME",
    )
    base.update(over)
    return base


def insert(c: Conn, run: uuid.UUID, **over: Any) -> uuid.UUID:
    r = row(run, **over)
    c.execute(f"INSERT INTO fetch_request ({_COLUMNS}) VALUES ({', '.join(['%s'] * 16)})", tuple(r[k.strip()] for k in _COLUMNS.split(",")))
    return r["fetch_request_id"]  # type: ignore[no-any-return]


def test_a_valid_row_is_accepted(pg_conn: Conn) -> None:
    run = new_run(pg_conn)
    insert(pg_conn, run)
    insert(pg_conn, run, attempt=2, outcome="HTTP_TRANSIENT", status=503, content_hash=None, selected_headers=Jsonb({"retry-after": "2"}))
    insert(
        pg_conn, run, candidate_key="k2", outcome="URL_REJECTED", status=None, final_url=None, redirect_chain=Jsonb([]), content_hash=None
    )
    assert pg_conn.execute("SELECT count(*) FROM fetch_request").fetchone() == (3,)


@pytest.mark.parametrize(
    "sql", ["UPDATE fetch_request SET status = 500", "UPDATE fetch_request SET content_hash = NULL", "DELETE FROM fetch_request"]
)
def test_rows_are_append_only_even_for_the_owner(pg_conn: Conn, sql: str) -> None:
    insert(pg_conn, new_run(pg_conn))
    with pytest.raises(pgerr.IntegrityConstraintViolation, match="append-only"):
        pg_conn.execute(sql)


def test_the_app_role_can_read_and_append_but_nothing_else(pg_conn: Conn) -> None:
    run = new_run(pg_conn)
    insert(pg_conn, run)
    pg_conn.execute("SET ROLE rig_app")
    try:
        assert pg_conn.execute("SELECT count(*) FROM fetch_request").fetchone() == (1,)
        insert(pg_conn, run, attempt=2, outcome="TIMEOUT", status=None, content_hash=None)
        for stmt in (
            "UPDATE fetch_request SET status = 500",
            "DELETE FROM fetch_request",
            "TRUNCATE fetch_request",
            "DROP TABLE fetch_request",
        ):
            with pytest.raises((pgerr.InsufficientPrivilege, pgerr.UndefinedTable)):
                pg_conn.execute(stmt)
    finally:
        pg_conn.execute("RESET ROLE")


@pytest.mark.parametrize(
    "header",
    ["set-cookie", "cookie", "authorization", "proxy-authorization", "www-authenticate", "server", "x-forwarded-for", "user-agent", "from"],
)
def test_the_database_rejects_any_header_outside_the_allowlist(pg_conn: Conn, header: str) -> None:
    run = new_run(pg_conn)
    with pytest.raises(pgerr.CheckViolation):
        insert(pg_conn, run, selected_headers=Jsonb({"content-type": "application/pdf", header: "x"}))


def test_the_database_accepts_exactly_the_code_allowlist(pg_conn: Conn) -> None:
    run = new_run(pg_conn)
    for i, name in enumerate(sorted(LOGGED_HEADERS), start=1):
        insert(pg_conn, run, attempt=i, selected_headers=Jsonb({name: "v"}))
    assert pg_conn.execute("SELECT count(*) FROM fetch_request").fetchone() == (len(LOGGED_HEADERS),)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt", 0),
        ("purpose", "CRAWL"),
        ("outcome", "SUCCESS"),
        ("status", 99),
        ("status", 600),
        ("trust_class", "CANONICAL"),
        ("content_hash", "A" * 64),
        ("content_hash", "a" * 63),
        ("elapsed_seconds", -1.0),
        ("elapsed_seconds", float("nan")),
        ("elapsed_seconds", float("inf")),
        ("redirect_chain", Jsonb({"a": 1})),
        ("selected_headers", Jsonb(["content-type"])),
        ("candidate_key", ""),
        ("requested_url", ""),
        ("policy_version", ""),
    ],
)
def test_check_constraints(pg_conn: Conn, field: str, value: Any) -> None:
    run = new_run(pg_conn)
    with pytest.raises((pgerr.CheckViolation, pgerr.StringDataRightTruncation)):
        insert(pg_conn, run, **{field: value})


@pytest.mark.parametrize(
    "over",
    [
        {"outcome": "OK", "content_hash": None},  # OK means a hashed body
        {"outcome": "OK", "status": 500},
        {"outcome": "NOT_MODIFIED", "status": 200, "content_hash": None},
        {"outcome": "URL_REJECTED", "status": 200, "content_hash": None},
    ],
)
def test_outcome_and_status_must_agree(pg_conn: Conn, over: dict[str, Any]) -> None:
    run = new_run(pg_conn)
    with pytest.raises(pgerr.CheckViolation):
        insert(pg_conn, run, **over)


def test_a_row_needs_a_run_and_each_attempt_is_unique(pg_conn: Conn) -> None:
    with pytest.raises(pgerr.ForeignKeyViolation):
        insert(pg_conn, uuid.uuid4())
    run = new_run(pg_conn)
    insert(pg_conn, run)
    with pytest.raises(pgerr.UniqueViolation):
        insert(pg_conn, run)  # same (run, candidate, purpose, attempt)
    insert(pg_conn, run, attempt=2)
    insert(pg_conn, run, purpose="DETAIL_PAGE")
    insert(pg_conn, run, candidate_key="other")


def test_the_table_has_no_content_or_credential_columns(pg_conn: Conn) -> None:
    cols = {
        r[0] for r in pg_conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'fetch_request'").fetchall()
    }
    assert cols == {
        "fetch_request_id", "ingest_run_id", "candidate_key", "attempt", "purpose", "requested_url", "final_url", "redirect_chain",
        "status", "outcome", "selected_headers", "retrieved_at", "elapsed_seconds", "policy_version", "content_hash", "trust_class",
    }  # fmt: skip


def test_the_pipeline_audit_is_written_as_the_app_role_and_survives_a_later_failure(pg_dsn: str, tmp_path: Path) -> None:
    """Audit rows are committed per attempt, independent of the candidate's own transaction."""
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        conn.execute("SET ROLE rig_app")
        env = Env(PgRepository(conn), tmp_path)
        env.serve("TEST-001", b"%PDF-1.7\n" + b"x" * 20000)  # oversized -> quarantined; the request row must still exist
        env.run([one()])
        assert conn.execute("SELECT outcome, status FROM fetch_request").fetchall() == [("SIZE_EXCEEDED", 200)]
        assert conn.execute("SELECT count(*) FROM quarantine_record").fetchone() == (1,)


# ---- quarantine_record.requested_url: provenance of a request vs an echoed, rejected input ----------------------------------------
# Policy (docs/phase1/ingestion-contract.md §3.3): a URL that PASSED validation is request provenance and is stored exactly
# (validated + normalised: no userinfo, no fragment, query only for a reviewed listing). A URL that FAILED validation is an echo of
# an untrusted string — nothing was requested — so only its log-safe form is stored; the exact input stays in the hash-pinned manifest.


def test_a_validated_url_is_stored_exactly_in_quarantine(pg_dsn: str, tmp_path: Path) -> None:
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        env = Env(PgRepository(conn), tmp_path)
        env.serve("TEST-001", pdf("q"), headers={"Content-Type": "text/html"})  # wrong media type -> quarantined after a real fetch
        env.run([one()])
        assert conn.execute("SELECT requested_url FROM quarantine_record").fetchall() == [(f"https://{HOST}/files/test-001.pdf",)]


def test_a_rejected_input_is_stored_in_log_safe_form_only(pg_dsn: str, tmp_path: Path) -> None:
    hostile = f"https://admin:hunter2@{HOST}/files/test-001.pdf?token=abc123#frag"  # rig:allow-secret: synthetic userinfo URL (the thing under test)
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        env = Env(PgRepository(conn), tmp_path)
        env.run([one(url=hostile)])
        ((stored,),) = conn.execute("SELECT requested_url FROM quarantine_record").fetchall()
        assert stored == f"https://{HOST}/files/test-001.pdf"
        for secret in ("hunter2", "admin", "abc123", "frag"):
            assert secret not in stored
        assert env.transport.requests == []
