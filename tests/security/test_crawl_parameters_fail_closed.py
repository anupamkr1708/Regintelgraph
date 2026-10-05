"""All 16 crawl-safety parameters are mandatory: missing -> the LIVE run is refused, never defaulted
(docs/phase1/crawl-safety-parameters.md; AGENTS.md H14; source-safety-contract §7/§8)."""

from __future__ import annotations

import dataclasses
import math
from typing import Any

import pytest

from packages.domain.manifest import CRAWL_PARAMETER_NAMES, SafetyLimits
from tests.support.builders import limits_params

PARAMS = dict(limits_params())


def test_there_are_exactly_sixteen_named_parameters_and_the_documented_two_are_among_them() -> None:
    assert len(CRAWL_PARAMETER_NAMES) == 16 == len(set(CRAWL_PARAMETER_NAMES))
    assert {"max_expansion_ratio", "pdf_eof_tail_bytes"} <= set(CRAWL_PARAMETER_NAMES)
    assert set(PARAMS) == set(CRAWL_PARAMETER_NAMES)  # the test builder supplies exactly the model's names


def test_no_parameter_has_a_default_value_anywhere_in_the_model() -> None:
    for f in dataclasses.fields(SafetyLimits):
        assert f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING, f.name
    with pytest.raises(TypeError):
        SafetyLimits()  # type: ignore[call-arg]


@pytest.mark.parametrize("name", CRAWL_PARAMETER_NAMES)
@pytest.mark.parametrize("bad", [0, -1, math.nan, math.inf, True, "5", None], ids=lambda v: repr(v))
def test_every_parameter_rejects_zero_negative_non_finite_boolean_and_non_numeric_values(name: str, bad: Any) -> None:
    with pytest.raises(ValueError, match=name):
        SafetyLimits(**{**PARAMS, name: bad})


@pytest.mark.parametrize("name", [n for n in CRAWL_PARAMETER_NAMES if isinstance(PARAMS[n], int)])
def test_integer_parameters_reject_fractions(name: str) -> None:
    with pytest.raises(ValueError, match=name):
        SafetyLimits(**{**PARAMS, name: 2.5})


def test_backoff_cap_may_not_be_below_the_base() -> None:
    with pytest.raises(ValueError, match="backoff_max_seconds"):
        SafetyLimits(**{**PARAMS, "backoff_base_seconds": 4.0, "backoff_max_seconds": 2.0})


def test_zero_redirects_is_not_expressible_and_that_is_documented_behaviour() -> None:
    """`max_redirects` must be >= 1 like every other parameter. 'Follow no redirects' is therefore not configurable; a redirect that
    is not wanted is handled by the exact host/path allowlist (every hop is validated), not by this knob. Pinned so a change is deliberate."""
    with pytest.raises(ValueError, match="max_redirects"):
        SafetyLimits(**{**PARAMS, "max_redirects": 0})
