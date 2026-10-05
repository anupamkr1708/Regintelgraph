"""REGRESSION (mandatory): a closed gate performs no request, therefore it leaves NO `fetch_request` row.

Complements test_gate_closed_zero_network: the audit trail must not claim requests that never happened, and the closed-gate
abort must not write audit rows as a side effect. Uses the REAL committed manifest so opening the gate is noticed by a human.
"""

from __future__ import annotations

from pathlib import Path

from packages.domain.ingest import AbortReason, RunStatus
from packages.ingestion.pipeline import run_ingest
from tests.regression.test_gate_closed_zero_network import build
from tests.support.fakes import TripwireResolver, TripwireTransport


def test_closed_gate_leaves_zero_fetch_requests(tmp_path: Path) -> None:
    resolver, transport = TripwireResolver(), TripwireTransport()
    config, deps, repo = build(tmp_path, resolver, transport)
    report = run_ingest(config, deps)
    assert report.run.status is RunStatus.ABORTED and report.run.abort_reason is AbortReason.GATE_CLOSED
    assert report.results == () and repo.fetch_requests() == []
    assert (resolver.calls, transport.calls) == (0, 0)
