# Source Ingestion Design

Status: PROPOSED. Phase 1–3 implement this. Everything fetched is **untrusted content** (see [security](security.md)).

## 1. Pipeline

`SOURCE → discovery → download → validation → fingerprint → raw storage → parsing → structure extraction → semantic segmentation → metadata extraction → indexing → graph extraction → verification → publishing`

| Stage | Output | Failure handling | Idempotency key |
|---|---|---|---|
| Discovery | Candidate (url, listed title/date) from reviewed manifest/listing pages | Log, continue other candidates | (source_id, canonical_url) |
| Download | Bytes + HTTP metadata | Retry with backoff (bounded); permanent errors recorded | (url, etag/last-modified if present) |
| Validation | Accept/reject: host allowlist, size cap, content-type sniff (magic bytes), PDF sanity | Reject → `QUARANTINED` with reason | content_hash |
| Fingerprint | sha256 of raw bytes | — | content_hash |
| Raw storage | Blob at content-addressed path | Write-once; verify after write | content_hash |
| Parsing | `TextLayer` (+ page map), parse report | Sandboxed subprocess; failure → `FAILED_PARSE`, raw retained | (version, extractor_version) |
| Structure extraction | `StructureNode` tree with offsets | Low-confidence structure flagged, still searchable as flat text | (text_layer, parser_version) |
| Semantic segmentation | `Chunk`s aligned to nodes | Deterministic | (text_layer, chunker_version) |
| Metadata extraction | `DateAssertion`s, reference numbers, title — each with evidence span or `status=UNKNOWN` | Never guess; missing stays missing | (version, extractor_version) |
| Indexing | FTS vectors, embeddings | Retry; partial index acceptable only if version not `PUBLISHED` | (chunk, embedding_version) |
| Graph extraction | `CANDIDATE` entities/edges/obligations | Model/rule failures isolated per chunk | (chunk, extraction_version) |
| Verification | Edge/obligation validation (anchor present, quote matches, schema valid, review queue for high-impact predicates) | Failed → `REJECTED`/review | (candidate id, verifier_version) |
| Publishing | Version flips to `PUBLISHED` (visible to retrieval) | Gate below | (version) |

**Publish gate:** text layer exists and hashes verify · structure parsed or explicitly flat · required metadata present or marked UNKNOWN · chunks indexed under the active index version · no quarantine flags. Graph extraction is **not** required to publish a version for text retrieval (graph is additive).

## 2. Source metadata

| Field | Meaning | Trust |
|---|---|---|
| `source_id` | Registered feed/listing (manifest entry) | config |
| `authority` | Issuing body (from manifest, not from document text) | config |
| `document_id` / `document_version_id` | Logical instrument / immutable snapshot | system |
| `canonical_url` | URL the content was fetched from (after redirect validation); several allowed per version | observed |
| `source_reference` | Issuer's own number/identifier as printed, nullable | extracted (UNTRUSTED until evidence-anchored) |
| `document_type` | circular, master circular, regulation, amendment, notification, … | extracted/manifest |
| `published_at`, `effective_from`, `effective_to` | Typed `DateAssertion`s, each with basis/evidence/status | extracted |
| `retrieved_at` | When *we* fetched it (knowledge time) | system |
| `content_hash` | sha256 of raw bytes | system |
| `parser_version`, `embedding_version`, `graph_extraction_version` | Generation versions | system |

## 3. Source manifest (config in git, reviewed)

```yaml
# data/manifests/<source>.yaml — shape only; real entries are added in Phase 1
source_id: <slug>
authority: sebi
allowed_hosts: [<host>]            # exact hosts; no wildcards
listing_urls: [<url>]              # discovery entry points
scope: {document_types: [...], date_window: {from: <date>, to: <date>}, topics: [...]}
document_overrides:                # manual identity/date corrections, each with a reason and reviewer
  - url: <url>; document_key: <key>; reason: <text>
crawl: {min_delay_seconds: <n>, max_bytes: <n>, user_agent_contact: env:RIG_CRAWLER_CONTACT_EMAIL}
```

Manifest changes are code-reviewed. Before the first live crawl (Phase 1) we must verify robots/terms of use of each source (**A-02**); the Executive Summary asserts that listing pages exist but that is unverified here.

## 4. Identity, immutability, versioning

- **No filename identity.** Filenames and URLs are attributes. `document_id` is resolved by, in order: (1) manifest `document_key`; (2) authority + document_type + issuer reference extracted *and anchored to evidence*; (3) a manual-resolution queue. Title similarity never auto-merges documents.
- **Immutability.** `raw_artifact`, `document_version`, `text_layer`, `evidence` are append-only. Corrections create new rows (new parser version, or a manual override record), never edits.
- **Same URL, new content** → new `DocumentVersion` under the same document (hash differs); older version stays retrievable, with `last_seen` on its location. **Different URL, same bytes** → same version, additional location row.
- A legal amendment (e.g., a notification that changes a regulation) is a *different document* related by `Amendment`; it is **not** a new version of the regulation file ([temporal-model](temporal-model.md)).

## 5. Operational concerns

- **Duplicate detection:** raw hash (exact); normalized-text hash (same text, different PDF bytes) → flagged as `DUPLICATE_OF` candidate for review, not auto-merged.
- **Changed-document detection:** conditional GET when supported + hash comparison; a change in content under a stable reference number is surfaced as an alert (possible corrigendum or re-issue), never silently replaced.
- **Partial failures:** per-document state machine `DISCOVERED→FETCHED→VALIDATED→STORED→PARSED→INDEXED→GRAPH_EXTRACTED→PUBLISHED` plus `QUARANTINED`/`FAILED_*`; stages are independently re-runnable; failure never deletes prior stages' outputs.
- **Retries:** bounded attempts with exponential backoff and jitter; poison jobs go to `FAILED` with reason for manual review.
- **Idempotency:** every job has an idempotency key (table above); re-running produces no duplicates (UNIQUE constraints enforce it).
- **Incremental ingestion:** listing diffs by (canonical_url, hash); time-window scoped; full re-crawl only by explicit job.
- **Parser version change:** new `TextLayer`/nodes/chunks under the new version alongside old; evidence stays valid because it anchors to the old immutable text layer; new evidence is created on the new layer. Promotion = switch the active parser version after a regression comparison on fixtures and benchmark.
- **Re-embedding / re-indexing:** new embedding table version built in the background; queries pin the active version; switch only after benchmark comparison; old version dropped by explicit action.
- **Graph re-extraction:** produces new `CANDIDATE` rows under a new `extraction_version`; verified set is not mutated in place; diff old vs new is reviewed before the new version is promoted.
- **Politeness & safety:** rate limits, contact User-Agent, single egress path, host allowlist, DNS/IP checks, redirect re-validation, size/time caps ([security](security.md) T-07/T-08).
- **Out of scope for Phase 1:** XBRL/EDGAR, OCR (only if a parse report shows scanned-image PDFs in the chosen corpus; then a separate decision).
