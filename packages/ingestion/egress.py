"""Safe egress client: the only path by which bytes enter the system (docs/phase1/source-safety-contract.md §2-§8).

Per request: static URL validation -> DNS/IP validation -> pinned-IP transport -> status handling. Redirects are followed
MANUALLY: every hop is fully re-validated (scheme, host allowlist, port, path scope, DNS/IP), loops and downgrades are
rejected and the hop count is bounded by the manifest. The body is streamed (never held unbounded in memory), size-capped,
decoded under an expansion-ratio guard and hashed incrementally (SHA-256 over the decoded bytes).

Not here, by design: no User-Agent rotation, no identity change on 403, no URL mutation on retry, no other-host fallback,
no cookies, no Authorization header, no proxy. A refusal (403/429/401) is a signal that is counted toward the circuit
breaker, never something to route around.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import threading
import zlib
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import BinaryIO
from urllib.parse import urljoin, urlsplit

from packages.domain.ingest import FetchFailure, FetchPurpose, QuarantineReason
from packages.domain.manifest import PathPrefixes, SafetyLimits
from packages.ingestion.errors import (
    CircuitOpenError,
    FetchError,
    TransportConnectError,
    TransportError,
    TransportTimeoutError,
    TransportTlsError,
    TransportTruncatedError,
    UrlRejected,
)
from packages.ingestion.ipcheck import validate_resolution
from packages.ingestion.logsafe import LOGGER, neutralize, safe_headers, safe_url
from packages.ingestion.ports import Clock, HttpTransport, Resolver, Rng, TransportRequest, TransportResponse
from packages.ingestion.urlpolicy import ValidatedUrl, validate_url

CHUNK = 64 * 1024
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
REFUSAL_STATUSES = frozenset({401, 403, 429})  # counted toward the circuit breaker (contract §7)
_DIGITS = re.compile(r"[0-9]{1,12}")
_CONTACT = re.compile(r"[^\s<>\"'\\\x00-\x1f\x7f]{3,254}")


def build_user_agent(contact: str) -> str:
    """Descriptive, honest identity with a contact reference. Never altered to evade blocking. Never logged or stored."""
    if _CONTACT.fullmatch(contact) is None:
        raise ValueError("crawler contact is empty or contains unsafe characters")
    return f"RegIntelGraph-ingest/1 (research; contact: {contact})"


def backoff_ceiling(attempt: int, base: float, cap: float) -> float:
    """Exponential ceiling for the jittered delay before retry number `attempt` (1-based). Monotone non-decreasing."""
    return float(min(cap, base * (2 ** (attempt - 1))))


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if _DIGITS.fullmatch(value):
        return float(int(value))
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, (when - now).total_seconds())


@dataclass(frozen=True, slots=True)
class Validators:
    """Stored HTTP validators used for conditional requests (a 304 means UNCHANGED_SKIPPED)."""

    etag: str | None = None
    last_modified: str | None = None


@dataclass(slots=True)
class FetchedResponse:
    requested_url: str  # normalised, validated hop-0 URL (the manifest-reviewed location key)
    final_url: str
    redirect_chain: tuple[str, ...]  # log-safe forms of every URL requested, in order
    status: int
    headers: Mapping[str, str]  # allowlisted subset only
    size: int
    sha256_hex: str
    not_modified: bool
    retrieved_at: datetime
    attempts: int = 1
    body: BinaryIO | None = None  # spooled, seekable, positioned at 0; the caller must close()

    def close(self) -> None:
        if self.body is not None:
            self.body.close()
            self.body = None


class HostPacer:
    """One in-flight request per host and a minimum delay between request STARTS (contract §7)."""

    def __init__(self, clock: Clock, min_delay_seconds: float) -> None:
        self._clock = clock
        self._min_delay = min_delay_seconds
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._last_start: dict[str, float] = {}

    @contextmanager
    def slot(self, host: str) -> Iterator[None]:
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            last = self._last_start.get(host)
            if last is not None:
                wait = last + self._min_delay - self._clock.monotonic()
                if wait > 0:
                    self._clock.sleep(wait)
            self._last_start[host] = self._clock.monotonic()
            yield


class EgressClient:
    def __init__(
        self,
        *,
        resolver: Resolver,
        transport: HttpTransport,
        clock: Clock,
        rng: Rng,
        allowed_hosts: Collection[str],
        path_prefixes: PathPrefixes,
        listing_urls: Collection[str],
        limits: SafetyLimits,
        user_agent: str,
        secrets: Collection[str] = (),
        spool_dir: str | None = None,
    ) -> None:
        self._resolver = resolver
        self._transport = transport
        self._clock = clock
        self._rng = rng
        self._hosts = tuple(allowed_hosts)
        self._prefixes = path_prefixes
        self._listing = tuple(listing_urls)
        self._limits = limits
        self._ua = user_agent
        self._secrets = tuple(secrets)
        self._spool_dir = spool_dir
        self._pacer = HostPacer(clock, limits.min_delay_seconds)
        self._consecutive_refusals = 0

    # ---- public --------------------------------------------------------------------------------------------------

    def fetch(self, url: str, purpose: FetchPurpose, *, validators: Validators | None = None) -> FetchedResponse:
        """Fetch `url` with bounded retries. Raises FetchError (with `.attempts`) or CircuitOpenError."""
        lim = self._limits
        deadline = self._clock.monotonic() + lim.candidate_deadline_seconds
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._attempt(url, purpose, validators, deadline)
                response.attempts = attempt
                return response
            except FetchError as err:
                err.attempts = attempt
                if not err.kind.retryable or attempt >= lim.max_attempts:
                    raise
                delay = max(
                    self._rng.uniform(0.0, backoff_ceiling(attempt, lim.backoff_base_seconds, lim.backoff_max_seconds)),
                    lim.min_delay_seconds,
                )
                if err.retry_after_seconds is not None:
                    delay = max(delay, err.retry_after_seconds)  # never retry sooner than the server asked
                if self._clock.monotonic() + delay > deadline:
                    raise
                LOGGER.info("retrying %s after %.2fs (attempt %d)", err.kind.value, delay, attempt)
                self._clock.sleep(delay)

    def check_url(self, url: str, purpose: FetchPurpose) -> ValidatedUrl:
        """Static validation only (no DNS, no network). Raises FetchError(URL_REJECTED, quarantine=...)."""
        return self._validate(url, purpose, hop=0)

    # ---- one attempt ---------------------------------------------------------------------------------------------

    def _validate(self, url: str, purpose: FetchPurpose, *, hop: int) -> ValidatedUrl:
        try:
            return validate_url(
                url,
                purpose,
                allowed_hosts=self._hosts,
                path_prefixes=self._prefixes,
                max_url_length=self._limits.max_url_length,
                listing_urls=self._listing,
            )
        except UrlRejected as exc:
            if hop == 0:
                raise FetchError(FetchFailure.URL_REJECTED, exc.detail, quarantine=exc.reason) from exc
            reason = (
                QuarantineReason.SCHEME_NOT_HTTPS
                if exc.reason is QuarantineReason.SCHEME_NOT_HTTPS
                else QuarantineReason.REDIRECT_OFF_ALLOWLIST
            )
            raise FetchError(FetchFailure.REDIRECT_REJECTED, f"redirect target rejected: {exc.detail}", quarantine=reason) from exc

    def _attempt(self, url: str, purpose: FetchPurpose, validators: Validators | None, deadline: float) -> FetchedResponse:
        lim = self._limits
        chain: list[str] = []
        seen: set[str] = set()
        current = url
        first_url = ""
        for hop in range(lim.max_redirects + 1):
            target = self._validate(current, purpose, hop=hop)
            if target.url in seen:
                raise FetchError(FetchFailure.REDIRECT_REJECTED, "redirect loop", quarantine=QuarantineReason.REDIRECT_OFF_ALLOWLIST)
            seen.add(target.url)
            first_url = first_url or target.url
            chain.append(safe_url(target.url, secrets=self._secrets))
            ips = validate_resolution(target.host, self._resolver)  # after URL validation, before any connect
            conditional = hop == 0 and validators is not None
            with self._pacer.slot(target.host):
                resp = self._open(target, ips[0], purpose, validators if conditional else None)
                try:
                    LOGGER.info(
                        "response %s %s headers=%s", resp.status, safe_url(target.url), safe_headers(resp.headers, secrets=self._secrets)
                    )
                    if resp.status in REDIRECT_STATUSES:
                        self._note_success()
                        current = self._next_location(resp, current)
                        continue
                    return self._finish(resp, target, first_url, tuple(chain), purpose, deadline)
                finally:
                    resp.close()
        raise FetchError(FetchFailure.REDIRECT_REJECTED, "too many redirects")

    def _next_location(self, resp: TransportResponse, current: str) -> str:
        location = resp.headers.get("location")
        if not location:
            raise FetchError(FetchFailure.HTTP_PERMANENT, "redirect without Location", http_status=resp.status)
        if len(location) > self._limits.max_url_length:
            raise FetchError(
                FetchFailure.REDIRECT_REJECTED, "Location exceeds the URL length limit", quarantine=QuarantineReason.REDIRECT_OFF_ALLOWLIST
            )
        absolute = urljoin(current, location)  # resolves relative references against the validated current URL
        try:
            scheme = urlsplit(absolute).scheme.lower()
        except ValueError as exc:
            raise FetchError(
                FetchFailure.REDIRECT_REJECTED, "malformed Location", quarantine=QuarantineReason.REDIRECT_OFF_ALLOWLIST
            ) from exc
        if scheme != "https":
            raise FetchError(FetchFailure.REDIRECT_REJECTED, "redirect to a non-https scheme", quarantine=QuarantineReason.SCHEME_NOT_HTTPS)
        return absolute

    def _open(self, target: ValidatedUrl, ip: str, purpose: FetchPurpose, validators: Validators | None) -> TransportResponse:
        headers = {
            "User-Agent": self._ua,
            "Accept": "application/pdf" if purpose is FetchPurpose.ATTACHMENT else "text/html,application/xhtml+xml",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        if validators is not None:
            if validators.etag:
                headers["If-None-Match"] = validators.etag
            if validators.last_modified:
                headers["If-Modified-Since"] = validators.last_modified
        request = TransportRequest(
            method="GET",
            host=target.host,
            ip=ip,
            port=target.port,
            target=target.target,
            headers=headers,
            connect_timeout=self._limits.connect_timeout_seconds,
            read_timeout=self._limits.read_timeout_seconds,
        )
        try:
            return self._transport.open(request)
        except TransportTimeoutError as exc:
            raise FetchError(FetchFailure.TIMEOUT, "connect/read timeout") from exc
        except TransportTlsError as exc:
            raise FetchError(FetchFailure.TLS_ERROR, neutralize(exc, secrets=self._secrets)) from exc
        except TransportConnectError as exc:
            raise FetchError(FetchFailure.HTTP_TRANSIENT, neutralize(exc, secrets=self._secrets)) from exc
        except TransportError as exc:
            raise FetchError(FetchFailure.HTTP_TRANSIENT, neutralize(exc, secrets=self._secrets)) from exc

    # ---- status handling and body streaming ----------------------------------------------------------------------

    def _note_success(self) -> None:
        self._consecutive_refusals = 0

    def _note_refusal(self) -> None:
        self._consecutive_refusals += 1
        if self._consecutive_refusals >= self._limits.circuit_breaker_threshold:
            raise CircuitOpenError(f"{self._consecutive_refusals} consecutive refusals (401/403/429)")

    def _finish(
        self, resp: TransportResponse, target: ValidatedUrl, first_url: str, chain: tuple[str, ...], purpose: FetchPurpose, deadline: float
    ) -> FetchedResponse:
        status = resp.status
        now = self._clock.now()
        if status == 304:
            self._note_success()
            return FetchedResponse(first_url, target.url, chain, status, safe_headers(resp.headers), 0, "", True, now)
        if status in REFUSAL_STATUSES:
            self._note_refusal()
        if status == 429 or 500 <= status <= 599:
            raise FetchError(
                FetchFailure.HTTP_TRANSIENT,
                f"HTTP {status}",
                http_status=status,
                retry_after_seconds=parse_retry_after(resp.headers.get("retry-after"), now),
            )
        if status != 200:
            raise FetchError(FetchFailure.HTTP_PERMANENT, f"HTTP {status}", http_status=status)
        self._note_success()

        cap = self._limits.max_bytes(purpose)
        declared = resp.headers.get("content-length")
        if declared is not None:
            if not _DIGITS.fullmatch(declared.strip()):
                raise FetchError(FetchFailure.HTTP_PERMANENT, "malformed Content-Length", http_status=status)
            if int(declared) > cap:  # rejected from the header alone, before any body byte is read
                raise FetchError(
                    FetchFailure.SIZE_EXCEEDED, "declared Content-Length exceeds the cap", quarantine=QuarantineReason.SIZE_EXCEEDED
                )
        encoding = resp.headers.get("content-encoding", "identity").strip().lower()
        if encoding in ("", "identity"):
            decoder = None
        elif encoding in ("gzip", "x-gzip"):
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == "deflate":
            decoder = zlib.decompressobj(zlib.MAX_WBITS)
        else:  # br, zstd, compress, stacked encodings: not decodable under our guards
            raise FetchError(FetchFailure.HTTP_PERMANENT, "unsupported Content-Encoding", quarantine=QuarantineReason.CONTENT_TYPE_MISMATCH)

        spool = tempfile.TemporaryFile(dir=self._spool_dir)  # noqa: SIM115 - ownership passes to FetchedResponse
        try:
            digest = hashlib.sha256()
            size, raw_total = self._stream(resp, spool, digest, decoder, cap, deadline)
            if decoder is None and declared is not None and raw_total != int(declared):
                raise FetchError(
                    FetchFailure.HTTP_PERMANENT,
                    "body shorter than Content-Length (truncated)",
                    quarantine=QuarantineReason.PDF_SANITY_FAILED,
                )
            spool.seek(0)
        except BaseException:
            spool.close()
            raise
        return FetchedResponse(
            first_url, target.url, chain, status, safe_headers(resp.headers), size, digest.hexdigest(), False, now, body=spool
        )

    def _stream(
        self, resp: TransportResponse, spool: BinaryIO, digest: hashlib._Hash, decoder: zlib._Decompress | None, cap: int, deadline: float
    ) -> tuple[int, int]:
        lim = self._limits
        started = self._clock.monotonic()
        size = raw_total = 0

        def emit(data: bytes) -> None:
            nonlocal size
            size += len(data)
            if size > cap:
                raise FetchError(
                    FetchFailure.SIZE_EXCEEDED, "body exceeds the cap while streaming", quarantine=QuarantineReason.SIZE_EXCEEDED
                )
            if decoder is not None and size > lim.max_expansion_ratio * max(raw_total, 1):
                raise FetchError(
                    FetchFailure.SIZE_EXCEEDED, "decompression expansion ratio exceeded", quarantine=QuarantineReason.SIZE_EXCEEDED
                )
            digest.update(data)
            spool.write(data)

        while True:
            if self._clock.monotonic() - started > lim.total_timeout_seconds or self._clock.monotonic() > deadline:
                raise FetchError(FetchFailure.TIMEOUT, "total timeout exceeded while reading body")
            try:
                chunk = resp.read(CHUNK)
            except TransportTruncatedError as exc:
                raise FetchError(FetchFailure.HTTP_PERMANENT, "truncated body", quarantine=QuarantineReason.PDF_SANITY_FAILED) from exc
            except TransportTimeoutError as exc:
                raise FetchError(FetchFailure.TIMEOUT, "read timeout") from exc
            except TransportTlsError as exc:
                raise FetchError(FetchFailure.TLS_ERROR, neutralize(exc)) from exc
            except TransportError as exc:
                raise FetchError(FetchFailure.HTTP_TRANSIENT, neutralize(exc)) from exc
            if not chunk:
                break
            raw_total += len(chunk)
            if decoder is None:
                emit(chunk)
                continue
            try:
                pending = chunk
                while pending:
                    out = decoder.decompress(pending, CHUNK)
                    if out:
                        emit(out)
                    pending = decoder.unconsumed_tail
                    if not out and not pending:
                        break
            except zlib.error as exc:
                raise FetchError(
                    FetchFailure.HTTP_PERMANENT, "corrupt compressed body", quarantine=QuarantineReason.PDF_SANITY_FAILED
                ) from exc
        if decoder is not None and not decoder.eof:
            raise FetchError(FetchFailure.HTTP_PERMANENT, "compressed body truncated", quarantine=QuarantineReason.PDF_SANITY_FAILED)
        return size, raw_total
