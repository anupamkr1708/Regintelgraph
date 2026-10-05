"""Durable request audit (`fetch_request`, ingestion-contract §3.2) — pipeline behaviour on BOTH repositories.

Synthetic fixtures only (TESTREG); fake transport/resolver, so no network object exists in these tests.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import timedelta

import pytest

from packages.domain.ingest import FetchOutcome, FetchPurpose, IngestOutcome
from packages.ingestion.errors import RepositoryConflict
from packages.ingestion.pipeline import SAFETY_POLICY_VERSION
from tests.support.builders import CONTACT_VALUE, HOST, pdf, sha
from tests.support.env import Env, one
from tests.support.fakes import Resp

URL_A = f"https://{HOST}/files/test-001.pdf"
PATH_A = "/files/test-001.pdf"


def rows(env: Env):  # type: ignore[no-untyped-def]
    return env.repo.fetch_requests()


def test_a_successful_fetch_leaves_one_reconstructable_row(env: Env) -> None:
    body = pdf("audit-ok")
    env.serve("TEST-001", body, headers={"ETag": '"v1"', "Content-Length": str(len(body))})
    report = env.run([one()])
    (row,) = rows(env)
    assert row.ingest_run_id == report.run.ingest_run_id and row.candidate_key == "testreg/TEST-001"
    assert (row.attempt, row.purpose, row.outcome, row.status) == (1, FetchPurpose.ATTACHMENT, FetchOutcome.OK, 200)
    assert row.requested_url == URL_A and row.final_url == URL_A and row.redirect_chain == (URL_A,)
    assert row.content_hash is not None and row.content_hash.value == sha(body) == report.results[0].content_hash.value  # type: ignore[union-attr]
    assert row.policy_version == SAFETY_POLICY_VERSION and row.elapsed_seconds >= 0
    assert row.selected_headers["etag"] == '"v1"' and row.selected_headers["content-type"] == "application/pdf"
    assert row.retrieved_at.utcoffset() == timedelta(0)


def test_a_redirect_is_recorded_hop_by_hop_in_one_row(env: Env) -> None:
    target = f"https://{HOST}/files/other.pdf"
    env.transport.route(HOST, PATH_A, Resp(302, {"Location": target}))
    env.transport.route(HOST, "/files/other.pdf", Resp(200, {"Content-Type": "application/pdf"}, pdf("redir")))
    env.run([one()])
    (row,) = rows(env)
    assert row.requested_url == URL_A and row.final_url == target and row.redirect_chain == (URL_A, target)
    assert row.outcome is FetchOutcome.OK and row.status == 200


def test_each_retry_is_its_own_attempt_row(env: Env) -> None:
    ok = Resp(200, {"Content-Type": "application/pdf"}, pdf("retry"))
    env.transport.route(HOST, PATH_A, Resp(503, {"Retry-After": "2"}), Resp(502), ok)
    report = env.run([one()])
    assert report.results[0].outcome is IngestOutcome.NEW_VERSION and report.results[0].attempts == 3
    got = rows(env)
    assert [(r.attempt, r.outcome, r.status) for r in got] == [
        (1, FetchOutcome.HTTP_TRANSIENT, 503),
        (2, FetchOutcome.HTTP_TRANSIENT, 502),
        (3, FetchOutcome.OK, 200),
    ]
    assert got[0].selected_headers == {"retry-after": "2"} and got[0].content_hash is None
    assert len({r.fetch_request_id for r in got}) == 3


def test_a_refusal_is_recorded_and_not_retried(env: Env) -> None:
    env.transport.route(HOST, PATH_A, Resp(403), Resp(200, {"Content-Type": "application/pdf"}, pdf("never")))
    report = env.run([one()])
    assert report.results[0].outcome is IngestOutcome.FAILED_PERMANENT
    (row,) = rows(env)
    assert (row.attempt, row.outcome, row.status, row.content_hash) == (1, FetchOutcome.HTTP_PERMANENT, 403, None)
    assert len(env.transport.requests) == 1  # no retry, no identity change, no other endpoint


def test_the_circuit_breaker_trip_is_audited_and_stops_the_run(env: Env) -> None:
    cands = [one(f"TEST-00{i}") for i in (1, 2, 3, 4)]
    for i in (1, 2, 3, 4):
        env.transport.route(HOST, f"/files/test-00{i}.pdf", Resp(403))
    env.run(cands)
    got = rows(env)
    assert [r.outcome for r in got] == [FetchOutcome.HTTP_PERMANENT, FetchOutcome.HTTP_PERMANENT, FetchOutcome.CIRCUIT_OPEN]
    assert all(r.status == 403 for r in got) and len(env.transport.requests) == 3  # the 4th candidate was never requested


def test_an_oversized_response_is_audited_with_no_hash(env: Env) -> None:
    env.serve("TEST-001", b"%PDF-1.7\n" + b"x" * 20000)  # SMALL_LIMITS.max_bytes_attachment is 8192
    env.run([one()])
    (row,) = rows(env)
    assert (row.outcome, row.status, row.content_hash) == (FetchOutcome.SIZE_EXCEEDED, 200, None)


def test_a_conditional_304_is_audited_as_not_modified(env: Env) -> None:
    env.serve("TEST-001", pdf("etag"), headers={"ETag": '"abc"'})
    env.run([one()])
    env.transport.route(HOST, PATH_A, Resp(304, {"ETag": '"abc"'}))
    env.run([one()])
    first, second = rows(env)
    assert first.outcome is FetchOutcome.OK and second.outcome is FetchOutcome.NOT_MODIFIED and second.status == 304
    assert second.content_hash is None  # no body was received, so there is nothing to hash


def test_a_url_rejected_before_any_request_is_audited_in_log_safe_form(env: Env) -> None:
    hostile = f"https://user:hunter2@{HOST}/files/test-001.pdf?token=abc123#frag"  # rig:allow-secret: synthetic userinfo URL (the thing under test)
    env.run([one(url=hostile)])
    (row,) = rows(env)
    assert (row.outcome, row.status, row.final_url, row.redirect_chain) == (FetchOutcome.URL_REJECTED, None, None, ())
    assert env.transport.requests == []  # rejected locally: nothing was sent
    for secret in ("hunter2", "abc123", "frag", "user:"):
        assert secret not in row.requested_url, secret


def test_a_second_run_adds_audit_rows_but_no_logical_source_rows(env: Env) -> None:
    """Idempotency is about the source layer, not 'zero rows': runtime/audit records are allowed to grow (spec §22)."""
    env.serve("TEST-001", pdf("idem"))
    env.run([one()])
    source_layer = {k: v for k, v in env.repo.counts().items() if k not in {"ingest_run", "ingest_result", "quarantine_record"}}
    env.serve("TEST-001", pdf("idem"))
    env.run([one()])
    assert {k: v for k, v in env.repo.counts().items() if k not in {"ingest_run", "ingest_result", "quarantine_record"}} == source_layer
    assert len(rows(env)) == 2 and len({r.ingest_run_id for r in rows(env)}) == 2


def test_rows_never_contain_content_cookies_or_the_contact_identity(env: Env) -> None:
    body = pdf("sensitive-body-marker")
    env.serve(
        "TEST-001",
        body,
        headers={"Set-Cookie": "session=SECRET-COOKIE", "Authorization": "Bearer SECRET-TOKEN", "Server": "banner/1.0", "ETag": '"e"'},
    )
    env.run([one()])
    (row,) = rows(env)
    assert set(row.selected_headers) <= {
        "content-type",
        "content-length",
        "content-encoding",
        "etag",
        "last-modified",
        "retry-after",
        "location",
    }
    dump = repr(dataclasses.asdict(row))
    for forbidden in (CONTACT_VALUE, "SECRET-COOKIE", "SECRET-TOKEN", "banner/1.0", "sensitive-body-marker", "ops-secret"):
        assert forbidden not in dump, forbidden


# ---- repository contract: both implementations reject the same invalid rows and round-trip the valid ones ------------------------


def test_a_stored_row_round_trips_exactly(env: Env) -> None:
    env.serve("TEST-001", pdf("rt"), headers={"ETag": '"rt"'})
    env.run([one()])
    (row,) = rows(env)
    again = env.repo.fetch_requests(row.ingest_run_id)
    assert again == [row] and env.repo.fetch_requests(uuid.uuid4()) == []


@pytest.mark.parametrize(
    "change",
    [
        {},  # exact duplicate of the attempt key
        {"fetch_request_id": uuid.uuid4(), "ingest_run_id": uuid.uuid4()},  # unknown run
        {"fetch_request_id": uuid.uuid4(), "attempt": 2, "selected_headers": {"set-cookie": "x"}},  # header outside the allowlist
        {"fetch_request_id": uuid.uuid4(), "attempt": 2, "selected_headers": {"authorization": "Bearer x"}},
    ],
    ids=["duplicate-attempt", "unknown-run", "cookie-header", "authorization-header"],
)
def test_invalid_rows_are_rejected_identically_by_both_repositories(env: Env, change: dict[str, object]) -> None:
    env.serve("TEST-001", pdf("conflict"))
    env.run([one()])
    (row,) = rows(env)
    with pytest.raises(RepositoryConflict):
        env.repo.record_fetch_request(dataclasses.replace(row, **change))  # type: ignore[arg-type]
    assert len(rows(env)) == 1  # nothing was partially written


def test_counts_semantics_are_unchanged_by_the_audit_table(env: Env) -> None:
    """`counts()` is the source-layer/bookkeeping view; the audit table is read through `fetch_requests()` only."""
    env.serve("TEST-001", pdf("counts"))
    env.run([one()])
    assert "fetch_request" not in env.repo.counts() and len(rows(env)) == 1
