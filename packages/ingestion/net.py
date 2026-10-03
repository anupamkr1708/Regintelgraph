"""The ONLY module that opens sockets: real `Resolver` and `HttpTransport` (stdlib `socket`, `ssl`, `http.client`).

Security properties (docs/phase1/source-safety-contract.md §4, §6):
 * the TCP connection goes to the already-validated IP (pinned) while TLS SNI and the Host header carry the original
   approved hostname — there is no second resolution between validation and connect (no DNS-rebinding window);
 * certificate and hostname verification are always on (`ssl.create_default_context`, TLS >= 1.2);
 * no redirects are followed and no proxy is used: `http.client` never reads proxy environment variables;
 * the module is only reached after the source-access gate and static URL validation have passed.
Nothing here runs at import time and constructing these objects performs no I/O.
"""

from __future__ import annotations

import http.client
import socket
import ssl
from collections.abc import Mapping, Sequence

from packages.ingestion.errors import (
    TransportConnectError,
    TransportError,
    TransportTimeoutError,
    TransportTlsError,
    TransportTruncatedError,
)
from packages.ingestion.ports import TransportRequest, TransportResponse


class SystemResolver:
    """`socket.getaddrinfo` returning every A/AAAA answer as an IP literal string."""

    performs_network = True

    def resolve(self, host: str) -> Sequence[str]:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return tuple(dict.fromkeys(str(info[4][0]) for info in infos))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection whose TCP peer is a pinned IP; `self.host` (original hostname) drives SNI and the Host header."""

    def __init__(self, host: str, ip: str, port: int, timeout: float, context: ssl.SSLContext) -> None:
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._pinned_ip = ip

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)  # type: ignore[attr-defined]


class _Response:
    def __init__(self, conn: _PinnedHTTPSConnection, resp: http.client.HTTPResponse) -> None:
        self._conn = conn
        self._resp = resp
        self.status: int = resp.status
        merged: dict[str, str] = {}
        for name, value in resp.getheaders():
            key = name.lower()
            merged[key] = f"{merged[key]}, {value}" if key in merged else value
        self.headers: Mapping[str, str] = merged

    def read(self, size: int) -> bytes:
        try:
            return self._resp.read(size)
        except http.client.IncompleteRead as exc:
            raise TransportTruncatedError("response body ended before its declared length") from exc
        except TimeoutError as exc:
            raise TransportTimeoutError("read timed out") from exc
        except ssl.SSLError as exc:
            raise TransportTlsError(f"TLS error while reading: {type(exc).__name__}") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise TransportError(f"read failed: {type(exc).__name__}") from exc

    def close(self) -> None:
        try:
            self._resp.close()
        finally:
            self._conn.close()


class StdlibTransport:
    performs_network = True

    def __init__(self) -> None:
        ctx = ssl.create_default_context()  # CERT_REQUIRED + check_hostname + system trust store
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self._ctx = ctx

    def open(self, request: TransportRequest) -> TransportResponse:
        conn = _PinnedHTTPSConnection(request.host, request.ip, request.port, request.connect_timeout, self._ctx)
        try:
            conn.connect()
            if conn.sock is not None:
                conn.sock.settimeout(request.read_timeout)
            conn.request(request.method, request.target, headers=dict(request.headers))
            return _Response(conn, conn.getresponse())
        except ssl.SSLCertVerificationError as exc:
            conn.close()
            raise TransportTlsError("certificate verification failed") from exc
        except ssl.SSLError as exc:
            conn.close()
            raise TransportTlsError(f"TLS handshake failed: {type(exc).__name__}") from exc
        except TimeoutError as exc:
            conn.close()
            raise TransportTimeoutError("timed out") from exc
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise TransportConnectError(f"connection failed: {type(exc).__name__}") from exc
