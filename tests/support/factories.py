from __future__ import annotations

from dataclasses import replace

from packages.domain.manifest import SafetyLimits
from packages.ingestion.egress import EgressClient
from tests.support.builders import HOST, PREFIXES, SMALL_LIMITS
from tests.support.fakes import FakeClock, FakeResolver, FakeTransport, SeededRng

UA = "RegIntelGraph-ingest/1 (research; contact: test@testreg.example)"


class Rig:
    """An EgressClient wired to fakes, plus handles on every fake for assertions."""

    def __init__(self, limits: SafetyLimits = SMALL_LIMITS, *, rng: SeededRng | None = None, resolver: FakeResolver | None = None) -> None:
        self.clock = FakeClock()
        self.resolver = resolver or FakeResolver()
        self.transport = FakeTransport(self.clock)
        self.limits = limits
        self.client = EgressClient(
            resolver=self.resolver,
            transport=self.transport,
            clock=self.clock,
            rng=rng or SeededRng(),
            allowed_hosts=(HOST,),
            path_prefixes=PREFIXES,
            listing_urls=(f"https://{HOST}/list/index",),
            limits=limits,
            user_agent=UA,
        )


def limits_with(**changes: float) -> SafetyLimits:
    return replace(SMALL_LIMITS, **changes)  # type: ignore[arg-type]


URL = f"https://{HOST}/files/a.pdf"
