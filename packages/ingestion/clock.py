"""Real clock and id factory. Tests inject `fakes.FakeClock` instead; no module reads the wall clock directly."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from random import SystemRandom

from packages.ingestion.ports import Clock, Rng


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


def system_rng() -> Rng:
    return SystemRandom()


def new_uuid7(now: datetime, rng: Rng) -> uuid.UUID:
    """RFC 9562 UUID v7: 48-bit unix-ms timestamp + random bits (Python 3.12 has no stdlib uuid7). Time is injected."""
    millis = int(now.timestamp() * 1000) & ((1 << 48) - 1)
    raw = bytearray(millis.to_bytes(6, "big") + rng.randbytes(10))
    raw[6] = (raw[6] & 0x0F) | 0x70  # version 7
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC variant
    return uuid.UUID(bytes=bytes(raw))


__all__ = ["Clock", "SystemClock", "new_uuid7", "system_rng"]
