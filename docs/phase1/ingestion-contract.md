# Phase 1 — Ingestion Contract (no implementation)

Status: PROPOSED for review. Specifies the **types and guarantees** that Phase 1B must implement. It refines — and does not replace — [ingestion-design](../ingestion-design.md), [data-model](../data-model.md) §3 and [security](../security.md) T-04…T-08; where wording differs, those documents and the ADRs win (H5). Companion: [source-safety-contract](source-safety-contract.md). Evidence for the shapes below: [recon-findings](recon-findings.md).

## 0. Scope and layering

- Covers `SOURCE → discovery → download → validation → fingerprint → raw storage` and the creation of an immutable `DocumentVersion` for manifest-keyed candidates. Parsing and later stages are out of scope ([ingestion-design](../ingestion-design.md) §1).
- Lives in `packages/ingestion` (imports `packages/domain` only; AGENTS.md layering). Types below are `domain` types; I/O sits behind interfaces (`BlobStore`, `HttpTransport`, `Resolver`, `Clock`, `JobQueue`) with deterministic fakes (H13).
- **Identity in Phase 1B is manifest-only** ([ingestion-design](../ingestion-design.md) §4 rule 1: `document_key`). Anchored-reference resolution, manual overrides and the review queue belong to Phase 2. A candidate with no `document_key` cannot become a `DocumentVersion` in Phase 1B.
- **Gate:** every run starts by reading the manifest; the run ends `ABORTED(GATE_CLOSED)` before any network call unless **all** hold: `ingestion_authorized` is `true`; `access_review.status` is neither `BLOCKED` nor `AMBIGUOUS_REQUIRES_REVIEW`; `access_review.reviewed_by` and `reviewed_at` are non-null. (`APPROVED_FOR_RECONNAISSANCE` by itself is not ingestion approval; a human sets `ingestion_authorized` deliberately.) Dry-run mode (no network, manifest + fixtures only) is always allowed.
- **Trust class:** `RawArtifact`, `DocumentVersion`, locations = `SOURCE`; `IngestRun`, `IngestResult`, `QuarantineRecord`, `FetchRequest` log = `RUNTIME` (provenance of *our* actions, not source content). Derived data never overwrites either (H8).
- Time comes only from an injected `Clock` (UTC). `retrieved_at` is knowledge time ([temporal-model](../temporal-model.md) §1) and is never a legal date.

## 1. Required guarantees (the acceptance contract)

| # | Guarantee | Mechanism | Test (offline) |
|---|---|---|---|
| G1 | **Same content hash → same `RawArtifact`.** | `raw_artifact.content_hash` UNIQUE; insert-or-get; blob write skipped when the blob exists and verifies | two fetches, different URLs, same bytes → 1 artifact, 1 version, 2 location rows |
| G2 | **Changed content hash → new `DocumentVersion`** under the same document. | `UNIQUE(document_id, content_hash)`; new row when hash differs; older version untouched | same URL, new bytes → 2 versions; first still retrievable; `CONTENT_CHANGED_UNDER_STABLE_REF` alert raised if a `source_reference` was already recorded |
| G3 | **Re-run → no duplicate logical ingestion.** With nothing changed, a re-run adds **zero rows to `raw_artifact`, `regulatory_document`, `document_version`, `document_version_location`** (it appends only run bookkeeping: one `ingest_run` and its `ingest_result` rows; `last_seen` updates are the one allowed in-place change, on locations). | idempotency keys §2; UNIQUE constraints | property test: N re-runs ⇒ identical source-layer row counts |
| G4 | **Failed validation → quarantine, never publish.** | validation precedes storage in the publish path; a version cannot reach `PUBLISHED` while a quarantine record exists for its content hash; DB constraint + publish-gate check | fixtures: wrong host, redirect to other host, private IP, wrong magic bytes, oversize, non-PDF → `QuarantineRecord`, zero versions |
| G5 | **Nothing is fetched outside the manifest scope.** | single egress component; exact host allowlist; no URLs from document content | security tests with fake resolver ([source-safety-contract](source-safety-contract.md)) |
| G6 | **Every artifact is traceable** to run, manifest version, code version, safety-policy version, request log. | provenance fields below | constraint test: no `document_version` without `ingest_run_id` |

