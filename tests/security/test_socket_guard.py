"""The offline guard itself: with RIG_OFFLINE_TESTS=1 external sockets and DNS fail the test, and cannot be swallowed."""

from __future__ import annotations

import socket

import pytest

from tests.conftest import OfflineViolation


def test_external_connect_is_blocked() -> None:
    with pytest.raises(OfflineViolation), socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        s.connect(("192.0.2.1", 443))


def test_external_connect_ex_is_blocked() -> None:
    with pytest.raises(OfflineViolation), socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect_ex(("198.51.100.7", 80))


def test_create_connection_is_blocked() -> None:
    with pytest.raises(OfflineViolation):
        socket.create_connection(("203.0.113.9", 443), timeout=0.2)


def test_dns_resolution_is_blocked() -> None:
    with pytest.raises(OfflineViolation):
        socket.getaddrinfo("example.org", 443)
    with pytest.raises(OfflineViolation):
        socket.gethostbyname("example.org")


def test_violation_cannot_be_swallowed_by_broad_except_exception() -> None:
    swallowed = False
    with pytest.raises(OfflineViolation):
        try:
            socket.getaddrinfo("example.org", 443)
        except Exception:
            swallowed = True
    assert not swallowed


def test_loopback_and_unix_sockets_remain_available_for_the_local_database() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.create_connection(server.getsockname(), timeout=1)
    client.close()
    server.close()
    a, b = socket.socketpair()
    a.close()
    b.close()
