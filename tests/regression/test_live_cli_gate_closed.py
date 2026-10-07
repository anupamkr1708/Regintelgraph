"""REGRESSION: with today's manifest the LIVE CLI aborts GATE_CLOSED before any resolver/transport is used, however well-formed the
selection, the runtime authorisation reference, the crawler contact and the runtime environment variables are. Presence of
configuration never opens the access-review gate."""

from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path

import pytest

from packages.domain.ingest import AbortReason
from packages.ingestion.cli import EXIT_INVALID, EXIT_NOT_COMPLETED, main
from packages.ingestion.errors import WiringError
from packages.ingestion.pipeline import Dependencies
from packages.ingestion.repository import InMemoryRepository
from packages.ingestion.wiring import ENV_BLOB_ROOT, ENV_DATABASE_URL, ENV_QUARANTINE_ROOT, REQUIRED_ENV, build_live_dependencies
from tests.support.fakes import FakeBlobStore, FakeClock, SeededRng, TripwireResolver, TripwireTransport

MANIFEST = str(Path(__file__).resolve().parents[2] / "data" / "manifests" / "sebi-mutual-funds.yaml")
FIVE = [
    "SEBI-MF-MC-20260320",
    "SEBI-MF-MC-20240627",
    "SEBI-MF-CIR-20260519-MCR",
    "SEBI-MF-REG2026-20260401",
    "SEBI-MF-REG2026-LASTAMENDED-20260707",
]
RUNTIME_REF = "RUNTIME-SUPPLIED-REFERENCE-FOR-TEST"
ENV = {
    "RIG_CRAWLER_CONTACT_EMAIL": "operator@example.org",
    ENV_DATABASE_URL: "postgresql://placeholder",
    ENV_BLOB_ROOT: "/placeholder/blobs",
    ENV_QUARANTINE_ROOT: "/placeholder/quarantine",
}


class Rig:
    def __init__(self) -> None:
        self.resolver, self.transport, self.repo = TripwireResolver(), TripwireTransport(), InMemoryRepository()
        self.built = 0

    def factory(self, _: Mapping[str, str]) -> Dependencies:
        self.built += 1
        return Dependencies(FakeClock(), SeededRng(), self.resolver, self.transport, FakeBlobStore(), FakeBlobStore(), self.repo)  # type: ignore[arg-type]


def live(rig: Rig, keys: list[str], *extra: str, env: Mapping[str, str] = ENV) -> tuple[int, str]:
    args = ["--mode", "live", "--manifest", MANIFEST, "--code-version", "test-sha"]
    for k in keys:
        args += ["--candidate-key", k]
    buf = io.StringIO()
    return main([*args, *extra], env=env, deps_factory=rig.factory, out=buf), buf.getvalue()


def test_live_with_valid_selection_authorisation_contact_and_env_is_still_gate_closed_with_zero_network() -> None:
    rig = Rig()
    code, text = live(rig, FIVE, "--authorisation-ref", RUNTIME_REF)
    assert code == EXIT_NOT_COMPLETED
    assert "result: ABORTED(GATE_CLOSED)" in text and "network_requests: 0" in text
    assert (rig.resolver.calls, rig.transport.calls) == (0, 0)
    (run,) = rig.repo.runs.values()
    assert run.abort_reason is AbortReason.GATE_CLOSED and run.stats["candidates_planned"] == 5
    assert rig.repo.fetch_requests() == [] and rig.repo.counts()["raw_artifact"] == 0
    assert RUNTIME_REF not in text  # the authorisation reference is never printed


def test_live_without_an_authorisation_ref_is_equally_gate_closed_the_gate_does_not_depend_on_it() -> None:
    rig = Rig()
    code, text = live(rig, FIVE)
    assert code == EXIT_NOT_COMPLETED and "ABORTED(GATE_CLOSED)" in text and (rig.resolver.calls, rig.transport.calls) == (0, 0)


def test_live_with_an_invalid_selection_never_builds_dependencies() -> None:
    rig = Rig()
    code, _ = live(rig, [*FIVE, "SEBI-MF-NOT-A-KEY"], "--authorisation-ref", RUNTIME_REF)
    assert code == EXIT_INVALID and rig.built == 0 and (rig.resolver.calls, rig.transport.calls) == (0, 0)


def test_live_never_prints_the_gate_as_open_and_does_not_fabricate_crawl_limits() -> None:
    rig = Rig()
    _, text = live(rig, FIVE, "--authorisation-ref", RUNTIME_REF)
    assert "live_gate: open" not in text and "COMPLETED" not in text.replace("ABORTED", "")


@pytest.mark.parametrize("missing", REQUIRED_ENV)
def test_default_wiring_requires_every_runtime_variable_and_names_only_variables(missing: str) -> None:
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(WiringError) as info:
        build_live_dependencies(env)
    assert missing in str(info.value) and "placeholder" not in str(info.value)


def test_default_wiring_rejects_nested_blob_and_quarantine_roots_before_any_database_connection(tmp_path: Path) -> None:
    env = {**ENV, ENV_BLOB_ROOT: str(tmp_path / "blobs"), ENV_QUARANTINE_ROOT: str(tmp_path / "blobs" / "quarantine")}
    (tmp_path / "blobs").mkdir()
    with pytest.raises(WiringError):
        build_live_dependencies(env)  # would raise a psycopg error if it got as far as connecting to the placeholder URL


def test_cli_surfaces_a_wiring_error_as_an_input_error_without_echoing_values() -> None:
    buf = io.StringIO()
    code = main(
        ["--mode", "live", "--manifest", MANIFEST, "--code-version", "v", "--candidate-key", FIVE[0]],
        env={"RIG_DATABASE_URL": "postgresql://" + "u" + ":" + "SECRETVALUE" + "@host/db"},  # built so no credential literal sits in source
        out=buf,
    )
    assert code == EXIT_INVALID and "SECRETVALUE" not in buf.getvalue() and "RIG_BLOB_ROOT" in buf.getvalue()
