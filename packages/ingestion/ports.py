"""Interfaces (ports) behind which all I/O sits, so every test can use deterministic fakes (H13).

`performs_network` lets a DRY_RUN refuse any component that could touch the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Protocol, runtime_checkable

from packages.domain.content_hash import ContentHash


@runtime_checkable
class Clock(Protocol):
    """Time source. `now()` is UTC wall time (knowledge time); `monotonic()` measures durations; `sleep` may be faked."""

    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class Rng(Protocol):
    """Randomness for jitter and id generation. Production uses `random.SystemRandom`; tests use a seeded fake."""

    def uniform(self, a: float, b: float) -> float: ...
    def randbytes(self, n: int) -> bytes: ...


@runtime_checkable
class Resolver(Protocol):
    """DNS. Returns ALL A/AAAA answers as IP literal strings, or raises. Called only after static URL validation."""

    performs_network: bool

    def resolve(self, host: str) -> Sequence[str]: ...


@dataclass(frozen=True, slots=True)
class TransportRequest:
    """One request to a PINNED, already validated IP. `host` is the original approved hostname (SNI and Host header)."""

    method: str  # GET | HEAD
    host: str
    ip: str
    port: int
    target: str  # path (+ query for the reviewed listing entry points only); never a full URL
    headers: Mapping[str, str]
    connect_timeout: float
    read_timeout: float


class TransportResponse(Protocol):
    status: int
    headers: Mapping[str, str]  # lower-cased names; duplicate headers are joined by the transport

    def read(self, size: int) -> bytes:
        """Return up to `size` bytes; b"" means end of body. May raise TransportTimeoutError."""
        ...

    def close(self) -> None: ...


@runtime_checkable
class HttpTransport(Protocol):
    """Single-request HTTP over TLS to a pinned IP. No redirects, no proxies, certificate verification always on."""

    performs_network: bool

    def open(self, request: TransportRequest) -> TransportResponse: ...


@dataclass(frozen=True, slots=True)
class PutResult:
    content_hash: ContentHash
    relative_path: str
    created: bool  # False: identical bytes were already stored (no-op)


class BlobStore(Protocol):
    """Content-addressed, write-once storage. Paths derive from the validated hash alone."""

    def put(self, content_hash: ContentHash, source: BinaryIO, size: int) -> PutResult: ...
    def exists(self, content_hash: ContentHash) -> bool: ...
    def open_read(self, content_hash: ContentHash) -> BinaryIO: ...