> **Open clarification OC-1.** [implementation-roadmap](../implementation-roadmap.md) Phase 1 says "re-run adds zero rows". G3 reads this as *zero source-layer rows*; run bookkeeping rows are appended. If the intent is literally zero rows in all tables, per-candidate `ingest_result` rows for unchanged items would be replaced by counts in `ingest_run.stats`. Needs your decision before Phase 1B (small, no ADR impact).

## 2. Idempotency keys (consolidated)

| Stage | Key (from [ingestion-design](../ingestion-design.md) §1) | Contract detail |
|---|---|---|
| Discovery | `(source_id, canonical_url)`; when `canonical_url` is null: `(source_id, document_key)` | both uniqueness constraints hold; a candidate never maps to two keys |
| Download | `(url, etag/last-modified)` | conditional GET when the validators were stored; a 304 produces `UNCHANGED_SKIPPED` without bytes |
| Validation / fingerprint | `content_hash` | sha256 over raw bytes, computed on the validated in-memory/temp stream before any publish |
| Raw storage | `content_hash` | write-once; verify after write; same hash is a no-op |
| Version | `(document_id, content_hash)` | |
| Job (queue) | job `idempotency_key` = hash of `(kind, candidate_key, manifest_version, attempt-independent params)` | `UNIQUE` in `job` |

## 3. Types

Field lists are the *contract*; DDL is produced in Phase 1B/2 migrations (data-model DDL is illustrative). `quarantine_record` and `ingest_result` are **new tables not in the illustrative data-model list**; they sit in the existing source-layer/runtime groups, need no new service or database (H15), and are flagged here for migration review rather than an ADR.

### 3.1 `Candidate`

A reviewed intention to ingest one logical document, derived from a manifest entry (or later from a reviewed listing diff). Not a fetch, not a document.

| Aspect | Contract |
|---|---|
| Identifier | `candidate_key = (source_id, document_key)`; unique within a manifest version |
| Inputs | manifest `candidate_documents[*]` fields: `document_key`, `tier`, `title`, `document_type`, `canonical_url?`, `document_url?`, `source_reference?` (+status), `listing_date?`, `listing_page_url?`, `sampled`, `resolution_status?`; run context: `manifest_version`, window bounds |
| Outputs | an ordered `FetchRequest` plan (§3.2): detail-page fetch then attachment fetch, or attachment only when `document_url` is already a validated-form URL |
| Failure / terminal states | `UNRESOLVED` (no usable URL, e.g. seed entries) · `OUT_OF_SCOPE` (type or date outside manifest scope) · `REJECTED_NOT_ALLOWLISTED` (URL fails the host/scheme/path policy before any request) · `GATE_CLOSED` |
| Provenance | `manifest_version`, manifest content hash, entry index, manifest `*_status` labels (`VERIFIED/OBSERVED/INFERRED/UNVERIFIED`) copied through unchanged — **a status is never upgraded by ingestion** |
| Idempotency | same manifest ⇒ same candidate set and order (deterministic sort by `document_key`); constructing a candidate has no side effects |
| Notes | Manifest values such as `source_reference` are *claims by the manifest author*, stored as extracted/UNTRUSTED until later evidence-anchored extraction (T-09). `document_url` values labelled `OBSERVED` are re-validated by the safety contract; the viewer-wrapper form is rejected, never fetched |

### 3.2 `FetchRequest`

One validated, policy-checked HTTP retrieval intent.

| Aspect | Contract |
|---|---|
| Identifier | `fetch_request_id`; dedup key `(ingest_run_id, normalized_url, purpose)` |
| Inputs | `candidate_key`, `ingest_run_id`, `purpose ∈ {LISTING, DETAIL_PAGE, ATTACHMENT}`, `url` (pre-validation), `attempt` (≥1), stored validators (`etag`, `last_modified`) if any, `policy_ref` (safety-policy version + manifest version) |
| Pre-flight (all must pass or the request is rejected locally with no network) | scheme/host/port/path policy, userinfo rejection, DNS→IP public-range check, rate-limit slot, gate check — specified in [source-safety-contract](source-safety-contract.md) |
| Outputs | `FetchedResponse`: `final_url`, `redirect_chain[]` (each hop re-validated), `status`, selected headers (`content-type`, `content-length`, `etag`, `last-modified`), body stream bounded by the size cap, `retrieved_at`, `elapsed`. The body is not persisted by this type |
| Failure states | `URL_REJECTED` · `DNS_REJECTED` · `REDIRECT_REJECTED` · `TLS_ERROR` · `TIMEOUT` · `SIZE_EXCEEDED` · `HTTP_PERMANENT` (e.g. 404/410/403 policy) · `HTTP_TRANSIENT` (429/5xx) · `RATE_LIMITED_LOCAL` · `GATE_CLOSED` |
| Retry semantics | only `HTTP_TRANSIENT`, `TIMEOUT`, `TLS_ERROR` are retryable; bounded attempts, exponential backoff with jitter, `Retry-After` honoured (parameters in the manifest `crawl` block; numeric values are **not set in Phase 1A** — H14). `HTTP_PERMANENT` and all `*_REJECTED` are never retried |
| Provenance | full request log row: `candidate_key`, URLs, redirect chain, status, header subset, `retrieved_at`, `attempt`, `policy_ref`, crawler identity token (not the contact address — T-16), `ingest_run_id`. No cookies, no auth headers, no response body in logs |
| Idempotency | repeating a request with identical dedup key in the same run returns the recorded outcome; across runs, conditional validators prevent re-download of unchanged content |

