"""A tiny, fast, fully in-memory ingestion world for property tests (no filesystem, no database, no network)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from packages.domain.content_hash import ContentHash
from packages.domain.ingest import RunMode
from packages.ingestion.pipeline import Dependencies, IngestConfig, IngestReport, run_ingest
from packages.ingestion.repository import InMemoryRepository
from tests.support.builders import CONTACT_ENV, CONTACT_VALUE, HOST, candidate, manifest, pdf
from tests.support.fakes import FakeBlobStore, FakeClock, FakeResolver, FakeTransport, Resp, SeededRng


class World:
    def __init__(self) -> None:
        self.clock = FakeClock()
        self.transport = FakeTransport(self.clock)
        self.repo = InMemoryRepository()
        self.blobs, self.quarantine = FakeBlobStore(), FakeBlobStore()
        self.deps = Dependencies(self.clock, SeededRng(), FakeResolver(), self.transport, self.blobs, self.quarantine, self.repo)

    def serve(self, payloads: Mapping[str, str]) -> None:
        """doc key -> content tag; the served body is a valid PDF whose bytes depend only on the tag."""
        for key, tag in payloads.items():
            self.transport.route(HOST, f"/files/{key.lower()}.pdf", Resp(200, {"Content-Type": "application/pdf"}, pdf(tag)))

    def run(self, keys: Sequence[str]) -> IngestReport:
        cands = [candidate(k, index=i) for i, k in enumerate(keys)]
        built = manifest(cands)
        cfg = IngestConfig(
            built,
            ContentHash("2" * 64),
            "tests/synthetic",
            RunMode.LIVE,
            "test-sha",
            authorisation_ref="TEST",
            env={CONTACT_ENV: CONTACT_VALUE},
            selected_candidate_keys=tuple(
                c.document_key for c in built.candidates
            ),  # explicit scope: the manifest's candidates, manifest order
        )
        return run_ingest(cfg, self.deps)

    def source_layer_counts(self) -> tuple[int, int, int, int]:
        c = self.repo.counts()
        return c["raw_artifact"], c["regulatory_document"], c["document_version"], c["document_version_location"]
