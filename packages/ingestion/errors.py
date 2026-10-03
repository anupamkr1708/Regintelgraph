"""Exception hierarchy for ingestion. Messages are written for logs: they never embed bodies, secrets or raw headers."""

from __future__ import annotations

from packages.domain.ingest import FetchFailure, QuarantineReason


class IngestError(Exception):
    """Base class for all ingestion errors."""


class ManifestError(IngestError):
    """The manifest is malformed or violates a structural rule."""


class PolicyViolation(IngestError):
    """Required safety policy is missing or invalid; the run must fail closed (ABORTED(POLICY_VIOLATION))."""

    def __init__(self, message: str, missing: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.missing = missing


class ModeViolation(IngestError):
    """A DRY_RUN was handed a component that performs real network I/O. A dry run never turns into a live run."""


class UrlRejected(IngestError):
    """A URL failed static validation (no DNS or network has been touched)."""

    def __init__(self, reason: QuarantineReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


class FetchError(IngestError):
    """A retrieval intent failed. `quarantine` is set when the failure maps to a QuarantineRecord reason code."""

    def __init__(
        self,
        kind: FetchFailure,
        detail: str,
        *,
        quarantine: QuarantineReason | None = None,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(f"{kind.value}: {detail}")
        self.kind = kind
        self.detail = detail
        self.attempts = 1
        self.quarantine = quarantine
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds


class TransportError(IngestError):
    """Base for low-level transport failures raised by `HttpTransport` implementations."""


class TransportConnectError(TransportError):
    """TCP connection could not be established (refused, unreachable, reset)."""


class TransportTimeoutError(TransportError):
    """A connect/read timeout elapsed."""


class TransportTlsError(TransportError):
    """TLS handshake or certificate/hostname verification failed. Verification is never disabled."""


class TransportTruncatedError(TransportError):
    """The body ended before its declared length (truncated download)."""


class CircuitOpenError(IngestError):
    """Consecutive refusals (403/429/401) reached the manifest threshold: the run must stop (ABORTED(CIRCUIT_OPEN))."""


class BlobStoreError(IngestError):
    """Base class for blob/quarantine store failures."""


class UnsafePathError(BlobStoreError):
    """A path component is a symlink / not a directory / outside the root. Nothing is written."""


class BlobCollisionError(BlobStoreError):
    """An existing blob has different bytes than its hash promises. Treated as corruption (P1); never overwritten."""


class VerifyAfterWriteError(BlobStoreError):
    """Re-reading what was just written did not reproduce the expected hash (P1). The temp file is removed."""


class RunAlreadyActive(IngestError):
    """Another run for the same source is RUNNING (two concurrent runs for one source are prevented)."""


class RepositoryConflict(IngestError):
    """A source-layer invariant was violated (e.g. a URL that already belongs to a different document)."""
