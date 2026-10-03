"""Size, type, magic bytes, truncation, encoding: validated before anything is published."""

from __future__ import annotations

import gzip
import io
import zlib

import pytest

from packages.domain.ingest import FetchFailure, FetchPurpose, QuarantineReason
from packages.ingestion import validate as v
from packages.ingestion.errors import FetchError
from tests.support.builders import HOST, SMALL_LIMITS, pdf, sha
from tests.support.factories import URL, Rig, limits_with
from tests.support.fakes import Resp

PDF = {"Content-Type": "application/pdf"}


def fetch(rig: Rig, resp: Resp):  # type: ignore[no-untyped-def]
    rig.transport.route(HOST, "/files/a.pdf", resp)
    return rig.client.fetch(URL, FetchPurpose.ATTACHMENT)


def fetch_error(rig: Rig, resp: Resp) -> FetchError:
    rig.transport.route(HOST, "/files/a.pdf", resp)
    with pytest.raises(FetchError) as exc:
        rig.client.fetch(URL, FetchPurpose.ATTACHMENT)
    return exc.value


def rejection(body: bytes, ctype: str | None = "application/pdf", limits=SMALL_LIMITS):  # type: ignore[no-untyped-def]
    return v.validate_response(ctype, io.BytesIO(body), len(body), FetchPurpose.ATTACHMENT, limits)


# ---- size ---------------------------------------------------------------------------------------------------------------


def test_content_length_over_the_cap_is_rejected_from_the_header_alone() -> None:
    err = fetch_error(Rig(), Resp(200, {**PDF, "Content-Length": str(SMALL_LIMITS.max_bytes_attachment + 1)}, b"%PDF-"))
    assert err.kind is FetchFailure.SIZE_EXCEEDED and err.quarantine is QuarantineReason.SIZE_EXCEEDED


def test_streaming_body_over_the_cap_is_rejected_without_content_length() -> None:
    big = b"%PDF-" + b"x" * (SMALL_LIMITS.max_bytes_attachment + 10)
    err = fetch_error(Rig(), Resp(200, PDF, big))
    assert err.quarantine is QuarantineReason.SIZE_EXCEEDED


def test_body_at_exactly_the_cap_is_accepted() -> None:
    body = pdf("cap")
    body = body + b"x" * (SMALL_LIMITS.max_bytes_attachment - len(body))
    resp = fetch(Rig(), Resp(200, {**PDF, "Content-Length": str(len(body))}, body))
    assert resp.size == SMALL_LIMITS.max_bytes_attachment
    resp.close()


@pytest.mark.parametrize("cl", ["abc", "-1", "1e3", "", "12 34"])
def test_malformed_content_length_is_rejected(cl: str) -> None:
    assert fetch_error(Rig(), Resp(200, {**PDF, "Content-Length": cl}, pdf())).kind is FetchFailure.HTTP_PERMANENT


# ---- truncation -----------------------------------------------------------------------------------------------------------


def test_body_shorter_than_content_length_is_truncated() -> None:
    body = pdf()
    err = fetch_error(Rig(), Resp(200, {**PDF, "Content-Length": str(len(body) + 50)}, body))
    assert err.quarantine is QuarantineReason.PDF_SANITY_FAILED and "truncated" in err.detail


def test_transport_level_truncation_is_reported() -> None:
    err = fetch_error(Rig(), Resp(200, PDF, pdf(), truncate=True))
    assert err.quarantine is QuarantineReason.PDF_SANITY_FAILED


# ---- content type / magic / sanity (validate_response) ----------------------------------------------------------------------


def test_a_clean_pdf_passes() -> None:
    assert rejection(pdf()) is None


@pytest.mark.parametrize(
    "ctype",
    ["text/html", "text/html; charset=utf-8", "application/octet-stream", "image/png", None, "", "application/pdfx", "application/json"],
)
def test_wrong_media_type(ctype: str | None) -> None:
    r = rejection(pdf(), ctype)
    assert r is not None and r.reason is QuarantineReason.CONTENT_TYPE_MISMATCH


def test_pdf_media_type_parameters_and_case_are_tolerated() -> None:
    assert rejection(pdf(), "Application/PDF; qs=0.9") is None


def test_html_served_as_pdf_is_a_magic_bytes_mismatch() -> None:
    r = rejection(b"<!DOCTYPE html><html><body>Access denied</body></html>")
    assert r is not None and r.reason is QuarantineReason.MAGIC_BYTES_MISMATCH


def test_zero_byte_body_is_rejected() -> None:
    r = rejection(b"")
    assert r is not None and r.reason is QuarantineReason.MAGIC_BYTES_MISMATCH and "empty" in r.detail


@pytest.mark.parametrize("body", [b"%PD", b"PDF-1.4", b" %PDF-1.4 %%EOF", b"\x00%PDF-1.4 %%EOF", b"%pdf-1.4 %%EOF"])
def test_invalid_magic_bytes(body: bytes) -> None:
    r = rejection(body)
    assert r is not None and r.reason is QuarantineReason.MAGIC_BYTES_MISMATCH


def test_missing_eof_marker_is_rejected() -> None:
    r = rejection(b"%PDF-1.4\n1 0 obj << >> endobj\n" + b"x" * 200)
    assert r is not None and r.reason is QuarantineReason.PDF_SANITY_FAILED and "EOF" in r.detail


