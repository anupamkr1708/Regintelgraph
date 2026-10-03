"""Ingest run orchestration: gate -> policy -> per-candidate pipeline (docs/phase1/ingestion-contract.md §0, §3-§8).

Candidate stages: DISCOVERED -> (URL checked, resolved) -> FETCHED -> VALIDATED -> STORED -> VERSIONED. Each candidate is
independent: a failure never destroys another candidate's result, and a later stage never erases an earlier valid artifact.
Phase 1C never marks anything PUBLISHED (that needs parse/index in later phases).

THE GATE IS CHECKED FIRST. A LIVE run whose manifest gate is closed records ABORTED(GATE_CLOSED) and returns before any
resolver, transport or egress object is touched: zero DNS, zero TCP, zero HTTP, zero robots/listing requests.
A DRY_RUN is always permitted but can never become live: it refuses network-capable components and persistent repositories.
"""

from __future__ import annotations

import os
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace

from packages.domain.content_hash import ContentHash
from packages.domain.ingest import (
    AbortReason,
    CandidateStage,
    FetchFailure,
    FetchPurpose,
    IngestOutcome,
    IngestResult,
    IngestRun,
    QuarantineReason,
    RunMode,
    RunStatus,
)
from packages.domain.manifest import ManifestCandidate, SafetyLimits, SourceManifest
from packages.ingestion.clock import new_uuid7
from packages.ingestion.egress import EgressClient, FetchedResponse, build_user_agent
from packages.ingestion.errors import BlobStoreError, CircuitOpenError, FetchError, ModeViolation
from packages.ingestion.logsafe import LOGGER, neutralize
from packages.ingestion.ports import BlobStore, Clock, HttpTransport, Resolver, Rng
from packages.ingestion.repository import ArtifactIngest, QuarantineIngest, Repository
from packages.ingestion.validate import Rejection, sniff_media_type, validate_response

SAFETY_POLICY_VERSION = "source-safety-contract@phase-1b"
DRY_RUN_CONTACT = "dry-run.invalid"
OUT_OF_SCOPE_TIER = "context_out_of_window"


@dataclass(frozen=True, slots=True)
class IngestConfig:
    manifest: SourceManifest
    manifest_hash: ContentHash
    manifest_ref: str
    mode: RunMode
    code_version: str
    safety_policy_version: str = SAFETY_POLICY_VERSION
    authorisation_ref: str | None = None  # recorded human authorisation; mandatory for a LIVE run that actually runs
    dry_run_limits: SafetyLimits | None = None  # synthetic limits: honoured ONLY in DRY_RUN, never for LIVE
    env: Mapping[str, str] | None = None  # defaults to os.environ; the contact VALUE is read here and never stored/logged
    spool_dir: str | None = None


@dataclass(slots=True)
class Dependencies:
    clock: Clock
    rng: Rng
    resolver: Resolver
    transport: HttpTransport
    blobs: BlobStore
    quarantine_blobs: BlobStore
    repo: Repository


@dataclass(frozen=True, slots=True)
class IngestReport:
    run: IngestRun
    results: tuple[IngestResult, ...]
    stage_reached: Mapping[str, CandidateStage]

    @property
    def counts(self) -> dict[str, int]:
        return dict(sorted(Counter(r.outcome.value for r in self.results).items()))

    def to_text(self) -> str:
        """Human-readable summary: counts, failures, quarantine reasons, alerts. Contains no document content."""
        lines = [
            f"run {self.run.ingest_run_id} mode={self.run.mode.value} status={self.run.status.value}"
            + (f" abort_reason={self.run.abort_reason.value}" if self.run.abort_reason else "")
        ]
        for outcome, n in self.counts.items():
            lines.append(f"  {outcome}: {n}")
        for r in self.results:
            if r.outcome.is_failure or r.outcome in (IngestOutcome.UNRESOLVED, IngestOutcome.OUT_OF_SCOPE) or r.alerts:
                alerts = f" alerts={[a.value for a in r.alerts]}" if r.alerts else ""
                lines.append(f"  - {r.candidate_key}: {r.outcome.value} ({r.reason_code or '-'}){alerts}")
        return "\n".join(lines)


