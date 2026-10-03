"""Provenance status labels and trust classes (docs/architecture.md §8, manifest status vocabulary)."""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class StatusLabel(StrEnum):
    """Manifest status vocabulary. Labels are copied through ingestion unchanged; code never upgrades one (H7).

    VERIFIED   read directly from the primary source.
    OBSERVED   seen in a non-canonical source, or only as a pointer inside another document.
    INFERRED   engineering deduction from observations.
    UNVERIFIED not checked / could not be checked.
    """

    VERIFIED = "VERIFIED"
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    UNVERIFIED = "UNVERIFIED"

    @classmethod
    def parse(cls, value: object) -> StatusLabel:
        """Strict parse: exact spelling only (no case folding, no aliases), so a typo can never become a stronger label."""
        if isinstance(value, str):
            try:
                return cls(value)
            except ValueError:
                pass
        raise ValueError(f"not a status label: {value!r}")


@unique
class TrustClass(StrEnum):
    """Every artifact carries an explicit trust class (H8). Derived data never overwrites SOURCE data."""

    SOURCE = "SOURCE"
    DERIVED_DETERMINISTIC = "DERIVED_DETERMINISTIC"
    DERIVED_MODEL = "DERIVED_MODEL"
    RUNTIME = "RUNTIME"
