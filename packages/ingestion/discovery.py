"""Pure detail-page -> attachment-reference discovery (Phase 1D). No network, no database, no filesystem, no manifest writes.

    HTML text + base URL  ->  observed references  ->  resolved URLs  ->  canonical URL validation  ->  deterministic result

What this module does NOT decide: whether a URL is safe or in scope. Every observed reference is handed to the caller-supplied
`check` callable, which in the pipeline is the canonical `EgressClient.check_url` -> `urlpolicy.validate_url` (ATTACHMENT purpose).
This module never re-implements URL policy and never fetches anything; fetching stays with the pipeline/transport.

Rules (docs/phase1/ingestion-contract.md; AGENTS.md H1, H4, H11):
  * only URLs actually OBSERVED in the HTML enter discovery; relative references are resolved against the page URL (resolution of an
    observed reference, not construction of a new one). Nothing is guessed: no filenames, ids, dates, templates or path probing.
  * the HTML is untrusted data: it is tokenised by the standard-library parser, never executed, and never obeyed. Only the small
    fixed set of URL-bearing attributes below is read. A `<base>` element is deliberately ignored (resolution is against the page URL).
  * a viewer wrapper (`/web/?file=<url>`) is NEVER treated as an attachment. It is recognised by the canonical validator's own
    VIEWER_WRAPPER_URL verdict; only then is the already-present `file` value read as a candidate and validated like any other URL.
    One level only: a wrapper inside a wrapper is rejected. The wrapper itself is never returned and is therefore never fetchable.
  * valid candidates are deduplicated by normalised URL (all raw references that led to one are kept). Exactly one distinct valid
    candidate => DISCOVERED; none => UNRESOLVED; two or more => AMBIGUOUS. There is no tie-break (not first, latest, largest,
    shortest or lexical): the system abstains (H11). No filename/suffix heuristic is used anywhere.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum, unique
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urljoin, urlsplit

from packages.domain.ingest import QuarantineReason
from packages.ingestion.errors import UrlRejected
from packages.ingestion.logsafe import neutralize

# element -> the ONE attribute read. Small, fixed, auditable. (`a[href]` is the ordinary hyperlink; the other three are the generic
# embedding elements a document viewer can use. The actual SEBI page structure is UNVERIFIED, so none of this is source-specific.)
SUPPORTED_REFERENCES: Mapping[str, str] = {"a": "href", "iframe": "src", "embed": "src", "object": "data"}

# Bounds on what is PERSISTED about one discovery (storage hygiene, not crawl policy): lists are truncated, values shortened.
AUDIT_LIST_LIMIT = 10
AUDIT_VALUE_MAX = 300

REASON_MALFORMED_REFERENCE = "MALFORMED_REFERENCE"
REASON_MALFORMED_PERCENT_ENCODING = "MALFORMED_PERCENT_ENCODING"
REASON_VIEWER_FILE_MISSING = "VIEWER_FILE_PARAMETER_MISSING"
REASON_VIEWER_FILE_REPEATED = "VIEWER_FILE_PARAMETER_REPEATED"

_HTML_WS = " \t\n\r\f"
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")

CheckUrl = Callable[[str], str]  # canonical validator adapter: returns the normalised URL or raises UrlRejected


@unique
class DiscoveryStatus(StrEnum):
    DISCOVERED = "DISCOVERED"  # exactly one distinct valid attachment URL
    UNRESOLVED = "UNRESOLVED"  # no valid attachment URL observed
    AMBIGUOUS = "AMBIGUOUS"  # two or more distinct valid attachment URLs: abstain


@dataclass(frozen=True, slots=True)
class ObservedReference:
    element: str
    attribute: str
    raw: str  # the attribute value as observed (HTML entities decoded, surrounding whitespace trimmed)


@dataclass(frozen=True, slots=True)
class AttachmentCandidate:
    url: str  # normalised URL returned by the canonical validator
    references: tuple[str, ...]  # every raw observed reference that resolved to this URL, in document order


@dataclass(frozen=True, slots=True)
class RejectedReference:
    raw: str
    reason: str  # a QuarantineReason value from the canonical validator, or one of the REASON_* codes above
    via_viewer: bool = False  # the rejected URL was extracted from a viewer wrapper


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    status: DiscoveryStatus
    references_observed: int
    candidates: tuple[AttachmentCandidate, ...]
    rejected: tuple[RejectedReference, ...]

    def to_audit(self, detail_page_url: str) -> dict[str, object]:
        """Small, bounded, log-safe JSON-able record (stored in run stats / artifact metadata). Never contains page content."""

        def clip(value: str) -> str:
            return neutralize(value, max_len=AUDIT_VALUE_MAX)

        return {
            "status": self.status.value,
            "detail_page_url": clip(detail_page_url),
            "references_observed": self.references_observed,
            "attachment_candidates": [
                {"url": clip(c.url), "references": [clip(r) for r in c.references[:AUDIT_LIST_LIMIT]]}
                for c in self.candidates[:AUDIT_LIST_LIMIT]
            ],
            "attachment_candidates_total": len(self.candidates),
            "rejected_references": [
                {"reference": clip(r.raw), "reason": r.reason, "via_viewer": r.via_viewer} for r in self.rejected[:AUDIT_LIST_LIMIT]
            ],
            "rejected_references_total": len(self.rejected),
        }


class _ReferenceCollector(HTMLParser):
    """Collects the supported URL-bearing attributes. Keeps nothing else (no tree, no text, no other attributes)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[ObservedReference] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attribute = SUPPORTED_REFERENCES.get(tag)
        if attribute is None:
            return
        for name, value in attrs:
            if name == attribute:  # first occurrence wins (HTML semantics); later duplicates are ignored
                raw = (value or "").strip(_HTML_WS)
                if raw:
                    self.references.append(ObservedReference(tag, name, raw))
                return