def _resolve_contact(config: IngestConfig) -> str | None:
    env = config.env if config.env is not None else os.environ
    value = env.get(config.manifest.crawl.contact_env_var, "").strip()
    return value or None


def run_ingest(config: IngestConfig, deps: Dependencies) -> IngestReport:
    manifest, clock = config.manifest, deps.clock
    run_id = new_uuid7(clock.now(), deps.rng)
    base = IngestRun(
        ingest_run_id=run_id,
        source_id=manifest.source_id,
        mode=config.mode,
        started_at=clock.now(),
        manifest_hash=config.manifest_hash.value,
        manifest_version=manifest.manifest_version,
        code_version=config.code_version,
        safety_policy_version=config.safety_policy_version,
        authorisation_ref=config.authorisation_ref,
    )

    # ---- 1. mode guards and THE GATE (nothing below this block has run yet: no network object has been touched) ---------
    if config.mode is RunMode.DRY_RUN:
        if deps.repo.persistent:
            raise ModeViolation("a DRY_RUN must not write to a persistent repository (synthetic content must never reach the source layer)")
        if getattr(deps.resolver, "performs_network", True) or getattr(deps.transport, "performs_network", True):
            raise ModeViolation("a DRY_RUN must not be given a network-capable resolver or transport")
        limits = config.dry_run_limits or manifest.crawl.limits()
        contact: str | None = DRY_RUN_CONTACT
    else:
        reasons = manifest.gate_closed_reasons()
        if reasons:
            deps.repo.ensure_source(manifest, config.manifest_ref, clock.now())
            aborted = replace(
                base,
                status=RunStatus.ABORTED,
                abort_reason=AbortReason.GATE_CLOSED,
                finished_at=clock.now(),
                stats={"gate_closed_reasons": list(reasons), "candidates_planned": len(manifest.candidates)},
            )
            deps.repo.begin_run(aborted)
            LOGGER.warning("LIVE run aborted: source-access gate is closed (%d reason(s)); no network call was made", len(reasons))
            return IngestReport(aborted, (), {})
        limits = manifest.crawl.limits()  # `dry_run_limits` is deliberately NOT consulted for LIVE
        contact = _resolve_contact(config)

    # ---- 2. fail-closed policy (any unset safety parameter / missing identity refuses to start) --------------------------
    missing = manifest.crawl.missing_parameters() if config.mode is RunMode.LIVE else ()
    problem: str | None = None
    if limits is None:
        problem = "required safety parameters are unset: " + ", ".join(missing or ("crawl block is not CONFIGURED",))
    elif contact is None:
        problem = f"crawler contact environment variable {manifest.crawl.contact_env_var} is not set"
    elif config.mode is RunMode.LIVE and not config.authorisation_ref:
        problem = "a LIVE run requires a recorded human authorisation reference"
    if problem is not None:
        deps.repo.ensure_source(manifest, config.manifest_ref, clock.now())
        aborted = replace(
            base,
            status=RunStatus.ABORTED,
            abort_reason=AbortReason.POLICY_VIOLATION,
            finished_at=clock.now(),
            stats={"policy_violation": problem},
        )
        deps.repo.begin_run(aborted)
        LOGGER.error("run aborted: %s", neutralize(problem))
        return IngestReport(aborted, (), {})
    assert limits is not None and contact is not None

    egress = EgressClient(
        resolver=deps.resolver,
        transport=deps.transport,
        clock=clock,
        rng=deps.rng,
        allowed_hosts=manifest.allowed_hosts,
        path_prefixes=manifest.url_policy.path_prefixes,
        listing_urls=manifest.url_policy.listing_urls,
        limits=limits,
        user_agent=build_user_agent(contact),
        secrets=(contact,),
        spool_dir=config.spool_dir,
    )

    # ---- 3. run -----------------------------------------------------------------------------------------------------
    deps.repo.ensure_source(manifest, config.manifest_ref, clock.now())
    deps.repo.begin_run(base)  # RunAlreadyActive if another run for this source is RUNNING
    results: list[IngestResult] = []
    stages: dict[str, CandidateStage] = {}
    abort: AbortReason | None = None
    try:
        for candidate in manifest.candidates:  # deterministic: sorted by document_key
            key = manifest.candidate_key(candidate)
            try:
                result = _process_candidate(config, deps, egress, limits, run_id, candidate, key, stages)
            except CircuitOpenError:
                result = IngestResult(run_id, key, IngestOutcome.FAILED_TRANSIENT, 1, "CIRCUIT_OPEN")
                deps.repo.record_result(result, clock.now())
                results.append(result)
                abort = AbortReason.CIRCUIT_OPEN
                LOGGER.error("circuit breaker opened: stopping the run; completed results remain valid")
                break
            results.append(result)
    except BaseException:
        deps.repo.finish_run(run_id, RunStatus.ABORTED, AbortReason.INTERNAL_ERROR, clock.now(), _stats(results, manifest))
        raise
    if abort is not None:
        status = RunStatus.ABORTED
    else:
        status = RunStatus.COMPLETED_WITH_FAILURES if any(r.outcome.is_failure for r in results) else RunStatus.COMPLETED
    finished = clock.now()
    stats = _stats(results, manifest)
    deps.repo.finish_run(run_id, status, abort, finished, stats)
    final = replace(base, status=status, abort_reason=abort, finished_at=finished, stats=stats)
    return IngestReport(final, tuple(results), stages)


