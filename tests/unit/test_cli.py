"""CLI (packages/ingestion/cli.py): parsing, the pure dry-run plan, input validation. No network, no database."""

from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path

import pytest

from packages.ingestion.cli import EXIT_INVALID, EXIT_OK, build_parser, main
from packages.ingestion.pipeline import Dependencies

MANIFEST = str(Path(__file__).resolve().parents[2] / "data" / "manifests" / "sebi-mutual-funds.yaml")
FIVE = [
    "SEBI-MF-MC-20260320",
    "SEBI-MF-MC-20240627",
    "SEBI-MF-CIR-20260519-MCR",
    "SEBI-MF-REG2026-20260401",
    "SEBI-MF-REG2026-LASTAMENDED-20260707",
]


def argv(mode: str, keys: list[str], *extra: str, manifest: str = MANIFEST) -> list[str]:
    out = ["--mode", mode, "--manifest", manifest]
    for k in keys:
        out += ["--candidate-key", k]
    return [*out, *extra]


def call(args: list[str], **kw: object) -> tuple[int, str]:
    buf = io.StringIO()
    code = main(args, out=buf, **kw)  # type: ignore[arg-type]
    return code, buf.getvalue()


def never_built(_: Mapping[str, str]) -> Dependencies:  # a dry-run must not even construct dependencies
    raise AssertionError("dependencies were constructed in a dry-run / invalid-input path")


def test_parser_collects_repeated_candidate_keys_in_order_and_parses_mode() -> None:
    ns = build_parser().parse_args(argv("dry-run", ["B", "A", "C"]))
    assert (ns.mode, ns.manifest, ns.candidate_keys, ns.authorisation_ref) == ("dry-run", MANIFEST, ["B", "A", "C"], None)
    assert build_parser().parse_args(argv("live", ["A"], "--authorisation-ref", "REF", "--code-version", "v")).authorisation_ref == "REF"


@pytest.mark.parametrize(
    "bad",
    [
        [],
        ["--mode", "dry-run"],
        ["--mode", "bogus", "--manifest", MANIFEST, "--candidate-key", "A"],
        ["--manifest", MANIFEST, "--candidate-key", "A"],
    ],
)
def test_missing_or_invalid_arguments_are_a_usage_error(bad: list[str]) -> None:
    with pytest.raises(SystemExit) as info:
        build_parser().parse_args(bad)
    assert info.value.code == 2


def test_there_is_no_all_flag_and_no_candidate_key_is_a_usage_error() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--mode", "dry-run", "--manifest", MANIFEST])
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv("dry-run", [], "--all"))


def test_dry_run_prints_the_pure_plan_in_caller_order_and_exits_ok() -> None:
    code, text = call(argv("dry-run", FIVE), deps_factory=never_built)
    assert code == EXIT_OK
    for line in ("mode: dry-run", "selected_candidates: 5", "candidate_selection: VALID", "network: disabled", "persistence: disabled",
                 "live_gate: closed", "crawl_configuration: not configured (16 of 16 parameters unset)", "live_ready: false"):  # fmt: skip
        assert line in text.splitlines()
    plan = [ln.strip() for ln in text.splitlines() if ln.startswith("  ") and ". SEBI-MF-" in ln]
    assert [p.split(": ")[0].split(". ")[1] for p in plan] == FIVE
    assert plan[0].endswith("WOULD_FETCH_ATTACHMENT") and plan[3].endswith("WOULD_FETCH_DETAIL_PAGE_THEN_DISCOVER")
    assert "SEBI-MF-DRAFTCIR-202605-THIRDPARTY-PAYMENTS" not in text


def test_dry_run_never_claims_retrieval_or_authorisation() -> None:
    _, text = call(argv("dry-run", FIVE))
    lowered = text.lower()
    assert "nothing was retrieved, downloaded, ingested or authorised" in lowered
    for banned in ("retrieved: ", "downloaded: ", "ingested: ", "authorised: true", "live_ready: true", "live_gate: open"):
        assert banned not in lowered


def test_dry_run_preserves_a_non_manifest_order() -> None:
    _, text = call(argv("dry-run", [FIVE[4], FIVE[0]]))
    assert text.index(FIVE[4]) < text.index(FIVE[0])


@pytest.mark.parametrize(
    ("keys", "code_text"),
    [([FIVE[0], "SEBI-MF-NOPE"], "SELECTION_UNKNOWN_KEY"), ([FIVE[0], FIVE[0]], "SELECTION_DUPLICATE_KEY")],
)
@pytest.mark.parametrize("mode", ["dry-run", "live"])
def test_invalid_selection_fails_before_any_pipeline_or_wiring(mode: str, keys: list[str], code_text: str) -> None:
    code, text = call(argv(mode, keys, "--code-version", "v"), deps_factory=never_built)
    assert code == EXIT_INVALID and code_text in text and "nothing was fetched" in text


def test_missing_manifest_is_an_input_error() -> None:
    code, text = call(argv("dry-run", FIVE, manifest="/nonexistent/manifest.yaml"), deps_factory=never_built)
    assert code == EXIT_INVALID and "manifest could not be loaded" in text


def test_dry_run_rejects_an_authorisation_ref_rather_than_pretending() -> None:
    code, text = call(argv("dry-run", FIVE, "--authorisation-ref", "SOME-RUNTIME-REF"), deps_factory=never_built)
    assert code == EXIT_INVALID and "SOME-RUNTIME-REF" not in text


def test_live_requires_an_explicit_code_version_and_never_guesses_one() -> None:
    code, text = call(argv("live", FIVE), deps_factory=never_built)
    assert code == EXIT_INVALID and "--code-version is required" in text
