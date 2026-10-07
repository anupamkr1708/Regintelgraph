"""Helpers for detail-page discovery tests: serve synthetic detail pages and attachments through the FakeTransport (never a network)."""

from __future__ import annotations

from urllib.parse import quote

from packages.domain.manifest import ManifestCandidate
from tests.support.builders import HOST, candidate, pdf
from tests.support.env import PDF_HEADERS, Env
from tests.support.fakes import Resp

HTML_HEADERS = {"Content-Type": "text/html; charset=utf-8"}


def detail_path(key: str) -> str:
    return f"/docs/{key.lower()}.html"


def file_url(name: str) -> str:
    return f"https://{HOST}/files/{name}.pdf"


def wrapper(inner: str) -> str:
    return f"https://{HOST}/web/?file={quote(inner, safe='')}"


def discovery_candidate(key: str, *, index: int = 0) -> ManifestCandidate:
    """A candidate with a detail page (canonical_url) and NO document_url: only discovery can find its attachment."""
    return candidate(key, index=index, url=None, canonical_url=f"https://{HOST}{detail_path(key)}")


def serve_detail(env: Env, key: str, html: str | bytes, *, headers: dict[str, str] | None = None, status: int = 200) -> None:
    body = html.encode() if isinstance(html, str) else html
    env.transport.route(HOST, detail_path(key), Resp(status, {**HTML_HEADERS, **(headers or {})}, body))


def serve_attachment(env: Env, name: str, body: bytes | None = None) -> None:
    env.transport.route(HOST, f"/files/{name}.pdf", Resp(200, PDF_HEADERS, body if body is not None else pdf(name)))


def targets(env: Env) -> list[str]:
    return [r.target for r in env.transport.requests]
