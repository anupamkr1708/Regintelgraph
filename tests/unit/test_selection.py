"""Explicit candidate selection (packages/ingestion/selection.py): pure, in-memory, fail closed."""

from __future__ import annotations

import pytest

from packages.ingestion.errors import SelectionError
from packages.ingestion.selection import select_candidates
from tests.support.builders import candidate, manifest

M = manifest([candidate(k, index=i) for i, k in enumerate(("TEST-A", "TEST-B", "TEST-C", "TEST-D"))])


def codes(keys: object) -> str:
    with pytest.raises(SelectionError) as info:
        select_candidates(M, keys)  # type: ignore[arg-type]
    return info.value.code


def test_none_is_refused_and_never_means_all() -> None:
    assert codes(None) == "SELECTION_NOT_PROVIDED"


@pytest.mark.parametrize("empty", [(), []])
def test_empty_selection_is_refused_and_never_means_all(empty: object) -> None:
    assert codes(empty) == "SELECTION_EMPTY"


def test_unknown_key_is_refused_without_fuzzy_matching() -> None:
    assert codes(("TEST-A", "TEST-Z")) == "SELECTION_UNKNOWN_KEY"
    assert codes(("test-a",)) == "SELECTION_UNKNOWN_KEY"  # no case folding / normalisation
    assert codes(("testreg/TEST-A",)) == "SELECTION_UNKNOWN_KEY"  # the runtime candidate_key form is not a selection key
    assert codes(("TEST-",)) == "SELECTION_UNKNOWN_KEY"  # no prefix matching


def test_duplicate_key_is_refused_never_collapsed() -> None:
    assert codes(("TEST-A", "TEST-B", "TEST-B")) == "SELECTION_DUPLICATE_KEY"


@pytest.mark.parametrize("bad", ["TEST-A", b"TEST-A", 7, {"TEST-A"}, ("TEST-A", ""), ("TEST-A", None), ("TEST-A", 3)])
def test_malformed_selection_shapes_are_refused(bad: object) -> None:
    assert codes(bad) == "SELECTION_INVALID_TYPE"


def test_a_bare_string_is_not_iterated_as_characters() -> None:
    assert codes("TEST-A") == "SELECTION_INVALID_TYPE"


def test_caller_order_is_preserved_and_not_resorted() -> None:
    assert [c.document_key for c in select_candidates(M, ("TEST-C", "TEST-A", "TEST-D"))] == ["TEST-C", "TEST-A", "TEST-D"]
    assert [c.document_key for c in select_candidates(M, ["TEST-D", "TEST-B"])] == ["TEST-D", "TEST-B"]


def test_only_the_selected_candidates_are_returned() -> None:
    assert {c.document_key for c in select_candidates(M, ("TEST-B",))} == {"TEST-B"}


def test_hostile_key_text_is_neutralised_in_the_error() -> None:
    with pytest.raises(SelectionError) as info:
        select_candidates(M, ("TEST-A\nFORGED log line",))
    assert "\n" not in str(info.value)
