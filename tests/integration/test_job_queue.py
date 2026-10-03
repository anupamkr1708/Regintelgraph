from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from packages.domain.ingest import JobState
from packages.ingestion.jobs import PgJobQueue, job_idempotency_key

pytestmark = pytest.mark.postgres
T = datetime(2026, 1, 1, tzinfo=UTC)


def queue(conn: psycopg.Connection[Any]) -> PgJobQueue:
    return PgJobQueue(conn, uuid.uuid4)


def events(conn: psycopg.Connection[Any], job_id: uuid.UUID) -> list[str]:
    return [r[0] for r in conn.execute("SELECT event_type FROM job_event WHERE job_id=%s ORDER BY event_id", (job_id,)).fetchall()]


def enqueue(
    q: PgJobQueue, key: str = "k1", *, at: datetime = T, max_attempts: int = 3, run_after: datetime | None = None
) -> tuple[uuid.UUID, bool]:
    return q.enqueue(
        "ingest_candidate",
        {"candidate_key": key},
        job_idempotency_key("ingest_candidate", {"candidate_key": key}),
        run_after=run_after or at,
        max_attempts=max_attempts,
        now=at,
    )


def test_idempotency_key_is_stable_and_order_independent() -> None:
    a = job_idempotency_key("k", {"a": 1, "b": [1, 2]})
    assert a == job_idempotency_key("k", {"b": [1, 2], "a": 1}) and len(a) == 64
    assert a != job_idempotency_key("k", {"a": 2, "b": [1, 2]}) and a != job_idempotency_key("other", {"a": 1, "b": [1, 2]})


def test_enqueue_is_idempotent_by_key(pg_conn: psycopg.Connection[Any]) -> None:
    q = queue(pg_conn)
    first, created = enqueue(q)
    again, created_again = enqueue(q)
    assert created and not created_again and first == again
    assert pg_conn.execute("SELECT count(*) FROM job").fetchone() == (1,) and events(pg_conn, first) == ["ENQUEUED"]


def test_claim_complete_lifecycle_and_events(pg_conn: psycopg.Connection[Any]) -> None:
    q = queue(pg_conn)
    jid, _ = enqueue(q)
    job = q.claim("w1", T)
    assert (
        job is not None
        and job.job_id == jid
        and job.state is JobState.RUNNING
        and job.attempts == 1
        and job.payload == {"candidate_key": "k1"}
    )
    assert q.claim("w2", T) is None  # nothing else is runnable; a running job is never handed out twice
    assert q.complete(jid, "w1", T + timedelta(seconds=1))
    assert pg_conn.execute("SELECT state, locked_by FROM job").fetchone() == ("SUCCEEDED", None)
    assert events(pg_conn, jid) == ["ENQUEUED", "CLAIMED", "SUCCEEDED"]
    assert not q.complete(jid, "w1", T)  # completing twice is refused


def test_only_the_claiming_worker_may_complete_or_fail(pg_conn: psycopg.Connection[Any]) -> None:
    q = queue(pg_conn)
    jid, _ = enqueue(q)
    q.claim("w1", T)
    assert not q.complete(jid, "intruder", T) and q.fail(jid, "intruder", "x", T, retry_at=None) is None
    assert pg_conn.execute("SELECT state, locked_by FROM job").fetchone() == ("RUNNING", "w1")


def test_failure_requeues_until_attempts_are_exhausted_then_fails_terminally(pg_conn: psycopg.Connection[Any]) -> None:
    q = queue(pg_conn)
    jid, _ = enqueue(q, max_attempts=2)
    q.claim("w", T)
    retry_at = T + timedelta(minutes=5)
    assert q.fail(jid, "w", "boom 1", T, retry_at=retry_at) is JobState.QUEUED
    assert (
        q.claim("w", T + timedelta(minutes=1)) is None
    )  # not runnable before retry_at (no backoff numbers are invented: the caller decides)
    job = q.claim("w", retry_at)
    assert job is not None and job.attempts == 2
    assert q.fail(jid, "w", "boom 2", retry_at, retry_at=retry_at + timedelta(hours=1)) is JobState.FAILED  # attempts exhausted
    assert q.claim("w", retry_at + timedelta(days=1)) is None
    assert pg_conn.execute("SELECT state, last_error FROM job").fetchone() == ("FAILED", "boom 2")
    assert events(pg_conn, jid) == ["ENQUEUED", "CLAIMED", "RETRY_SCHEDULED", "CLAIMED", "FAILED"]


def test_fail_without_retry_time_is_terminal(pg_conn: psycopg.Connection[Any]) -> None:
    q = queue(pg_conn)
    jid, _ = enqueue(q)
    q.claim("w", T)
    assert q.fail(jid, "w", "poison", T, retry_at=None) is JobState.FAILED


def test_claim_order_is_by_run_after_then_creation(pg_conn: psycopg.Connection[Any]) -> None:
    q = queue(pg_conn)
    later, _ = enqueue(q, "later", at=T, run_after=T + timedelta(minutes=2))
    first, _ = enqueue(q, "first", at=T + timedelta(seconds=1), run_after=T)
    second, _ = enqueue(q, "second", at=T + timedelta(seconds=2), run_after=T)
    now = T + timedelta(hours=1)
    assert [q.claim("w", now).job_id for _ in range(3)] == [first, second, later]  # type: ignore[union-attr]
    assert q.claim("w", now) is None


def test_future_jobs_are_not_claimed(pg_conn: psycopg.Connection[Any]) -> None:
    q = queue(pg_conn)
    enqueue(q, run_after=T + timedelta(days=1))
    assert q.claim("w", T) is None


def test_concurrent_workers_never_claim_the_same_job(pg_dsn: str) -> None:
    n_jobs, n_workers = 20, 6
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        q = queue(c)
        for i in range(n_jobs):
            enqueue(q, f"c{i}")
    claimed: list[uuid.UUID] = []
    lock, barrier, errors = threading.Lock(), threading.Barrier(n_workers), []

    def worker(w: int) -> None:
        try:
            with psycopg.connect(pg_dsn, autocommit=True) as conn:
                wq = queue(conn)
                barrier.wait()
                while (job := wq.claim(f"w{w}", T)) is not None:
                    with lock:
                        claimed.append(job.job_id)
                    wq.complete(job.job_id, f"w{w}", T)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(n_workers)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and len(claimed) == n_jobs and len(set(claimed)) == n_jobs  # every job exactly once


def test_state_constraint_ties_locked_by_to_running(pg_conn: psycopg.Connection[Any]) -> None:
    q = queue(pg_conn)
    jid, _ = enqueue(q)
    with pytest.raises(psycopg.errors.CheckViolation):
        pg_conn.execute("UPDATE job SET state='RUNNING' WHERE job_id=%s", (jid,))  # RUNNING without a locker is impossible


def test_queue_requires_an_autocommit_connection(pg_dsn: str) -> None:
    with psycopg.connect(pg_dsn) as conn, pytest.raises(ValueError, match="autocommit"):
        PgJobQueue(conn, uuid.uuid4)
