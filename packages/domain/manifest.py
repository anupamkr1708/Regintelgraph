"""Source manifest domain types (docs/phase1/ingestion-contract.md §0, §3.1; source-safety-contract header).

Pure data + invariants. Parsing YAML is infrastructure and lives in `packages/ingestion/manifest.py`.
Numeric safety limits are NEVER defaulted here (H14): an unset parameter makes `CrawlConfig.limits()` return None and the
fetcher fails closed (`ABORTED(POLICY_VIOLATION)`).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum, unique

from packages.domain.ingest import FetchPurpose
from packages.domain.status import StatusLabel

_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


@unique
class AccessReviewStatus(StrEnum):
    APPROVED_FOR_RECONNAISSANCE = "APPROVED_FOR_RECONNAISSANCE"
    BLOCKED = "BLOCKED"
    AMBIGUOUS_REQUIRES_REVIEW = "AMBIGUOUS_REQUIRES_REVIEW"


@dataclass(frozen=True, slots=True)
class AccessReview:
    status: AccessReviewStatus
    reviewed_by: str | None
    reviewed_at: str | None
    open_items: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PathPrefixes:
    """Reviewed URL path families per fetch purpose (manifest `allowed_url_policy.path_prefixes`)."""

    listing: tuple[str, ...]
    detail: tuple[str, ...]
    attachment: tuple[str, ...]

    def __post_init__(self) -> None:
        for group in (self.listing, self.detail, self.attachment):
            for prefix in group:
                if not prefix.startswith("/") or ".." in prefix or "\\" in prefix or "%" in prefix:
                    raise ValueError(f"unsafe path prefix: {prefix!r}")

    def for_purpose(self, purpose: FetchPurpose) -> tuple[str, ...]:
        if purpose is FetchPurpose.LISTING:
            return self.listing
        if purpose is FetchPurpose.DETAIL_PAGE:
            return self.detail
        return self.attachment


@dataclass(frozen=True, slots=True)
class UrlPolicy:
    path_prefixes: PathPrefixes
    listing_urls: tuple[str, ...] = ()  # the only URLs ever eligible for the LISTING purpose (no pagination)


@dataclass(frozen=True, slots=True)
class SafetyLimits:
    """A COMPLETE set of numeric safety parameters, all supplied by a human via the manifest `crawl` block."""

    min_delay_seconds: float
    connect_timeout_seconds: float
    read_timeout_seconds: float
    total_timeout_seconds: float
    max_bytes_listing: int
    max_bytes_detail_page: int
    max_bytes_attachment: int
    max_url_length: int
    max_redirects: int
    max_attempts: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    candidate_deadline_seconds: float
    circuit_breaker_threshold: int
    max_expansion_ratio: float
    pdf_eof_tail_bytes: int

    def __post_init__(self) -> None:
        for name in _CRAWL_FIELDS:
            _check_number(name, getattr(self, name), _CRAWL_FIELDS[name])
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("backoff_max_seconds must be >= backoff_base_seconds")

    def max_bytes(self, purpose: FetchPurpose) -> int:
        if purpose is FetchPurpose.LISTING:
            return self.max_bytes_listing
        if purpose is FetchPurpose.DETAIL_PAGE:
            return self.max_bytes_detail_page
        return self.max_bytes_attachment


# field -> kind. Seconds/ratios are positive finite numbers; the rest are positive integers (bool is rejected).
_CRAWL_FIELDS: dict[str, str] = {
    "min_delay_seconds": "float",
    "connect_timeout_seconds": "float",
    "read_timeout_seconds": "float",
    "total_timeout_seconds": "float",
    "max_bytes_listing": "int",
    "max_bytes_detail_page": "int",
    "max_bytes_attachment": "int",
    "max_url_length": "int",
    "max_redirects": "int",
    "max_attempts": "int",
    "backoff_base_seconds": "float",
    "backoff_max_seconds": "float",
    "candidate_deadline_seconds": "float",
    "circuit_breaker_threshold": "int",
    "max_expansion_ratio": "float",
    "pdf_eof_tail_bytes": "int",
}
CRAWL_PARAMETER_NAMES: tuple[str, ...] = tuple(_CRAWL_FIELDS)


def _check_number(name: str, value: object, kind: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"crawl.{name} must be a number")
    if kind == "int" and not isinstance(value, int):
        raise ValueError(f"crawl.{name} must be an integer")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"crawl.{name} must be a positive finite number")


@dataclass(frozen=True, slots=True)
class CrawlConfig:
    """The manifest `crawl` block. Every numeric parameter is optional here and None means UNSET (fail closed)."""

    status: str  # NOT_CONFIGURED | CONFIGURED
    contact_env_var: str  # name of the environment variable holding the contact; the value is never stored or logged
    parameters: tuple[tuple[str, float | int], ...] = ()  # only the keys the manifest actually set, sorted by name

    def __post_init__(self) -> None:
        if self.status not in ("NOT_CONFIGURED", "CONFIGURED"):
            raise ValueError("crawl.status must be NOT_CONFIGURED or CONFIGURED")
        if _ENV_NAME.fullmatch(self.contact_env_var) is None:
            raise ValueError("crawl.user_agent_contact must reference an environment variable name")
        names = [n for n, _ in self.parameters]
        if len(set(names)) != len(names) or any(n not in _CRAWL_FIELDS for n in names):
            raise ValueError("crawl.parameters must be unique known parameter names")

    def missing_parameters(self) -> tuple[str, ...]:
        have = {n for n, _ in self.parameters}
        return tuple(n for n in CRAWL_PARAMETER_NAMES if n not in have)

    def limits(self) -> SafetyLimits | None:
        """Complete limits, or None if the block is not CONFIGURED or any parameter is unset (fail closed)."""
        if self.status != "CONFIGURED" or self.missing_parameters():
            return None
        return SafetyLimits(**dict(self.parameters))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class DateWindow:
    """Bounded corpus window from the manifest (inclusive). Dates are listing dates, not legal dates (H9)."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("date window end precedes start")

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end


