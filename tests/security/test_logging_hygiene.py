"""Logs must never carry secrets, the contact value, auth headers, cookies, response bodies; control characters are escaped."""

from __future__ import annotations

import logging

import pytest

from packages.domain.ingest import FetchPurpose
from packages.ingestion.logsafe import LOGGED_HEADERS, MAX_LOG_VALUE, neutralize, safe_headers, safe_url
from tests.support.builders import CONTACT_VALUE, HOST, pdf
from tests.support.factories import URL, Rig
from tests.support.fakes import Resp

SECRET = "s3cr3t-token-123"
USERINFO_URL = (
    "https://user:pw@docs.testreg.example/files/a.pdf?token=abc#frag"  # rig:allow-secret: synthetic userinfo URL (the thing under test)
)
BODY_MARKER = "UNIQUE-BODY-MARKER-9f3a"


def test_control_characters_and_newlines_are_escaped() -> None:
    out = neutralize("line1\nFAKE LOG ENTRY\r\x1b[31mred\x00\x7f\u2028end")
    assert "\n" not in out and "\r" not in out and "\x1b" not in out and "\x00" not in out and "\u2028" not in out
    assert "\\x0a" in out and "\\x1b" in out and "\\x00" in out


def test_length_is_bounded() -> None:
    assert len(neutralize("a" * 10_000)) <= MAX_LOG_VALUE and neutralize("a" * 10_000).endswith("…")
    assert len(neutralize("x" * 5000, max_len=2000)) <= 2000


def test_known_secret_values_are_redacted_wherever_they_appear() -> None:
    assert SECRET not in neutralize(f"Authorization: Bearer {SECRET} and again {SECRET}", secrets=[SECRET])
    assert "[REDACTED]" in neutralize(SECRET, secrets=[SECRET])
    assert neutralize("nothing", secrets=["", ""]) == "nothing"  # empty secrets never blank the message


def test_urls_are_logged_without_userinfo_query_or_fragment() -> None:
    out = safe_url(USERINFO_URL)
    assert out == "https://docs.testreg.example/files/a.pdf"
    assert safe_url("https://[::1:bad") == "[unparseable-url]"
    assert "\n" not in safe_url("https://h.example/a\nb")


def test_only_allowlisted_headers_are_ever_logged() -> None:
    headers = {
        "Set-Cookie": "sid=abc",
        "Authorization": "Bearer x",
        "Content-Type": "application/pdf",
        "ETag": '"e"',
        "X-Internal": "y",
        "Proxy-Authorization": "z",
    }
    shown = safe_headers(headers)
    assert set(shown) == {"content-type", "etag"} and set(shown) <= LOGGED_HEADERS  # keys are normalised to lower case
    assert shown["content-type"] == "application/pdf"
    assert safe_headers({"ETag": "a\r\nSet-Cookie: x"})["etag"].count("\n") == 0


def test_a_full_fetch_never_logs_contact_credentials_cookies_or_body(caplog: pytest.LogCaptureFixture) -> None:
    rig = Rig()
    body = pdf(BODY_MARKER)
    rig.client._secrets = (CONTACT_VALUE, SECRET)  # the pipeline passes the contact value as a redaction secret
    rig.client._ua = f"RegIntelGraph-ingest/1 (research; contact: {CONTACT_VALUE})"
    rig.transport.route(
        HOST,
        "/files/a.pdf",
        Resp(302, {"Location": "/files/b.pdf", "Set-Cookie": f"session={SECRET}", "Authorization": f"Bearer {SECRET}"}),
    )
    rig.transport.route(
        HOST, "/files/b.pdf", Resp(200, {"Content-Type": "application/pdf", "Set-Cookie": f"s={SECRET}", "X-Debug": SECRET}, body)
    )
    with caplog.at_level(logging.DEBUG, logger="regintelgraph"):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT).close()
    text = "\n".join(f"{r.getMessage()} {r.__dict__.get('args', '')}" for r in caplog.records)
    assert caplog.records, "the fetch should log something so that this test is meaningful"
    for forbidden in (CONTACT_VALUE, SECRET, BODY_MARKER, "Set-Cookie", "session=", "Bearer", "Authorization"):
        assert forbidden not in text, forbidden


def test_failures_do_not_leak_bodies_or_contact_into_log_records(caplog: pytest.LogCaptureFixture) -> None:
    rig = Rig()
    rig.transport.route(HOST, "/files/a.pdf", Resp(503, {"Retry-After": "1"}, f"<html>{BODY_MARKER} {CONTACT_VALUE}</html>".encode()))
    with caplog.at_level(logging.DEBUG, logger="regintelgraph"), pytest.raises(Exception, match="HTTP"):
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    assert BODY_MARKER not in caplog.text and CONTACT_VALUE not in caplog.text
