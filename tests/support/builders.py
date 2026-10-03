"""Builders for synthetic TESTREG manifests, limits and PDFs. All values are explicit test configuration, NOT production policy."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date

from packages.domain.manifest import (
    AccessReview,
    AccessReviewStatus,
    CrawlConfig,
    DateWindow,
    DomainScope,
    ManifestCandidate,
    PathPrefixes,
    SafetyLimits,
    SourceManifest,
    UrlPolicy,
)
from packages.domain.status import StatusLabel

HOST = "docs.testreg.example"
CONTACT_ENV = "RIG_TEST_CONTACT"
CONTACT_VALUE = "ops-secret-contact@testreg.example"
SMALL_LIMITS = SafetyLimits(
    min_delay_seconds=1.0,
    connect_timeout_seconds=2.0,
    read_timeout_seconds=2.0,
    total_timeout_seconds=30.0,
    max_bytes_listing=4096,
    max_bytes_detail_page=4096,
    max_bytes_attachment=8192,
    max_url_length=200,
    max_redirects=3,
    max_attempts=3,
    backoff_base_seconds=1.0,
    backoff_max_seconds=8.0,
    candidate_deadline_seconds=120.0,
    circuit_breaker_threshold=3,
    max_expansion_ratio=20.0,
    pdf_eof_tail_bytes=64,
)
PREFIXES = PathPrefixes(listing=("/list/",), detail=("/docs/",), attachment=("/files/",))


def limits_params(limits: SafetyLimits = SMALL_LIMITS) -> tuple[tuple[str, float | int], ...]:
    from dataclasses import asdict

    return tuple(sorted(asdict(limits).items()))


def pdf(tag: str = "a", *, extra: bytes = b"") -> bytes:
    """A tiny well-formed-enough PDF (header + EOF marker). `tag` makes the bytes (and hash) unique."""
    return b"%PDF-1.4\n% synthetic " + tag.encode() + b"\n1 0 obj\n<< /Type /Catalog >>\nendobj\n" + extra + b"\ntrailer\n<< >>\n%%EOF\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def candidate(
    key: str = "TEST-001", *, url: str | None = "default", index: int = 0, ref: str | None = "TESTREG/2026/1", **kw: object
) -> ManifestCandidate:
    document_url = f"https://{HOST}/files/{key.lower()}.pdf" if url == "default" else url
    defaults: dict[str, object] = dict(
        tier="primary",
        title=f"Synthetic {key}",
        document_type="circular",
        entry_index=index,
        document_url=document_url,
        source_reference=ref,
        listing_date=date(2026, 1, 5),
        sampled=False,
        status_labels=(("document_url_status", StatusLabel.OBSERVED),),
    )
    defaults.update(kw)
    return ManifestCandidate(document_key=key, **defaults)  # type: ignore[arg-type]


def manifest(
    candidates: Sequence[ManifestCandidate] | None = None,
    *,
    gate_open: bool = True,
    crawl_configured: bool = True,
    authorized: bool | None = None,
) -> SourceManifest:
    cands = tuple(sorted(candidates if candidates is not None else (candidate(),), key=lambda c: c.document_key))
    reviewed = gate_open
    return SourceManifest(
        manifest_version="testreg-1",
        source_id="testreg",
        authority="testreg",
        scope=DomainScope("TESTREG-land", "TESTREG", "synthetic"),
        access_review=AccessReview(
            AccessReviewStatus.APPROVED_FOR_RECONNAISSANCE if gate_open else AccessReviewStatus.AMBIGUOUS_REQUIRES_REVIEW,
            "tester" if reviewed else None,
            "2026-01-01T00:00:00Z" if reviewed else None,
        ),
        ingestion_authorized=gate_open if authorized is None else authorized,
        allowed_hosts=(HOST,),
        url_policy=UrlPolicy(PREFIXES, (f"https://{HOST}/list/index",)),
        document_types=("circular",),
        date_window=DateWindow(date(2026, 1, 1), date(2026, 12, 31)),
        crawl=CrawlConfig("CONFIGURED" if crawl_configured else "NOT_CONFIGURED", CONTACT_ENV, limits_params() if crawl_configured else ()),
        candidates=cands,
    )
