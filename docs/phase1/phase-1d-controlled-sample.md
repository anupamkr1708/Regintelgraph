# Phase 1D — explicit selection, detail-page discovery, CLI (offline infrastructure)

Status: implemented and tested OFFLINE. This document describes stable contracts only. It grants no authorisation: the access-review
gate is unchanged (`AMBIGUOUS_REQUIRES_REVIEW`, `ingestion_authorized: false`) and LIVE retrieval remains impossible until a human
review changes the manifest. Safety policy version is unchanged (`source-safety-contract@phase-1c`). Pagination is still
`PAGINATION_METHOD_UNRESOLVED`; nothing here addresses it.

## Explicit candidate selection (`packages/ingestion/selection.py`)
- Execution scope is `IngestConfig.selected_candidate_keys` (manifest `document_key`s). The manifest is inventory, not scope.
- `None`, empty, unknown, duplicated or non-string selections are refused: `ABORTED(POLICY_VIOLATION)` before any resolver/transport
  exists. There is no fallback to "all candidates"; `sampled`, `tier` and the presence of `document_url` select nothing.
- The caller's order is the execution order (never re-sorted). The five-document benchmark is a caller-supplied selection, not a
  hardcoded list.

## Detail-page discovery (`packages/ingestion/discovery.py`, pure)
- A selected candidate with no `document_url` but a `canonical_url` fetches that detail page (purpose `DETAIL_PAGE`), then discovers.
- Only URLs OBSERVED in `a[href]`, `iframe[src]`, `embed[src]`, `object[data]` are considered, resolved against the page URL. Nothing is
  guessed or constructed; `<base>` is ignored; nothing is executed; no recursion.
- Every candidate goes through the canonical validator (`EgressClient.check_url` -> `urlpolicy.validate_url`, ATTACHMENT purpose).
- A viewer wrapper (`/web/?file=<url>`) is recognised by the validator's `VIEWER_WRAPPER_URL` verdict and is never fetched; only its
  already-present, strictly decoded `file` value is validated as a candidate (one level, no recursion).
- Distinct valid URLs: 1 -> `DISCOVERED`; 0 -> `UNRESOLVED(NO_VALID_ATTACHMENT_REFERENCES)`; 2+ ->
  `UNRESOLVED(AMBIGUOUS_ATTACHMENT_REFERENCES)` with no attachment fetch (abstention, no tie-break).
- Provenance (no migration): the detail fetch is a `fetch_request` row with purpose `DETAIL_PAGE`; a stored artifact carries
  `http_meta["discovery"]`; every outcome is in `ingest_run.stats["discovery"]` (bounded, log-safe). The page body is not stored.
- Decoding: the egress layer yields raw bytes; `validate.decode_html` decodes strictly using the declared charset (UTF-8 default).
  Undecodable pages are `UNRESOLVED(DETAIL_PAGE_UNDECODABLE)`; no lossy replacement.

## CLI (`python -m packages.ingestion.cli`)
```
python -m packages.ingestion.cli --mode dry-run --manifest <path> --candidate-key <key> [--candidate-key <key> ...]
python -m packages.ingestion.cli --mode live    --manifest <path> --candidate-key <key> [...] \
    --code-version <id> [--authorisation-ref <runtime-supplied>]
```
- `dry-run` is a PURE plan: no dependencies constructed, no network, no persistence, no URL validation (that needs configured limits).
  It reports the gate and crawl-configuration state and never claims readiness or authorisation.
- `live` calls the existing `run_ingest`, which owns every gate. Runtime configuration (never in Git): `RIG_DATABASE_URL`,
  `RIG_BLOB_ROOT`, `RIG_QUARANTINE_ROOT` (disjoint from the blob root), crawler contact env var named by the manifest. Presence of
  these never opens the gate.
- Exit codes: 0 planned/completed; 1 run did not complete (e.g. `ABORTED(GATE_CLOSED)`); 2 invalid input/configuration.

## Deferred
Canonical document/version modelling, supersession, amendment lineage, pagination, and the human decisions on access review, reviewed
crawl limits, crawler contact and the authorisation reference remain open and are out of scope here.
