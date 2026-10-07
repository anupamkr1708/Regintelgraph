"""Pure detail-page discovery (packages/ingestion/discovery.py), driven by the REAL canonical validator (urlpolicy.validate_url)."""

from __future__ import annotations

from urllib.parse import quote

import pytest

from packages.domain.ingest import FetchPurpose
from packages.ingestion.discovery import (
    AUDIT_LIST_LIMIT,
    AUDIT_VALUE_MAX,
    DiscoveryStatus,
    discover_attachment_urls,
    observe_references,
)
from packages.ingestion.urlpolicy import validate_url
from tests.support.builders import HOST, PREFIXES

BASE = f"https://{HOST}/docs/detail-page.html"
PDF = f"https://{HOST}/files/example.pdf"
PDF2 = f"https://{HOST}/files/other.pdf"
MAX_URL = 200


class Recorder:
    """The canonical validator behind a call recorder: proves what discovery submitted for validation."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        return validate_url(url, FetchPurpose.ATTACHMENT, allowed_hosts=(HOST,), path_prefixes=PREFIXES, max_url_length=MAX_URL).url


def run(html: str) -> tuple[object, Recorder]:
    rec = Recorder()
    return discover_attachment_urls(html, base_url=BASE, check=rec), rec


def urls(result: object) -> list[str]:
    return [c.url for c in result.candidates]  # type: ignore[attr-defined]


def wrapper(inner: str, *, encode: bool = True) -> str:
    return f"https://{HOST}/web/?file=" + (quote(inner, safe="") if encode else inner)


# ---- observed references ------------------------------------------------------------------------------------------------------------


def test_absolute_link_is_discovered() -> None:
    r, _ = run(f'<p><a href="{PDF}">PDF</a></p>')
    assert r.status is DiscoveryStatus.DISCOVERED and urls(r) == [PDF]  # type: ignore[attr-defined]
    assert r.candidates[0].references == (PDF,)  # type: ignore[attr-defined]


def test_relative_links_are_resolved_against_the_page_url_only() -> None:
    for ref in ("/files/example.pdf", "../files/example.pdf", f"//{HOST}/files/example.pdf"):
        r, rec = run(f'<a href="{ref}">x</a>')
        assert urls(r) == [PDF], ref
        assert rec.calls == [PDF], ref  # submitted for validation in resolved absolute form; nothing else was built


@pytest.mark.parametrize("tag", ['<iframe src="{u}"></iframe>', '<embed src="{u}">', '<object data="{u}"></object>', '<a href="{u}">x</a>'])
def test_supported_url_bearing_elements(tag: str) -> None:
    r, _ = run(tag.format(u=PDF))
    assert urls(r) == [PDF]


@pytest.mark.parametrize(
    "html",
    [
        f'<img src="{PDF}">',
        f'<script src="{PDF}"></script>',
        f'<link rel="x" href="{PDF}">',
        f'<form action="{PDF}"></form>',
        f'<a data-href="{PDF}" ping="{PDF}" onclick="location=\'{PDF}\'">x</a>',
        f"<!-- <a href='{PDF}'>hidden in a comment</a> -->",
        f'<script>var l = "<a href=\\"{PDF}\\">js</a>";</script>',
        f'<style>a[href="{PDF}"] {{ color: red }}</style>',
        f"<p>{PDF}</p>",
    ],
)
def test_only_the_supported_attributes_are_read_and_nothing_is_executed_or_scraped_from_text(html: str) -> None:
    r, rec = run(html)
    assert r.status is DiscoveryStatus.UNRESOLVED and rec.calls == []  # type: ignore[attr-defined]


def test_base_element_is_ignored() -> None:
    r, rec = run('<base href="https://evil.example/"><a href="/files/example.pdf">x</a>')
    assert urls(r) == [PDF] and rec.calls == [PDF]


def test_html_entities_in_attributes_are_decoded_and_whitespace_trimmed() -> None:
    r, _ = run('<a href="\n  /files/example.pdf \t">x</a>')
    assert urls(r) == [PDF]
    assert [x.raw for x in observe_references('<a href="/files/a&#46;pdf">x</a>')] == ["/files/a.pdf"]


def test_first_duplicate_attribute_wins() -> None:
    r, _ = run(f'<a href="{PDF}" href="{PDF2}">x</a>')
    assert urls(r) == [PDF]


# ---- classification -----------------------------------------------------------------------------------------------------------------


def test_zero_references_is_unresolved_and_nothing_is_invented() -> None:
    r, rec = run("<html><body><h1>Regulation</h1><p>No attachment here.</p></body></html>")
    assert r.status is DiscoveryStatus.UNRESOLVED and r.candidates == () and r.references_observed == 0  # type: ignore[attr-defined]
    assert rec.calls == []  # no URL was ever constructed or submitted


def test_only_non_attachment_links_is_unresolved_with_diagnostics() -> None:
    r, _ = run('<a href="/docs/other.html">nav</a><a href="https://elsewhere.example/x.pdf">ext</a><a href="mailto:a@b.c">m</a>')
    assert r.status is DiscoveryStatus.UNRESOLVED and r.candidates == ()  # type: ignore[attr-defined]
    assert [x.reason for x in r.rejected] == ["MANIFEST_SCOPE_MISMATCH", "HOST_NOT_ALLOWED", "SCHEME_NOT_HTTPS"]  # type: ignore[attr-defined]


def test_two_distinct_valid_attachments_are_ambiguous_with_no_tiebreak() -> None:
    r, _ = run(f'<a href="{PDF2}">b</a><a href="{PDF}">a</a>')
    assert r.status is DiscoveryStatus.AMBIGUOUS  # type: ignore[attr-defined]
    assert urls(r) == [PDF2, PDF]  # document order is reported; it is NOT a selection rule


def test_duplicate_references_to_one_url_collapse_and_keep_every_raw_reference() -> None:
    inner = wrapper(PDF)
    r, _ = run(f'<a href="{PDF}">a</a><a href="/files/example.pdf">b</a><iframe src="{inner}"></iframe>')
    assert r.status is DiscoveryStatus.DISCOVERED and urls(r) == [PDF]  # type: ignore[attr-defined]
    assert r.candidates[0].references == (PDF, "/files/example.pdf", inner)  # type: ignore[attr-defined]


def test_an_unsafe_link_never_becomes_a_candidate_next_to_a_valid_one() -> None:
    r, _ = run(f'<a href="https://evil.example/files/x.pdf">x</a><a href="{PDF}">ok</a>')
    assert r.status is DiscoveryStatus.DISCOVERED and urls(r) == [PDF]  # type: ignore[attr-defined]
    assert [x.reason for x in r.rejected] == ["HOST_NOT_ALLOWED"]  # type: ignore[attr-defined]


# ---- unsafe URLs are rejected by the canonical validator, never repaired ------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "reason"),
    [
        ("https://evil.example/files/x.pdf", "HOST_NOT_ALLOWED"),
        (f"https://{HOST}.evil.example/files/x.pdf", "HOST_NOT_ALLOWED"),
        (
            "https://" + "u" + ":" + "p" + "@" + f"{HOST}/files/x.pdf",
            "HOST_NOT_ALLOWED",
        ),  # userinfo, built so no credential literal sits in source
        (f"https://{HOST}:8443/files/x.pdf", "HOST_NOT_ALLOWED"),
        ("https://127.0.0.1/files/x.pdf", "HOST_NOT_ALLOWED"),
        (f"http://{HOST}/files/x.pdf", "SCHEME_NOT_HTTPS"),
        ("javascript:alert(1)", "SCHEME_NOT_HTTPS"),
        ("data:application/pdf;base64,AAAA", "SCHEME_NOT_HTTPS"),
        ("file:///etc/passwd", "SCHEME_NOT_HTTPS"),
        ("ftp://docs.testreg.example/files/x.pdf", "SCHEME_NOT_HTTPS"),
        (f"https://{HOST}/other/x.pdf", "MANIFEST_SCOPE_MISMATCH"),
        (f"https://{HOST}/files/../secret.pdf", "MANIFEST_SCOPE_MISMATCH"),
        (f"https://{HOST}/files/%2e%2e/x.pdf", "MANIFEST_SCOPE_MISMATCH"),
        (f"https://{HOST}/files/a%2Fb.pdf", "MANIFEST_SCOPE_MISMATCH"),
        (f"https://{HOST}/files/x.pdf?download=1", "MANIFEST_SCOPE_MISMATCH"),
        (f"https://{HOST}/files/" + "a" * 300 + ".pdf", "MANIFEST_SCOPE_MISMATCH"),
    ],
)
def test_unsafe_references_are_rejected_with_the_validator_reason(ref: str, reason: str) -> None:
    r, _ = run(f'<a href="{ref}">x</a>')
    assert r.status is DiscoveryStatus.UNRESOLVED and r.candidates == ()  # type: ignore[attr-defined]
    assert [x.reason for x in r.rejected] == [reason]  # type: ignore[attr-defined]


def test_malformed_url_is_rejected_deterministically_not_raised() -> None:
    r, _ = run('<a href="https://[::1/files/x.pdf">x</a>')
    assert r.status is DiscoveryStatus.UNRESOLVED and len(r.rejected) == 1  # type: ignore[attr-defined]


# ---- viewer wrapper: only the observed embedded URL can ever become a candidate ------------------------------------------------------


@pytest.mark.parametrize("encode", [True, False])
def test_wrapper_yields_only_the_validated_inner_url(encode: bool) -> None:
    w = wrapper(PDF, encode=encode)
    r, rec = run(f'<iframe src="{w}"></iframe>')
    assert r.status is DiscoveryStatus.DISCOVERED and urls(r) == [PDF]  # type: ignore[attr-defined]
    assert r.candidates[0].references == (w,)  # type: ignore[attr-defined]  # the observed wrapper reference is kept as provenance
    assert all("/web/" not in c.url for c in r.candidates)  # type: ignore[attr-defined]
    assert rec.calls == [w, PDF]  # the wrapper was offered to the validator only to be recognised; the candidate is the inner URL


def test_relative_wrapper_reference_is_recognised_by_the_canonical_validator() -> None:
    r, _ = run(f'<a href="/web/?file={quote(PDF, safe="")}">x</a>')
    assert urls(r) == [PDF]


def test_wrapper_with_an_unsafe_embedded_host_is_rejected_without_falling_back_to_the_wrapper() -> None:
    r, _ = run(f'<a href="{wrapper("https://attacker.example/document.pdf")}">x</a>')
    assert r.status is DiscoveryStatus.UNRESOLVED and r.candidates == ()  # type: ignore[attr-defined]
    assert [(x.reason, x.via_viewer) for x in r.rejected] == [("HOST_NOT_ALLOWED", True)]  # type: ignore[attr-defined]


def test_wrapper_with_embedded_path_outside_the_reviewed_prefixes_is_rejected() -> None:
    r, _ = run(f'<a href="{wrapper(f"https://{HOST}/private/x.pdf")}">x</a>')
    assert [(x.reason, x.via_viewer) for x in r.rejected] == [("MANIFEST_SCOPE_MISMATCH", True)]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ("", "MANIFEST_SCOPE_MISMATCH"),  # no `file=`: not a wrapper per the canonical validator, just an out-of-scope path
        ("other=1", "MANIFEST_SCOPE_MISMATCH"),
        ("file=", "VIEWER_FILE_PARAMETER_MISSING"),
        (f"file={quote(PDF, safe='')}&file={quote(PDF2, safe='')}", "VIEWER_FILE_PARAMETER_REPEATED"),
        ("file=https%3A%2F%2Fdocs.testreg.example%2Ffiles%2Fx%zz.pdf", "MALFORMED_PERCENT_ENCODING"),
        ("file=%ff%fe", "MALFORMED_PERCENT_ENCODING"),
        ("file=%", "MALFORMED_PERCENT_ENCODING"),
        (f"file={quote(wrapper(PDF), safe='')}", "VIEWER_WRAPPER_URL"),  # one level only: no recursion
        ("file=/files/example.pdf", "SCHEME_NOT_HTTPS"),  # a relative embedded value is rejected, never resolved or guessed
    ],
)
def test_malformed_or_hostile_wrappers_are_rejected_deterministically(query: str, reason: str) -> None:
    r, _ = run(f'<a href="https://{HOST}/web/?{query}">x</a>')
    assert r.status is DiscoveryStatus.UNRESOLVED and r.candidates == ()  # type: ignore[attr-defined]
    assert [x.reason for x in r.rejected] == [reason]  # type: ignore[attr-defined]


def test_double_encoded_inner_url_is_rejected() -> None:
    r, _ = run(f'<a href="{wrapper(quote(PDF, safe=""))}">x</a>')
    assert r.candidates == ()  # type: ignore[attr-defined]


# ---- malformed HTML ----------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "html",
    [
        f'<a href="{PDF}"',
        f'<a href="{PDF}',
        f"<a href='{PDF}'<<>>",
        "<a href>",
        "<a href=>",
        "<<<>>><a",
        '<a href="\x00\x01">',
        "</a></a><iframe",
        f'<a href=">"{PDF}>',
        "&#xZZZZ;<a href='&#99999999999;'>",
    ],
)
def test_malformed_html_never_raises_and_never_invents_a_link(html: str) -> None:
    r, _ = run(html)
    assert r.status in (DiscoveryStatus.UNRESOLVED, DiscoveryStatus.DISCOVERED)  # type: ignore[attr-defined]
    assert set(urls(r)) <= {PDF}  # at most the one URL that is literally present in the input; never anything else


def test_valid_references_survive_surrounding_garbage() -> None:
    r, _ = run(f'<<<>>> <b ></b> <a href="{PDF}">ok</a> <a href= </p></div><div')
    assert urls(r) == [PDF]


def test_output_is_deterministic() -> None:
    html = f'<a href="{PDF2}">b</a><a href="{PDF}">a</a><a href="https://evil.example/x">e</a>'
    assert run(html)[0] == run(html)[0]


# ---- audit record is bounded and log-safe -----------------------------------------------------------------------------------------


def test_audit_record_is_bounded_and_neutralised() -> None:
    refs = "".join(f'<a href="https://evil{i}.example/x">x</a>' for i in range(AUDIT_LIST_LIMIT + 5))
    hostile = '<a href="https://evil.example/' + "A" * 5000 + '\nFORGED">x</a>'
    r, _ = run(refs + hostile)
    audit = r.to_audit(BASE)  # type: ignore[attr-defined]
    assert audit["status"] == "UNRESOLVED" and audit["detail_page_url"] == BASE
    assert len(audit["rejected_references"]) == AUDIT_LIST_LIMIT  # type: ignore[arg-type]
    assert audit["rejected_references_total"] == AUDIT_LIST_LIMIT + 6
    assert all(len(x["reference"]) <= AUDIT_VALUE_MAX and "\n" not in x["reference"] for x in audit["rejected_references"])  # type: ignore[attr-defined,union-attr]
