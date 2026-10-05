"""Repository port + in-memory implementation (offline tests and DRY_RUN). The PostgreSQL implementation is in `pgrepo.py`.

Both implementations obey the same contract (ingestion-contract §6, §8), proven by one shared test suite:
  same content_hash ................... one raw_artifact (reused)
  same document + same content_hash .. one document_version
  stable ref + changed bytes .......... new document_version, earlier version preserved, CONTENT_CHANGED_UNDER_STABLE_REF
  same bytes under another document ... DUPLICATE_BYTES_OTHER_DOCUMENT candidate-for-review, never auto-merged
  re-run, unchanged ................... no new artifact/document/version/location rows; only `last_seen` moves
Writes for one candidate (artifact + document + version + location + ingest_result) are atomic.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Protocol

from packages.domain.content_hash import ContentHash
from packages.domain.ingest import (
    AbortReason,
    FetchRequest,
    IngestAlert,
    IngestOutcome,
    IngestResult,
    IngestRun,
    QuarantineReason,
    RunStatus,
)
from packages.domain.manifest import ManifestCandidate, SourceManifest
from packages.ingestion.egress import Validators
from packages.ingestion.errors import RepositoryConflict, RunAlreadyActive
from packages.ingestion.logsafe import LOGGED_HEADERS

IdFactory = Callable[[], uuid.UUID]


@dataclass(frozen=True, slots=True)
class ArtifactIngest:
    """Everything needed to persist one successfully fetched, validated and stored candidate."""

    ingest_run_id: uuid.UUID
    source_id: str
    authority_id: str
    candidate: ManifestCandidate
    candidate_key: str
    content_hash: ContentHash
    size_bytes: int
    media_type_sniffed: str
    blob_path_rel: str
    location_url: str
    http_meta: Mapping[str, Any]
    retrieved_at: datetime
    attempts: int
    now: datetime
    new_id: IdFactory


@dataclass(frozen=True, slots=True)
class QuarantineIngest:
    ingest_run_id: uuid.UUID
    candidate_key: str
    reason: QuarantineReason
    content_hash: ContentHash | None
    requested_url: str
    final_url: str | None
    redirect_chain: tuple[str, ...]
    http_status: int | None
    headers: Mapping[str, str]
    sniffed_media_type: str | None
    reason_detail: str
    safety_policy_version: str
    attempts: int
    now: datetime
    new_id: IdFactory


class Repository(Protocol):
    persistent: bool

    def ensure_source(self, manifest: SourceManifest, manifest_ref: str, now: datetime) -> None: ...
    def begin_run(self, run: IngestRun) -> None: ...
    def finish_run(
        self, run_id: uuid.UUID, status: RunStatus, abort_reason: AbortReason | None, finished_at: datetime, stats: Mapping[str, Any]
    ) -> None: ...
    def find_validators(self, candidate_key: str, url: str) -> Validators | None: ...
    def ingest_artifact(self, req: ArtifactIngest) -> IngestResult: ...
    def record_unchanged(
        self, run_id: uuid.UUID, candidate_key: str, url: str, retrieved_at: datetime, attempts: int, now: datetime
    ) -> IngestResult | None: ...
    def quarantine(self, req: QuarantineIngest) -> IngestResult: ...
    def record_result(self, result: IngestResult, now: datetime) -> None: ...
    def counts(self) -> dict[str, int]: ...
    def record_fetch_request(self, req: FetchRequest) -> None: ...
    def fetch_requests(self, run_id: uuid.UUID | None = None) -> list[FetchRequest]: ...
    def version_hashes(self, candidate_key: str) -> list[str]: ...
    def last_seen(self, candidate_key: str, url: str) -> datetime | None: ...


# ---- in-memory implementation ------------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Doc:
    document_id: uuid.UUID
    source_reference: str | None
    versions: list[uuid.UUID] = field(default_factory=list)  # in creation order


@dataclass(slots=True)
class _Ver:
    version_id: uuid.UUID
    document_id: uuid.UUID
    content_hash: str
    locations: dict[str, datetime] = field(default_factory=dict)  # url -> last_seen
    http_meta: dict[str, Any] = field(default_factory=dict)


class InMemoryRepository:
    """Non-persistent: safe for DRY_RUN (synthetic content can never reach a durable source layer)."""

    persistent = False

    def __init__(self) -> None:
        self.sources: dict[str, str] = {}
        self.runs: dict[uuid.UUID, IngestRun] = {}
        self.artifacts: dict[str, uuid.UUID] = {}
        self.docs: dict[str, _Doc] = {}
        self.versions: dict[uuid.UUID, _Ver] = {}
        self.quarantines: dict[tuple[uuid.UUID, str, str, str | None], uuid.UUID] = {}
        self.results: dict[tuple[uuid.UUID, str], IngestResult] = {}
        self._fetch_requests: dict[tuple[uuid.UUID, str, str, int], FetchRequest] = {}

    def ensure_source(self, manifest: SourceManifest, manifest_ref: str, now: datetime) -> None:
        self.sources.setdefault(manifest.source_id, manifest.authority)

    def begin_run(self, run: IngestRun) -> None:
        if run.status is RunStatus.RUNNING and any(
            r.source_id == run.source_id and r.status is RunStatus.RUNNING for r in self.runs.values()
        ):
            raise RunAlreadyActive(f"a run for source {run.source_id} is already RUNNING")
        self.runs[run.ingest_run_id] = run

    def finish_run(
        self, run_id: uuid.UUID, status: RunStatus, abort_reason: AbortReason | None, finished_at: datetime, stats: Mapping[str, Any]
    ) -> None:
        self.runs[run_id] = replace(self.runs[run_id], status=status, abort_reason=abort_reason, finished_at=finished_at, stats=dict(stats))

    def _latest_version(self, candidate_key: str) -> _Ver | None:
        doc = self.docs.get(candidate_key)
        return self.versions[doc.versions[-1]] if doc and doc.versions else None

    def find_validators(self, candidate_key: str, url: str) -> Validators | None:
        doc = self.docs.get(candidate_key)
        for vid in reversed(doc.versions if doc else []):
            ver = self.versions[vid]
            if url in ver.locations and (ver.http_meta.get("etag") or ver.http_meta.get("last_modified")):
                return Validators(ver.http_meta.get("etag"), ver.http_meta.get("last_modified"))
        return None

    def ingest_artifact(self, req: ArtifactIngest) -> IngestResult:
        h = req.content_hash.value
        self.artifacts.setdefault(h, req.new_id())
        doc = self.docs.get(req.candidate_key)
        if doc is None:
            doc = self.docs[req.candidate_key] = _Doc(req.new_id(), req.candidate.source_reference)
        previous = self._latest_version(req.candidate_key)
        existing = next((self.versions[v] for v in doc.versions if self.versions[v].content_hash == h), None)
        alerts: list[IngestAlert] = []
        if existing is None:
            ver = _Ver(req.new_id(), doc.document_id, h, {req.location_url: req.retrieved_at}, dict(req.http_meta))
            if any(v.content_hash == h and v.document_id != doc.document_id for v in self.versions.values()):
                alerts.append(IngestAlert.DUPLICATE_BYTES_OTHER_DOCUMENT)
            if previous is not None and doc.source_reference is not None:
                alerts.append(IngestAlert.CONTENT_CHANGED_UNDER_STABLE_REF)
            self.versions[ver.version_id] = ver
            doc.versions.append(ver.version_id)
            outcome = IngestOutcome.NEW_VERSION
        elif req.location_url in existing.locations:
            existing.locations[req.location_url] = max(existing.locations[req.location_url], req.retrieved_at)
            ver, outcome, previous = existing, IngestOutcome.UNCHANGED_SKIPPED, None
        else:
            existing.locations[req.location_url] = req.retrieved_at
            ver, outcome, previous = existing, IngestOutcome.NEW_LOCATION_SAME_VERSION, None
        result = IngestResult(
            req.ingest_run_id,
            req.candidate_key,
            outcome,
            req.attempts,
            None,
            ver.version_id,
            req.content_hash,
            ContentHash(previous.content_hash) if previous is not None and outcome is IngestOutcome.NEW_VERSION else None,
            None,
            tuple(alerts),
        )
        self.results[(req.ingest_run_id, req.candidate_key)] = result
        return result

    def record_unchanged(
        self, run_id: uuid.UUID, candidate_key: str, url: str, retrieved_at: datetime, attempts: int, now: datetime
    ) -> IngestResult | None:
        doc = self.docs.get(candidate_key)
        for vid in reversed(doc.versions if doc else []):
            ver = self.versions[vid]
            if url in ver.locations:
                ver.locations[url] = max(ver.locations[url], retrieved_at)
                result = IngestResult(
                    run_id,
                    candidate_key,
                    IngestOutcome.UNCHANGED_SKIPPED,
                    attempts,
                    "NOT_MODIFIED",
                    ver.version_id,
                    ContentHash(ver.content_hash),
                )
                self.results[(run_id, candidate_key)] = result
                return result
        return None

    def quarantine(self, req: QuarantineIngest) -> IngestResult:
        key = (req.ingest_run_id, req.candidate_key, req.reason.value, req.content_hash.value if req.content_hash else None)
        qid = self.quarantines.setdefault(key, req.new_id())
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
        self.results[(req.ingest_run_id, req.candidate_key)] = result
        return result

    def record_result(self, result: IngestResult, now: datetime) -> None:
        self.results[(result.ingest_run_id, result.candidate_key)] = result

    def record_fetch_request(self, req: FetchRequest) -> None:
        """Mirrors the database constraints (FK to the run, unique attempt key, header allowlist) so offline tests catch violations."""
        if req.ingest_run_id not in self.runs:
            raise RepositoryConflict("fetch_request refers to an unknown ingest_run")
        if not set(req.selected_headers) <= LOGGED_HEADERS:
            raise RepositoryConflict("fetch_request.selected_headers contains a header outside the allowlist")
        key = (req.ingest_run_id, req.candidate_key, req.purpose.value, req.attempt)
        if key in self._fetch_requests:
            raise RepositoryConflict("duplicate fetch_request attempt")
        self._fetch_requests[key] = req

    def fetch_requests(self, run_id: uuid.UUID | None = None) -> list[FetchRequest]:
        rows = [r for r in self._fetch_requests.values() if run_id is None or r.ingest_run_id == run_id]
        return sorted(rows, key=lambda r: (r.retrieved_at, r.candidate_key, r.attempt, str(r.fetch_request_id)))

    def counts(self) -> dict[str, int]:
        return {
            "raw_artifact": len(self.artifacts),
            "regulatory_document": len(self.docs),
            "document_version": len(self.versions),
            "document_version_location": sum(len(v.locations) for v in self.versions.values()),
            "quarantine_record": len(self.quarantines),
            "ingest_result": len(self.results),
            "ingest_run": len(self.runs),
        }

    def version_hashes(self, candidate_key: str) -> list[str]:
        doc = self.docs.get(candidate_key)
        return [self.versions[v].content_hash for v in doc.versions] if doc else []

    def last_seen(self, candidate_key: str, url: str) -> datetime | None:
        doc = self.docs.get(candidate_key)
        seen = [self.versions[v].locations[url] for v in (doc.versions if doc else []) if url in self.versions[v].locations]
        return max(seen) if seen else None
