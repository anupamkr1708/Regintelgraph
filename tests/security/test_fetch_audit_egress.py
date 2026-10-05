"""The per-attempt audit hook of the egress client: ordering, fail-closed behaviour, timing semantics, completeness."""

from __future__ import annotations

import pytest

from packages.domain.ingest import FetchFailure, FetchOutcome, FetchPurpose
from packages.ingestion.egress import AttemptAudit
from packages.ingestion.errors import CircuitOpenError, FetchError, TransportConnectError, TransportTimeoutError, TransportTlsError
from tests.support.builders import HOST, pdf
from tests.support.factories import URL, Rig, limits_with
from tests.support.fakes import Resp

OK = Resp(200, {"Content-Type": "application/pdf"}, pdf("ok"))


def collect(rig: Rig) -> tuple[list[AttemptAudit], list[int]]:
    """An observer plus a list of 'transport requests seen so far' at the moment each audit was delivered."""
    audits: list[AttemptAudit] = []
    seen: list[int] = []

    def observe(a: AttemptAudit) -> None:
        audits.append(a)
        seen.append(len(rig.transport.requests))

    rig.observe = observe  # type: ignore[attr-defined]
    return audits, seen


def test_the_observer_is_called_after_every_attempt_before_the_next_request() -> None:
    rig = Rig()
    audits, seen = collect(rig)
    rig.transport.route(HOST, "/files/a.pdf", Resp(503), Resp(502), OK)
    resp = rig.client.fetch(URL, FetchPurpose.ATTACHMENT, on_attempt=rig.observe)  # type: ignore[attr-defined]
    resp.close()
    assert [a.attempt for a in audits] == [1, 2, 3] and seen == [1, 2, 3]  # durable BEFORE the retry went out
    assert [a.outcome for a in audits] == [FetchOutcome.HTTP_TRANSIENT, FetchOutcome.HTTP_TRANSIENT, FetchOutcome.OK]


def test_an_observer_that_fails_aborts_the_fetch_fail_closed() -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", Resp(503), OK)

    def broken(_: AttemptAudit) -> None:
        raise RuntimeError("audit store unavailable")

    with pytest.raises(RuntimeError, match="audit store unavailable"):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT, on_attempt=broken)
    assert len(rig.transport.requests) == 1  # no un-audited follow-up request was made


def test_no_observer_means_unchanged_behaviour() -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", OK)
    resp = rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert resp.attempts == 1
    resp.close()


@pytest.mark.parametrize(
    ("failure", "outcome"),
    [
        (TransportTimeoutError("t"), FetchOutcome.TIMEOUT),
        (TransportTlsError("t"), FetchOutcome.TLS_ERROR),
        (TransportConnectError("c"), FetchOutcome.HTTP_TRANSIENT),
    ],
)
def test_transport_failures_are_audited_without_a_status(failure: Exception, outcome: FetchOutcome) -> None:
    rig = Rig(limits_with(max_attempts=1))
    audits, _ = collect(rig)
    rig.transport.route(HOST, "/files/a.pdf", Resp(raises_on_open=failure))
    with pytest.raises(FetchError):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT, on_attempt=rig.observe)  # type: ignore[attr-defined]
    (a,) = audits
    assert (a.outcome, a.status, a.content_hash) == (outcome, None, None) and a.requested_url == URL


def test_a_url_rejected_at_hop_zero_is_audited_in_log_safe_form() -> None:
    rig = Rig()
    audits, _ = collect(rig)
    with pytest.raises(FetchError):
        hostile = f"https://u:p@{HOST}/files/a.pdf?x=1#f"  # rig:allow-secret: synthetic userinfo URL (the thing under test)
        rig.client.fetch(hostile, FetchPurpose.ATTACHMENT, on_attempt=rig.observe)  # type: ignore[attr-defined]
    (a,) = audits
    assert a.outcome is FetchOutcome.URL_REJECTED and a.status is None and a.final_url is None and rig.transport.requests == []
    assert all(s not in a.requested_url for s in ("u:p", "x=1", "#f"))


