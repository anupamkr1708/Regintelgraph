"""Ingestion-contract invariants G1-G6, run states and safety behaviour — on BOTH repositories.

Fixtures are synthetic (TESTREG). `LIVE` here uses fake transport/resolver: no network object exists in these tests.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from packages.domain.ingest import AbortReason, IngestAlert, IngestOutcome, QuarantineReason, RunMode, RunStatus
from packages.ingestion.errors import ModeViolation, RunAlreadyActive
from packages.ingestion.repository import InMemoryRepository
from tests.support.builders import CONTACT_ENV, HOST, manifest, pdf, sha
from tests.support.env import Env, one
from tests.support.fakes import Resp, TripwireResolver, TripwireTransport

KEY = "testreg/TEST-001"
URL_A = f"https://{HOST}/files/test-001.pdf"


def counts(env: Env) -> dict[str, int]:
    return env.repo.counts()


def only(report):  # type: ignore[no-untyped-def]
    assert len(report.results) == 1, report.results
    return report.results[0]


# ---- happy path / provenance (G6) --------------------------------------------------------------------------------------------


def test_new_document_is_stored_versioned_and_recorded(env: Env) -> None:
    body = pdf("one")
    env.serve("TEST-001", body)
    report = env.run([one()])
    r = only(report)
    assert r.outcome is IngestOutcome.NEW_VERSION and r.content_hash is not None and r.content_hash.value == sha(body)
    assert report.run.status is RunStatus.COMPLETED and report.run.abort_reason is None
    assert counts(env) == {
        "raw_artifact": 1,
        "regulatory_document": 1,
        "document_version": 1,
        "document_version_location": 1,
        "quarantine_record": 0,
        "ingest_result": 1,
        "ingest_run": 1,
    }
    assert env.blobs.exists(r.content_hash) and not env.qblobs.exists(r.content_hash)
    assert (env.blobs.root / r.content_hash.relative_path).read_bytes() == body
    assert env.repo.version_hashes(KEY) == [sha(body)]


def test_result_never_claims_publication(env: Env) -> None:
    env.serve("TEST-001", pdf("p"))
    only(env.run([one()]))
    assert {o.value for o in IngestOutcome} >= {"NEW_VERSION"} and "PUBLISHED" not in {o.value for o in IngestOutcome}


# ---- G1: same bytes ---------------------------------------------------------------------------------------------------------


def test_same_bytes_at_a_new_location_reuse_artifact_and_version(env: Env) -> None:
    body = pdf("loc")
    env.serve("TEST-001", body)
    only(env.run([one()]))
    env.transport.route(HOST, "/files/moved.pdf", Resp(200, {"Content-Type": "application/pdf"}, body))
    r = only(env.run([one(url=f"https://{HOST}/files/moved.pdf")]))
    assert r.outcome is IngestOutcome.NEW_LOCATION_SAME_VERSION and r.alerts == ()
    c = counts(env)
    assert (c["raw_artifact"], c["document_version"], c["document_version_location"]) == (1, 1, 2)


def test_same_bytes_under_a_different_document_share_the_artifact_and_raise_a_review_alert_without_merging(env: Env) -> None:
    body = pdf("dup")
    env.serve("TEST-001", body)
    env.serve("TEST-002", body)
    report = env.run([one("TEST-001"), one("TEST-002", ref="TESTREG/2026/2", index=1)])
    first, second = report.results
    assert first.outcome is second.outcome is IngestOutcome.NEW_VERSION
    assert IngestAlert.DUPLICATE_BYTES_OTHER_DOCUMENT in second.alerts and first.alerts == ()
    c = counts(env)
    assert (c["raw_artifact"], c["regulatory_document"], c["document_version"]) == (1, 2, 2)  # never auto-merged


# ---- G2: stable reference, changed bytes --------------------------------------------------------------------------------------


def test_changed_bytes_under_a_stable_reference_create_a_new_version_and_an_alert(env: Env) -> None:
    v1, v2 = pdf("v1"), pdf("v2")
    env.serve("TEST-001", v1)
    only(env.run([one()]))
    env.serve("TEST-001", v2)
    r = only(env.run([one()]))
    assert r.outcome is IngestOutcome.NEW_VERSION and IngestAlert.CONTENT_CHANGED_UNDER_STABLE_REF in r.alerts
    assert r.previous_content_hash is not None and r.previous_content_hash.value == sha(v1)
    assert env.repo.version_hashes(KEY) == [sha(v1), sha(v2)]  # the earlier version is preserved
    assert counts(env)["raw_artifact"] == 2 and all(env.blobs.exists(h) for h in (r.content_hash, r.previous_content_hash))  # type: ignore[arg-type]


def test_changed_bytes_without_a_source_reference_still_version_but_do_not_alert(env: Env) -> None:
    env.serve("TEST-001", pdf("a"))
    only(env.run([one(ref=None)]))
    env.serve("TEST-001", pdf("b"))
    r = only(env.run([one(ref=None)]))
    assert r.outcome is IngestOutcome.NEW_VERSION and r.alerts == () and len(env.repo.version_hashes(KEY)) == 2


# ---- G3: idempotency ---------------------------------------------------------------------------------------------------------


def test_rerun_creates_no_duplicate_source_layer_rows_but_records_run_bookkeeping_and_moves_last_seen(env: Env) -> None:
    env.serve("TEST-001", pdf("idem"))
    only(env.run([one()]))
    before, seen_before = counts(env), env.repo.last_seen(KEY, URL_A)
    r = only(env.run([one()]))
    after = counts(env)
    assert r.outcome is IngestOutcome.UNCHANGED_SKIPPED
    for table in ("raw_artifact", "regulatory_document", "document_version", "document_version_location"):
        assert after[table] == before[table], table  # no duplicate logical source-layer artifacts or versions
    assert (after["ingest_run"], after["ingest_result"]) == (
        before["ingest_run"] + 1,
        before["ingest_result"] + 1,
    )  # run-scoped bookkeeping
    seen_after = env.repo.last_seen(KEY, URL_A)
    assert seen_before is not None and seen_after is not None and seen_after > seen_before  # last_seen: the one allowed mutable field
    assert len(env.blob_files()) == 1  # the blob store did not grow either


def test_conditional_request_304_is_unchanged_and_moves_last_seen(env: Env) -> None:
    env.serve("TEST-001", pdf("etag"), headers={"ETag": '"abc"', "Last-Modified": "Wed, 01 Jan 2026 00:00:00 GMT"})
    only(env.run([one()]))
    env.transport.route(HOST, "/files/test-001.pdf", Resp(304))
    seen_before = env.repo.last_seen(KEY, URL_A)
    r = only(env.run([one()]))
    sent = env.transport.requests[-1].headers
    assert sent["If-None-Match"] == '"abc"' and sent["If-Modified-Since"].startswith("Wed, 01 Jan 2026")
    assert r.outcome is IngestOutcome.UNCHANGED_SKIPPED and r.reason_code == "NOT_MODIFIED"
    assert counts(env)["document_version"] == 1 and env.repo.last_seen(KEY, URL_A) > seen_before  # type: ignore[operator]


def test_content_negotiation_variants_of_the_same_bytes_are_one_artifact(env: Env) -> None:
    body = pdf("neg")
    env.transport.route(
        HOST, "/files/test-001.pdf", Resp(200, {"Content-Type": "application/pdf", "Content-Encoding": "gzip"}, gzip.compress(body))
    )
    only(env.run([one()]))
    env.serve("TEST-001", body)
    assert only(env.run([one()])).outcome is IngestOutcome.UNCHANGED_SKIPPED and counts(env)["raw_artifact"] == 1


# ---- G4: quarantine ----------------------------------------------------------------------------------------------------------


def test_off_allowlist_candidate_is_quarantined_with_zero_requests(env: Env) -> None:
    r = only(env.run([one(url="https://evil.example/files/x.pdf")]))
    assert (
        r.outcome is IngestOutcome.QUARANTINED and r.reason_code == QuarantineReason.HOST_NOT_ALLOWED.value and r.quarantine_id is not None
    )
    assert env.transport.requests == [] and env.resolver.calls == []  # nothing was sent, nothing was even resolved
    c = counts(env)
    assert (c["quarantine_record"], c["document_version"], c["raw_artifact"]) == (1, 0, 0)


@pytest.mark.parametrize(
    ("body", "headers", "reason"),
    [
        (b"<html>Access denied</html>", {}, QuarantineReason.MAGIC_BYTES_MISMATCH),  # HTML served as PDF
        (pdf("x"), {"Content-Type": "text/html"}, QuarantineReason.CONTENT_TYPE_MISMATCH),
        (b"<html>denied</html>", {"Content-Type": "text/html"}, QuarantineReason.CONTENT_TYPE_MISMATCH),  # FIRST failing reason wins
        (b"", {}, QuarantineReason.MAGIC_BYTES_MISMATCH),
        (b"%PDF-1.4 no end marker " + b"x" * 200, {}, QuarantineReason.PDF_SANITY_FAILED),
        (pdf("js", extra=b"/JavaScript (x)"), {}, QuarantineReason.PDF_SANITY_FAILED),
    ],
)
def test_invalid_content_is_quarantined_kept_for_review_and_never_published(
    env: Env, body: bytes, headers: dict[str, str], reason: QuarantineReason
) -> None:
    env.serve("TEST-001", body, headers=headers)
    r = only(env.run([one()]))
    assert r.outcome is IngestOutcome.QUARANTINED and r.reason_code == reason.value and r.content_hash is not None
    assert env.qblobs.exists(r.content_hash) and not env.blobs.exists(r.content_hash)  # quarantine is a SEPARATE store
    assert env.blob_files() == []  # nothing in the published store
    c = counts(env)
    assert (c["document_version"], c["raw_artifact"], c["regulatory_document"], c["quarantine_record"]) == (0, 0, 0, 1)
    assert env.repo.version_hashes(KEY) == []


def test_quarantined_files_are_owner_only(env: Env) -> None:
    env.serve("TEST-001", b"<html/>")
    r = only(env.run([one()]))
    assert r.content_hash is not None and ((env.qblobs.root / r.content_hash.relative_path).stat().st_mode & 0o777) == 0o400


def test_size_cap_and_redirect_refusals_are_quarantined_without_bytes(env: Env) -> None:
    env.transport.route(HOST, "/files/test-001.pdf", Resp(200, {"Content-Type": "application/pdf", "Content-Length": "999999"}, b""))
    env.transport.route(HOST, "/files/test-002.pdf", Resp(302, {"Location": "https://evil.example/x"}))
    report = env.run([one("TEST-001"), one("TEST-002", index=1)])
    assert [r.reason_code for r in report.results] == ["SIZE_EXCEEDED", "REDIRECT_OFF_ALLOWLIST"]
    assert all(r.outcome is IngestOutcome.QUARANTINED and r.content_hash is None for r in report.results)
    assert env.blob_files() == [] and env.blob_files(env.qblobs) == []


# ---- G5: per-candidate isolation / run states ---------------------------------------------------------------------------------


def test_a_failed_candidate_does_not_affect_the_others(env: Env) -> None:
    env.serve("TEST-001", pdf("a"))
    env.serve("TEST-002", None, status=404)
    env.serve("TEST-003", pdf("c"))
    report = env.run([one("TEST-001"), one("TEST-002", index=1), one("TEST-003", index=2)])
    assert [r.outcome for r in report.results] == [IngestOutcome.NEW_VERSION, IngestOutcome.FAILED_PERMANENT, IngestOutcome.NEW_VERSION]
    assert report.run.status is RunStatus.COMPLETED_WITH_FAILURES and counts(env)["document_version"] == 2


def test_transient_failures_after_retries_are_failed_transient(env: Env) -> None:
    env.transport.route(HOST, "/files/test-001.pdf", Resp(503))
    r = only(env.run([one()]))
    assert r.outcome is IngestOutcome.FAILED_TRANSIENT and r.attempts == 3 and r.reason_code == "HTTP_TRANSIENT"


def test_circuit_breaker_aborts_the_run_but_completed_work_stays_valid(env: Env) -> None:
    env.serve("TEST-001", pdf("ok"))
    for k in ("TEST-002", "TEST-003", "TEST-004"):
        env.transport.route(HOST, f"/files/{k.lower()}.pdf", Resp(403))
    env.serve("TEST-005", pdf("never"))
    report = env.run([one(f"TEST-00{i}", index=i - 1) for i in range(1, 6)])
    assert report.run.status is RunStatus.ABORTED and report.run.abort_reason is AbortReason.CIRCUIT_OPEN
    assert [r.candidate_key for r in report.results] == [f"testreg/TEST-00{i}" for i in range(1, 5)]  # TEST-005 was never attempted
    assert report.results[0].outcome is IngestOutcome.NEW_VERSION and report.results[-1].reason_code == "CIRCUIT_OPEN"
    assert counts(env)["document_version"] == 1  # the completed candidate remains valid
    rerun = env.run([one("TEST-001")])  # re-running is safe
    assert only(rerun).outcome is IngestOutcome.UNCHANGED_SKIPPED


def test_scope_and_unresolved_candidates_never_touch_the_network(env: Env) -> None:
    cands = [
        one("A-OUT-TIER", tier="context_out_of_window"),
        one("B-OUT-DATE", index=1, listing_date=__import__("datetime").date(2020, 1, 1)),
        one("C-OUT-TYPE", index=2, document_type="press_release"),
        one("D-NO-URL", index=3, url=None),
    ]
    report = env.run(cands)
    assert [(r.outcome, r.reason_code) for r in report.results] == [
        (IngestOutcome.OUT_OF_SCOPE, "TIER_CONTEXT_OUT_OF_WINDOW"),
        (IngestOutcome.OUT_OF_SCOPE, "OUTSIDE_DATE_WINDOW"),
        (IngestOutcome.OUT_OF_SCOPE, "DOCUMENT_TYPE_NOT_IN_SCOPE"),
        (IngestOutcome.UNRESOLVED, "NO_ATTACHMENT_URL_IN_MANIFEST"),
    ]
    assert env.transport.requests == [] and report.run.status is RunStatus.COMPLETED


def test_candidates_are_processed_in_document_key_order(env: Env) -> None:
    for k in ("TEST-C", "TEST-A", "TEST-B"):
        env.serve(k, pdf(k))
    report = env.run([one("TEST-C"), one("TEST-A", index=1), one("TEST-B", index=2)])
    assert [r.candidate_key for r in report.results] == ["testreg/TEST-A", "testreg/TEST-B", "testreg/TEST-C"]
    assert [r.target for r in env.transport.requests] == ["/files/test-a.pdf", "/files/test-b.pdf", "/files/test-c.pdf"]


def test_the_run_records_manifest_code_and_policy_provenance(env: Env) -> None:
    env.serve("TEST-001", pdf("prov"))
    report = env.run([one()])
    run = report.run
    assert (run.manifest_hash, run.manifest_version, run.code_version, run.safety_policy_version) == (
        "1" * 64,
        "testreg-1",
        "test-sha",
        "source-safety-contract@phase-1c",
    )
    assert (
        run.mode is RunMode.LIVE
        and run.authorisation_ref == "TEST-AUTH-SYNTHETIC"
        and run.finished_at is not None
        and run.stats["outcomes"] == {"NEW_VERSION": 1}
    )  # type: ignore[index]


def test_two_concurrent_runs_for_one_source_are_prevented(env: Env) -> None:
    from dataclasses import replace

    from packages.domain.ingest import IngestRun
    from packages.ingestion.clock import new_uuid7

    m = manifest([one()])
    env.repo.ensure_source(m, "x", env.clock.now())
    base = IngestRun(new_uuid7(env.clock.now(), env.rng), "testreg", RunMode.LIVE, env.clock.now(), "1" * 64, "v", "c", "p", "AUTH")
    env.repo.begin_run(base)
    with pytest.raises(RunAlreadyActive):
        env.repo.begin_run(replace(base, ingest_run_id=new_uuid7(env.clock.now(), env.rng)))
    env.repo.finish_run(base.ingest_run_id, RunStatus.COMPLETED, None, env.clock.now(), {})
    env.repo.begin_run(replace(base, ingest_run_id=new_uuid7(env.clock.now(), env.rng)))  # allowed again once the first run finished


# ---- THE GATE and fail-closed policy -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["not_authorized", "ambiguous_review_but_authorized", "no_reviewer"])
def test_closed_gate_aborts_live_runs_with_zero_network_calls(env: Env, variant: str) -> None:
    from dataclasses import replace

    m = manifest([one()], gate_open=True)
    if variant == "not_authorized":
        m = replace(m, ingestion_authorized=False)
    elif variant == "ambiguous_review_but_authorized":
        m = replace(
            m,
            access_review=replace(
                m.access_review, status=__import__("packages.domain.manifest", fromlist=["x"]).AccessReviewStatus.AMBIGUOUS_REQUIRES_REVIEW
            ),
        )
    else:
        m = replace(m, access_review=replace(m.access_review, reviewed_by=None))
    assert not m.gate_open
    resolver, transport = TripwireResolver(), TripwireTransport()
    env.resolver, env.transport = resolver, transport  # type: ignore[assignment]
    report = env.run(manifest_obj=m)
    assert report.run.status is RunStatus.ABORTED and report.run.abort_reason is AbortReason.GATE_CLOSED and report.results == ()
    assert resolver.calls == 0 and transport.calls == 0  # no DNS, no TCP, no HTTP, no robots, no listing
    assert env.blob_files() == [] and env.blob_files(env.qblobs) == []
    c = counts(env)
    assert (c["ingest_run"], c["raw_artifact"], c["document_version"], c["quarantine_record"], c["ingest_result"]) == (1, 0, 0, 0, 0)


def test_unconfigured_safety_parameters_refuse_to_start_even_with_the_gate_open(env: Env) -> None:
    resolver, transport = TripwireResolver(), TripwireTransport()
    env.resolver, env.transport = resolver, transport  # type: ignore[assignment]
    report = env.run(manifest_obj=manifest([one()], gate_open=True, crawl_configured=False))
    assert report.run.abort_reason is AbortReason.POLICY_VIOLATION and (resolver.calls, transport.calls) == (0, 0)
    assert "unset" in str(report.run.stats["policy_violation"])


def test_dry_run_limits_are_never_honoured_for_a_live_run(env: Env) -> None:
    from tests.support.builders import SMALL_LIMITS

    resolver, transport = TripwireResolver(), TripwireTransport()
    env.resolver, env.transport = resolver, transport  # type: ignore[assignment]
    report = env.run(manifest_obj=manifest([one()], crawl_configured=False), dry_run_limits=SMALL_LIMITS)
    assert report.run.abort_reason is AbortReason.POLICY_VIOLATION and (resolver.calls, transport.calls) == (0, 0)


@pytest.mark.parametrize(("kw", "needle"), [({"env": {}}, CONTACT_ENV), ({"authorisation_ref": None}, "authorisation")])
def test_missing_contact_or_authorisation_aborts_before_any_network(env: Env, kw: dict[str, object], needle: str) -> None:
    resolver, transport = TripwireResolver(), TripwireTransport()
    env.resolver, env.transport = resolver, transport  # type: ignore[assignment]
    report = env.run([one()], **kw)
    assert (
        report.run.abort_reason is AbortReason.POLICY_VIOLATION
        and needle in str(report.run.stats["policy_violation"])
        and (resolver.calls, transport.calls) == (0, 0)
    )


# ---- DRY_RUN never turns live ---------------------------------------------------------------------------------------------------


def test_dry_run_works_on_fixtures_without_persisting(tmp_path: Path) -> None:
    """A DRY_RUN is only ever given a non-persistent repository (so this never depends on the parametrised repo)."""
    from tests.support.builders import SMALL_LIMITS

    env = Env(InMemoryRepository(), tmp_path)
    env.serve("TEST-001", pdf("dry"))
    report = env.run([one()], mode=RunMode.DRY_RUN, dry_run_limits=SMALL_LIMITS)
    assert only(report).outcome is IngestOutcome.NEW_VERSION and report.run.mode is RunMode.DRY_RUN


def test_dry_run_refuses_a_persistent_repository_or_network_capable_components(env: Env) -> None:
    from tests.support.builders import SMALL_LIMITS

    if env.repo.persistent:
        with pytest.raises(ModeViolation, match="persistent"):
            env.run([one()], mode=RunMode.DRY_RUN, dry_run_limits=SMALL_LIMITS)
    env.repo = InMemoryRepository()
    env.resolver = TripwireResolver()  # type: ignore[assignment]
    with pytest.raises(ModeViolation, match="network-capable"):
        env.run([one()], mode=RunMode.DRY_RUN, dry_run_limits=SMALL_LIMITS)
    env.resolver = __import__("tests.support.fakes", fromlist=["FakeResolver"]).FakeResolver()
    env.transport = TripwireTransport()  # type: ignore[assignment]
    with pytest.raises(ModeViolation, match="network-capable"):
        env.run([one()], mode=RunMode.DRY_RUN, dry_run_limits=SMALL_LIMITS)


def test_run_ingest_surfaces_unexpected_errors_after_closing_the_run(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.serve("TEST-001", pdf("boom"))

    def explode(*a: object, **k: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(env.repo, "ingest_artifact", explode)
    with pytest.raises(RuntimeError, match="db down"):
        env.run([one()])
    runs = [] if not isinstance(env.repo, InMemoryRepository) else list(env.repo.runs.values())
    if runs:
        assert runs[0].status is RunStatus.ABORTED and runs[0].abort_reason is AbortReason.INTERNAL_ERROR  # never left RUNNING
