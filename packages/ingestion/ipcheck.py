"""DNS/IP validation (docs/phase1/source-safety-contract.md §4). Resolution happens ONLY after static URL validation.

EVERY answer must be a globally routable address; one bad record rejects the whole answer set (no "pick the good one").
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

from packages.domain.ingest import FetchFailure, QuarantineReason
from packages.ingestion.errors import FetchError
from packages.ingestion.ports import Resolver

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Ranges that `ipaddress.is_global` may not (consistently across versions) flag, listed explicitly so the policy is
# visible and tested rather than implied by a library version.
_BLOCKED_V4 = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
    )
)
_BLOCKED_V6 = tuple(
    ipaddress.ip_network(n)
    for n in ("::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8", "2001:db8::/32", "100::/64", "2001::/23", "2002::/16")
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")


def _embedded_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """IPv4 carried inside an IPv6 address: mapped (::ffff:a.b.c.d), compatible (::a.b.c.d), NAT64 (64:ff9b::/96)."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    value = int(ip)
    if value >> 32 == 0 and value > 1:  # ::a.b.c.d  (deprecated IPv4-compatible)
        return ipaddress.IPv4Address(value & 0xFFFFFFFF)
    if ip in _NAT64:
        return ipaddress.IPv4Address(value & 0xFFFFFFFF)
    return None


def is_public_address(ip: IPAddress) -> bool:
    """True only for globally routable unicast addresses."""
    if isinstance(ip, ipaddress.IPv4Address):
        return ip.is_global and not any(ip in net for net in _BLOCKED_V4)
    embedded = _embedded_v4(ip)
    if embedded is not None:
        return False  # an IPv4 address smuggled through IPv6 is never an answer we expect from a public host; reject outright
    if ip in _NAT64_LOCAL or any(ip in net for net in _BLOCKED_V6):
        return False
    return ip.is_global


def validate_resolution(host: str, resolver: Resolver) -> tuple[str, ...]:
    """Resolve `host` and return its validated IP literals, or raise FetchError. Never returns a partial good subset."""
    try:
        answers: Sequence[str] = resolver.resolve(host)
    except Exception as exc:  # resolver failure of any kind fails closed; the message is the class name only
        raise FetchError(FetchFailure.DNS_REJECTED, f"resolver failed: {type(exc).__name__}") from exc
    if not answers:
        raise FetchError(FetchFailure.DNS_REJECTED, "resolver returned no addresses")
    validated: list[str] = []
    for raw in answers:
        if not isinstance(raw, str) or "%" in raw:  # scoped (zone-id) addresses are never public
            raise FetchError(FetchFailure.DNS_REJECTED, "malformed resolver answer", quarantine=QuarantineReason.PRIVATE_OR_RESERVED_IP)
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise FetchError(
                FetchFailure.DNS_REJECTED, "resolver answer is not an IP literal", quarantine=QuarantineReason.PRIVATE_OR_RESERVED_IP
            ) from exc
        if not is_public_address(ip):
            raise FetchError(
                FetchFailure.DNS_REJECTED,
                "resolver answer set contains a non-public address",
                quarantine=QuarantineReason.PRIVATE_OR_RESERVED_IP,
            )
        validated.append(str(ip))
    return tuple(dict.fromkeys(validated))  # de-duplicated, order preserved
