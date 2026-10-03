"""PostgreSQL implementation of the Repository port (psycopg 3, plain SQL, no ORM).

Idempotency is enforced by the database, not by read-then-write races: `INSERT ... ON CONFLICT` on the natural keys
(content_hash, identity_key, (document_id, content_hash), (version, url)). Every method that writes more than one row runs
in ONE transaction, so a fault between statements leaves zero rows. Time values are always supplied by the caller (the
injected clock); the database never defaults a timestamp.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from packages.domain.content_hash import ContentHash
from packages.domain.ingest import AbortReason, IngestAlert, IngestOutcome, IngestResult, IngestRun, RunStatus
from packages.domain.manifest import SourceManifest
from packages.ingestion.egress import Validators
from packages.ingestion.errors import RunAlreadyActive
from packages.ingestion.repository import ArtifactIngest, QuarantineIngest

_COUNT_TABLES = (
    "raw_artifact",
    "regulatory_document",
    "document_version",
    "document_version_location",
    "quarantine_record",
    "ingest_result",
    "ingest_run",
)


def _one(conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...]) -> tuple[Any, ...]:
    """Fetch exactly one row; a missing row here means a violated invariant (e.g. a conflict row that vanished)."""
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError("expected exactly one row")
    return tuple(row)


class PgRepository:
    persistent = True

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        # Autocommit is REQUIRED: each repository method opens its own explicit `transaction()`. On a non-autocommit
        # connection a plain SELECT would leave an implicit transaction open and silently turn those blocks into savepoints.
        if not conn.autocommit:
            raise ValueError("PgRepository requires an autocommit connection (it manages its own transactions)")
        self._conn = conn

    # ---- runs ----------------------------------------------------------------------------------------------------

    def ensure_source(self, manifest: SourceManifest, manifest_ref: str, now: datetime) -> None:
        with self._conn.transaction():
            self._conn.execute(
                "INSERT INTO source (source_id, authority_id, manifest_ref, enabled, created_at) VALUES (%s, %s, %s, true, %s) ON CONFLICT (source_id) DO NOTHING",
                (manifest.source_id, manifest.authority, manifest_ref, now),
            )

    def begin_run(self, run: IngestRun) -> None:
        try:
            with self._conn.transaction():
                self._conn.execute(
                    "INSERT INTO ingest_run (ingest_run_id, source_id, mode, status, abort_reason, started_at, finished_at, manifest_hash, manifest_version,"
                    " code_version, safety_policy_version, authorisation_ref, stats) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        run.ingest_run_id,
                        run.source_id,
                        run.mode.value,
                        run.status.value,
                        run.abort_reason.value if run.abort_reason else None,
                        run.started_at,
                        run.finished_at,
                        run.manifest_hash,
                        run.manifest_version,
                        run.code_version,
                        run.safety_policy_version,
                        run.authorisation_ref,
                        Jsonb(run.stats),
                    ),
                )
        except psycopg.errors.UniqueViolation as exc:
            if "ingest_run_one_running_per_source" in str(exc):
                raise RunAlreadyActive(f"a run for source {run.source_id} is already RUNNING") from exc
            raise

    def finish_run(
        self, run_id: uuid.UUID, status: RunStatus, abort_reason: AbortReason | None, finished_at: datetime, stats: Mapping[str, Any]
    ) -> None:
        with self._conn.transaction():
            self._conn.execute(
                "UPDATE ingest_run SET status=%s, abort_reason=%s, finished_at=%s, stats=%s WHERE ingest_run_id=%s AND status='RUNNING'",
                (status.value, abort_reason.value if abort_reason else None, finished_at, Jsonb(dict(stats)), run_id),
            )

    # ---- lookups -------------------------------------------------------------------------------------------------

    def find_validators(self, candidate_key: str, url: str) -> Validators | None:
        row = self._conn.execute(
            "SELECT l.http_meta->>'etag', l.http_meta->>'last_modified' FROM document_version_location l"
            " JOIN document_version v USING (document_version_id) JOIN regulatory_document d USING (document_id)"
            " WHERE d.identity_key=%s AND l.canonical_url=%s ORDER BY v.retrieved_at DESC, v.document_version_id DESC LIMIT 1",
            (candidate_key, url),
        ).fetchone()
        if row is None or not (row[0] or row[1]):
            return None
        return Validators(row[0], row[1])

    # ---- candidate writes (each one transaction) -------------------------------------------------------------------

    def ingest_artifact(self, req: ArtifactIngest) -> IngestResult:
        c = self._conn
        h = req.content_hash.value
        with c.transaction():
            row = c.execute(
                "INSERT INTO raw_artifact (raw_artifact_id, content_hash, size_bytes, media_type_sniffed, blob_path_rel, ingest_run_id, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (content_hash) DO NOTHING RETURNING raw_artifact_id",
                (req.new_id(), h, req.size_bytes, req.media_type_sniffed, req.content_hash.relative_path, req.ingest_run_id, req.now),
            ).fetchone()
            artifact_id = row[0] if row else _one(c, "SELECT raw_artifact_id FROM raw_artifact WHERE content_hash=%s", (h,))[0]

            c.execute(
                "INSERT INTO regulatory_document (document_id, source_id, authority_id, document_type, source_reference, title, identity_key, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (identity_key) DO NOTHING",
                (
                    req.new_id(),
                    req.source_id,
                    req.authority_id,
                    req.candidate.document_type,
                    req.candidate.source_reference,
                    req.candidate.title,
                    req.candidate_key,
                    req.now,
                ),
            )
            document_id, source_reference = _one(
                c, "SELECT document_id, source_reference FROM regulatory_document WHERE identity_key=%s", (req.candidate_key,)
            )
            prev = c.execute(
                "SELECT content_hash FROM document_version WHERE document_id=%s ORDER BY retrieved_at DESC, document_version_id DESC LIMIT 1",
                (document_id,),
            ).fetchone()
            previous_hash = str(prev[0]).strip() if prev else None

            row = c.execute(
                "INSERT INTO document_version (document_version_id, document_id, raw_artifact_id, content_hash, retrieved_at, ingest_run_id,"
                " manifest_entry_index, manifest_status_labels, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'STORED') ON CONFLICT (document_id, content_hash) DO NOTHING RETURNING document_version_id",
                (
                    req.new_id(),
                    document_id,
                    artifact_id,
                    h,
                    req.retrieved_at,
                    req.ingest_run_id,
                    req.candidate.entry_index,
                    Jsonb({k: v.value for k, v in req.candidate.status_labels}),
                ),
            ).fetchone()
            version_created = row is not None
            version_id = (
                row[0]
                if row
                else _one(c, "SELECT document_version_id FROM document_version WHERE document_id=%s AND content_hash=%s", (document_id, h))[
                    0
                ]
            )

            alerts: list[IngestAlert] = []
            if version_created:
                if c.execute(
                    "SELECT 1 FROM document_version WHERE content_hash=%s AND document_id<>%s LIMIT 1", (h, document_id)
                ).fetchone():
                    alerts.append(IngestAlert.DUPLICATE_BYTES_OTHER_DOCUMENT)
                if previous_hash is not None and source_reference is not None:
                    alerts.append(IngestAlert.CONTENT_CHANGED_UNDER_STABLE_REF)

            loc = c.execute(
                "INSERT INTO document_version_location (document_version_id, canonical_url, first_seen, last_seen, http_meta) VALUES (%s,%s,%s,%s,%s)"
                " ON CONFLICT (document_version_id, canonical_url) DO UPDATE SET last_seen = EXCLUDED.last_seen"
                " WHERE document_version_location.last_seen < EXCLUDED.last_seen RETURNING (xmax = 0)",
                (version_id, req.location_url, req.retrieved_at, req.retrieved_at, Jsonb(dict(req.http_meta))),
            ).fetchone()
            location_inserted = bool(loc and loc[0])

            if version_created:
                outcome = IngestOutcome.NEW_VERSION
            elif location_inserted:
                outcome = IngestOutcome.NEW_LOCATION_SAME_VERSION
            else:
                outcome = IngestOutcome.UNCHANGED_SKIPPED
            prev_for_result = previous_hash if outcome is IngestOutcome.NEW_VERSION else None
            result = IngestResult(
                req.ingest_run_id,
                req.candidate_key,
                outcome,
                req.attempts,
                None,
                version_id,
                req.content_hash,
                ContentHash(prev_for_result) if prev_for_result else None,
                None,
                tuple(alerts),
            )
            self._insert_result(result, req.now)
        return result

    def record_unchanged(
        self, run_id: uuid.UUID, candidate_key: str, url: str, retrieved_at: datetime, attempts: int, now: datetime
    ) -> IngestResult | None:
        with self._conn.transaction():
            row = self._conn.execute(
                "SELECT v.document_version_id, v.content_hash FROM document_version_location l JOIN document_version v USING (document_version_id)"
                " JOIN regulatory_document d USING (document_id) WHERE d.identity_key=%s AND l.canonical_url=%s"
                " ORDER BY v.retrieved_at DESC, v.document_version_id DESC LIMIT 1",
                (candidate_key, url),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE document_version_location SET last_seen=%s WHERE document_version_id=%s AND canonical_url=%s AND last_seen < %s",
                (retrieved_at, row[0], url, retrieved_at),
            )
            result = IngestResult(
                run_id, candidate_key, IngestOutcome.UNCHANGED_SKIPPED, attempts, "NOT_MODIFIED", row[0], ContentHash(str(row[1]).strip())
            )
            self._insert_result(result, now)
        return result

    def quarantine(self, req: QuarantineIngest) -> IngestResult:
        h = req.content_hash.value if req.content_hash else None
        with self._conn.transaction():
            row = self._conn.execute(
                "INSERT INTO quarantine_record (quarantine_id, ingest_run_id, candidate_key, reason_code, content_hash, bytes_stored, requested_url, final_url,"
                " redirect_chain, http_status, headers, sniffed_media_type, reason_detail, safety_policy_version, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING quarantine_id",
                (
                    req.new_id(),
                    req.ingest_run_id,
                    req.candidate_key,
                    req.reason.value,
                    h,
                    h is not None,
                    req.requested_url,
                    req.final_url,
                    Jsonb(list(req.redirect_chain)),
                    req.http_status,
                    Jsonb(dict(req.headers)),
                    req.sniffed_media_type,
                    req.reason_detail,
                    req.safety_policy_version,
                    req.now,
                ),
            ).fetchone()
            qid = (
                row[0]
                if row
                else _one(
                    self._conn,
                    "SELECT quarantine_id FROM quarantine_record WHERE ingest_run_id=%s AND candidate_key=%s AND reason_code=%s AND content_hash IS NOT DISTINCT FROM %s",
                    (req.ingest_run_id, req.candidate_key, req.reason.value, h),
                )[0]
            )
            result = IngestResult(
                req.ingest_run_id,
                req.candidate_key,
                IngestOutcome.QUARANTINED,
                req.attempts,
                req.reason.value,
                None,
                req.content_hash,
                None,
                qid,
            )
            self._insert_result(result, req.now)
        return result

    def record_result(self, result: IngestResult, now: datetime) -> None:
        with self._conn.transaction():
            self._insert_result(result, now)

    def _insert_result(self, r: IngestResult, now: datetime) -> None:
        self._conn.execute(
            "INSERT INTO ingest_result (ingest_run_id, candidate_key, outcome, attempts, reason_code, document_version_id, content_hash, previous_content_hash,"
            " quarantine_id, alerts, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                r.ingest_run_id,
                r.candidate_key,
                r.outcome.value,
                r.attempts,
                r.reason_code,
                r.document_version_id,
                r.content_hash.value if r.content_hash else None,
                r.previous_content_hash.value if r.previous_content_hash else None,
                r.quarantine_id,
                [a.value for a in r.alerts],
                now,
            ),
        )

    # ---- introspection (tests / reports) -----------------------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        return {t: int(_one(self._conn, f"SELECT count(*) FROM {t}", ())[0]) for t in _COUNT_TABLES}

    def last_seen(self, candidate_key: str, url: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT max(l.last_seen) FROM document_version_location l JOIN document_version v USING (document_version_id)"
            " JOIN regulatory_document d USING (document_id) WHERE d.identity_key=%s AND l.canonical_url=%s",
            (candidate_key, url),
        ).fetchone()
        return None if row is None else row[0]

    def version_hashes(self, candidate_key: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT v.content_hash FROM document_version v JOIN regulatory_document d USING (document_id) WHERE d.identity_key=%s"
            " ORDER BY v.retrieved_at, v.document_version_id",
            (candidate_key,),
        ).fetchall()
        return [str(r[0]).strip() for r in rows]