def test_a_rejected_redirect_target_is_recorded_only_as_the_location_header() -> None:
    rig = Rig()
    audits, _ = collect(rig)
    rig.transport.route(HOST, "/files/a.pdf", Resp(302, {"Location": "https://evil.example/files/a.pdf"}))
    with pytest.raises(FetchError):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT, on_attempt=rig.observe)  # type: ignore[attr-defined]
    (a,) = audits
    assert (a.outcome, a.status, a.requested_url, a.final_url) == (FetchOutcome.REDIRECT_REJECTED, 302, URL, URL)
    # The rejected target was never requested and is not part of the chain; it survives only as what the server SAID, in the
    # allowlisted `location` response header (neutralised, length-bounded) — useful provenance for a human reviewing the block.
    assert a.redirect_chain == (URL,) and len(rig.transport.requests) == 1
    assert dict(a.headers) == {"location": "https://evil.example/files/a.pdf"}


def test_the_circuit_trip_is_audited_with_the_refusal_status() -> None:
    rig = Rig(limits_with(circuit_breaker_threshold=1))
    audits, _ = collect(rig)
    rig.transport.route(HOST, "/files/a.pdf", Resp(429, {"Retry-After": "30"}))
    with pytest.raises(CircuitOpenError):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT, on_attempt=rig.observe)  # type: ignore[attr-defined]
    (a,) = audits
    assert (a.outcome, a.status, dict(a.headers)) == (FetchOutcome.CIRCUIT_OPEN, 429, {"retry-after": "30"})


# ---- timing semantics: elapsed_seconds = time holding the host request slot, summed over hops, excluding pacing waits ----------------


def test_elapsed_counts_time_inside_the_request_slot() -> None:
    rig = Rig()
    audits, _ = collect(rig)
    rig.transport.route_fn(HOST, "/files/a.pdf", lambda _req: (rig.clock.advance(5.0), OK)[1])
    rig.client.fetch(URL, FetchPurpose.ATTACHMENT, on_attempt=rig.observe).close()  # type: ignore[attr-defined]
    assert audits[0].elapsed_seconds == pytest.approx(5.0)


def test_elapsed_excludes_the_politeness_wait_between_requests() -> None:
    rig = Rig()  # min_delay_seconds = 1.0: the second request sleeps ~1s before it may start
    audits, _ = collect(rig)
    rig.transport.route(HOST, "/files/a.pdf", OK)
    rig.client.fetch(URL, FetchPurpose.ATTACHMENT, on_attempt=rig.observe).close()  # type: ignore[attr-defined]
    rig.transport.route(HOST, "/files/a.pdf", OK)
    rig.client.fetch(URL, FetchPurpose.ATTACHMENT, on_attempt=rig.observe).close()  # type: ignore[attr-defined]
    assert rig.clock.sleeps and [a.elapsed_seconds for a in audits] == [0.0, 0.0]


def test_elapsed_accumulates_across_redirect_hops() -> None:
    rig = Rig()
    audits, _ = collect(rig)
    other = f"https://{HOST}/files/b.pdf"
    rig.transport.route_fn(HOST, "/files/a.pdf", lambda _req: (rig.clock.advance(2.0), Resp(302, {"Location": other}))[1])
    rig.transport.route_fn(HOST, "/files/b.pdf", lambda _req: (rig.clock.advance(3.0), OK)[1])
    rig.client.fetch(URL, FetchPurpose.ATTACHMENT, on_attempt=rig.observe).close()  # type: ignore[attr-defined]
    assert audits[0].elapsed_seconds == pytest.approx(5.0) and audits[0].redirect_chain == (URL, other)


# ---- completeness ---------------------------------------------------------------------------------------------------------------


def test_every_failure_class_that_can_make_a_request_has_an_audit_outcome() -> None:
    failures = {f.value for f in FetchFailure}
    outcomes = {o.value for o in FetchOutcome}
    assert failures - outcomes == {"GATE_CLOSED"}  # a closed gate performs no request, so it has no row by design
    assert outcomes - failures == {"OK", "NOT_MODIFIED", "CIRCUIT_OPEN"}


def test_the_audit_never_carries_request_headers_or_the_user_agent() -> None:
    rig = Rig()
    audits, _ = collect(rig)
    rig.transport.route(HOST, "/files/a.pdf", OK)
    rig.client.fetch(URL, FetchPurpose.ATTACHMENT, on_attempt=rig.observe).close()  # type: ignore[attr-defined]
    assert "test@testreg.example" not in repr(audits) and "RegIntelGraph-ingest" not in repr(audits)
