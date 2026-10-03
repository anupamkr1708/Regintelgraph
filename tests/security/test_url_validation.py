from __future__ import annotations

import pytest

from packages.domain.ingest import FetchPurpose, QuarantineReason
from packages.ingestion.errors import UrlRejected
from packages.ingestion.urlpolicy import validate_url
from tests.support.builders import HOST, PREFIXES

USERINFO_CORRECT_HOST = f"https://user:pw@{HOST}/files/a.pdf"  # rig:allow-secret: synthetic userinfo URL (the thing under test)
KW = dict(allowed_hosts=(HOST,), path_prefixes=PREFIXES, max_url_length=200, listing_urls=(f"https://{HOST}/list/index",))


def check(url: object, purpose: FetchPurpose = FetchPurpose.ATTACHMENT):  # type: ignore[no-untyped-def]
    return validate_url(url, purpose, **KW)  # type: ignore[arg-type]


def test_a_reviewed_attachment_url_is_accepted_and_normalised() -> None:
    v = check(f"HTTPS://{HOST.upper()}/files/a.pdf#page=2")
    assert (v.host, v.port, v.path, v.query, v.url) == (HOST, 443, "/files/a.pdf", "", f"https://{HOST}/files/a.pdf")  # fragment dropped
    assert check(f"https://{HOST}:443/files/a.pdf").url == f"https://{HOST}/files/a.pdf"
    assert check(f"https://{HOST}./files/a.pdf").host == HOST  # trailing dot normalised, then exact-matched


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://evil.example/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),  # wrong host
        (f"https://{HOST}.evil.example/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),  # look-alike (suffix) host
        (f"https://evil{HOST}/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),  # look-alike (prefix glued) host
        (f"https://x.{HOST}/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),  # a subdomain is NOT the allowlisted host
        (f"https://{HOST}@evil.example/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),  # userinfo trick
        (USERINFO_CORRECT_HOST, QuarantineReason.HOST_NOT_ALLOWED),  # userinfo, correct host
        (f"https://evil.example\\@{HOST}/files/a.pdf", QuarantineReason.MANIFEST_SCOPE_MISMATCH),  # backslash authority confusion
        (f"https://{HOST}:8443/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),  # non-443
        (f"https://{HOST}:80/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),
        (f"https://{HOST}:abc/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),  # malformed port
        ("https://127.0.0.1/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),  # IP literals
        ("https://169.254.169.254/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),
        ("https://[::1]/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),
        ("https://[::ffff:10.0.0.1]/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),
        ("https://2130706433/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),  # decimal IP
        ("https://0x7f.1/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),  # hex/short IP forms
        ("https://0177.0.0.1/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),
        ("https://localhost/files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),
        ("https:///files/a.pdf", QuarantineReason.HOST_NOT_ALLOWED),  # empty host
        (f"http://{HOST}/files/a.pdf", QuarantineReason.SCHEME_NOT_HTTPS),
        ("file:///etc/passwd", QuarantineReason.SCHEME_NOT_HTTPS),
        ("javascript://%0aalert(1)", QuarantineReason.SCHEME_NOT_HTTPS),
        ("data:text/html;base64,PHNjcmlwdD4=", QuarantineReason.SCHEME_NOT_HTTPS),
        ("ftp://docs.testreg.example/files/a.pdf", QuarantineReason.SCHEME_NOT_HTTPS),
        (f"//{HOST}/files/a.pdf", QuarantineReason.SCHEME_NOT_HTTPS),  # protocol-relative
        ("/files/a.pdf", QuarantineReason.SCHEME_NOT_HTTPS),  # no scheme at all
    ],
)
def test_rejected_urls(url: str, reason: QuarantineReason) -> None:
    with pytest.raises(UrlRejected) as exc:
        check(url)
    assert exc.value.reason is reason


@pytest.mark.parametrize(
    "path",
    [
        "/files/../secret",  # dot segments
        "/files/%2e%2e/secret",  # encoded dot segments
        "/files/%2E%2E/secret",
        "/files/..%2fsecret",  # encoded slash
        "/files/..%5csecret",  # encoded backslash
        "/files/%252e%252e/secret",  # double-encoding
        "/files/a%00.pdf",  # NUL
        "/files/a b.pdf",  # raw space
        "/files/a\tb.pdf",
        "/files//a.pdf",  # empty segment
        "/files/./a.pdf",
        "/files/ä.pdf",  # non-ASCII must be percent-encoded
        "/other/a.pdf",  # outside the reviewed prefix
        "/filesX/a.pdf",  # prefix boundary: '/files/' is a prefix, '/filesX' is not
        "/",
    ],
)
def test_path_attacks_are_rejected(path: str) -> None:
    with pytest.raises(UrlRejected) as exc:
        check(f"https://{HOST}{path}")
    assert exc.value.reason in (QuarantineReason.MANIFEST_SCOPE_MISMATCH, QuarantineReason.HOST_NOT_ALLOWED)


def test_overlong_url_is_rejected_before_parsing() -> None:
    with pytest.raises(UrlRejected, match="length"):
        check(f"https://{HOST}/files/" + "a" * 500)


@pytest.mark.parametrize("bad", [None, 5, b"https://x", "", []])
def test_non_string_input_is_rejected(bad: object) -> None:
    with pytest.raises(UrlRejected):
        check(bad)


def test_viewer_wrapper_urls_are_never_fetched() -> None:
    with pytest.raises(UrlRejected) as exc:
        check(f"https://{HOST}/web/?file=https://{HOST}/files/a.pdf")
    assert exc.value.reason is QuarantineReason.VIEWER_WRAPPER_URL


def test_query_strings_only_for_reviewed_listing_entry_points() -> None:
    with pytest.raises(UrlRejected):
        check(f"https://{HOST}/files/a.pdf?x=1")
    assert check(f"https://{HOST}/list/index", FetchPurpose.LISTING).url == f"https://{HOST}/list/index"
    with pytest.raises(UrlRejected):  # pagination / unreviewed listing URLs are not fetchable
        check(f"https://{HOST}/list/index?page=2", FetchPurpose.LISTING)
    with pytest.raises(UrlRejected):
        check(f"https://{HOST}/list/other", FetchPurpose.LISTING)


def test_purposes_have_separate_prefix_sets() -> None:
    check(f"https://{HOST}/docs/page", FetchPurpose.DETAIL_PAGE)
    with pytest.raises(UrlRejected):
        check(f"https://{HOST}/docs/page", FetchPurpose.ATTACHMENT)