@pytest.mark.parametrize("token", [b"/Encrypt", b"/JavaScript", b"/JS", b"/Launch", b"/OpenAction", b"/EmbeddedFile"])
def test_active_content_tokens_are_rejected(token: bytes) -> None:
    r = rejection(pdf(extra=b"<< " + token + b" 5 0 R >>"))
    assert r is not None and r.reason is QuarantineReason.PDF_SANITY_FAILED and token.decode() in r.detail


@pytest.mark.parametrize("benign", [b"/JSON", b"/JavaScriptX", b"/Encrypted", b"/Names /Dests", b"/JS#41"])
def test_token_lookalikes_are_not_false_positives(benign: bytes) -> None:
    assert rejection(pdf(extra=b"<< " + benign + b" >>")) is None


def test_token_straddling_scan_windows_is_still_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(v, "_SCAN_CHUNK", 16)
    for pad in range(0, 40):
        body = pdf(extra=b"a" * pad + b"/JavaScript")
        r = rejection(body)
        assert r is not None and "JavaScript" in r.detail, pad
    assert rejection(pdf(extra=b"/JSON " * 20)) is None  # a would-be match touching a window edge is deferred, not mis-reported


def test_html_purpose_is_typed_and_bounded_only() -> None:
    ok = v.validate_response("text/html; charset=utf-8", io.BytesIO(b"<html/>"), 7, FetchPurpose.DETAIL_PAGE, SMALL_LIMITS)
    assert ok is None
    bad = v.validate_response("application/pdf", io.BytesIO(b"%PDF-"), 5, FetchPurpose.DETAIL_PAGE, SMALL_LIMITS)
    assert bad is not None and bad.reason is QuarantineReason.CONTENT_TYPE_MISMATCH


def test_sniffing_is_coarse_and_never_trusted() -> None:
    assert v.sniff_media_type(b"%PDF-1.7") == "application/pdf"
    assert v.sniff_media_type(b"  <!DOCTYPE HTML><html>") == "text/html"
    assert v.sniff_media_type(b"\x89PNG") == "application/octet-stream"


# ---- content encoding / fingerprint ----------------------------------------------------------------------------------------


def test_hash_is_over_the_decoded_bytes_and_streamed() -> None:
    plain = pdf("enc")
    resp = fetch(Rig(), Resp(200, {**PDF, "Content-Encoding": "gzip"}, gzip.compress(plain)))
    assert resp.sha256_hex == sha(plain) and resp.size == len(plain) and resp.body is not None and resp.body.read() == plain
    resp.close()
    resp = fetch(Rig(), Resp(200, {**PDF, "Content-Encoding": "deflate"}, zlib.compress(plain)))
    assert resp.sha256_hex == sha(plain)
    resp.close()


def test_identity_and_gzip_of_the_same_bytes_have_the_same_fingerprint() -> None:
    plain = pdf("same")
    a = fetch(Rig(), Resp(200, PDF, plain))
    b = fetch(Rig(), Resp(200, {**PDF, "Content-Encoding": "gzip"}, gzip.compress(plain)))
    assert a.sha256_hex == b.sha256_hex == sha(plain)
    a.close()
    b.close()


def test_decompression_bomb_is_stopped_by_the_expansion_ratio() -> None:
    rig = Rig(limits_with(max_bytes_attachment=50_000_000))  # the size cap alone would NOT stop it
    bomb = gzip.compress(b"%PDF-" + b"\x00" * 5_000_000)
    assert len(bomb) < 20_000
    err = fetch_error(rig, Resp(200, {**PDF, "Content-Encoding": "gzip"}, bomb))
    assert err.quarantine is QuarantineReason.SIZE_EXCEEDED and "ratio" in err.detail


def test_decompression_is_bounded_by_the_size_cap() -> None:
    rig = Rig(limits_with(max_expansion_ratio=1e9))
    err = fetch_error(rig, Resp(200, {**PDF, "Content-Encoding": "gzip"}, gzip.compress(b"%PDF-" + b"\x00" * 100_000)))
    assert err.quarantine is QuarantineReason.SIZE_EXCEEDED


@pytest.mark.parametrize("encoding", ["br", "zstd", "compress", "gzip, br", "gzip, gzip"])
def test_unsupported_content_encodings_are_rejected(encoding: str) -> None:
    assert fetch_error(Rig(), Resp(200, {**PDF, "Content-Encoding": encoding}, pdf())).quarantine is QuarantineReason.CONTENT_TYPE_MISMATCH


def test_corrupt_and_truncated_compressed_bodies() -> None:
    assert (
        fetch_error(Rig(), Resp(200, {**PDF, "Content-Encoding": "gzip"}, b"not gzip at all")).quarantine
        is QuarantineReason.PDF_SANITY_FAILED
    )
    cut = gzip.compress(pdf("cut") * 20)[:-12]
    assert fetch_error(Rig(), Resp(200, {**PDF, "Content-Encoding": "gzip"}, cut)).quarantine is QuarantineReason.PDF_SANITY_FAILED


def test_status_mapping() -> None:
    assert fetch_error(Rig(), Resp(404)).kind is FetchFailure.HTTP_PERMANENT
    assert fetch_error(Rig(), Resp(410)).kind is FetchFailure.HTTP_PERMANENT
    assert fetch_error(Rig(), Resp(206, PDF, pdf())).kind is FetchFailure.HTTP_PERMANENT  # partial content is not a full document
    assert fetch_error(Rig(), Resp(204)).kind is FetchFailure.HTTP_PERMANENT
    assert fetch_error(Rig(), Resp(502)).kind is FetchFailure.HTTP_TRANSIENT