@dataclass(frozen=True, slots=True)
class DomainScope:
    jurisdiction: str
    regulator: str
    corpus: str


@dataclass(frozen=True, slots=True)
class ManifestCandidate:
    """A reviewed intention to ingest one logical document. Not a fetch and not a document (contract §3.1)."""

    document_key: str
    tier: str
    title: str
    document_type: str
    entry_index: int  # position in the manifest file: provenance only; ordering is by document_key
    canonical_url: str | None = None
    document_url: str | None = None
    source_reference: str | None = None
    listing_date: date | None = None
    listing_page_url: str | None = None
    sampled: bool = False
    resolution_status: str | None = None
    # `*_status` labels exactly as the manifest wrote them (never upgraded), sorted by field name.
    status_labels: tuple[tuple[str, StatusLabel], ...] = ()

    def __post_init__(self) -> None:
        if _KEY.fullmatch(self.document_key) is None:
            raise ValueError(f"invalid document_key: {self.document_key!r}")
        if not self.title or not self.document_type or not self.tier:
            raise ValueError(f"{self.document_key}: title, tier and document_type are required")

    def label(self, field_name: str) -> StatusLabel | None:
        return dict(self.status_labels).get(field_name)


@dataclass(frozen=True, slots=True)
class SourceManifest:
    manifest_version: str
    source_id: str
    authority: str
    scope: DomainScope
    access_review: AccessReview
    ingestion_authorized: bool
    allowed_hosts: tuple[str, ...]
    url_policy: UrlPolicy
    document_types: tuple[str, ...]
    date_window: DateWindow | None
    crawl: CrawlConfig
    candidates: tuple[ManifestCandidate, ...]  # deterministic order: sorted by document_key

    def __post_init__(self) -> None:
        if _SLUG.fullmatch(self.source_id) is None or _SLUG.fullmatch(self.authority) is None:
            raise ValueError("source_id and authority must be lowercase slugs")
        keys = [c.document_key for c in self.candidates]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            raise ValueError("candidates must be unique and sorted by document_key")
        if not self.allowed_hosts:
            raise ValueError("allowed_hosts must not be empty")

    def candidate_key(self, candidate: ManifestCandidate) -> str:
        """`source_id/document_key` — unique within a manifest version and used as the document identity key."""
        return f"{self.source_id}/{candidate.document_key}"

    def gate_closed_reasons(self) -> tuple[str, ...]:
        """Why the source-access gate is closed (empty tuple = open). Contract §0: ALL conditions must hold.

        Fail-closed by construction; `APPROVED_FOR_RECONNAISSANCE` alone is not ingestion approval.
        """
        reasons: list[str] = []
        if self.ingestion_authorized is not True:
            reasons.append("ingestion_authorized is not true")
        if self.access_review.status in (AccessReviewStatus.BLOCKED, AccessReviewStatus.AMBIGUOUS_REQUIRES_REVIEW):
            reasons.append(f"access_review.status is {self.access_review.status.value}")
        if not self.access_review.reviewed_by:
            reasons.append("access_review.reviewed_by is not recorded")
        if not self.access_review.reviewed_at:
            reasons.append("access_review.reviewed_at is not recorded")
        return tuple(reasons)

    @property
    def gate_open(self) -> bool:
        return not self.gate_closed_reasons()
