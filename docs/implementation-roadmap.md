# Implementation Roadmap

Status: PROPOSED. Phases are gates: do not start phase N+1 until phase N's "done" holds. No calendar estimates (no basis yet). Every phase also: updates docs/ADRs it affects, adds tests (H6), preserves provenance (H7). "Benchmark impact" refers to [RegIntelBench](benchmarks/benchmark-spec.md).

## Phase 0 — Architecture foundation (this stage)
- **Goal:** authoritative engineering foundation. **Inputs:** brief, research PDF. **Outputs:** this repository's docs, ADRs, governance files. **Affected:** `docs/`, root files, `scripts/`, CI stub. **Tests:** `scripts/check_foundation.py`. **Acceptance:** your review of [stage-1-review](stage-1-review.md); decisions OD-01 (domain) resolved. **Benchmark:** none. **Risks:** unreviewed assumptions. **Deps:** none. **Done:** you approve or amend ADRs; Phase 1 plan confirmed.

## Phase 1 — Regulatory source manifest + ingestion
- **Goal:** safe, reproducible, idempotent fetch of the manifest-scoped corpus into immutable raw storage with provenance. **Inputs:** ADR-009 decision; ToU/robots check (A-02). **Outputs:** manifest(s); `packages/ingestion` (discovery, fetch, validate, fingerprint, blob store); job-queue skeleton; first migrations (source tables); tooling bootstrap (pyproject, ruff, mypy, pytest, CI); local Postgres setup. **Affected:** `data/manifests`, `packages/ingestion`, `packages/domain`, `migrations`, `workers/ingestion`. **Tests:** SSRF/path/size/redirect unit tests with fake resolver; idempotency; dedupe; recorded-HTTP fixtures; no live network in CI. **Acceptance:** re-run adds zero rows; every fetch passes allowlist; quarantine paths tested. **Benchmark:** defines corpus snapshot id. **Risks:** ToU/anti-bot, site changes. **Deps:** 0. **Done:** corpus snapshot matches manifest listing; ingest report reviewed.

## Phase 2 — Canonical document + version model
- **Goal:** identity, immutability, versioning in the DB. **Inputs:** Phase 1 snapshot. **Outputs:** DDL for document/version/location/date_assertion; identity resolution + manual override records; domain types. **Affected:** `migrations`, `packages/domain`, `packages/ingestion`. **Tests:** migrate-from-empty; immutability; unique constraints; property tests on idempotent upsert; changed-content → new version. **Acceptance:** duplicate bytes → one version; changed bytes → new version; no filename identity. **Benchmark:** none. **Risks:** identity ambiguities in real data. **Deps:** 1. **Done:** all snapshot docs resolved to documents/versions or queued for manual review.

## Phase 3 — Structure-aware parsing
- **Goal:** text layers, structure nodes, chunks, date/reference candidates with anchors. **Inputs:** raw artifacts; parser choice spike (OD-03). **Outputs:** sandboxed parse, `text_layer`, `structure_node`, `chunk`, parse reports. **Affected:** `packages/ingestion`, `workers/ingestion`. **Tests:** golden synthetic docs; offset round-trip property; hidden-text and malformed-PDF fixtures; parser-version coexistence. **Acceptance:** every evidence slice re-hashes; structure coverage reported on real corpus. **Benchmark:** structure/metadata extraction accuracy on labelled subset. **Risks:** scanned PDFs, tables, headers/footers. **Deps:** 2. **Done:** published versions have verified text layers; parse report reviewed.

## Phase 4 — Baseline hybrid RAG
- **Goal:** retrieval pipeline per contract. **Inputs:** chunks. **Outputs:** FTS + embeddings (stub + one real model), identifier index, fusion, optional rerank, evidence selection, retrieval API/CLI, `retrieval_run` records. **Affected:** `packages/retrieval`, `apps/api`, `workers/indexing`. **Tests:** contract tests; provenance through stages; RRF properties; filter correctness; offline stub path. **Acceptance:** contract satisfied; runs offline with stubs. **Benchmark:** enables A1–A3. **Risks:** ANN filtering behaviour (A-05), FTS ranking (OD-04). **Deps:** 3. **Done:** retrieval returns provenance-complete results for smoke queries.

## Phase 5 — Retrieval benchmark
- **Goal:** first measurements. **Inputs:** Phase 4. **Outputs:** RegIntelBench v0 (synthetic + labelled SEBI subset), harness, run records, `targets.yaml` (ASPIRATION). **Affected:** `packages/evaluation`, `data/eval`. **Tests:** harness determinism; label schema validation. **Acceptance:** A1–A3r run reproducibly with CIs. **Benchmark:** baseline established. **Risks:** labelling effort; small N. **Deps:** 4. **Done:** documented ablation of dense/sparse/hybrid/rerank; decisions recorded (ADR-003 updated).

