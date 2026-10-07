"""REGRESSION: the five-document controlled-sample selection executes exactly those five candidates, in the caller's order, and can never
execute the deliberately excluded tier-C draft circular. Fully offline: the repository's real manifest drives a DRY_RUN against a
scripted FakeTransport; nothing here can reach SEBI (the fakes cannot open a socket and the socket guard is active)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from packages.domain.ingest import FetchPurpose, IngestOutcome, RunMode, RunStatus
from packages.domain.manifest import ManifestCandidate
from packages.ingestion.manifest import load_manifest
from packages.ingestion.repository import InMemoryRepository
from tests.support.builders import SMALL_LIMITS, pdf
from tests.support.env import PDF_HEADERS, Env
from tests.support.fakes import Resp

MANIFEST = Path(__file__).resolve().parents[2] / "data" / "manifests" / "sebi-mutual-funds.yaml"
FIVE = (
    "SEBI-MF-MC-20260320",
    "SEBI-MF-MC-20240627",
    "SEBI-MF-CIR-20260519-MCR",
    "SEBI-MF-REG2026-20260401",
    "SEBI-MF-REG2026-LASTAMENDED-20260707",
)
EXCLUDED = "SEBI-MF-DRAFTCIR-202605-THIRDPARTY-PAYMENTS"
LIMITS = replace(SMALL_LIMITS, max_url_length=2048, max_bytes_detail_page=65536)  # test-only dry-run limits; not production values


def path_of(url: str) -> str:
    return urlsplit(url).path


@pytest.fixture
def world(tmp_path: Path) -> tuple[Env, dict[str, ManifestCandidate]]:
    manifest = load_manifest(MANIFEST).manifest
    by_key = {c.document_key: c for c in manifest.candidates}
    env = Env(InMemoryRepository(), tmp_path)
    host = manifest.allowed_hosts[0]
    for key, cand in by_key.items():
        if cand.document_url:  # candidates with a manifest attachment URL
            env.transport.route(host, path_of(cand.document_url), Resp(200, PDF_HEADERS, pdf(key)))
    for n, key in enumerate(("SEBI-MF-REG2026-20260401", "SEBI-MF-REG2026-LASTAMENDED-20260707")):
        assert by_key[key].document_url is None and by_key[key].canonical_url
        html = f'<html><a href="/sebi_data/attachdocs/synthetic/synthetic-{n}.pdf">PDF</a></html>'  # synthetic, never a real SEBI URL
        env.transport.route(
            host, path_of(by_key[key].canonical_url), Resp(200, {"Content-Type": "text/html; charset=utf-8"}, html.encode())
        )
        env.transport.route(host, f"/sebi_data/attachdocs/synthetic/synthetic-{n}.pdf", Resp(200, PDF_HEADERS, pdf(key)))
    return env, by_key


def run(env: Env, by_key: dict[str, ManifestCandidate], keys: tuple[str, ...]):  # type: ignore[no-untyped-def]
    manifest = load_manifest(MANIFEST).manifest
    return env.run(manifest_obj=manifest, mode=RunMode.DRY_RUN, dry_run_limits=LIMITS, selected_candidate_keys=keys, authorisation_ref=None)


def test_the_five_key_selection_executes_exactly_those_five_in_order(world) -> None:  # type: ignore[no-untyped-def]
    env, by_key = world
    report = run(env, by_key, FIVE)
    assert report.run.status is RunStatus.COMPLETED
    assert [r.candidate_key for r in report.results] == [f"sebi-mutual-funds/{k}" for k in FIVE]
    assert all(r.outcome is IngestOutcome.NEW_VERSION for r in report.results)
    assert report.run.stats["candidates_planned"] == 5


def test_the_five_key_run_requests_only_the_expected_urls_and_never_the_tier_c_draft(world) -> None:  # type: ignore[no-untyped-def]
    env, by_key = world
    run(env, by_key, FIVE)
    excluded = by_key[EXCLUDED]
    assert excluded.tier == "C" and excluded.document_url  # the premise: it WOULD be fetched by an all-candidates run
    requested = [r.target for r in env.transport.requests]
    assert path_of(excluded.document_url) not in requested and path_of(excluded.canonical_url or "") not in requested
    assert requested == [
        path_of(by_key["SEBI-MF-MC-20260320"].document_url or ""),
        path_of(by_key["SEBI-MF-MC-20240627"].document_url or ""),
        path_of(by_key["SEBI-MF-CIR-20260519-MCR"].document_url or ""),
        path_of(by_key["SEBI-MF-REG2026-20260401"].canonical_url or ""),
        "/sebi_data/attachdocs/synthetic/synthetic-0.pdf",
        path_of(by_key["SEBI-MF-REG2026-LASTAMENDED-20260707"].canonical_url or ""),
        "/sebi_data/attachdocs/synthetic/synthetic-1.pdf",
    ]
    assert {r.host for r in env.transport.requests} == {"www.sebi.gov.in"}


def test_audit_purposes_follow_the_request_kind(world) -> None:  # type: ignore[no-untyped-def]
    env, by_key = world
    run(env, by_key, FIVE)
    purposes = [r.purpose for r in env.repo.fetch_requests()]
    assert purposes == [FetchPurpose.ATTACHMENT] * 3 + [FetchPurpose.DETAIL_PAGE, FetchPurpose.ATTACHMENT] * 2


def test_control_selecting_the_draft_explicitly_would_fetch_it_so_the_exclusion_is_meaningful(world) -> None:  # type: ignore[no-untyped-def]
    env, by_key = world
    run(env, by_key, (*FIVE, EXCLUDED))
    assert path_of(by_key[EXCLUDED].document_url or "") in [r.target for r in env.transport.requests]


def test_caller_order_wins_over_manifest_order(world) -> None:  # type: ignore[no-untyped-def]
    env, by_key = world
    reordered = (FIVE[4], FIVE[0], FIVE[2], FIVE[1], FIVE[3])
    report = run(env, by_key, reordered)
    assert [r.candidate_key for r in report.results] == [f"sebi-mutual-funds/{k}" for k in reordered]


@pytest.mark.parametrize("bad", [None, (), (*FIVE, FIVE[0]), (*FIVE, "SEBI-MF-NOT-A-REAL-KEY")])
def test_invalid_selection_on_the_real_manifest_executes_nothing(world, bad) -> None:  # type: ignore[no-untyped-def]
    env, by_key = world
    report = run(env, by_key, bad)  # type: ignore[arg-type]
    assert report.results == () and env.transport.requests == [] and env.resolver.calls == []