def _stats(results: list[IngestResult], manifest: SourceManifest) -> dict[str, object]:
    return {
        "candidates_planned": len(manifest.candidates),
        "candidates_processed": len(results),
        "outcomes": dict(sorted(Counter(r.outcome.value for r in results).items())),
    }


# ---- one candidate -----------------------------------------------------------------------------------------------------


def _out_of_scope(manifest: SourceManifest, c: ManifestCandidate) -> str | None:
    if c.tier == OUT_OF_SCOPE_TIER:
        return "TIER_CONTEXT_OUT_OF_WINDOW"
    if c.document_type not in manifest.document_types:
        return "DOCUMENT_TYPE_NOT_IN_SCOPE"
    if c.listing_date is not None and manifest.date_window is not None and not manifest.date_window.contains(c.listing_date):
        return "OUTSIDE_DATE_WINDOW"
    return None


def _process_candidate(
    config: IngestConfig,
    deps: Dependencies,
    egress: EgressClient,
    limits: SafetyLimits,
    run_id: uuid.UUID,
    c: ManifestCandidate,
    key: str,
    stages: dict[str, CandidateStage],
) -> IngestResult:
    manifest, repo, clock = config.manifest, deps.repo, deps.clock
    stages[key] = CandidateStage.DISCOVERED

    def new_id() -> uuid.UUID:
        return new_uuid7(clock.now(), deps.rng)

    def finish(outcome: IngestOutcome, reason: str | None, attempts: int = 0) -> IngestResult:
        result = IngestResult(run_id, key, outcome, attempts, reason)
        repo.record_result(result, clock.now())
        return result

    def quarantine(
        reason: QuarantineReason,
        detail: str,
        *,
        url: str,
        attempts: int,
        resp: FetchedResponse | None = None,
        content_hash: ContentHash | None = None,
        sniffed: str | None = None,
    ) -> IngestResult:
        return repo.quarantine(
            QuarantineIngest(
                ingest_run_id=run_id,
                candidate_key=key,
                reason=reason,
                content_hash=content_hash,
                requested_url=neutralize(url, max_len=2000),
                final_url=resp.final_url if resp else None,
                redirect_chain=resp.redirect_chain if resp else (),
                http_status=resp.status if resp else None,
                headers=dict(resp.headers) if resp else {},
                sniffed_media_type=sniffed,
                reason_detail=neutralize(detail),
                safety_policy_version=config.safety_policy_version,
                attempts=attempts,
                now=clock.now(),
                new_id=new_id,
            )
        )

    scope = _out_of_scope(manifest, c)
    if scope is not None:
        return finish(IngestOutcome.OUT_OF_SCOPE, scope)
    if c.document_url is None:
        # Detail-page -> attachment discovery needs HTML extraction against a never-observed page structure (the attachment
        # link element is UNVERIFIED in the manifest). That is Phase 1D work after human approval; nothing is guessed here.
        return finish(IngestOutcome.UNRESOLVED, "NO_ATTACHMENT_URL_IN_MANIFEST")

    try:
        target = egress.check_url(c.document_url, FetchPurpose.ATTACHMENT)  # static checks only; nothing was sent
    except FetchError as err:
        assert err.quarantine is not None
        return quarantine(err.quarantine, err.detail, url=c.document_url, attempts=0)

    validators = repo.find_validators(key, target.url)
    try:
        resp = egress.fetch(c.document_url, FetchPurpose.ATTACHMENT, validators=validators)
    except FetchError as err:
        if err.quarantine is not None:
            return quarantine(err.quarantine, err.detail, url=target.url, attempts=err.attempts)
        outcome = IngestOutcome.FAILED_TRANSIENT if err.kind.retryable else IngestOutcome.FAILED_PERMANENT
        LOGGER.warning("candidate %s failed: %s", neutralize(key), neutralize(err))
        return finish(outcome, err.kind.value, err.attempts)

    stages[key] = CandidateStage.FETCHED
    try:
        if resp.not_modified:
            unchanged = repo.record_unchanged(run_id, key, resp.requested_url, resp.retrieved_at, resp.attempts, clock.now())
            return (
                unchanged
                if unchanged is not None
                else finish(IngestOutcome.FAILED_PERMANENT, FetchFailure.HTTP_PERMANENT.value, resp.attempts)
            )
        assert resp.body is not None
        content_hash = ContentHash.parse(resp.sha256_hex)  # validated 64-hex before it can touch a path
        head = resp.body.read(512)
        resp.body.seek(0)
        sniffed = sniff_media_type(head)
        rejection: Rejection | None = validate_response(
            resp.headers.get("content-type"), resp.body, resp.size, FetchPurpose.ATTACHMENT, limits
        )
        if rejection is not None:
            resp.body.seek(0)
            deps.quarantine_blobs.put(content_hash, resp.body, resp.size)  # keep the bytes for human review, in a SEPARATE store
            return quarantine(
                rejection.reason,
                rejection.detail,
                url=target.url,
                attempts=resp.attempts,
                resp=resp,
                content_hash=content_hash,
                sniffed=sniffed,
            )
        stages[key] = CandidateStage.VALIDATED
        resp.body.seek(0)
        put = deps.blobs.put(content_hash, resp.body, resp.size)
        stages[key] = CandidateStage.STORED
        meta = {
            "status": resp.status,
            "content_type": resp.headers.get("content-type"),
            "etag": resp.headers.get("etag"),
            "last_modified": resp.headers.get("last-modified"),
            "final_url": resp.final_url,
            "redirect_chain": list(resp.redirect_chain),
            "attempts": resp.attempts,
        }
        result = repo.ingest_artifact(
            ArtifactIngest(
                ingest_run_id=run_id,
                source_id=manifest.source_id,
                authority_id=manifest.authority,
                candidate=c,
                candidate_key=key,
                content_hash=content_hash,
                size_bytes=resp.size,
                media_type_sniffed=sniffed,
                blob_path_rel=put.relative_path,
                location_url=resp.requested_url,
                http_meta=meta,
                retrieved_at=resp.retrieved_at,
                attempts=resp.attempts,
                now=clock.now(),
                new_id=new_id,
            )
        )
        stages[key] = CandidateStage.VERSIONED
        return result
    except BlobStoreError as err:
        LOGGER.error("P1 blob store failure for %s: %s", neutralize(key), neutralize(err))
        return finish(IngestOutcome.FAILED_PERMANENT, type(err).__name__, resp.attempts)
    finally:
        resp.close()


__all__ = ["SAFETY_POLICY_VERSION", "Dependencies", "IngestConfig", "IngestReport", "run_ingest"]
