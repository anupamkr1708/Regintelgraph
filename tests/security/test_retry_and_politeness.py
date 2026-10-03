from __future__ import annotations

import itertools
import threading
import time
from datetime import timedelta

import pytest

from packages.domain.ingest import FetchFailure, FetchPurpose
from packages.ingestion.egress import HostPacer, backoff_ceiling, build_user_agent, parse_retry_after
from packages.ingestion.errors import CircuitOpenError, FetchError, TransportConnectError, TransportTimeoutError, TransportTlsError
from tests.support.builders import HOST, pdf
from tests.support.factories import URL, Rig, limits_with
from tests.support.fakes import FakeClock, Resp, SeededRng

OK = Resp(200, {"Content-Type": "application/pdf"}, pdf("ok"))


def test_transient_failures_are_retried_and_counted() -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", Resp(503), Resp(502), OK)
    resp = rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert resp.attempts == 3 and len(rig.transport.requests) == 3
    resp.close()


def test_retries_are_bounded_by_max_attempts() -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", Resp(503))
    with pytest.raises(FetchError) as exc:
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert exc.value.kind is FetchFailure.HTTP_TRANSIENT and exc.value.attempts == 3 and len(rig.transport.requests) == 3


@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        (TransportTimeoutError("t"), FetchFailure.TIMEOUT),
        (TransportTlsError("t"), FetchFailure.TLS_ERROR),
        (TransportConnectError("c"), FetchFailure.HTTP_TRANSIENT),
    ],
)
def test_timeout_tls_and_connect_errors_are_the_retryable_categories(failure: Exception, kind: FetchFailure) -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", Resp(raises_on_open=failure), OK)
    resp = rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert resp.attempts == 2
    resp.close()
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", Resp(raises_on_open=failure))
    with pytest.raises(FetchError) as exc:
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert exc.value.kind is kind and exc.value.attempts == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_permanent_statuses_are_never_retried(status: int) -> None:
    rig = Rig(limits_with(circuit_breaker_threshold=99))
    rig.transport.route(HOST, "/files/a.pdf", Resp(status), OK)
    with pytest.raises(FetchError) as exc:
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert exc.value.kind is FetchFailure.HTTP_PERMANENT and len(rig.transport.requests) == 1


def test_403_is_not_worked_around_by_changing_identity_url_or_host() -> None:
    rig = Rig(limits_with(circuit_breaker_threshold=99))
    rig.transport.route(HOST, "/files/a.pdf", Resp(403))
    for _ in range(2):
        with pytest.raises(FetchError):
            rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    reqs = rig.transport.requests
    assert len(reqs) == 2
    assert len({r.headers["User-Agent"] for r in reqs}) == 1  # identity never rotated
    assert {r.target for r in reqs} == {"/files/a.pdf"} and {r.host for r in reqs} == {HOST}  # URL and host never altered
    assert all(not {"cookie", "authorization"} & {k.lower() for k in r.headers} for r in reqs)


def test_retry_after_seconds_is_honoured_beyond_the_backoff_cap() -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", Resp(429, {"Retry-After": "30"}), OK)
    rig.client.fetch(URL, FetchPurpose.ATTACHMENT).close()
    assert rig.clock.sleeps and rig.clock.sleeps[0] >= 30  # cap on our own backoff is 8s: the server's request wins