def observe_references(html: str) -> tuple[ObservedReference, ...]:
    """Tokenise `html` (never executed) and return every supported URL-bearing reference in document order."""
    collector = _ReferenceCollector()
    collector.feed(html)
    collector.close()
    return tuple(collector.references)


def discover_attachment_urls(html: str, *, base_url: str, check: CheckUrl) -> DiscoveryResult:
    """Classify the attachment references observed in `html`. `check` is the canonical URL validator (see module docstring)."""
    observed = observe_references(html)
    accepted: dict[str, list[str]] = {}  # normalised URL -> raw references, insertion (document) order
    rejected: list[RejectedReference] = []
    for ref in observed:
        url, reason, via_viewer = _evaluate(ref.raw, base_url, check)
        if url is not None:
            accepted.setdefault(url, []).append(ref.raw)
        else:
            rejected.append(RejectedReference(ref.raw, reason or REASON_MALFORMED_REFERENCE, via_viewer))
    candidates = tuple(AttachmentCandidate(url, tuple(refs)) for url, refs in accepted.items())
    if not candidates:
        status = DiscoveryStatus.UNRESOLVED
    elif len(candidates) == 1:
        status = DiscoveryStatus.DISCOVERED
    else:
        status = DiscoveryStatus.AMBIGUOUS
    return DiscoveryResult(status, len(observed), candidates, tuple(rejected))


def _evaluate(raw: str, base_url: str, check: CheckUrl) -> tuple[str | None, str | None, bool]:
    """(normalised url, None, via_viewer) if `raw` yields a valid candidate; else (None, reason, via_viewer)."""
    try:
        resolved = urljoin(base_url, raw)
    except ValueError:
        return None, REASON_MALFORMED_REFERENCE, False
    try:
        return check(resolved), None, False
    except UrlRejected as exc:
        if exc.reason is not QuarantineReason.VIEWER_WRAPPER_URL:
            return None, exc.reason.value, False
    # The canonical validator says `resolved` is a viewer wrapper: it is NOT a candidate itself. Read its already-present `file`
    # value as an observed embedded URL and validate THAT. The wrapper is never returned, so it can never be fetched.
    embedded, problem = _embedded_url(resolved)
    if embedded is None:
        return None, problem, True
    try:
        return check(embedded), None, True
    except UrlRejected as exc:  # includes a nested wrapper (VIEWER_WRAPPER_URL): one level only
        return None, exc.reason.value, True


def _embedded_url(wrapper: str) -> tuple[str | None, str]:
    """The single `file` query value of an observed viewer-wrapper URL, strictly decoded; else (None, reason code)."""
    try:
        query = urlsplit(wrapper).query
    except ValueError:
        return None, REASON_MALFORMED_REFERENCE
    if _BAD_PERCENT.search(query):
        return None, REASON_MALFORMED_PERCENT_ENCODING
    try:
        pairs = parse_qsl(query, keep_blank_values=True, errors="strict")
    except ValueError:  # includes UnicodeDecodeError: invalid UTF-8 is never replaced
        return None, REASON_MALFORMED_PERCENT_ENCODING
    values = [value for key, value in pairs if key == "file"]
    if len(values) > 1:
        return None, REASON_VIEWER_FILE_REPEATED
    if not values or not values[0]:
        return None, REASON_VIEWER_FILE_MISSING
    return values[0], ""
