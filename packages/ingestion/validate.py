"""Response validation before anything is published (docs/phase1/source-safety-contract.md §5). Order is the contract's:
content-type -> magic bytes -> PDF sanity. The FIRST failing reason is the one returned/preserved.

PHASE BOUNDARY (stated, not faked): this is bounded byte-level triage only. Phase 1C does NOT parse PDF structure, so it
cannot verify page counts, object trees, cross-reference tables, encrypted/obfuscated names (`/J#53`), or that the PDF's
text declares the expected authority (AUTHORITY_MISMATCH). Those need the Phase 3 sandboxed parser and are NOT claimed here.
What IS done: media type, `%PDF-` header, `%%EOF` marker near the end, and a streamed token scan that rejects documents
containing `/Encrypt`, `/JavaScript`, `/JS`, `/Launch`, `/OpenAction` or `/EmbeddedFile`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import BinaryIO

from packages.domain.ingest import FetchPurpose, QuarantineReason
from packages.domain.manifest import SafetyLimits
from packages.ingestion.errors import HtmlDecodeError

PDF_MAGIC = b"%PDF-"
_ACTIVE_TOKENS = re.compile(rb"/(?:Encrypt|JavaScript|JS|Launch|OpenAction|EmbeddedFile)(?![A-Za-z0-9#])")
_SCAN_CHUNK = 1024 * 1024
_SCAN_OVERLAP = 32  # longer than the longest token (`/EmbeddedFile`, 13 bytes) plus lookahead
_PDF_TYPES = frozenset({"application/pdf"})
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_CHARSET = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9._:-]{1,40})", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Rejection:
    reason: QuarantineReason
    detail: str


def media_type(content_type: str | None) -> str:
    """`Content-Type` without parameters, lower-cased ('' if absent)."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def decode_html(body: bytes, content_type: str | None) -> str:
    """Strictly decode an already size-capped HTML body using the response's declared charset (UTF-8 when none is declared).

    The egress layer exposes raw bytes, so this small step lives with the other response checks and keeps the discovery parser a
    pure text function. It never replaces invalid bytes (no U+FFFD): an unknown charset, a non-text codec or undecodable bytes
    raise `HtmlDecodeError`. A charset declared only inside the HTML (`<meta>`) is not honoured: it would need a second parse.
    """
    declared = _CHARSET.search(content_type or "")
    charset = declared.group(1) if declared else "utf-8"
    try:
        return body.decode(charset, errors="strict")
    except (LookupError, UnicodeDecodeError) as exc:
        raise HtmlDecodeError(f"detail page is not decodable as {charset!r}") from exc


def sniff_media_type(head: bytes) -> str:
    """Coarse media type from the first bytes. Used for provenance (`media_type_sniffed`), never as trust."""
    if head.startswith(PDF_MAGIC):
        return "application/pdf"
    probe = head.lstrip()[:64].lower()
    if probe.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return "text/html"
    return "application/octet-stream"


def validate_response(content_type: str | None, body: BinaryIO, size: int, purpose: FetchPurpose, limits: SafetyLimits) -> Rejection | None:
    """Return the first Rejection, or None if the response may proceed to fingerprinting/storage. `body` is left at offset 0."""
    declared = media_type(content_type)
    expected = _PDF_TYPES if purpose is FetchPurpose.ATTACHMENT else _HTML_TYPES
    if declared not in expected:
        return Rejection(
            QuarantineReason.CONTENT_TYPE_MISMATCH, f"Content-Type {declared or '(absent)'!r} is not allowed for {purpose.value}"
        )
    if size > limits.max_bytes(purpose):
        return Rejection(QuarantineReason.SIZE_EXCEEDED, "body exceeds the cap")
    if purpose is not FetchPurpose.ATTACHMENT:
        return None  # HTML structure is untrusted data handled by later phases; Phase 1C only types and bounds it
    try:
        return _check_pdf_bytes(body, size, limits)
    finally:
        body.seek(0)  # every exit path leaves the stream at offset 0: the caller stores/quarantines these exact bytes next


def _check_pdf_bytes(body: BinaryIO, size: int, limits: SafetyLimits) -> Rejection | None:
    body.seek(0)
    head = body.read(len(PDF_MAGIC))
    if head != PDF_MAGIC:
        return Rejection(QuarantineReason.MAGIC_BYTES_MISMATCH, "body does not start with %PDF-" if size else "body is empty")
    tail_len = min(size, limits.pdf_eof_tail_bytes)
    body.seek(size - tail_len)
    if b"%%EOF" not in body.read(tail_len):
        return Rejection(QuarantineReason.PDF_SANITY_FAILED, "no %%EOF marker within the tail window")
    token = _scan_active_tokens(body)
    if token is not None:
        return Rejection(QuarantineReason.PDF_SANITY_FAILED, f"document contains the {token} token")
    return None


def _scan_active_tokens(body: BinaryIO) -> str | None:
    """Streamed, bounded-memory scan. A match that touches the end of a non-final window is deferred to the next window."""
    body.seek(0)
    carry = b""
    while True:
        chunk = body.read(_SCAN_CHUNK)
        final = not chunk
        window = carry + chunk
        for match in _ACTIVE_TOKENS.finditer(window):
            if final or match.end() < len(window):
                return match.group(0).decode("ascii")
        if final:
            return None
        carry = window[-_SCAN_OVERLAP:]
