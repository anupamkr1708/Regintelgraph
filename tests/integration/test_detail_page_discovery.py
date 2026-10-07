"""Detail page -> attachment discovery through the REAL pipeline (fake transport, real stores): purposes, provenance, outcomes."""

from __future__ import annotations

from pathlib import Path

from packages.domain.ingest import FetchPurpose, IngestOutcome, QuarantineReason, RunStatus
from packages.ingestion.repository import InMemoryRepository
from tests.support.builders import HOST
from tests.support.discovery_world import (
    discovery_candidate,
    file_url,
    serve_attachment,
    serve_detail,
    targets,
    wrapper,
)
from tests.support.env import Env, one
from tests.support.fakes import Resp

KEY = "TEST-D"


def test_direct_link_is_discovered_fetched_and_audited_with_the_right_purposes(env: Env) -> None:
    serve_detail(env, KEY, '<html><a href="/docs/nav.html">nav</a><a href="/files/doc-d.pdf">Download</a></html>')
    serve_attachment(env, "doc-d")
    report = env.run([discovery_candidate(KEY)])
    (result,) = report.results
    assert result.outcome is IngestOutcome.NEW_VERSION and report.run.status is RunStatus.COMPLETED
    assert targets(env) == ["/docs/test-d.html", "/files/doc-d.pdf"]  # detail page first, then ONLY the discovered attachment
    requests = env.repo.fetch_requests()
    assert [(r.purpose, r.requested_url) for r in requests] == [
        (FetchPurpose.DETAIL_PAGE, f"https://{HOST}/docs/test-d.html"),
        (FetchPurpose.ATTACHMENT, file_url("doc-d")),
    ]


def test_discovery_provenance_is_kept_in_run_stats(env: Env) -> None:
    serve_detail(env, KEY, '<a href="/files/doc-d.pdf">Download</a>')
    serve_attachment(env, "doc-d")
    report = env.run([discovery_candidate(KEY)])
    (entry,) = report.run.stats["discovery"]  # type: ignore[misc]
    assert entry["candidate_key"] == f"testreg/{KEY}" and entry["status"] == "DISCOVERED"
    assert entry["detail_page_url"] == f"https://{HOST}/docs/test-d.html"
    assert entry["attachment_candidates"] == [{"url": file_url("doc-d"), "references": ["/files/doc-d.pdf"]}]


def test_discovery_provenance_is_kept_in_artifact_metadata(tmp_path: Path) -> None:
    env = Env(InMemoryRepository(), tmp_path)  # the in-memory repository exposes stored versions; the contract is the same on PostgreSQL
    serve_detail(env, KEY, '<a href="/files/doc-d.pdf">Download</a>')
    serve_attachment(env, "doc-d")
    report = env.run([discovery_candidate(KEY)])
    (entry,) = report.run.stats["discovery"]  # type: ignore[misc]
    (version,) = env.repo.versions.values()
    assert version.http_meta["discovery"] == {k: v for k, v in entry.items() if k != "candidate_key"}
    assert list(version.locations) == [file_url("doc-d")]  # the document location is the attachment, not the detail page


def test_wrapper_reference_fetches_only_the_inner_url(env: Env) -> None:
    serve_detail(env, KEY, f'<iframe src="{wrapper(file_url("doc-d"))}"></iframe>')
    serve_attachment(env, "doc-d")
    report = env.run([discovery_candidate(KEY)])
    assert report.results[0].outcome is IngestOutcome.NEW_VERSION
    assert targets(env) == ["/docs/test-d.html", "/files/doc-d.pdf"]
    assert not any("/web/" in t or "file=" in t for t in targets(env))
    assert [r.requested_url for r in env.repo.fetch_requests()] == [f"https://{HOST}/docs/test-d.html", file_url("doc-d")]


def test_ambiguous_page_abstains_and_fetches_no_attachment(env: Env) -> None:
    serve_detail(env, KEY, '<a href="/files/one.pdf">1</a><a href="/files/two.pdf">2</a>')
    serve_attachment(env, "one")
    serve_attachment(env, "two")
    report = env.run([discovery_candidate(KEY)])
    (result,) = report.results
    assert (result.outcome, result.reason_code) == (IngestOutcome.UNRESOLVED, "AMBIGUOUS_ATTACHMENT_REFERENCES")
    assert targets(env) == ["/docs/test-d.html"]
    assert env.repo.counts()["raw_artifact"] == 0
    (entry,) = report.run.stats["discovery"]  # type: ignore[misc]
    assert entry["status"] == "AMBIGUOUS" and entry["attachment_candidates_total"] == 2


