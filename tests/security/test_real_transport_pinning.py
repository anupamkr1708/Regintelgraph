"""The real stdlib transport, tested without any network: IP pinning, SNI/Host, certificate verification, no proxies."""

from __future__ import annotations

import ssl
from typing import Any

import pytest

from packages.ingestion import net
from packages.ingestion.net import StdlibTransport, _PinnedHTTPSConnection
from packages.ingestion.ports import TransportRequest


class FakeSock:
    def setsockopt(self, *a: object) -> None: ...
    def settimeout(self, *a: object) -> None: ...
    def close(self) -> None: ...


def test_connect_goes_to_the_pinned_ip_with_sni_of_the_original_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def create_connection(address: tuple[str, int], timeout: float) -> FakeSock:
        seen["peer"], seen["timeout"] = address, timeout
        return FakeSock()

    class Ctx:
        def wrap_socket(self, sock: object, server_hostname: str) -> object:
            seen["sni"] = server_hostname
            return sock

    monkeypatch.setattr(net.socket, "create_connection", create_connection)
    conn = _PinnedHTTPSConnection("docs.testreg.example", "93.184.216.34", 443, 3.0, Ctx())  # type: ignore[arg-type]
    conn.connect()
    assert seen == {"peer": ("93.184.216.34", 443), "timeout": 3.0, "sni": "docs.testreg.example"}  # never a hostname lookup
    assert conn.host == "docs.testreg.example"  # http.client derives the Host header from this


def test_host_header_is_the_original_hostname_not_the_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[bytes] = []

    class Sock(FakeSock):
        def sendall(self, data: bytes) -> None:
            sent.append(data)

    monkeypatch.setattr(net.socket, "create_connection", lambda *a, **k: Sock())

    class Ctx:
        def wrap_socket(self, sock: object, server_hostname: str) -> object:
            return sock

    conn = _PinnedHTTPSConnection("docs.testreg.example", "93.184.216.34", 443, 3.0, Ctx())  # type: ignore[arg-type]
    conn.connect()
    conn.putrequest("GET", "/files/a.pdf")
    conn.endheaders()
    request = b"".join(sent).decode()
    assert "Host: docs.testreg.example\r\n" in request and "93.184.216.34" not in request


def test_tls_context_verifies_certificates_and_hostnames() -> None:
    ctx = StdlibTransport()._ctx
    assert ctx.verify_mode is ssl.CERT_REQUIRED and ctx.check_hostname is True
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_certificate_verification_failure_maps_to_tls_error_without_leaking_details(monkeypatch: pytest.MonkeyPatch) -> None:
    from packages.ingestion.errors import TransportTlsError

    def connect(self: object) -> None:
        raise ssl.SSLCertVerificationError("hostname 'x' doesn't match secret-internal-name")

    monkeypatch.setattr(_PinnedHTTPSConnection, "connect", connect)
    req = TransportRequest("GET", "docs.testreg.example", "93.184.216.34", 443, "/f", {}, 1.0, 1.0)
    with pytest.raises(TransportTlsError) as exc:
        StdlibTransport().open(req)
    assert "secret-internal-name" not in str(exc.value)


def test_no_proxy_environment_is_consulted(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "HTTP_PROXY"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    seen: dict[str, Any] = {}
    monkeypatch.setattr(net.socket, "create_connection", lambda address, timeout: seen.setdefault("peer", address) and FakeSock())

    class Ctx:
        def wrap_socket(self, sock: object, server_hostname: str) -> object:
            return sock

    _PinnedHTTPSConnection("docs.testreg.example", "93.184.216.34", 443, 1.0, Ctx()).connect()  # type: ignore[arg-type]
    assert seen["peer"] == ("93.184.216.34", 443)  # the proxy address was never used


def test_real_resolver_and_transport_do_nothing_at_construction() -> None:
    net.SystemResolver()
    StdlibTransport()  # constructing them performs no I/O (the socket guard would fail this test otherwise)
    assert net.SystemResolver.performs_network and StdlibTransport.performs_network
