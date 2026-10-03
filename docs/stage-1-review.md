# Stage 1 Review

Self-review of the foundation. Findings are what I could determine by reading the documents against each other; nothing here was validated against real SEBI data (none was fetched).

## 1. Environment inspected

Empty workspace, not a git repository. Ubuntu 24.04 (x86_64), 1 CPU, ~4 GB RAM. Python 3.12.3, pip 24.0, uv 0.11.7, pipx. Node v22 / npm 10.9 (no pnpm/yarn). **No Docker/Podman, no PostgreSQL (no psql/pg_config), no local model runtime or GPU.** git 2.43. Reproduce with `scripts/inspect_env.sh`. Implication: Postgres-dependent integration tests need a local Postgres or a CI service container, set up at the start of Phase 1 (not installed here).

## 2. What was decided

Modular monolith (ADR-001) · Postgres + pgvector + FTS + content-addressed blobs; Redis/Airflow/broker deferred (ADR-002) · hybrid retrieval with RRF as a *hypothesis* tested by ablation (ADR-003) · relational graph with evidence-bound edges, Neo4j deferred with triggers (ADR-004) · bounded state-machine orchestrator, read-only tools (ADR-005) · provider-agnostic model interfaces, selection by benchmark (ADR-006) · explicit-relation temporal policy, no recency heuristics (ADR-007) · evidence anchors + citation by handle + deterministic-first verification (ADR-008) · proposed first domain SEBI Mutual Funds (ADR-009, **needs your decision**).

## 2b. Phase 1 status (updated in Phase 1C)

| State | Status |
|---|---|
| Phase 1A reconnaissance / 1B contracts and plans | done |
| Phase 1C contract-first implementation | **implemented locally; tested offline; integration-tested against a project-local PostgreSQL 16** |
| CI workflow | configured; **not yet observed running on GitHub** |
| Source-access gate (human review) | **closed** — `ingestion_authorized: false`; robots.txt, automated-retrieval permission, copyright/reproduction scope, listing pagination and canonical Master Circular byte verification are unresolved |
| Phase 1D controlled live sample / live corpus ingested | **not started; blocked on the gate** |

## 3. Open decisions

| ID | Decision | Needed by |
|---|---|---|
| OD-01 | Confirm initial domain (ADR-009) — **RESOLVED**: India · SEBI · Mutual Funds (not a legal-compliance determination) | Before Phase 1 |
| OD-02 | Migration tool — **RESOLVED** (Phase 1C): minimal in-repository forward-only SQL runner (`scripts/migrate.py`) | Phase 1–2 |
| OD-03 | PDF/HTML parsing library (spike on real documents) | Phase 3 |
| OD-04 | Sparse ranking: `ts_rank` vs BM25 extension vs app-side BM25 | Phase 5 |
| OD-05 | Vector index type (exact/HNSW/IVFFlat) | Phase 5 |
| OD-06 | Dedicated predicate for corrigenda | Phase 6 |
| OD-07 | Telemetry backend/exporter | Phase 14 (instrumentation from Phase 1) |
| OD-08/09/10 | Embedding model, reranker, per-task LLMs | Phases 5, 9–10 |
| OD-11 | AuthN/AuthZ mechanism | Phase 12/16 |
| OD-12 | Whether to publish derived "as amended" consolidated views | Phase 6 |
| OD-13 | Data retention policy (queries, traces, run records) | Phase 14 |
| OD-14 | PDF sandbox mechanism (subprocess + rlimits vs container) | Phase 3 |

## 4. Assumptions to validate

| ID | Assumption | Validate by |
|---|---|---|
| A-01 | The SEBI Mutual Funds subset is a suitable first corpus (size, structure, parse quality) | Phase 0b/1 sampling |
| A-02 | Documents are publicly downloadable and terms/robots permit automated retrieval; the PDF's statements about site structure are accurate | Phase 1, before first crawl |
| A-03 | Corpus scale (chunks, versions) is small enough for Postgres FTS + pgvector without a dedicated engine | Phase 1 counts, Phase 5 latency |
| A-04 | Graph size and traversal depth stay modest | Phase 7–8 |
| A-05 | pgvector filtered search returns adequate results on the chosen version | Phase 4–5 |
| A-06 | Most source PDFs are born-digital (no OCR) | Phase 3 parse report |
| A-07 | Hosted model use is acceptable for public data | Your confirmation |
| A-08 | Effective dates and supersession are usually stated explicitly in the text | Phase 6 sampling |
| A-09 | A domain reviewer is available for labels and review queues | Your confirmation |
| A-10 | English-language sources only | Phase 1 |

## 5. Risks