def test_retry_after_http_date_is_honoured() -> None:
    rig = Rig()
    when = (rig.clock._now + timedelta(seconds=45)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    rig.transport.route(HOST, "/files/a.pdf", Resp(503, {"Retry-After": when}), OK)
    rig.client.fetch(URL, FetchPurpose.ATTACHMENT).close()
    assert rig.clock.sleeps[0] >= 40


def test_retry_after_longer_than_the_candidate_deadline_stops_without_sleeping() -> None:
    rig = Rig(limits_with(candidate_deadline_seconds=60))
    rig.transport.route(HOST, "/files/a.pdf", Resp(429, {"Retry-After": "3600"}), OK)
    with pytest.raises(FetchError):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert rig.clock.sleeps == [] and len(rig.transport.requests) == 1


@pytest.mark.parametrize("value", [None, "", "abc", "-5", "1.5", "Mon, 99 Foo 2026", "12345678901234567890"])
def test_unparseable_retry_after_is_ignored_not_trusted(value: str | None) -> None:
    now = FakeClock().now()
    parsed = parse_retry_after(value, now)
    assert parsed is None or (value is not None and value.isdigit())


def test_backoff_is_exponential_capped_and_monotone() -> None:
    ceilings = [backoff_ceiling(a, 1.0, 8.0) for a in range(1, 9)]
    assert ceilings == [1, 2, 4, 8, 8, 8, 8, 8] and ceilings == sorted(ceilings)


def test_observed_sleeps_follow_backoff_order_with_max_jitter() -> None:
    rig = Rig(limits_with(max_attempts=6), rng=SeededRng(fixed_uniform=1.0))
    rig.transport.route(HOST, "/files/a.pdf", Resp(503))
    with pytest.raises(FetchError):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert rig.clock.sleeps == [1, 2, 4, 8, 8]


def test_jitter_never_goes_below_the_minimum_delay() -> None:
    rig = Rig(limits_with(max_attempts=4), rng=SeededRng(fixed_uniform=0.0))
    rig.transport.route(HOST, "/files/a.pdf", Resp(503))
    with pytest.raises(FetchError):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert all(s >= 1.0 for s in rig.clock.sleeps)


def test_429_is_bounded_and_trips_the_circuit_breaker_at_the_threshold() -> None:
    rig = Rig(limits_with(circuit_breaker_threshold=99))
    rig.transport.route(HOST, "/files/a.pdf", Resp(429))
    with pytest.raises(FetchError):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert len(rig.transport.requests) == 3  # bounded by max_attempts
    rig = Rig()  # threshold 3: three consecutive refusals open the circuit
    rig.transport.route(HOST, "/files/a.pdf", Resp(429))
    with pytest.raises(CircuitOpenError):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert len(rig.transport.requests) == 3


def test_circuit_breaker_counts_403_across_candidates_and_resets_on_success() -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", Resp(403))
    rig.transport.route(HOST, "/files/ok.pdf", OK)
    for _ in range(2):
        with pytest.raises(FetchError):
            rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    rig.client.fetch(f"https://{HOST}/files/ok.pdf", FetchPurpose.ATTACHMENT).close()  # success resets the streak
    for _ in range(2):
        with pytest.raises(FetchError):
            rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    with pytest.raises(CircuitOpenError):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT)


def test_minimum_delay_between_request_starts_via_the_fake_clock() -> None:
    rig = Rig(limits_with(min_delay_seconds=5.0))
    rig.transport.route(HOST, "/files/a.pdf", OK)
    for _ in range(3):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT).close()
    t = rig.transport.request_times
    assert all(b - a >= 5.0 for a, b in itertools.pairwise(t)) and len(t) == 3


def test_pacer_allows_one_in_flight_request_per_host() -> None:
    pacer = HostPacer(FakeClock(), 0.0)
    active = {"n": 0, "max": 0}
    lock = threading.Lock()

    def worker() -> None:
        with pacer.slot("h"):
            with lock:
                active["n"] += 1
                active["max"] = max(active["max"], active["n"])
            time.sleep(0.02)
            with lock:
                active["n"] -= 1

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert active["max"] == 1


def test_user_agent_is_honest_and_header_injection_is_impossible() -> None:
    ua = build_user_agent("ops@testreg.example")
    assert "RegIntelGraph" in ua and "ops@testreg.example" in ua
    for bad in ["", "a b", "x\r\nHost: evil", "a\nb", "<x@y>", "x" * 400, "a\x00b"]:
        with pytest.raises(ValueError):
            build_user_agent(bad)


def test_requests_carry_identity_encoding_and_no_ambient_credentials() -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", OK)
    rig.client.fetch(URL, FetchPurpose.ATTACHMENT).close()
    (req,) = rig.transport.requests
    names = {k.lower() for k in req.headers}
    assert req.method == "GET" and req.headers["Accept-Encoding"] == "identity"
    assert names.isdisjoint({"cookie", "authorization", "proxy-authorization"}) and req.port == 443
