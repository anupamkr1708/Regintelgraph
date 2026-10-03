"""Redirects are a security boundary: manual, bounded, and every hop is fully re-validated."""

from __future__ import annotations

import pytest

from packages.domain.ingest import FetchFailure, FetchPurpose, QuarantineReason
from packages.ingestion.errors import FetchError
from tests.support.builders import HOST, pdf
from tests.support.factories import URL, Rig
from tests.support.fakes import Resp

PDF_HEADERS = {"Content-Type": "application/pdf"}


def redirect(location: str, status: int = 302) -> Resp:
    return Resp(status, {"Location": location})


def fetch_error(rig: Rig, url: str = URL) -> FetchError:
    with pytest.raises(FetchError) as exc:
        rig.client.fetch(url, FetchPurpose.ATTACHMENT)
    return exc.value


@pytest.mark.parametrize(
    ("location", "reason"),
    [
        ("https://evil.example/files/a.pdf", QuarantineReason.REDIRECT_OFF_ALLOWLIST),  # off-allowlist
        (f"https://{HOST}.evil.example/files/a.pdf", QuarantineReason.REDIRECT_OFF_ALLOWLIST),  # look-alike
        ("//evil.example/files/a.pdf", QuarantineReason.REDIRECT_OFF_ALLOWLIST),  # protocol-relative -> evil host
        ("https://127.0.0.1/files/a.pdf", QuarantineReason.REDIRECT_OFF_ALLOWLIST),  # private IP literal
        ("https://169.254.169.254/latest/meta-data", QuarantineReason.REDIRECT_OFF_ALLOWLIST),  # metadata endpoint
        ("https://localhost/files/a.pdf", QuarantineReason.REDIRECT_OFF_ALLOWLIST),
        (f"https://user@{HOST}/files/a.pdf", QuarantineReason.REDIRECT_OFF_ALLOWLIST),
        (f"https://{HOST}:8443/files/a.pdf", QuarantineReason.REDIRECT_OFF_ALLOWLIST),
        (f"https://{HOST}/other/a.pdf", QuarantineReason.REDIRECT_OFF_ALLOWLIST),  # same host, outside reviewed prefixes
        (f"http://{HOST}/files/a.pdf", QuarantineReason.SCHEME_NOT_HTTPS),  # https -> http downgrade
        ("javascript:alert(1)", QuarantineReason.SCHEME_NOT_HTTPS),
        ("data:text/html,x", QuarantineReason.SCHEME_NOT_HTTPS),
        ("file:///etc/passwd", QuarantineReason.SCHEME_NOT_HTTPS),
    ],
)
def test_unsafe_redirect_targets_are_rejected_before_any_request_to_them(location: str, reason: QuarantineReason) -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", redirect(location))
    err = fetch_error(rig)
    assert err.kind is FetchFailure.REDIRECT_REJECTED and err.quarantine is reason
    assert len(rig.transport.requests) == 1  # only the original request was ever sent
    assert rig.resolver.calls == [HOST]  # the rejected target was never even resolved


def test_relative_redirect_is_resolved_against_the_current_url_and_revalidated() -> None:
    rig = Rig()
    body = pdf("rel")
    rig.transport.route(HOST, "/files/a.pdf", redirect("/files/b.pdf", 301))
    rig.transport.route(HOST, "/files/b.pdf", Resp(200, PDF_HEADERS, body))
    resp = rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert resp.requested_url == URL and resp.final_url == f"https://{HOST}/files/b.pdf" and len(resp.redirect_chain) == 2
    assert [r.target for r in rig.transport.requests] == ["/files/a.pdf", "/files/b.pdf"]
    resp.close()


def test_relative_redirect_that_climbs_out_of_scope_is_rejected() -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", redirect("../../etc/passwd"))
    assert fetch_error(rig).quarantine is QuarantineReason.REDIRECT_OFF_ALLOWLIST


def test_redirect_loop_is_detected() -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", redirect("/files/b.pdf"))
    rig.transport.route(HOST, "/files/b.pdf", redirect("/files/a.pdf"))
    err = fetch_error(rig)
    assert err.kind is FetchFailure.REDIRECT_REJECTED and "loop" in err.detail and err.quarantine is QuarantineReason.REDIRECT_OFF_ALLOWLIST
    assert len(rig.transport.requests) == 2


def test_self_redirect_is_a_loop() -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", redirect("/files/a.pdf"))
    assert "loop" in fetch_error(rig).detail


def test_redirect_chain_limit_is_enforced() -> None:
    rig = Rig()  # SMALL_LIMITS.max_redirects == 3
    for i in range(6):
        rig.transport.route(HOST, f"/files/{i}.pdf", redirect(f"/files/{i + 1}.pdf"))
    err = fetch_error(rig, f"https://{HOST}/files/0.pdf")
    assert err.kind is FetchFailure.REDIRECT_REJECTED and "too many" in err.detail
    assert len(rig.transport.requests) == 4  # the original + exactly max_redirects hops


def test_chain_at_exactly_the_limit_succeeds() -> None:
    rig = Rig()
    for i in range(3):
        rig.transport.route(HOST, f"/files/{i}.pdf", redirect(f"/files/{i + 1}.pdf"))
    rig.transport.route(HOST, "/files/3.pdf", Resp(200, PDF_HEADERS, pdf("end")))
    rig.client.fetch(f"https://{HOST}/files/0.pdf", FetchPurpose.ATTACHMENT).close()


def test_redirect_to_an_allowlisted_host_that_resolves_privately_is_rejected_at_the_hop() -> None:
    rig = Rig()
    rig.resolver.table[HOST] = [["93.184.216.34"], ["10.0.0.9"]]  # rebinding: public for hop 1, private for hop 2
    rig.transport.route(HOST, "/files/a.pdf", redirect("/files/b.pdf"))
    err = fetch_error(rig)
    assert err.kind is FetchFailure.DNS_REJECTED and err.quarantine is QuarantineReason.PRIVATE_OR_RESERVED_IP
    assert len(rig.transport.requests) == 1


def test_redirect_without_location_or_with_oversized_location() -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", Resp(302, {}))
    assert fetch_error(rig).kind is FetchFailure.HTTP_PERMANENT
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", redirect("/files/" + "x" * 500))
    assert fetch_error(rig).quarantine is QuarantineReason.REDIRECT_OFF_ALLOWLIST


def test_every_hop_is_resolved_and_pinned_to_its_validated_ip() -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", redirect("/files/b.pdf"))
    rig.transport.route(HOST, "/files/b.pdf", Resp(200, PDF_HEADERS, pdf("pin")))
    rig.client.fetch(URL, FetchPurpose.ATTACHMENT).close()
    assert rig.resolver.calls == [HOST, HOST]
    assert {(r.host, r.ip, r.port) for r in rig.transport.requests} == {
        (HOST, "93.184.216.34", 443)
    }  # SNI/Host = hostname, peer = pinned IP
