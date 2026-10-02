# Executive Summary (research input) and Reconciliation Register

This file records what the attached research PDF (`Executive_Summary.pdf`, titled "FinRegGraph") proposes and how each proposal was treated. The PDF is **research input, not authority**. It is summarised, not reproduced. Its figures (architecture picture, Gantt chart) are not machine-readable here; its text claims about external sites and APIs were **not independently verified in Stage 1** (see A-02).

## 1. What the PDF proposes (summary)

A "FinRegGraph" agentic-RAG system for regulatory intelligence across SEBI, RBI, SEC/EDGAR (incl. XBRL), FINRA, gazettes and other regulators: scraping/API ingestion with PDF/HTML/XBRL parsing; NER/relation extraction into a knowledge graph (Neo4j or graph tables); hybrid sparse+dense+graph retrieval with score fusion; a planner/retriever/generator agent with MCP-style tool endpoints; LLM/NLI-based verification and contradiction detection; temporal reasoning over amendments; security/observability sections; QA metrics (Recall@k, EM, F1) with FinanceBench-like sets; hosted vs local LLM comparison; microservices deployment (FastAPI, Postgres+pgvector, Neo4j, Redis, Next.js, Docker/Kubernetes, CI/CD); a 6–12 month roadmap; and a list of open-source contribution ideas.

## 2. Reconciliation register

Dispositions: **ADOPTED**, **ADOPTED-MODIFIED**, **DEFERRED**, **REJECTED**, **CORRECTED**, **UNVERIFIED**.

| ID | PDF position | Disposition | Reason / decision | Recorded in |
|---|---|---|---|---|
| R-01 | Product name "FinRegGraph", broad finance/regulatory scope | ADOPTED-MODIFIED | Brief governs: RegIntelGraph, regulatory intelligence + evidence analysis | README |
| R-02 | Multi-regulator ingestion (SEC, FINRA, RBI, gazettes, FCA/ESMA, XBRL) | DEFERRED | Stage-1 constraint: India/SEBI subset only | ADR-009 |
| R-03 | Microservices; Docker + Kubernetes/compose | REJECTED (for now) | Principle K; consistency of provenance in one transaction | ADR-001 |
| R-04 | Airflow/Prefect orchestration | DEFERRED | Postgres job table first | ADR-002 |
| R-05 | Redis for cache and agent memory | DEFERRED | Measure cache need first; agent state in Postgres | ADR-002, performance |
| R-06 | PostgreSQL + pgvector | ADOPTED | System of record + vectors | ADR-002 |
| R-07 | Neo4j (or graph tables) | ADOPTED-MODIFIED | Graph tables adopted; Neo4j deferred with triggers | ADR-004 |
| R-08 | Elasticsearch/OpenSearch or Postgres FTS | ADOPTED-MODIFIED | Postgres FTS baseline; BM25 option open (OD-04) | ADR-002/003 |
| R-09 | Weighted-sum or ML-reranker fusion across vector/sparse/graph | ADOPTED-MODIFIED | RRF baseline; weighted fusion as ablation; graph additive | ADR-003 |
| R-10 | Embed graph nodes in the vector index | DEFERRED | No evidence it helps; may be an ablation later | graph-schema |
| R-11 | spaCy/fine-tuned NER and relation classifiers | ADOPTED-MODIFIED | Rules + models produce CANDIDATEs; verification gate; curated vocabulary; no fine-tuning in scope | graph-schema |
| R-12 | Example schema (`amended_by`, `hasClause`, `associatedWith`, …) | ADOPTED-MODIFIED | Only relations with a defined query pattern; dates are assertions not edges | graph-schema |
| R-13 | Versioning "by document date or gazette number" | REJECTED | Identity is not date/filename; versions are immutable content snapshots; legal state derived | ADR-007, ingestion-design |
| R-14 | Agent pseudocode appends top results then generates | REJECTED | Evidence packs + verification gate + composition | ADR-005 |
| R-15 | "MCP (Model-Chain / Multi-Chain Protocol)" | CORRECTED | MCP is the Model Context Protocol; read-only domain tools via thin adapter | mcp-design |
| R-16 | Tool endpoints: search_regs, get_document, get_section, find_relations, compare_versions, verify_claim | ADOPTED-MODIFIED | Expanded/renamed per brief; strict schemas; no generic execution | mcp-design |
| R-17 | Verify with LLM YES/NO or NLI model | ADOPTED-MODIFIED | Deterministic checks first; model may only lower/confirm; model choice deferred and validated | ADR-008 |
| R-18 | Contradiction = answer unsupported by source | REJECTED | Distinct states: unsupported ≠ contradicted | evidence-model |
| R-19 | On conflict, "preferentially use the latest rule" | REJECTED | Recency never decides; explicit relations or abstain | ADR-007 |
| R-20 | `EFFECTIVE_DATE` edge to Date node; compare dates | ADOPTED-MODIFIED | Typed `date_assertion` with basis/evidence | temporal-model |
| R-21 | Citation format `【docID†Lx-Ly】` | REPLACED | Offset+hash anchors; citation by handle | ADR-008 |
| R-22 | Strict input schemas, no code execution, audit log | ADOPTED | Extended to full threat model | security |
| R-23 | Provider guardrail products (e.g., cloud policy frameworks) | DEFERRED | Provider-specific; structural controls first | security |
| R-24 | Prometheus/Grafana, ELK/Splunk | DEFERRED | OTel-compatible instrumentation first; backend choice OD-07 | observability |
| R-25 | FinanceBench/FinQA/TaxQA; EM/F1; LLM-as-judge | ADOPTED-MODIFIED | RegIntelBench with citation/temporal/abstention metrics; existence/relevance of external sets unverified (PDF itself is conditional); EM/F1 insufficient; judge auxiliary only | evaluation |
| R-26 | Targets: <2 s average latency, ≥80% benchmark success | REJECTED | No definitions, no baseline; unrealistic for verified multi-step routes; budgets are labelled ASPIRATION | performance, evaluation |
| R-27 | Hosted (GPT-4o/Claude 3) vs local (Llama-3/Mistral/Falcon) tables | DEFERRED | Tables unsourced, dated, internally questionable; select per task by benchmark | ADR-006 |
| R-28 | Cloud LLMs acceptable because data are public | ADOPTED (assumption) | A-07; does not cover future private documents | ADR-006 |
| R-29 | 5-phase, 6–12-month calendar roadmap | REPLACED | 19 controlled phases with acceptance criteria; no calendar estimate without a basis | implementation-roadmap |
| R-30 | Keep source docs "in version control or snapshot storage" | REJECTED (git) | Raw files in content-addressed blob store; manifests in git | ingestion-design, .gitignore |
| R-31 | Domain experts to validate output | ADOPTED | Needed for labels and review queues | benchmark-spec |
| R-32 | Claims about SEBI/RBI/FINRA/EDGAR pages, APIs, bulk files, licensing | UNVERIFIED | Verify in Phase 1 before relying on them | A-02 |
| R-33 | Open-source contribution list (some projects/claims uncertain) | OUT OF SCOPE | Not part of the foundation; not verified | — |
| R-34 | Log all queries/accesses for audit | ADOPTED-MODIFIED | Plus retention/privacy policy: queries can be sensitive | observability |

Where the PDF and engineering reasoning disagree, the smallest defensible decision was taken and recorded in the ADR named above; nothing was silently dropped.
