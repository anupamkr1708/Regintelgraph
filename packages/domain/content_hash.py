"""Content hash value type. The only value allowed to contribute to a storage path (source-safety-contract §9, §10)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# fullmatch (not `$`) so a trailing newline can never sneak through.
_HEX64 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ContentHash:
    """Lowercase hexadecimal SHA-256 of the exact received (decoded) bytes: 64 characters, nothing else."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or _HEX64.fullmatch(self.value) is None:
            raise ValueError("content hash must be 64 lowercase hexadecimal characters")

    @classmethod
    def parse(cls, value: object) -> ContentHash:
        if isinstance(value, ContentHash):
            return value
        if not isinstance(value, str):
            raise ValueError("content hash must be a string")
        return cls(value)

    @property
    def relative_path(self) -> str:
        """Fan-out path derived from the hash alone: `ab/cd/<hash>` (never from a URL, title or filename)."""
        return f"{self.value[:2]}/{self.value[2:4]}/{self.value}"

    def __str__(self) -> str:
        return self.value
