"""SECURITY: what discovery may cause the transport to carry. A GuardedTransport fails the test mechanically if it is ever asked for a
viewer wrapper, a foreign host, or anything that was not a validated detail page / attachment of the allowlisted host."""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.domain.ingest import FetchOutcome, FetchPurpose, IngestOutcome, QuarantineReason
from packages.ingestion.errors import FetchError
from packages.ingestion.ports import TransportRequest
from packages.ingestion.repository import InMemoryRepository
from tests.support.builders import HOST
from tests.support.discovery_world import discovery_candidate, file_url, serve_attachment, serve_detail, targets, wrapper
from tests.support.env import Env, one
from tests.support.factories import Rig
from tests.support.fakes import FakeTransport


class WrapperRequested(AssertionError):
    pass


class GuardedTransport(FakeTransport):
    """FakeTransport that raises (an AssertionError, never swallowed by the pipeline's FetchError handling) on forbidden requests."""

    def open(self, request: TransportRequest):  # type: ignore[no-untyped-def]
        if request.host != HOST:
            raise WrapperRequested(f"foreign host requested: {request.host}")
        if request.target.startswith("/web") or "file=" in request.target or "%2F" in request.target.upper():
            raise WrapperRequested(f"viewer wrapper / encoded URL requested: {request.target}")
        return super().open(request)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    e = Env(InMemoryRepository(), tmp_path)
    e.transport = GuardedTransport(e.clock)  # type: ignore[assignment]
    return e


KEY = "TEST-D"


@pytest.mark.parametrize("encode_inner_path", [True, False])
def test_viewer_wrapper_is_never_transported_only_its_validated_inner_url(env: Env, encode_inner_path: bool) -> None:
    ref = wrapper(file_url("doc-d")) if encode_inner_path else f"https://{HOST}/web/?file={file_url('doc-d')}"
    serve_detail(env, KEY, f'<a href="{ref}">view</a>')
    serve_attachment(env, "doc-d")
    report = env.run([discovery_candidate(KEY)])
    assert report.results[0].outcome is IngestOutcome.NEW_VERSION
    assert targets(env) == ["/docs/test-d.html", "/files/doc-d.pdf"]


def test_guarded_transport_really_would_catch_a_wrapper_request(env: Env) -> None:
    """Self-test of the tripwire: it is not vacuous."""
    request = TransportRequest("GET", HOST, "93.184.216.34", 443, "/web/?file=x", {}, 1.0, 1.0)
    with pytest.raises(WrapperRequested):
        env.transport.open(request)


def test_a_manifest_document_url_that_is_a_viewer_wrapper_is_refused_before_transport(env: Env) -> None:
    report = env.run([one("TEST-W", url=wrapper(file_url("x")).replace("%3A", ":").replace("%2F", "/"))])
    assert report.results[0].outcome is IngestOutcome.QUARANTINED
    assert report.results[0].reason_code == QuarantineReason.VIEWER_WRAPPER_URL.value
    assert env.transport.requests == []


def test_the_egress_client_itself_refuses_the_wrapper_with_zero_transport_calls() -> None:
    rig = Rig()
    for purpose in (FetchPurpose.ATTACHMENT, FetchPurpose.DETAIL_PAGE):
        with pytest.raises(FetchError) as info:
            rig.client.fetch(f"https://{HOST}/web/?file=https://{HOST}/files/a.pdf", purpose)
        assert info.value.quarantine is QuarantineReason.VIEWER_WRAPPER_URL
    assert rig.transport.requests == []


def test_ambiguous_discovery_causes_zero_attachment_fetches(env: Env) -> None:
    serve_detail(env, KEY, '<a href="/files/one.pdf">1</a><iframe src="' + wrapper(file_url("two")) + '"></iframe>')
    serve_attachment(env, "one")
    serve_attachment(env, "two")
    report = env.run([discovery_candidate(KEY)])
    assert report.results[0].reason_code == "AMBIGUOUS_ATTACHMENT_REFERENCES"
    assert not any(t.startswith("/files/") for t in targets(env)) and targets(env) == ["/docs/test-d.html"]


@pytest.mark.parametrize(
    "ref",
    [
        "https://attacker.example/files/x.pdf",
        f"http://{HOST}/files/x.pdf",
        f"https://{HOST}:8443/files/x.pdf",
        f"https://{HOST}/files/../private.pdf",
        f"https://{HOST}/files/x.pdf?download=1",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "//attacker.example/files/x.pdf",
        wrapper("https://attacker.example/x.pdf"),
        wrapper(f"http://{HOST}/files/x.pdf"),
    ],
)
def test_an_unsafe_discovered_url_causes_zero_attachment_fetches(env: Env, ref: str) -> None:
    serve_detail(env, KEY, f'<a href="{ref}">x</a>')
    report = env.run([discovery_candidate(KEY)])
    assert report.results[0].outcome is IngestOutcome.UNRESOLVED
    assert targets(env) == ["/docs/test-d.html"]  # only the (validated) detail page; the unsafe link was never followed
    assert env.repo.counts()["raw_artifact"] == 0


def test_discovery_is_not_recursive_the_attachment_is_not_parsed_for_links_and_no_third_page_is_fetched(env: Env) -> None:
    serve_detail(env, KEY, '<a href="/files/doc-d.pdf">x</a><a href="/docs/related.html">related</a>')
    serve_attachment(env, "doc-d", b"%PDF-1.4\n/URI (https://" + HOST.encode() + b"/files/hidden.pdf)\n%%EOF\n")
    env.run([discovery_candidate(KEY)])
    assert targets(env) == ["/docs/test-d.html", "/files/doc-d.pdf"]  # the related page and the PDF's embedded link are never requested


def test_detail_page_fetch_failures_never_trigger_fallback_guessing(env: Env) -> None:
    serve_detail(env, KEY, "", status=503)
    report = env.run([discovery_candidate(KEY)])
    assert report.results[0].outcome is IngestOutcome.FAILED_TRANSIENT
    assert set(targets(env)) == {"/docs/test-d.html"}  # retries hit the same URL only, never a variant
    assert all(r.outcome is not FetchOutcome.OK for r in env.repo.fetch_requests())