def test_page_without_attachment_reference_is_unresolved_and_invents_nothing(env: Env) -> None:
    serve_detail(env, KEY, "<html><body><p>Regulation text only.</p></body></html>")
    report = env.run([discovery_candidate(KEY)])
    (result,) = report.results
    assert (result.outcome, result.reason_code) == (IngestOutcome.UNRESOLVED, "NO_VALID_ATTACHMENT_REFERENCES")
    assert targets(env) == ["/docs/test-d.html"]  # no guessed URL, no fallback, no second request of any kind


def test_unsafe_references_are_diagnosed_but_never_fetched(env: Env) -> None:
    html = '<a href="https://attacker.example/x.pdf">e</a><a href="http://docs.testreg.example/files/x.pdf">h</a>'
    serve_detail(env, KEY, html + f'<a href="{wrapper("https://attacker.example/y.pdf")}">w</a>')
    report = env.run([discovery_candidate(KEY)])
    assert report.results[0].reason_code == "NO_VALID_ATTACHMENT_REFERENCES"
    assert targets(env) == ["/docs/test-d.html"] and {r.host for r in env.transport.requests} == {HOST}
    (entry,) = report.run.stats["discovery"]  # type: ignore[misc]
    assert [r["reason"] for r in entry["rejected_references"]] == ["HOST_NOT_ALLOWED", "SCHEME_NOT_HTTPS", "HOST_NOT_ALLOWED"]


def test_candidates_with_a_document_url_never_fetch_a_detail_page(env: Env) -> None:
    env.serve("TEST-A", None)
    report = env.run([one("TEST-A", canonical_url=f"https://{HOST}/docs/test-a.html")])
    assert report.results[0].outcome is IngestOutcome.NEW_VERSION
    assert targets(env) == ["/files/test-a.pdf"]
    assert "discovery" not in report.run.stats


def test_candidate_with_neither_url_stays_unresolved_without_traffic(env: Env) -> None:
    report = env.run([one("TEST-N", url=None)])
    assert (report.results[0].outcome, report.results[0].reason_code) == (IngestOutcome.UNRESOLVED, "NO_ATTACHMENT_URL_IN_MANIFEST")
    assert env.transport.requests == []


def test_detail_url_outside_the_reviewed_prefixes_is_rejected_before_any_request(env: Env) -> None:
    report = env.run([one("TEST-X", url=None, canonical_url=f"https://{HOST}/elsewhere/page.html")])
    assert report.results[0].outcome is IngestOutcome.QUARANTINED and env.transport.requests == []
    (req,) = env.repo.fetch_requests()
    assert req.purpose is FetchPurpose.DETAIL_PAGE and req.outcome.value == "URL_REJECTED"


def test_non_html_detail_page_is_quarantined_without_discovery(env: Env) -> None:
    serve_detail(env, KEY, b"%PDF-1.4 not a web page", headers={"Content-Type": "application/pdf"})
    report = env.run([discovery_candidate(KEY)])
    assert report.results[0].outcome is IngestOutcome.QUARANTINED
    assert report.results[0].reason_code == QuarantineReason.CONTENT_TYPE_MISMATCH.value
    assert targets(env) == ["/docs/test-d.html"] and "discovery" not in report.run.stats


def test_undecodable_detail_page_is_unresolved_never_lossily_decoded(env: Env) -> None:
    serve_detail(env, KEY, b'<a href="/files/doc-d.pdf">\xff\xfe</a>')
    serve_attachment(env, "doc-d")
    report = env.run([discovery_candidate(KEY)])
    assert (report.results[0].outcome, report.results[0].reason_code) == (IngestOutcome.UNRESOLVED, "DETAIL_PAGE_UNDECODABLE")
    assert targets(env) == ["/docs/test-d.html"]


def test_detail_page_http_error_is_a_failure_and_stops_there(env: Env) -> None:
    env.transport.route(HOST, "/docs/test-d.html", Resp(404, {}, b"missing"))
    report = env.run([discovery_candidate(KEY)])
    assert report.results[0].outcome is IngestOutcome.FAILED_PERMANENT and targets(env) == ["/docs/test-d.html"]
    assert [r.purpose for r in env.repo.fetch_requests()] == [FetchPurpose.DETAIL_PAGE]


def test_discovery_composes_with_direct_attachments_in_one_selection(env: Env) -> None:
    env.serve("TEST-A", None)
    serve_detail(env, KEY, '<a href="/files/doc-d.pdf">x</a>')
    serve_attachment(env, "doc-d")
    cands = [one("TEST-A"), discovery_candidate(KEY, index=1)]
    report = env.run(cands, selected_candidate_keys=("TEST-D", "TEST-A"))
    assert [r.candidate_key for r in report.results] == ["testreg/TEST-D", "testreg/TEST-A"]
    assert targets(env) == ["/docs/test-d.html", "/files/doc-d.pdf", "/files/test-a.pdf"]
