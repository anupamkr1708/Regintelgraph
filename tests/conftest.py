"""Global test configuration.

Socket guard: with RIG_OFFLINE_TESTS=1 (the default) any attempt to reach a non-loopback network address, or to resolve a
hostname, fails the test immediately. Unix-domain sockets and loopback stay allowed (the local/CI PostgreSQL service).
`NetworkTouched` derives from BaseException so production `except Exception` blocks cannot swallow it.
"""

from __future__ import annotations

import ipaddress
import os
import socket

import pytest

os.environ.setdefault("RIG_OFFLINE_TESTS", "1")

pytest_plugins = ["tests.support.pgfixtures"]


class OfflineViolation(BaseException):
    """A test tried to use the external network while RIG_OFFLINE_TESTS=1."""


def _is_loopback(host: object) -> bool:
    if not isinstance(host, str):
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _offline_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("RIG_OFFLINE_TESTS") != "1":
        return
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guard_addr(sock: socket.socket, address: object) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not (isinstance(address, tuple) and _is_loopback(address[0])):
            raise OfflineViolation(f"external network access attempted: {address!r}")

    def connect(self: socket.socket, address: object) -> None:
        guard_addr(self, address)
        return real_connect(self, address)  # type: ignore[arg-type]

    def connect_ex(self: socket.socket, address: object) -> int:
        guard_addr(self, address)
        return real_connect_ex(self, address)  # type: ignore[arg-type]

    def getaddrinfo(host: object, *args: object, **kwargs: object) -> list[object]:
        if not _is_loopback(host):
            raise OfflineViolation(f"DNS resolution attempted for {host!r}")
        return socket.getaddrinfo.__wrapped__(host, *args, **kwargs)  # type: ignore[attr-defined,no-any-return]

    real_getaddrinfo = socket.getaddrinfo
    getaddrinfo.__wrapped__ = real_getaddrinfo  # type: ignore[attr-defined]
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

    # `gethostbyname*` / `gethostbyaddr` are separate C entry points that do NOT go through getaddrinfo: guard them too,
    # otherwise DNS would silently escape the guard.
    def guard_name(real: object) -> object:
        def wrapper(host: object, *args: object, **kwargs: object) -> object:
            if not _is_loopback(host):
                raise OfflineViolation(f"DNS resolution attempted for {host!r}")
            return real(host, *args, **kwargs)  # type: ignore[operator]

        return wrapper

    for name in ("gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
        monkeypatch.setattr(socket, name, guard_name(getattr(socket, name)))


try:  # Hypothesis: deterministic and bounded (docs/testing-strategy.md §1). No wall-clock deadlines, no on-disk example database.
    from hypothesis import HealthCheck, settings

    settings.register_profile(
        "rig",
        max_examples=40,
        deadline=None,
        derandomize=True,
        database=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
    )
    settings.load_profile("rig")
except ImportError:  # pragma: no cover
    pass
