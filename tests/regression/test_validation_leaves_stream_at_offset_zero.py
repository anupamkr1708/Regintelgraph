"""Regression: validate_response rejected a body after reading part of it and left the stream mid-file, so quarantining wrote
from the wrong offset (a false 'P1 hash mismatch') instead of recording QUARANTINED. Every exit path must leave offset 0."""

from __future__ import annotations

import io

import pytest

from packages.domain.ingest import FetchPurpose
from packages.ingestion.validate import validate_response
from tests.support.builders import SMALL_LIMITS, pdf


@pytest.mark.parametrize(
    "body",
    [b"<html>denied</html>", b"", b"%PDF-1.4 " + b"x" * 300, pdf("js", extra=b"/JavaScript"), pdf("ok")],
    ids=["magic", "empty", "no-eof", "active-token", "clean"],
)
def test_stream_is_left_at_offset_zero_on_every_exit_path(body: bytes) -> None:
    stream = io.BytesIO(body)
    validate_response("application/pdf", stream, len(body), FetchPurpose.ATTACHMENT, SMALL_LIMITS)
    assert stream.tell() == 0
    assert stream.read() == body