### 3.3 `RawArtifact`

Immutable bytes, addressed by content.

| Aspect | Contract |
|---|---|
| Identifier | `content_hash` = lowercase hex sha256 of the exact bytes; `raw_artifact_id` is a surrogate |
| Inputs | a validated byte stream (post-validation §3.4 checks) + `FetchedResponse` metadata |
| Outputs | `raw_artifact(raw_artifact_id, content_hash UNIQUE, size_bytes, media_type_sniffed, blob_path_rel)`; blob at a path **derived only from the hash** under `RIG_BLOB_ROOT` (never from URL, title or any source text) |
| Failure states | `WRITE_FAILED` · `VERIFY_AFTER_WRITE_FAILED` (re-read hash ≠ expected → delete the temp file, never publish the blob, raise P1 integrity alert) · `BLOB_PATH_COLLISION_DIFFERENT_BYTES` (treated as corruption, P1) |
| Provenance | `content_hash`, size, sniffed media type, first `ingest_run_id`; every **location** (URL where seen) lives in `document_version_location(canonical_url, first_seen, last_seen, http_meta)` — many URLs may serve one artifact |
| Idempotency | same bytes ⇒ same artifact, no second blob, no second row (G1). Writes go to a temp file in the same directory, are fsync'd, hash-verified, then atomically renamed; a crash leaves no half-written blob visible |

### 3.4 `QuarantineRecord`

A refusal to publish, with the reason. Append-only.

| Aspect | Contract |
|---|---|
| Identifier | `quarantine_id`; unique on `(ingest_run_id, candidate_key, reason_code, content_hash?)` |
| Inputs | the failing `Candidate`/`FetchRequest` context, and the observed bytes' hash if bytes were received |
| Reason codes | `HOST_NOT_ALLOWED` · `REDIRECT_OFF_ALLOWLIST` · `SCHEME_NOT_HTTPS` · `PRIVATE_OR_RESERVED_IP` · `CONTENT_TYPE_MISMATCH` · `MAGIC_BYTES_MISMATCH` · `SIZE_EXCEEDED` · `PDF_SANITY_FAILED` (structure, page cap, encryption/JS/launch actions present) · `AUTHORITY_MISMATCH` (document claims another authority, or a bundle/foreign first page, per manifest-derived authority) · `VIEWER_WRAPPER_URL` · `MANIFEST_SCOPE_MISMATCH` |
| Outputs | the record, plus (only when bytes were fully received and are within the size cap) the bytes in a **separate quarantine directory** with a hash-derived name and **no execute permission**, outside the content-addressed published store; oversize or never-received bodies are discarded |
| Never | publish, index, parse, or surface in retrieval; delete silently; "fix" content |
| Release | only by an explicit, reviewed action that writes a **new** record (e.g. `RELEASED_BY_REVIEW` with reviewer and reason) and re-enters validation under a manifest override; quarantine rows are never updated or deleted |
| Provenance | run, candidate, URLs/redirect chain, header subset, sniffed type, reason detail (redacted: no secrets, no body excerpts beyond a bounded safe prefix flag), `policy_ref` |
| Idempotency | a re-run that hits the same refusal produces `IngestResult(QUARANTINED)` referencing the existing quarantine record; no duplicate row (UNIQUE key above) |

### 3.5 `IngestResult`

The outcome of one candidate in one run (RUNTIME).

