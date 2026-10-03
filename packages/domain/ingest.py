"""Ingestion vocabulary and runtime records. Names follow docs/phase1/ingestion-contract.md exactly (§3, §4)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum, unique

from packages.domain.content_hash import ContentHash


@unique
class RunMode(StrEnum):
    DRY_RUN = "DRY_RUN"  # no network: manifest + fixtures only; always permitted
    LIVE = "LIVE"  # real egress; refused while the source-access gate is closed


@unique
class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_FAILURES = "COMPLETED_WITH_FAILURES"
    ABORTED = "ABORTED"


@unique
class AbortReason(StrEnum):
    GATE_CLOSED = "GATE_CLOSED"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    OPERATOR_CANCEL = "OPERATOR_CANCEL"
    INTERNAL_ERROR = "INTERNAL_ERROR"  # unexpected infrastructure failure: the run is closed, never left RUNNING


@unique
class FetchPurpose(StrEnum):
    LISTING = "LISTING"
    DETAIL_PAGE = "DETAIL_PAGE"
    ATTACHMENT = "ATTACHMENT"


@unique
class FetchFailure(StrEnum):
    """Failure states of one retrieval intent (contract §3.2)."""

    URL_REJECTED = "URL_REJECTED"
    DNS_REJECTED = "DNS_REJECTED"
    REDIRECT_REJECTED = "REDIRECT_REJECTED"
    TLS_ERROR = "TLS_ERROR"
    TIMEOUT = "TIMEOUT"
    SIZE_EXCEEDED = "SIZE_EXCEEDED"
    HTTP_PERMANENT = "HTTP_PERMANENT"
    HTTP_TRANSIENT = "HTTP_TRANSIENT"
    RATE_LIMITED_LOCAL = "RATE_LIMITED_LOCAL"
    GATE_CLOSED = "GATE_CLOSED"

    @property
    def retryable(self) -> bool:
        """Only these three categories are ever retried (source-safety-contract §7). Everything else never is."""
        return self in (FetchFailure.HTTP_TRANSIENT, FetchFailure.TIMEOUT, FetchFailure.TLS_ERROR)


@unique
class QuarantineReason(StrEnum):
    """Reason codes of a refusal to publish (contract §3.4). The first failing reason is the one preserved."""

    HOST_NOT_ALLOWED = "HOST_NOT_ALLOWED"
    REDIRECT_OFF_ALLOWLIST = "REDIRECT_OFF_ALLOWLIST"
    SCHEME_NOT_HTTPS = "SCHEME_NOT_HTTPS"
    PRIVATE_OR_RESERVED_IP = "PRIVATE_OR_RESERVED_IP"
    CONTENT_TYPE_MISMATCH = "CONTENT_TYPE_MISMATCH"
    MAGIC_BYTES_MISMATCH = "MAGIC_BYTES_MISMATCH"
    SIZE_EXCEEDED = "SIZE_EXCEEDED"
    PDF_SANITY_FAILED = "PDF_SANITY_FAILED"
    AUTHORITY_MISMATCH = "AUTHORITY_MISMATCH"
    VIEWER_WRAPPER_URL = "VIEWER_WRAPPER_URL"
    MANIFEST_SCOPE_MISMATCH = "MANIFEST_SCOPE_MISMATCH"


@unique
class IngestOutcome(StrEnum):
    """Outcome of one candidate in one run (contract §3.5)."""

    NEW_VERSION = "NEW_VERSION"
    NEW_LOCATION_SAME_VERSION = "NEW_LOCATION_SAME_VERSION"
    UNCHANGED_SKIPPED = "UNCHANGED_SKIPPED"
    QUARANTINED = "QUARANTINED"
    FAILED_TRANSIENT = "FAILED_TRANSIENT"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    UNRESOLVED = "UNRESOLVED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    GATE_CLOSED = "GATE_CLOSED"

    @property
    def is_failure(self) -> bool:
        """Outcomes that make a finished run COMPLETED_WITH_FAILURES (a refusal needs a human, so it counts)."""
        return self in (IngestOutcome.FAILED_TRANSIENT, IngestOutcome.FAILED_PERMANENT, IngestOutcome.QUARANTINED)


@unique
class IngestAlert(StrEnum):
    CONTENT_CHANGED_UNDER_STABLE_REF = "CONTENT_CHANGED_UNDER_STABLE_REF"
    DUPLICATE_BYTES_OTHER_DOCUMENT = "DUPLICATE_BYTES_OTHER_DOCUMENT"  # candidate-for-review, never auto-merged


@unique
class CandidateStage(StrEnum):
    """Per-candidate state machine (contract §4). VERSIONED means a document_version row exists with status STORED."""

    DISCOVERED = "DISCOVERED"
    FETCHED = "FETCHED"
    VALIDATED = "VALIDATED"
    STORED = "STORED"
    VERSIONED = "VERSIONED"


@unique
class CandidateTerminal(StrEnum):
    """Planning-level terminal states of a candidate (contract §3.1)."""

    UNRESOLVED = "UNRESOLVED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    REJECTED_NOT_ALLOWLISTED = "REJECTED_NOT_ALLOWLISTED"
    GATE_CLOSED = "GATE_CLOSED"


@unique
class DocumentVersionStatus(StrEnum):
    """Phase 1C only ever creates STORED. PUBLISHED needs parse/index (Phase 3+) and is deliberately not defined yet."""

    STORED = "STORED"


@unique
class JobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


def require_utc(value: datetime, name: str) -> datetime:
    """Instants are UTC-aware `datetime`s; naive or non-UTC values are rejected (temporal-model: timestamptz)."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be a timezone-aware UTC datetime")
    return value


@dataclass(frozen=True, slots=True)
class DocumentVersionKey:
    """Identity of a document version: one logical document + the exact content it had (UNIQUE(document_id, hash))."""

    document_id: uuid.UUID
    content_hash: ContentHash


@dataclass(frozen=True, slots=True)
class IngestRun:
    """One execution of the pipeline over a manifest (RUNTIME). Time comes only from an injected clock."""

    ingest_run_id: uuid.UUID
    source_id: str
    mode: RunMode
    started_at: datetime
    manifest_hash: str
    manifest_version: str
    code_version: str
    safety_policy_version: str
    authorisation_ref: str | None = None
    status: RunStatus = RunStatus.RUNNING
    abort_reason: AbortReason | None = None
    finished_at: datetime | None = None
    stats: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_utc(self.started_at, "started_at")
        if self.finished_at is not None:
            require_utc(self.finished_at, "finished_at")
        if (self.status is RunStatus.ABORTED) != (self.abort_reason is not None):
            raise ValueError("abort_reason is set if and only if the run is ABORTED")
        ContentHash.parse(self.manifest_hash)


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Outcome of one candidate in one run (RUNTIME). UNIQUE(ingest_run_id, candidate_key)."""

    ingest_run_id: uuid.UUID
    candidate_key: str
    outcome: IngestOutcome
    attempts: int = 0
    reason_code: str | None = None
    document_version_id: uuid.UUID | None = None
    content_hash: ContentHash | None = None
    previous_content_hash: ContentHash | None = None
    quarantine_id: uuid.UUID | None = None
    alerts: tuple[IngestAlert, ...] = ()
