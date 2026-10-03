from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from packages.domain.content_hash import ContentHash
from packages.domain.ingest import AbortReason, FetchFailure, IngestOutcome, IngestRun, RunMode, RunStatus
from packages.domain.manifest import CRAWL_PARAMETER_NAMES, CrawlConfig
from packages.domain.status import StatusLabel
from tests.support.builders import manifest

H = "a" * 64


def test_content_hash_accepts_only_64_lowercase_hex() -> None:
    assert ContentHash(H).relative_path == f"aa/aa/{H}"
    for bad in ["", "A" * 64, "a" * 63, "a" * 65, "g" * 64, H + "\n", " " + H, "../" + "a" * 61, "a" * 62 + "/."]:
        with pytest.raises(ValueError):
            ContentHash(bad)
    with pytest.raises(ValueError):
        ContentHash.parse(123)


def test_domain_dataclasses_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        ContentHash(H).value = "b" * 64  # type: ignore[misc]
    m = manifest()
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.ingestion_authorized = True  # type: ignore[misc]


def test_status_labels_are_strict_and_never_upgraded() -> None:
    assert StatusLabel.parse("OBSERVED") is StatusLabel.OBSERVED
    for bad in ["observed", "Verified", "VERIFIED ", "", None, 1, "CONFIRMED"]:
        with pytest.raises(ValueError):
            StatusLabel.parse(bad)


def test_ingest_run_invariants() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    base = dict(
        ingest_run_id=uuid.uuid4(),
        source_id="s",
        mode=RunMode.LIVE,
        started_at=now,
        manifest_hash=H,
        manifest_version="v",
        code_version="c",
        safety_policy_version="p",
    )
    IngestRun(**base)  # type: ignore[arg-type]
    with pytest.raises(ValueError):  # ABORTED without a reason
        IngestRun(**base, status=RunStatus.ABORTED)  # type: ignore[arg-type]
    with pytest.raises(ValueError):  # a reason without ABORTED
        IngestRun(**base, abort_reason=AbortReason.GATE_CLOSED)  # type: ignore[arg-type]
    with pytest.raises(ValueError):  # naive datetime
        IngestRun(**{**base, "started_at": datetime(2026, 1, 1)})  # type: ignore[arg-type]
    with pytest.raises(ValueError):  # non-UTC offset
        IngestRun(**{**base, "started_at": datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=5, minutes=30)))})  # type: ignore[arg-type]


def test_only_declared_failures_are_retryable() -> None:
    retryable = {f for f in FetchFailure if f.retryable}
    assert retryable == {FetchFailure.HTTP_TRANSIENT, FetchFailure.TIMEOUT, FetchFailure.TLS_ERROR}


def test_failure_outcomes_make_a_run_completed_with_failures() -> None:
    assert {o for o in IngestOutcome if o.is_failure} == {
        IngestOutcome.FAILED_TRANSIENT,
        IngestOutcome.FAILED_PERMANENT,
        IngestOutcome.QUARANTINED,
    }


def test_gate_is_closed_unless_every_condition_holds() -> None:
    assert manifest(gate_open=True).gate_open
    assert not manifest(gate_open=False).gate_open
    assert not manifest(gate_open=True, authorized=False).gate_open  # ingestion_authorized alone gates everything
    assert any("ingestion_authorized" in r for r in manifest(gate_open=False).gate_closed_reasons())


def test_crawl_limits_fail_closed_when_any_parameter_is_unset() -> None:
    assert CrawlConfig("NOT_CONFIGURED", "RIG_X").limits() is None
    full = manifest().crawl
    assert full.limits() is not None
    partial = CrawlConfig("CONFIGURED", "RIG_X", full.parameters[:-1])
    assert partial.limits() is None and len(partial.missing_parameters()) == 1
    assert set(CRAWL_PARAMETER_NAMES) == {n for n, _ in full.parameters}
    with pytest.raises(ValueError):
        CrawlConfig("CONFIGURED", "not an env name", ())
