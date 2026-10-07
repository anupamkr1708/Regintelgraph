"""REGRESSION (mandatory): while `ingestion_authorized` is false, a LIVE run is ABORTED(GATE_CLOSED) with ZERO network calls.

This must stay true no matter how the pipeline evolves. It is exercised three ways:
  1. against the REAL repository manifest (so flipping the real gate is noticed by a human, not by accident);
  2. with the REAL SystemResolver/StdlibTransport objects under the socket guard (any DNS/TCP would raise OfflineViolation);
  3. with tripwire doubles that count every call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.domain.ingest import AbortReason, RunMode, RunStatus
from packages.ingestion.blobstore import FsBlobStore
from packages.ingestion.manifest import load_manifest
from packages.ingestion.net import StdlibTransport, SystemResolver
from packages.ingestion.pipeline import Dependencies, IngestConfig, run_ingest
from packages.ingestion.repository import InMemoryRepository
from tests.support.fakes import FakeClock, SeededRng, TripwireResolver, TripwireTransport

REAL_MANIFEST = Path(__file__).resolve().parents[2] / "data/manifests/sebi-mutual-funds.yaml"


def build(tmp_path: Path, resolver: object, transport: object, **cfg: object) -> tuple[IngestConfig, Dependencies, InMemoryRepository]:
    loaded = load_manifest(REAL_MANIFEST)
    repo = InMemoryRepository()
    config = IngestConfig(
        manifest=loaded.manifest,
        manifest_hash=loaded.content_hash,
        manifest_ref="data/manifests/sebi-mutual-funds.yaml",
        mode=RunMode.LIVE,
        code_version="test",
        authorisation_ref=str(cfg.get("authorisation_ref", "irrelevant-while-gate-is-closed")),
        env={"RIG_CRAWLER_CONTACT_EMAIL": "x@example.org"},
        selected_candidate_keys=tuple(c.document_key for c in loaded.manifest.candidates),  # explicit scope, as every run now needs
    )
    deps = Dependencies(FakeClock(), SeededRng(), resolver, transport, FsBlobStore(tmp_path / "b"), FsBlobStore(tmp_path / "q"), repo)  # type: ignore[arg-type]
    return config, deps, repo


def test_the_committed_manifest_gate_is_closed() -> None:
    m = load_manifest(REAL_MANIFEST).manifest
    assert m.ingestion_authorized is False and not m.gate_open
    assert m.crawl.status == "NOT_CONFIGURED" and m.crawl.limits() is None  # the crawl block is not production-ready either


def test_live_run_on_the_real_manifest_is_aborted_with_zero_network_calls(tmp_path: Path) -> None:
    resolver, transport = TripwireResolver(), TripwireTransport()
    config, deps, repo = build(tmp_path, resolver, transport)
    report = run_ingest(config, deps)
    assert report.run.status is RunStatus.ABORTED and report.run.abort_reason is AbortReason.GATE_CLOSED
    assert report.results == () and (resolver.calls, transport.calls) == (0, 0)
    assert repo.counts()["raw_artifact"] == 0 and repo.counts()["document_version"] == 0
    assert [p for p in (tmp_path / "b").rglob("*") if p.is_file()] == []


def test_real_resolver_and_transport_are_never_touched_while_the_gate_is_closed(tmp_path: Path) -> None:
    """With the real network adapters and the socket guard active, any DNS/TCP attempt raises OfflineViolation (a BaseException)."""
    config, deps, _ = build(tmp_path, SystemResolver(), StdlibTransport())
    report = run_ingest(config, deps)  # would raise OfflineViolation on any socket use
    assert report.run.abort_reason is AbortReason.GATE_CLOSED


def test_gate_stays_closed_even_with_dry_run_style_limits_and_a_recorded_authorisation_ref(tmp_path: Path) -> None:
    from tests.support.builders import SMALL_LIMITS

    resolver, transport = TripwireResolver(), TripwireTransport()
    config, deps, _ = build(tmp_path, resolver, transport, authorisation_ref="SOMEONE-SAID-OK")
    from dataclasses import replace

    report = run_ingest(replace(config, dry_run_limits=SMALL_LIMITS), deps)
    assert report.run.abort_reason is AbortReason.GATE_CLOSED and (resolver.calls, transport.calls) == (
        0,
        0,
    )  # an authorisation_ref string is not the gate


def test_no_code_path_sets_the_gate_open() -> None:
    """Nothing in project code assigns `ingestion_authorized = True` or flips the manifest; only a human edit of the YAML can."""
    import re

    repo = Path(__file__).resolve().parents[2]
    for root in ("packages", "scripts", "apps", "workers"):
        for p in (repo / root).rglob("*.py"):
            assert not re.search(r"ingestion_authorized\s*=\s*True", p.read_text()), p
    assert "ingestion_authorized: false" in REAL_MANIFEST.read_text()


@pytest.mark.parametrize("mode", [RunMode.DRY_RUN])
def test_dry_run_is_always_permitted_without_the_gate_but_never_becomes_live(tmp_path: Path, mode: RunMode) -> None:
    from dataclasses import replace

    from tests.support.builders import SMALL_LIMITS
    from tests.support.fakes import FakeResolver, FakeTransport

    config, deps, _ = build(tmp_path, FakeResolver(), FakeTransport(), authorisation_ref="x")
    report = run_ingest(replace(config, mode=mode, dry_run_limits=SMALL_LIMITS), deps)
    assert (
        report.run.mode is RunMode.DRY_RUN and report.run.abort_reason is None
    )  # permitted: plans every candidate on fixtures, no network object used
    with pytest.raises(Exception, match="network-capable"):
        run_ingest(
            replace(config, mode=mode, dry_run_limits=SMALL_LIMITS),
            Dependencies(deps.clock, deps.rng, SystemResolver(), deps.transport, deps.blobs, deps.quarantine_blobs, InMemoryRepository()),
        )
