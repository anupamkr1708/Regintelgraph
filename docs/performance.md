# Performance Goals

Status: PROPOSED. Every number below is an **`ASPIRATION` (unmeasured)** engineering budget used to allocate effort; none is a result (H14). They are revised after Phase 5 (retrieval baseline) and Phase 9/10 (agentic + verification) measurements. The Executive Summary's blanket "<2 s average end-to-end" is not adopted: it ignores verification and multi-step routes.

## 1. Latency budgets (ASPIRATION)

| Route / subsystem | p50 | p95 | p99 | Notes |
|---|---|---|---|---|
| Retrieval-only API (filter+sparse+dense+fuse) | ≤ 400 ms | ≤ 1 s | report only | Excludes rerank |
| Reranking (top N_rerank) | ≤ 300 ms | ≤ 800 ms | — | Optional; arm A3r decides |
| Temporal resolution | ≤ 50 ms | ≤ 150 ms | — | Deterministic, in-DB data |
| Graph traversal (depth ≤ 3) | ≤ 150 ms | ≤ 500 ms | — | Triggers Neo4j revisit if exceeded on real data |
| Verification (deterministic V1–V5, V7) | ≤ 200 ms | ≤ 600 ms | — | Plus model check when enabled |
| Generation (draft) | route-dependent | route-dependent | — | Dominated by model latency |
| **End-to-end simple answer (hybrid → draft → verify)** | ≤ 4 s | ≤ 8 s | report only | Streaming of verified parts allowed |
| End-to-end temporal/graph answer | ≤ 6 s | ≤ 12 s | — | |
| Agentic / impact report | async | ≤ 60 s | — | Progress events; partial results labelled |

## 2. Principles

1. **Measure first.** Instrument (Phase 1+), baseline (Phase 5/9/10), then optimize (Phase 15). No optimization without a profile showing it matters.
2. **Correctness before speed.** Early stopping and limits may reduce recall; each is evaluated in the ablation harness.
3. **Cheap path first.** Deterministic routing and identifier lookup skip model calls.

## 3. Candidate optimizations — and what must be measured first

| Candidate | Measure before | Adopt when |
|---|---|---|
| Query/embedding cache | Repeat-query rate, embedding latency share | Hit rate × saved cost justifies invalidation complexity (versions in cache key) |
| Result caching (retrieval) | p95 retrieval, repeat rate | Retrieval over budget with high repeat rate |
| Batch embeddings (ingestion) | Embedding throughput, provider limits | Ingestion wall-time unacceptable |
| Incremental ingestion | Full-run duration | Already default by design (hash diff) |
| Model routing / local models | Per-route cost share and quality by model (ADR-006) | A cheaper model matches quality on that route in the ablation |
| Query-complexity routing | Route mix | Already by design; tune thresholds from traces |
| Early stopping in agent | Calls/run, marginal evidence per iteration | Diminishing returns visible |
| Graph traversal limits | Depth/fan-out distributions | Budget breaches |
| Result/rerank candidate limits | Recall@K vs N curves | Curve flattens |
| Token budgeting of packs | Pack size vs claim-support | Support does not degrade |
| ANN index (HNSW/IVFFlat) vs exact | Corpus size, exact-scan latency | Exact scan exceeds budget |
| Redis / external cache | In-process/Postgres cache hit rate and latency | Cross-process sharing needed and measured |

## 4. Measurement plan

Benchmarks run on pinned hardware class with warm/cold distinction; record p50/p95/p99 per span from traces; separate query-path from ingestion load; load tests introduced Phase 15. Results stored as run records ([evaluation](evaluation.md) §4).
