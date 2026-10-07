"""Explicit candidate selection: the ONLY execution boundary of an ingest run (Phase 1D).

The manifest is a source INVENTORY; which of its candidates a run executes is a separate, runtime decision made by the caller.
There is deliberately no fallback to "every candidate": a missing (`None`) or empty selection is an error, never "all". Neither
`sampled`, `tier` nor the presence of a `document_url` selects or authorises anything. Pure: no I/O, no network, no database.

Contract: `keys` are manifest `document_key` values; unknown keys are rejected (no fuzzy matching, no normalisation); duplicates are
rejected (never collapsed); the caller's order is preserved exactly (never re-sorted by manifest order, date, tier or URL).
"""

from __future__ import annotations

from collections.abc import Sequence

from packages.domain.manifest import ManifestCandidate, SourceManifest
from packages.ingestion.errors import SelectionError
from packages.ingestion.logsafe import neutralize

_SHOWN_KEY = 80


def select_candidates(manifest: SourceManifest, keys: Sequence[str] | None) -> tuple[ManifestCandidate, ...]:
    """Return the manifest candidates named by `keys`, in caller order, or raise `SelectionError` (fail closed)."""
    if keys is None:
        raise SelectionError("SELECTION_NOT_PROVIDED", "no candidate selection was supplied (there is no implicit 'all candidates')")
    if isinstance(keys, str) or not isinstance(keys, tuple | list):
        raise SelectionError("SELECTION_INVALID_TYPE", "the selection must be a sequence of candidate keys")
    if not keys:
        raise SelectionError("SELECTION_EMPTY", "the candidate selection is empty (an empty selection never means 'all')")
    if not all(isinstance(k, str) and k for k in keys):
        raise SelectionError("SELECTION_INVALID_TYPE", "every selected candidate key must be a non-empty string")
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise SelectionError("SELECTION_DUPLICATE_KEY", f"candidate key selected more than once: {neutralize(key, max_len=_SHOWN_KEY)}")
        seen.add(key)
    by_key = {c.document_key: c for c in manifest.candidates}
    for key in keys:
        if key not in by_key:
            raise SelectionError("SELECTION_UNKNOWN_KEY", f"candidate key is not in the manifest: {neutralize(key, max_len=_SHOWN_KEY)}")
    return tuple(by_key[key] for key in keys)