| ID | Risk | Mitigation |
|---|---|---|
| RK-01 | Source access/terms/anti-bot limits | A-02 check; polite crawling; manual manifest fallback |
| RK-02 | Temporal extraction errors | Verified assertions, abstention, manual review on benchmark corpus |
| RK-03 | Labelling cost/time; small N, wide CIs | Synthetic corpus for breadth; stratified real subset; report CIs honestly |
| RK-04 | PDF structure quality (tables, headers, scans) | Flat-text fallback; parse reports; OD-03 spike |
| RK-05 | Over-abstention reduces usefulness | Report false-abstain separately; tune with benchmark |
| RK-06 | Model verifier unreliability | Deterministic-first, calibrated on labels, never sole judge |
| RK-07 | AI-assisted drift | AGENTS.md (single constitution), ADR rule, `check_foundation.py`, ratchets |
| RK-08 | Scope creep | ADR-009; roadmap gates |
| RK-09 | Weak lexical ranking in Postgres FTS | OD-04 ablation |
| RK-10 | Graph extraction quality and human review burden | Curated vocabulary; review only high-impact predicates; demote graph if no gain |
| RK-11 | Outputs perceived as legal advice | Disclaimers, scope statements, H18 |
| RK-12 | Residual injection risk | Structural defences; obedient-model tests; read-only agent |
| RK-13 | Single-maintainer bus factor | Docs-first design; deterministic tests |

## 6. Contradictions discovered

**Brief vs research PDF:** microservices/K8s/Redis/Airflow (vs principle K) · Neo4j (vs "do not assume") · multi-regulator scope (vs constrained scope) · "<2 s", "≥80%" targets (vs measurement-before-claims) · "latest rule" preference (vs temporal correctness) · contradiction defined as unsupported (vs distinct states) · line-style citations (vs anchored provenance) · raw docs in version control (vs git safety) · all resolved via the ADRs/register in [executive-summary](executive-summary.md).

**Within the PDF:** wants answers in <2 s yet plans planner LLM + multiple tool calls + LLM verification; says no privacy issues yet logs all queries; calls MCP "Model-Chain/Multi-Chain Protocol"; model table figures look inconsistent.

**Brief internal tension:** it asks for "target ranges" while forbidding invented numbers → resolved as hard invariants + relative hypotheses now, absolute targets frozen from baselines later ([evaluation](evaluation.md) §4). Its relation list includes `SUPPORTED_BY` as a relation → modelled as the evidence join to prevent unsupported "support" edges ([graph-schema](graph-schema.md)). Its `.gitignore` list includes generic names (`models/`, `build/`, `logs/`) that would also hide source packages of the same name → anchored to repo root.

**Found while self-reviewing my own documents:** `docs/benchmarks` initially said run records were git-ignored while `.gitignore` did not ignore them → fixed (committed only when cited). No ADR conflicts found: ADR-003 (graph additive) and ADR-005 (agent drives) are consistent; ADR-007 and ADR-008 are referenced by temporal and evidence docs respectively.

## 7. Review checklist results

| Check | Finding | Action |
|---|---|---|
| Architecture drift | Package boundaries defined but not mechanically enforced yet | Import-boundary check in Phase 1 CI |
| Data-model inconsistency | `document_version.status` lifecycle values are listed in ingestion-design but not enumerated in DDL | Enumerate in Phase 2 migration |
| Temporal gaps | No SEBI-specific rules (by design); hierarchy of norms intentionally not encoded; date precision/time zones not specified | Phase 6; add timezone/precision rules |
| Provenance gaps | HTML has no pages; tables/scans anchoring unspecified; `RELATIVE_TEXT` computed dates need formula provenance | Phase 3 decisions; store formula + inputs |
| Security gaps | Sandbox mechanism and AuthN/Z unspecified (OD-14, OD-11) | Decide at Phases 3 and 12 |
| Evaluation gaps | Judge calibration protocol and annotator availability undefined | Phase 5 plan; A-09 |
| Unnecessary complexity | Candidates for trimming: `Requirement`, `known_as_of` axis, claim `criticality`, 7 ablation arms | `Requirement` explicitly deferred; others kept minimal and revisitable |
| Undefined interfaces | Pydantic schemas, `JobQueue`, `GraphReader`, provider interfaces, prompt templates are described, not specified | Phase 1–4 first deliverables |
| Conflicting ADRs | None found | — |
| Unsupported assumptions | Listed in §4 | Validate per table |

## 8. Recommended next implementation phase

**Phase 1** after you resolve OD-01 (domain) and confirm A-07/A-09. First sub-step: a read-only validation of A-01/A-02 (ToU/robots, document counts, sample parse), *then* the manifest, then the ingestion code. Do not start crawling before that check.
