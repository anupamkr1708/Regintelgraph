"""Characterisation of WHERE the time limits are measured (docs/phase1/crawl-safety-parameters.md §3).

These tests pin the CURRENT, documented semantics; they do not assert a wish. In particular `total_timeout_seconds` is measured from
the start of body streaming, not from the start of the request. If that ever changes, these tests must change together with the
contract text and `policy_version` (see the parameter document), never silently.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from packages.domain.ingest import FetchFailure, FetchPurpose
from packages.ingestion.egress import EgressClient
from packages.ingestion.errors import FetchError
from packages.ingestion.ports import TransportRequest, TransportResponse
from tests.support.builders import HOST, PREFIXES, pdf
from tests.support.factories import UA, URL, Rig, limits_with
from tests.support.fakes import FakeClock, Resp, SeededRng

# SMALL_LIMITS: total_timeout_seconds=30, candidate_deadline_seconds=120, max_bytes_attachment=8192


class _SlowBody:
    """A response whose every read() takes `per_read` seconds on the injected clock."""

    def __init__(self, clock: FakeClock, body: bytes, per_read: float, piece: int = 1000) -> None:
        self.status = 200
        self.headers: Mapping[str, str] = {"content-type": "application/pdf"}
        self._clock, self._body, self._per_read, self._piece = clock, body, per_read, piece

    def read(self, size: int) -> bytes:
        self._clock.advance(self._per_read)
        chunk, self._body = self._body[: self._piece], self._body[self._piece :]
        return chunk

    def close(self) -> None:
        pass


class _SlowTransport:
    performs_network = False

    def __init__(self, response: TransportResponse, *, before_body: float = 0.0, clock: FakeClock | None = None) -> None:
        self._response, self._before, self._clock = response, before_body, clock

    def open(self, request: TransportRequest) -> TransportResponse:
        if self._clock is not None:
            self._clock.advance(self._before)  # time spent connecting / waiting for headers
        return self._response


def client_for(rig: Rig, transport: _SlowTransport) -> EgressClient:
    return EgressClient(
        resolver=rig.resolver,
        transport=transport,  # type: ignore[arg-type]
        clock=rig.clock,
        rng=SeededRng(),
        allowed_hosts=(HOST,),
        path_prefixes=PREFIXES,
        listing_urls=(),
        limits=rig.limits,
        user_agent=UA,
    )


def test_time_before_the_body_starts_does_not_count_toward_total_timeout() -> None:
    rig = Rig()
    ok = Resp(200, {"Content-Type": "application/pdf"}, pdf("late-headers"))
    rig.transport.route_fn(HOST, "/files/a.pdf", lambda _r: (rig.clock.advance(50.0), ok)[1])  # 50s > total_timeout (30s), < deadline
    resp = rig.client.fetch(URL, FetchPurpose.ATTACHMENT)  # succeeds: the body clock had not started yet
    assert resp.size > 0
    resp.close()


def test_candidate_deadline_is_absolute_and_does_include_time_before_the_body() -> None:
    rig = Rig(limits_with(max_attempts=1))  # TIMEOUT is retryable; keep the one-shot body from being re-read on attempt 2
    slow = _SlowTransport(_SlowBody(rig.clock, pdf("x"), 0.0), before_body=130.0, clock=rig.clock)  # 130s > candidate deadline (120s)
    with pytest.raises(FetchError) as exc:
        client_for(rig, slow).fetch(URL, FetchPurpose.ATTACHMENT)
    assert exc.value.kind is FetchFailure.TIMEOUT


def test_total_timeout_applies_to_time_spent_streaming_the_body() -> None:
    rig = Rig(limits_with(max_attempts=1))  # TIMEOUT is retryable; keep the one-shot body from being re-read on attempt 2
    body = b"%PDF-1.7\n" + b"x" * 6000
    slow = _SlowTransport(_SlowBody(rig.clock, body, per_read=20.0))  # reads: t=20, t=40 -> the next check sees >30s since streaming began
    with pytest.raises(FetchError) as exc:
        client_for(rig, slow).fetch(URL, FetchPurpose.ATTACHMENT)
    assert exc.value.kind is FetchFailure.TIMEOUT and "while reading body" in exc.value.detail


def test_total_timeout_is_checked_between_reads_so_one_slow_read_can_overshoot_it() -> None:
    """Documented limitation: a single read that takes longer than total_timeout still completes; the check happens after it."""
    rig = Rig(limits_with(max_attempts=1))  # TIMEOUT is retryable; keep the one-shot body from being re-read on attempt 2
    body = pdf("one-slow-read")
    slow = _SlowTransport(_SlowBody(rig.clock, body, per_read=45.0, piece=len(body)))  # whole body arrives in ONE 45s read (> 30s total)
    with pytest.raises(FetchError) as exc:
        client_for(rig, slow).fetch(URL, FetchPurpose.ATTACHMENT)
    assert exc.value.kind is FetchFailure.TIMEOUT  # detected on the NEXT loop iteration (the read that returns b'' to end the body)
