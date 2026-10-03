"""A complete ingestion environment wired to fakes (clock, rng, resolver, transport) and REAL local-filesystem stores."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from packages.domain.content_hash import ContentHash
from packages.domain.ingest import RunMode
from packages.domain.manifest import ManifestCandidate, SourceManifest
from packages.ingestion.blobstore import FsBlobStore
from packages.ingestion.pipeline import Dependencies, IngestConfig, IngestReport, run_ingest
from packages.ingestion.repository import Repository
from tests.support.builders import CONTACT_ENV, CONTACT_VALUE, HOST, candidate, manifest, pdf
from tests.support.fakes import FakeClock, FakeResolver, FakeTransport, Resp, SeededRng

PDF_HEADERS = {"Content-Type": "application/pdf"}


class Env:
    def __init__(self, repo: Repository, tmp_path: Path) -> None:
        self.clock = FakeClock()
        self.rng = SeededRng()
        self.resolver = FakeResolver()
        self.transport = FakeTransport(self.clock)
        self.blobs = FsBlobStore(tmp_path / "blobs")
        self.qblobs = FsBlobStore(tmp_path / "quarantine", file_mode=0o400)
        self.repo = repo
        self.manifest_hash = ContentHash("1" * 64)

    def serve(self, key: str, body: bytes | None, *, headers: dict[str, str] | None = None, status: int = 200) -> None:
        self.transport.route(
            HOST, f"/files/{key.lower()}.pdf", Resp(status, {**PDF_HEADERS, **(headers or {})}, body if body is not None else pdf(key))
        )

    def deps(self) -> Dependencies:
        return Dependencies(self.clock, self.rng, self.resolver, self.transport, self.blobs, self.qblobs, self.repo)

    def config(self, m: SourceManifest, *, mode: RunMode = RunMode.LIVE, **kw: Any) -> IngestConfig:
        base: dict[str, Any] = dict(
            manifest=m,
            manifest_hash=self.manifest_hash,
            manifest_ref="tests/synthetic",
            mode=mode,
            code_version="test-sha",
            authorisation_ref="TEST-AUTH-SYNTHETIC" if mode is RunMode.LIVE else None,
            env={CONTACT_ENV: CONTACT_VALUE},
        )
        base.update(kw)
        return IngestConfig(**base)

    def run(self, candidates: Sequence[ManifestCandidate] | None = None, **kw: Any) -> IngestReport:
        m = kw.pop("manifest_obj", None) or manifest(candidates)
        return run_ingest(self.config(m, **kw), self.deps())

    def blob_files(self, store: FsBlobStore | None = None) -> list[Path]:
        return [p for p in (store or self.blobs).root.rglob("*") if p.is_file()]


def one(key: str = "TEST-001", **kw: Any) -> ManifestCandidate:
    return candidate(key, **kw)
