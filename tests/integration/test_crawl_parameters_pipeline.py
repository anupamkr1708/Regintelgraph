"""Pipeline-level proof that each of the 16 crawl-safety parameters is mandatory (run on both repositories).
Model-level checks (no defaults, value validation) live in tests/security/test_crawl_parameters_fail_closed.py."""

from __future__ import annotations

import dataclasses

import pytest

from packages.domain.ingest import AbortReason, RunStatus
from packages.domain.manifest import CRAWL_PARAMETER_NAMES, CrawlConfig
from tests.support.builders import CONTACT_ENV, limits_params, manifest
from tests.support.env import Env, one
from tests.support.fakes import TripwireResolver, TripwireTransport

PARAMS = dict(limits_params())


@pytest.mark.parametrize("missing", CRAWL_PARAMETER_NAMES)
def test_a_live_run_is_refused_when_any_single_parameter_is_missing(env: Env, missing: str) -> None:
    resolver, transport = TripwireResolver(), TripwireTransport()
    env.resolver, env.transport = resolver, transport  # type: ignore[assignment]
    partial = tuple(sorted((k, v) for k, v in PARAMS.items() if k != missing))
    m = dataclasses.replace(manifest([one()], gate_open=True), crawl=CrawlConfig("CONFIGURED", CONTACT_ENV, partial))
    report = env.run(manifest_obj=m)
    assert report.run.status is RunStatus.ABORTED and report.run.abort_reason is AbortReason.POLICY_VIOLATION
    assert missing in str(report.run.stats["policy_violation"])  # the refusal names exactly what is missing
    assert (resolver.calls, transport.calls) == (0, 0) and env.repo.fetch_requests() == [] and report.results == ()


def test_complete_parameters_with_status_not_configured_are_still_refused(env: Env) -> None:
    resolver, transport = TripwireResolver(), TripwireTransport()
    env.resolver, env.transport = resolver, transport  # type: ignore[assignment]
    m = dataclasses.replace(
        manifest([one()], gate_open=True), crawl=CrawlConfig("NOT_CONFIGURED", CONTACT_ENV, tuple(sorted(PARAMS.items())))
    )
    report = env.run(manifest_obj=m)
    assert report.run.abort_reason is AbortReason.POLICY_VIOLATION and (resolver.calls, transport.calls) == (0, 0)
