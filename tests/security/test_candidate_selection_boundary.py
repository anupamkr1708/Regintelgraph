"""SECURITY: explicit candidate selection is the execution boundary. An invalid selection never reaches the network; an omitted
candidate never runs; the caller's order is the execution order."""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.domain.ingest import AbortReason, IngestOutcome, RunStatus
from packages.ingestion.pipeline import run_ingest
from packages.ingestion.repository import InMemoryRepository
from tests.support.builders import candidate, manifest, pdf
from tests.support.env import Env, one
from tests.support.fakes import TripwireResolver, TripwireTransport

KEYS = ("TEST-A", "TEST-B", "TEST-C")


def cands() -> list:  # type: ignore[type-arg]
    return [one(k, index=i) for i, k in enumerate(KEYS)]


@pytest.fixture
def env(tmp_path: Path) -> Env:
    e = Env(InMemoryRepository(), tmp_path)
    for k in KEYS:
        e.serve(k, pdf(k))
    return e


@pytest.mark.parametrize(
    ("selection", "code"),
    [
        (None, "SELECTION_NOT_PROVIDED"),
        ((), "SELECTION_EMPTY"),
        (("TEST-A", "NOPE"), "SELECTION_UNKNOWN_KEY"),
        (("TEST-A", "TEST-A"), "SELECTION_DUPLICATE_KEY"),
    ],
)
def test_invalid_selection_aborts_before_any_network_object_is_touched(selection: object, code: str, tmp_path: Path) -> None:
    e = Env(InMemoryRepository(), tmp_path)
    resolver, transport = TripwireResolver(), TripwireTransport()
    e.resolver, e.transport = resolver, transport  # type: ignore[assignment]
    report = e.run(cands(), selected_candidate_keys=selection)
    assert report.run.status is RunStatus.ABORTED and report.run.abort_reason is AbortReason.POLICY_VIOLATION
    assert code in str(report.run.stats["policy_violation"])
    assert report.results == () and (resolver.calls, transport.calls) == (0, 0)
    assert e.repo.counts()["raw_artifact"] == 0 and e.repo.fetch_requests() == []


def test_invalid_selection_is_refused_even_when_the_gate_is_closed_and_nothing_is_fetched(tmp_path: Path) -> None:
    e = Env(InMemoryRepository(), tmp_path)
    transport = TripwireTransport()
    e.transport = transport  # type: ignore[assignment]
    report = e.run(cands(), manifest_obj=manifest(cands(), gate_open=False), selected_candidate_keys=None)
    assert report.run.abort_reason is AbortReason.POLICY_VIOLATION and transport.calls == 0


def test_omitting_the_selection_does_not_fall_back_to_all_candidates(env: Env) -> None:
    config = env.config(manifest(cands()), selected_candidate_keys=None)
    assert config.selected_candidate_keys is None
    report = run_ingest(config, env.deps())
    assert report.results == () and env.transport.requests == []


def test_an_omitted_candidate_is_never_executed(env: Env) -> None:
    report = env.run(cands(), selected_candidate_keys=("TEST-B",))
    assert [r.candidate_key for r in report.results] == ["testreg/TEST-B"]
    assert [r.target for r in env.transport.requests] == ["/files/test-b.pdf"]
    assert report.run.stats["candidates_planned"] == 1


def test_caller_order_is_the_execution_order(env: Env) -> None:
    report = env.run(cands(), selected_candidate_keys=("TEST-C", "TEST-A", "TEST-B"))
    assert [r.candidate_key for r in report.results] == ["testreg/TEST-C", "testreg/TEST-A", "testreg/TEST-B"]
    assert [r.target for r in env.transport.requests] == ["/files/test-c.pdf", "/files/test-a.pdf", "/files/test-b.pdf"]


def test_tier_sampled_and_document_url_do_not_select_anything(env: Env) -> None:
    extra = [candidate("TEST-TIER-A", index=3, tier="A", sampled=True), candidate("TEST-NOURL", index=4, url=None)]
    env.serve("TEST-TIER-A", pdf("tier"))
    report = env.run([*cands(), *extra], selected_candidate_keys=("TEST-A",))
    assert [r.candidate_key for r in report.results] == ["testreg/TEST-A"]
    assert all("tier" not in r.target for r in env.transport.requests)
    assert all(r.outcome is not IngestOutcome.UNRESOLVED for r in report.results)
