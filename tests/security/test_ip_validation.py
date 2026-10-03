from __future__ import annotations

import ipaddress

import pytest

from packages.domain.ingest import FetchFailure, QuarantineReason
from packages.ingestion.errors import FetchError
from packages.ingestion.ipcheck import is_public_address, validate_resolution
from tests.support.fakes import FakeResolver

PUBLIC = ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "2a00:1450:4001::200e"]
NON_PUBLIC = [
    "10.0.0.1",
    "10.255.255.255",
    "172.16.0.1",
    "172.31.255.254",
    "192.168.1.1",  # RFC1918
    "127.0.0.1",
    "127.255.255.254",
    "0.0.0.0",
    "::1",
    "::",  # loopback / unspecified
    "169.254.169.254",
    "169.254.0.1",
    "fe80::1",
    "fe80::abcd",  # link-local incl. cloud metadata
    "100.64.0.1",
    "100.127.255.254",  # CGNAT
    "fc00::1",
    "fd00::1",
    "fd00:ec2::254",  # IPv6 unique-local (incl. EC2 IPv6 metadata)
    "::ffff:10.0.0.1",
    "::ffff:127.0.0.1",
    "::ffff:169.254.169.254",
    "::ffff:8.8.8.8",  # IPv4-mapped (private AND public are refused)
    "::10.0.0.1",
    "64:ff9b::a00:1",
    "64:ff9b::808:808",  # IPv4-compatible / NAT64
    "2002:a00:1::1",
    "2001:db8::1",  # 6to4, documentation
    "192.0.2.1",
    "198.51.100.1",
    "203.0.113.1",
    "198.18.0.1",
    "224.0.0.1",
    "240.0.0.1",
    "255.255.255.255",
    "192.0.0.1",
]


@pytest.mark.parametrize("ip", PUBLIC)
def test_public_addresses_are_accepted(ip: str) -> None:
    assert is_public_address(ipaddress.ip_address(ip))
    assert validate_resolution("h.example", FakeResolver({"h.example": [ip]})) == (str(ipaddress.ip_address(ip)),)


@pytest.mark.parametrize("ip", NON_PUBLIC)
def test_non_public_addresses_are_rejected(ip: str) -> None:
    assert not is_public_address(ipaddress.ip_address(ip))
    with pytest.raises(FetchError) as exc:
        validate_resolution("h.example", FakeResolver({"h.example": [ip]}))
    assert exc.value.kind is FetchFailure.DNS_REJECTED and exc.value.quarantine is QuarantineReason.PRIVATE_OR_RESERVED_IP


@pytest.mark.parametrize(
    "answers", [["93.184.216.34", "10.0.0.5"], ["10.0.0.5", "93.184.216.34"], ["93.184.216.34", "2606:4700::1111", "::1"]]
)
def test_one_bad_record_rejects_the_whole_answer_set(answers: list[str]) -> None:
    with pytest.raises(FetchError) as exc:
        validate_resolution("h.example", FakeResolver({"h.example": answers}))
    assert exc.value.quarantine is QuarantineReason.PRIVATE_OR_RESERVED_IP


def test_resolver_failures_fail_closed() -> None:
    for failure in (OSError("nxdomain"), TimeoutError(), RuntimeError("boom")):
        with pytest.raises(FetchError) as exc:
            validate_resolution("h.example", FakeResolver({"h.example": failure}))
        assert exc.value.kind is FetchFailure.DNS_REJECTED and exc.value.quarantine is None
        assert "boom" not in str(exc.value)  # only the class name is surfaced
    with pytest.raises(FetchError, match="no addresses"):
        validate_resolution("h.example", FakeResolver({"h.example": []}))


@pytest.mark.parametrize("junk", ["not-an-ip", "93.184.216.34%eth0", "fe80::1%lo", "", "999.1.1.1", "1.2.3"])
def test_malformed_answers_are_rejected(junk: str) -> None:
    with pytest.raises(FetchError):
        validate_resolution("h.example", FakeResolver({"h.example": [junk]}))


def test_duplicate_answers_are_collapsed_preserving_order() -> None:
    assert validate_resolution("h.example", FakeResolver({"h.example": ["8.8.8.8", "1.1.1.1", "8.8.8.8"]})) == ("8.8.8.8", "1.1.1.1")
