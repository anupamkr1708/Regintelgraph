"""Domain invariants of `FetchRequest` and the migration's header allowlist (offline; no database needed)."""

from __future__ import annotations

import dataclasses
import re
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from packages.domain.content_hash import ContentHash
from packages.domain.ingest import FetchOutcome, FetchPurpose, FetchRequest
from packages.ingestion.logsafe import LOGGED_HEADERS

T = datetime(2026, 1, 1, tzinfo=UTC)
H = ContentHash("a" * 64)
MIGRATION = Path(__file__).resolve().parents[2] / "migrations/0002_fetch_request.sql"


def make(**over: object) -> FetchRequest:
    base: dict[str, object] = dict(
        fetch_request_id=uuid.uuid4(),
        ingest_run_id=uuid.uuid4(),
        candidate_key="testreg/TEST-001",
        attempt=1,
        purpose=FetchPurpose.ATTACHMENT,
        requested_url="https://docs.testreg.example/files/a.pdf",
        final_url="https://docs.testreg.example/files/a.pdf",
        redirect_chain=("https://docs.testreg.example/files/a.pdf",),
        status=200,
        outcome=FetchOutcome.OK,
        selected_headers={"content-type": "application/pdf"},
        retrieved_at=T,
        elapsed_seconds=0.25,
        policy_version="p",
        content_hash=H,
    )
    base.update(over)
    return FetchRequest(**base)  # type: ignore[arg-type]


def test_a_valid_record_is_accepted_and_frozen() -> None:
    r = make()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.attempt = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "change",
    [
        {"attempt": 0},
        {"status": 99},
        {"status": 600},
        {"elapsed_seconds": -0.1},
        {"elapsed_seconds": float("nan")},
        {"elapsed_seconds": float("inf")},
        {"retrieved_at": datetime(2026, 1, 1)},  # naive
        {"retrieved_at": datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=5, minutes=30)))},  # not UTC
        {"candidate_key": ""},
        {"requested_url": ""},
        {"policy_version": ""},
        {"selected_headers": {"Content-Type": "x"}},  # keys must be lower-case
        {"outcome": FetchOutcome.OK, "content_hash": None},  # OK means a hashed body
        {"outcome": FetchOutcome.OK, "status": 500},
        {"outcome": FetchOutcome.NOT_MODIFIED, "status": 200, "content_hash": None},
        {"outcome": FetchOutcome.URL_REJECTED, "status": 200, "content_hash": None},
    ],
)
def test_invalid_records_are_rejected(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        make(**change)


def test_valid_non_ok_records() -> None:
    make(outcome=FetchOutcome.NOT_MODIFIED, status=304, content_hash=None)
    make(outcome=FetchOutcome.URL_REJECTED, status=None, final_url=None, redirect_chain=(), content_hash=None)
    make(outcome=FetchOutcome.TIMEOUT, status=None, content_hash=None)
    make(outcome=FetchOutcome.CIRCUIT_OPEN, status=429, content_hash=None, selected_headers={"retry-after": "30"})


def test_the_database_header_allowlist_equals_the_code_allowlist() -> None:
    """Anti-drift: the CHECK in migration 0002 must list exactly the headers the egress layer is allowed to log."""
    sql = MIGRATION.read_text()
    match = re.search(r"selected_headers - ARRAY\[([^\]]+)\]", sql)
    assert match is not None
    in_sql = set(re.findall(r"'([a-z-]+)'", match.group(1)))
    assert in_sql == set(LOGGED_HEADERS)


def test_the_migration_stores_no_content_cookie_or_credential_columns() -> None:
    sql = MIGRATION.read_text().lower()
    table = sql[sql.index("create table fetch_request") : sql.index("create index")]
    for word in ("body", "cookie", "authorization", "password", "secret", "token", "contact", "email", "user_agent", "text"):
        assert not re.search(rf"^\s+\w*{word}\w*\s", table, re.M), word  # no column named like content/credential storage
