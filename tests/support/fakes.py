"""Deterministic test doubles: clock, resolver, transport, blob store, seeded rng, and network tripwires (H13).

Nothing here touches the network, the real clock or real randomness. Fixtures are SYNTHETIC (TESTREG), never real SEBI data.
"""

from __future__ import annotations

import io
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import BinaryIO

from packages.domain.content_hash import ContentHash
from packages.ingestion.errors import TransportError, TransportTruncatedError
from packages.ingestion.ports import PutResult, TransportRequest, TransportResponse

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeClock:
    """Wall + monotonic time that only moves when `sleep` is called (or `advance`). Tests never actually wait."""

    def __init__(self, start: datetime = T0) -> None:
        self._now = start
        self._mono = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        self._now += timedelta(microseconds=1)  # strictly increasing instants (ids, last_seen ordering)
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._mono += seconds
        self._now += timedelta(seconds=seconds)


class SeededRng:
    def __init__(self, seed: int = 7, *, fixed_uniform: float | None = None) -> None:
        self._r = random.Random(seed)
        self._fixed = fixed_uniform

    def uniform(self, a: float, b: float) -> float:
        return b if self._fixed == 1.0 else (self._fixed if self._fixed is not None else self._r.uniform(a, b))

    def randbytes(self, n: int) -> bytes:
        return self._r.randbytes(n)


class FakeResolver:
    """host -> answers. A list of lists is consumed one entry per call (DNS rebinding); an Exception is raised."""

    performs_network = False

    def __init__(self, table: Mapping[str, object] | None = None, default: Sequence[str] = ("93.184.216.34",)) -> None:
        self.table = dict(table or {})
        self.default = tuple(default)
        self.calls: list[str] = []

    def resolve(self, host: str) -> Sequence[str]:
        self.calls.append(host)
        entry = self.table.get(host, self.default)
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, list) and entry and isinstance(entry[0], list | tuple):
            seq = list(entry)
            chosen = seq.pop(0) if len(seq) > 1 else seq[0]
            self.table[host] = seq
            return list(chosen)
        return list(entry)  # type: ignore[call-overload,no-any-return]


@dataclass
class Resp:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    truncate: bool = False  # body ends early: the transport raises TransportTruncatedError after delivering `body`
    raises_on_open: Exception | None = None


class _FakeResponse:
    def __init__(self, spec: Resp) -> None:
        self.status = spec.status
        self.headers: Mapping[str, str] = {k.lower(): v for k, v in spec.headers.items()}
        self._buf = io.BytesIO(spec.body)
        self._truncate = spec.truncate
        self.closed = False

    def read(self, size: int) -> bytes:
        data = self._buf.read(size)
        if not data and self._truncate:
            raise TransportTruncatedError("truncated")
        return data

    def close(self) -> None:
        self.closed = True


Handler = Callable[[TransportRequest], Resp]


class FakeTransport:
    """Scripted per (host, target). A list is consumed in order (last one repeats); a callable computes the response."""

    performs_network = False

    def __init__(self, clock: FakeClock | None = None) -> None:
        self._script: dict[tuple[str, str], list[Resp] | Handler] = {}
        self.requests: list[TransportRequest] = []
        self.request_times: list[float] = []
        self._clock = clock
        self.fallback = Resp(404)

    def route(self, host: str, target: str, *responses: Resp) -> FakeTransport:
        self._script[(host, target)] = list(responses)
        return self

    def route_fn(self, host: str, target: str, fn: Handler) -> FakeTransport:
        self._script[(host, target)] = fn
        return self

    def open(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        if self._clock is not None:
            self.request_times.append(self._clock.monotonic())
        entry = self._script.get((request.host, request.target))
        spec = self.fallback if entry is None else (entry(request) if callable(entry) else (entry.pop(0) if len(entry) > 1 else entry[0]))
        if spec.raises_on_open is not None:
            raise spec.raises_on_open
        return _FakeResponse(spec)


class NetworkTouched(BaseException):
    """Raised by a tripwire double: something tried to use the network when it must not."""


class TripwireResolver:
    """Stands in for a REAL resolver (`performs_network=True`); any call fails the test, loudly."""

    performs_network = True

    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, host: str) -> Sequence[str]:
        self.calls += 1
        raise NetworkTouched(f"resolver used for {host}")


class TripwireTransport:
    performs_network = True

    def __init__(self) -> None:
        self.calls = 0

    def open(self, request: TransportRequest) -> TransportResponse:
        self.calls += 1
        raise NetworkTouched(f"transport used for {request.host}")


class FakeBlobStore:
    """In-memory write-once store with the same semantics as FsBlobStore (no paths involved)."""

    persistent = False

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put(self, content_hash: ContentHash, source: BinaryIO, size: int) -> PutResult:
        data = source.read()
        import hashlib

        if hashlib.sha256(data).hexdigest() != content_hash.value or len(data) != size:
            raise TransportError("fake store: bytes do not match the hash")
        created = content_hash.value not in self.blobs
        if not created and self.blobs[content_hash.value] != data:
            raise AssertionError("collision")
        self.blobs[content_hash.value] = data
        return PutResult(content_hash, content_hash.relative_path, created)

    def exists(self, content_hash: ContentHash) -> bool:
        return content_hash.value in self.blobs

    def open_read(self, content_hash: ContentHash) -> BinaryIO:
        return io.BytesIO(self.blobs[content_hash.value])
