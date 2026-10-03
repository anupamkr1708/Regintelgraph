"""PostgreSQL-backed job queue skeleton (no Redis/Celery/Kafka). Workers stay thin adapters over this.

enqueue (idempotent by key) -> claim (`FOR UPDATE SKIP LOCKED`, safe for concurrent workers) -> complete | fail.
Payloads are DATA: candidate keys and parameters only, never retrieved document text, and never executed or interpolated
into SQL/paths/commands. Retry timing is supplied by the caller (`retry_at`); no backoff numbers are invented here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from packages.domain.ingest import JobState


def job_idempotency_key(kind: str, params: Mapping[str, Any]) -> str:
    """Stable key: sha256 over the kind and canonical (sorted, compact) JSON of the parameters."""
    canonical = json.dumps({"kind": kind, "params": params}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class Job:
    job_id: uuid.UUID
    kind: str
    payload: Mapping[str, Any]
    idempotency_key: str
    state: JobState
    attempts: int
    max_attempts: int


class PgJobQueue:
    def __init__(self, conn: psycopg.Connection[Any], new_id: Callable[[], uuid.UUID]) -> None:
        if not conn.autocommit:
            raise ValueError("PgJobQueue requires an autocommit connection (it manages its own transactions)")
        self._conn = conn
        self._new_id = new_id

    def _event(self, job_id: uuid.UUID, event: str, at: datetime, detail: str | None = None) -> None:
        self._conn.execute("INSERT INTO job_event (job_id, event_type, at, detail) VALUES (%s,%s,%s,%s)", (job_id, event, at, detail))

    def enqueue(
        self, kind: str, payload: Mapping[str, Any], idempotency_key: str, *, run_after: datetime, max_attempts: int, now: datetime
    ) -> tuple[uuid.UUID, bool]:
        """Return (job_id, created). A repeated idempotency key returns the existing job and creates nothing."""
        with self._conn.transaction():
            row = self._conn.execute(
                "INSERT INTO job (job_id, kind, payload, idempotency_key, state, attempts, max_attempts, run_after, created_at, updated_at)"
                " VALUES (%s,%s,%s,%s,'QUEUED',0,%s,%s,%s,%s) ON CONFLICT (idempotency_key) DO NOTHING RETURNING job_id",
                (self._new_id(), kind, Jsonb(dict(payload)), idempotency_key, max_attempts, run_after, now, now),
            ).fetchone()
            if row is not None:
                self._event(row[0], "ENQUEUED", now)
                return row[0], True
            existing = self._conn.execute("SELECT job_id FROM job WHERE idempotency_key=%s", (idempotency_key,)).fetchone()
            assert existing is not None
            return existing[0], False

    def claim(self, worker_id: str, now: datetime) -> Job | None:
        """Atomically take the oldest runnable job. Concurrent workers never receive the same job."""
        with self._conn.transaction():
            row = self._conn.execute(
                "UPDATE job SET state='RUNNING', locked_by=%s, locked_at=%s, attempts=attempts+1, updated_at=%s WHERE job_id = ("
                " SELECT job_id FROM job WHERE state='QUEUED' AND run_after <= %s ORDER BY run_after, created_at, job_id FOR UPDATE SKIP LOCKED LIMIT 1)"
                " RETURNING job_id, kind, payload, idempotency_key, state, attempts, max_attempts",
                (worker_id, now, now, now),
            ).fetchone()
            if row is None:
                return None
            self._event(row[0], "CLAIMED", now, worker_id)
            return Job(row[0], row[1], row[2], row[3], JobState(row[4]), row[5], row[6])

    def complete(self, job_id: uuid.UUID, worker_id: str, now: datetime) -> bool:
        with self._conn.transaction():
            cur = self._conn.execute(
                "UPDATE job SET state='SUCCEEDED', locked_by=NULL, locked_at=NULL, updated_at=%s WHERE job_id=%s AND state='RUNNING' AND locked_by=%s",
                (now, job_id, worker_id),
            )
            if cur.rowcount == 1:
                self._event(job_id, "SUCCEEDED", now)
            return cur.rowcount == 1

    def fail(self, job_id: uuid.UUID, worker_id: str, error: str, now: datetime, *, retry_at: datetime | None) -> JobState | None:
        """Record a failure. Re-queued at `retry_at` while attempts remain; otherwise FAILED (terminal)."""
        with self._conn.transaction():
            row = self._conn.execute(
                "SELECT attempts, max_attempts FROM job WHERE job_id=%s AND state='RUNNING' AND locked_by=%s FOR UPDATE",
                (job_id, worker_id),
            ).fetchone()
            if row is None:
                return None
            requeue = retry_at is not None and row[0] < row[1]
            state = JobState.QUEUED if requeue else JobState.FAILED
            self._conn.execute(
                "UPDATE job SET state=%s, locked_by=NULL, locked_at=NULL, last_error=%s, run_after=COALESCE(%s, run_after), updated_at=%s WHERE job_id=%s",
                (state.value, error[:500], retry_at if requeue else None, now, job_id),
            )
            self._event(job_id, "RETRY_SCHEDULED" if requeue else "FAILED", now, error[:500])
            return state