## Phase 6 — Temporal/version reasoning
- **Goal:** resolver + version diff. **Inputs:** authority-specific patterns derived from real text. **Outputs:** verified date assertions, `effective_period`, `TemporalResolution`, as-of filtering, structural diff. **Affected:** `packages/domain`, `retrieval`, `ingestion`. **Tests:** property tests (permutation invariance, monotonicity, no newest-wins); synthetic temporal cases; benchmark temporal. **Acceptance:** ambiguous/missing cases abstain. **Benchmark:** temporal category. **Risks:** extraction accuracy; unclear supersession. **Deps:** 5. **Done:** temporal benchmark run recorded.

## Phase 7 — Regulatory graph
- **Goal:** evidence-bound graph. **Inputs:** chunks, curated vocabulary. **Outputs:** kg tables, extractors, verification gate, review queue, amendments/supersessions/obligations. **Affected:** `packages/graph`, `migrations`, `workers`. **Tests:** DB constraints; fake-supersession injection fixtures; extraction P/R vs gold subset. **Acceptance:** no VERIFIED edge without evidence; review queue exercised. **Benchmark:** extraction metrics. **Risks:** extraction quality, review cost. **Deps:** 6. **Done:** verified graph for the corpus with measured P/R.

## Phase 8 — Graph retrieval
- **Goal:** router-gated traversal. **Outputs:** `GraphReader`, limits, coverage check, graph evidence in packs. **Tests:** traversal limits; coverage fallback; misroute safety. **Acceptance:** baseline always runs; truncation flagged. **Benchmark:** arm A4 vs A3 on multi-hop/amendment. **Risks:** no gain (then demote via ADR). **Deps:** 7. **Done:** A4 recorded.

## Phase 9 — Agentic query orchestration
- **Goal:** bounded orchestrator. **Outputs:** state machine, tool registry, budgets, loop prevention, fallbacks. **Affected:** `packages/agents`. **Tests:** budget exhaustion, loops, obedient-stub injection tests, read-only enforcement. **Acceptance:** all termination reasons exercised. **Benchmark:** arm A5. **Risks:** cost/latency; marginal benefit. **Deps:** 8. **Done:** A5 recorded.

## Phase 10 — Evidence/claim verification
- **Goal:** citations by handle + verification V1–V7. **Outputs:** claim extraction, matching, checks, composition, `answer_record`. **Tests:** fabricated-citation rejection; hash integrity; state composition; stub entailment. **Acceptance:** citation validity 100% by construction. **Benchmark:** citation precision/recall, claim support. **Risks:** verifier reliability. **Deps:** 9 (core can start after 4). **Done:** metrics recorded.

## Phase 11 — Contradiction/abstention
- **Goal:** conflict-vs-change detection and abstention policy. **Tests:** cross-version change ≠ contradiction; abstention reasons. **Acceptance:** abstention fixtures pass. **Benchmark:** contradiction/abstention categories; arm A6. **Risks:** over-abstention. **Deps:** 10. **Done:** false-answer vs false-abstain rates reported.

## Phase 12 — MCP
- **Goal:** read-only MCP adapter. **Outputs:** tools per [mcp-design](mcp-design.md), authz, audit. **Tests:** schema fuzzing, scope matrix, tool-abuse, no forbidden capability. **Acceptance:** no unrestricted capability exposed. **Benchmark:** agent-consumer tasks (optional). **Risks:** client-side injection. **Deps:** 10. **Done:** contract tests green.

## Phase 13 — Security hardening
- **Goal:** close threat-model gaps. **Outputs:** sandbox review, fuzz runs, secret scanning, dependency audit, expanded injection corpus, authz matrix, retention policy. **Tests:** full `tests/security`. **Acceptance:** all T-xx have tests. **Benchmark:** adversarial category. **Deps:** 12. **Done:** threat model re-reviewed.

## Phase 14 — Observability
- **Goal:** traces, metrics, explanation tooling. **Outputs:** exporter (OD-07), dashboards, alerts, "why this answer" view. **Tests:** span/ID propagation; redaction. **Acceptance:** any answer reconstructable from records. **Deps:** 10+ (instrumentation begins Phase 1). **Done:** reconstruction drill passes.

## Phase 15 — Performance/cost optimization
- **Goal:** meet budgets with evidence. **Outputs:** profiles, load tests, chosen optimizations from [performance](performance.md), cost report. **Acceptance:** each optimization shows measured gain without quality regression. **Benchmark:** latency/cost/cache metrics. **Deps:** 11. **Done:** report with before/after run records.

## Phase 16 — API/UI
- **Goal:** stable API and UI (evidence viewer with highlighted spans, temporal panel, abstention display, disclaimers). **Affected:** `apps/api`, `apps/web`. **Tests:** API contract, UI e2e (offline stubs). **Acceptance:** every displayed claim links to its evidence. **Deps:** 11. **Done:** usability review.

## Phase 17 — End-to-end evaluation
- **Goal:** freeze targets, run full ablation, human review, limitations report. **Acceptance:** results recorded with CIs; targets frozen before running test split. **Deps:** 5–16. **Done:** independent read of results.

## Phase 18 — Production packaging/deployment
- **Goal:** reproducible deployment. **Outputs:** containers, config, backup/restore tests, runbooks, release process, SBOM, limitations disclosure. **Acceptance:** restore drill, security checklist. **Deps:** 17. **Done:** release candidate with signed-off limitations.
