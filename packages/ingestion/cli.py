"""Thin command-line entry point:  python -m packages.ingestion.cli --mode {dry-run,live} --manifest PATH --candidate-key KEY [...]

Orchestration only. It parses arguments, loads the manifest, validates the explicit candidate selection, then either
  * dry-run: prints a PURE plan (no resolver/transport/repository is constructed, nothing is written, no network is possible), or
  * live:    builds the real dependencies (wiring.py) and calls the existing `run_ingest`, which owns EVERY gate.
It contains no scraping, HTML parsing, URL policy, transport, retry or database logic, never opens the access gate, never supplies
crawl limits, and never invents an authorisation reference: `--authorisation-ref` is passed through verbatim and never printed.

Exit codes:  0 = dry-run planned / run completed;  1 = run did not complete (aborted e.g. GATE_CLOSED, or failures);
             2 = invalid input/config.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from packages.domain.ingest import RunMode, RunStatus
from packages.domain.manifest import CRAWL_PARAMETER_NAMES, ManifestCandidate
from packages.ingestion.errors import IngestError, SelectionError, WiringError
from packages.ingestion.logsafe import neutralize
from packages.ingestion.manifest import LoadedManifest, load_manifest
from packages.ingestion.pipeline import Dependencies, IngestConfig, plan_candidates, run_ingest
from packages.ingestion.selection import select_candidates
from packages.ingestion.wiring import build_live_dependencies

EXIT_OK = 0
EXIT_NOT_COMPLETED = 1
EXIT_INVALID = 2

DepsFactory = Callable[[Mapping[str, str]], Dependencies]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m packages.ingestion.cli", description="Controlled, explicitly-scoped source ingestion.")
    parser.add_argument("--mode", choices=("dry-run", "live"), required=True)
    parser.add_argument("--manifest", required=True, help="path to the source manifest YAML")
    parser.add_argument(
        "--candidate-key",
        action="append",
        required=True,
        dest="candidate_keys",
        metavar="KEY",
        help="manifest document_key to execute; repeat once per candidate; execution order = argument order; there is no 'all'",
    )
    parser.add_argument("--authorisation-ref", default=None, help="live only: runtime-supplied authorisation reference (never printed)")
    parser.add_argument("--code-version", default=None, help="live only: code version recorded on the run (e.g. a git commit id)")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    deps_factory: DepsFactory | None = None,
    out: TextIO | None = None,
) -> int:
    """`env` and `deps_factory` are test seams; defaults are the process environment and the real wiring."""
    args = build_parser().parse_args(argv)
    stream = out if out is not None else sys.stdout
    environment: Mapping[str, str] = env if env is not None else os.environ

    def say(line: str) -> None:
        print(line, file=stream)

    try:
        loaded = load_manifest(Path(args.manifest))
    except (IngestError, OSError, ValueError) as exc:
        say(f"error: manifest could not be loaded: {neutralize(exc, max_len=200)}")
        return EXIT_INVALID
    keys = tuple(args.candidate_keys)
    try:
        selected = select_candidates(loaded.manifest, keys)
    except SelectionError as exc:
        say(f"error: candidate selection refused ({exc.code}): {neutralize(exc.detail, max_len=200)}")
        say("result: not started (nothing was fetched; no network was used)")
        return EXIT_INVALID

    if args.mode == "dry-run":
        if args.authorisation_ref is not None:
            say("error: --authorisation-ref is not accepted in dry-run (a dry-run has no authorisation and claims none)")
            return EXIT_INVALID
        return _dry_run(args.manifest, loaded, selected, say)
    return _live(args, loaded, keys, environment, deps_factory or build_live_dependencies, say)


def _dry_run(manifest_path: str, loaded: LoadedManifest, selected: tuple[ManifestCandidate, ...], say: Callable[[str], None]) -> int:
    manifest = loaded.manifest
    plan = plan_candidates(manifest, selected)
    gate = manifest.gate_closed_reasons()
    unset = manifest.crawl.missing_parameters()
    limits_ok = manifest.crawl.limits() is not None
    say("mode: dry-run")
    say(f"manifest: {neutralize(manifest_path, max_len=200)}")
    say(f"manifest_sha256: {loaded.content_hash}")
    say(f"selected_candidates: {len(selected)}")
    say("candidate_selection: VALID")
    say("network: disabled")
    say("persistence: disabled")
    say("live_gate: " + ("closed" if gate else "open"))
    for reason in gate:
        say(f"  live_gate_reason: {neutralize(reason, max_len=200)}")
    say(
        "crawl_configuration: "
        + ("configured" if limits_ok else f"not configured ({len(unset)} of {len(CRAWL_PARAMETER_NAMES)} parameters unset)")
    )
    say(
        "live_ready: "
        + ("false" if gate or not limits_ok else "unknown (runtime contact and authorisation reference are not evaluated in dry-run)")
    )
    say("plan (selected, in execution order; planned only):")
    for entry in plan:
        detail = f" [{entry.reason}]" if entry.reason else ""
        say(f"  {entry.position}. {entry.document_key}: {entry.action.value}{detail}")
    say("note: dry-run only plans. Nothing was retrieved, downloaded, ingested or authorised;")
    say("      URL validation is deferred to a run with configured limits.")
    return EXIT_OK


def _live(
    args: argparse.Namespace,
    loaded: LoadedManifest,
    keys: tuple[str, ...],
    environment: Mapping[str, str],
    factory: DepsFactory,
    say: Callable[[str], None],
) -> int:
    if not args.code_version:
        say("error: --code-version is required in live mode (it is recorded on the run; it is never guessed)")
        return EXIT_INVALID
    try:
        deps = factory(environment)  # construction only; nothing is requested here
    except WiringError as exc:
        say(f"error: live wiring failed: {neutralize(exc, max_len=200)}")
        return EXIT_INVALID
    config = IngestConfig(
        manifest=loaded.manifest,
        manifest_hash=loaded.content_hash,
        manifest_ref=args.manifest,
        mode=RunMode.LIVE,
        code_version=args.code_version,
        authorisation_ref=args.authorisation_ref,
        env=environment,
        selected_candidate_keys=keys,
    )
    report = run_ingest(config, deps)  # the pipeline owns the access-review gate, the operational-policy gate and all fetching
    abort = report.run.abort_reason
    say("mode: live")
    say(f"selected_candidates: {len(keys)}")
    say(f"result: {report.run.status.value}" + (f"({abort.value})" if abort else ""))
    if report.run.status is RunStatus.ABORTED and not report.results:
        say("network_requests: 0 (the run aborted before any candidate was processed)")
    say(report.to_text())
    return EXIT_OK if report.run.status is RunStatus.COMPLETED else EXIT_NOT_COMPLETED


if __name__ == "__main__":
    sys.exit(main())