| Aspect | Contract |
|---|---|
| Identifier | `UNIQUE(ingest_run_id, candidate_key)` |
| Inputs | candidate, its `FetchRequest` outcomes, validation outcome, storage outcome |
| Outcome enum | `NEW_VERSION` · `NEW_LOCATION_SAME_VERSION` · `UNCHANGED_SKIPPED` · `QUARANTINED` · `FAILED_TRANSIENT` (attempts exhausted; retryable by a later run) · `FAILED_PERMANENT` · `UNRESOLVED` · `OUT_OF_SCOPE` · `GATE_CLOSED` |
| Outputs | `document_version_id?`, `content_hash?`, `previous_content_hash?` (when changed), `quarantine_id?`, `alerts[]` (`CONTENT_CHANGED_UNDER_STABLE_REF`, `DUPLICATE_BYTES_OTHER_DOCUMENT` → candidate-for-review, **never auto-merge**), `attempts`, `reason_code?` |
| Failure semantics | a failure in one candidate never aborts others (per-candidate isolation); it never deletes earlier-stage outputs |
| Provenance | `ingest_run_id`, `candidate_key`, request ids |
| Idempotency | a second result for the same `(run, candidate)` is impossible by constraint; across runs, results are new run-scoped rows that point at the same source-layer rows |

### 3.6 `IngestRun`

One execution of the pipeline over a manifest.

| Aspect | Contract |
|---|---|
| Identifier | `ingest_run_id` (UUID v7) |
| Inputs | `source_id`, manifest path + **content hash** + `manifest_version`, `mode ∈ {DRY_RUN, LIVE}`, `code_version`, `safety_policy_version`, candidate filter (default: all manifest candidates in scope), injected `Clock`/`Transport`/`Resolver`/`BlobStore` |
| Start preconditions | gate §0; `RIG_CRAWLER_CONTACT_EMAIL` set for `LIVE` (value never logged or stored in the run record); blob root writable and validated (no symlink escape); `LIVE` requires a recorded human authorisation reference |
| Outputs | `ingest_run(ingest_run_id, source_id, started_at, finished_at, status, stats jsonb, code_version, …)` per [data-model](../data-model.md) §3, + `ingest_result` rows + a human-readable ingest report (counts by outcome, quarantine reasons, alerts, unresolved candidates, listing-vs-manifest diff) |
| Status | `RUNNING` → `COMPLETED` · `COMPLETED_WITH_FAILURES` · `ABORTED` (`GATE_CLOSED`, `POLICY_VIOLATION`, `CIRCUIT_OPEN`: configured consecutive refusals/blocks — threshold set in manifest `crawl`, **not** invented here — or operator cancel) |
| Failure behaviour | on `ABORTED`, completed results remain valid; partial runs are resumable by re-running (idempotent) |
| Provenance | the stored manifest hash lets a snapshot be reproduced: **corpus snapshot id = hash of (manifest hash, set of `content_hash` per `document_key` produced)**, defined at Phase 1 "done" (roadmap) |
| Idempotency | a re-run is a *new* `IngestRun`; it must satisfy G3. Two concurrent runs for one `source_id` are prevented (advisory lock or job-key uniqueness) |

## 4. Per-candidate state machine (Phase 1B slice)

```
DISCOVERED → (gate) → FETCHED → VALIDATED → STORED → VERSIONED
        ╲          ╲          ╲
         └ UNRESOLVED / OUT_OF_SCOPE / GATE_CLOSED / FAILED_* / QUARANTINED   (terminal for the run)
```

`VERSIONED` = `DocumentVersion` row exists (status `STORED`; **not** `PUBLISHED` — publishing needs parse/index per the publish gate and is Phase 3+). `PUBLISHED` is never set by Phase 1B. Stages are independently re-runnable; later stages' failures never delete earlier outputs ([ingestion-design](../ingestion-design.md) §5).

## 5. Validation order (precedes storage)

`pre-flight policy → fetch → size/time caps → content-type header check → magic-byte sniff → PDF structural sanity (bounded, no rendering, no JS, no embedded-file extraction) → authority/host consistency → sha256 → raw storage`. Any failure → `QuarantineRecord` with the first failing reason. Details and limits: [source-safety-contract](source-safety-contract.md) §6–§8.

## 6. What Phase 1B must not do (reaffirmed)

No bulk enumeration beyond manifest candidates; no listing pagination beyond reviewed entry points; no fetching of URLs found inside documents; no parsing; no OCR; no new service/database/broker (H15); no change to any ADR (H5).
