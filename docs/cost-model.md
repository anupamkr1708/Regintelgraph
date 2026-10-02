# Cost Model

Status: PROPOSED. This is a **model with unfilled parameters**. Prices change and are not asserted here; fill from provider price sheets and measured token counts at run time (H14).

## 1. Per-query cost

```
cost(query) = Σ_llm_calls [ in_tokens × p_in(model) + out_tokens × p_out(model) ]
            + embedding_calls × p_embed
            + rerank_cost                       (0 if local CPU/GPU amortized separately)
            + compute_share(retrieval, graph, verification)
```

Per route (FACTUAL, TEMPORAL, COMPARISON, MULTI_HOP, IMPACT): measure mean/p95 of each term from `llm.call` spans. Verification model calls are counted separately so the cost of safety is visible.

## 2. One-time and recurring costs

| Item | Formula | Driver |
|---|---|---|
| Initial embedding | corpus_tokens × p_embed | Corpus size, chunking |
| Re-embedding (model/chunker change) | same as above, per change | Frequency of version bumps — gate behind benchmark gain |
| Graph extraction | chunks × (in+out tokens) × p_llm, + verification | Extraction model choice, candidate rate |
| Storage | raw + text layers + indexes | Small for a focused subset (assumption **A-03**) |
| Ingestion compute | parse CPU time | Corpus size |

## 3. Cost levers (each evaluated, none assumed)

Deterministic routing and identifier lookup (zero model calls) · pack token budgets · model routing by task (classification/extraction/verification may tolerate smaller models — **tested on the benchmark per task**) · local models behind the same provider interface (trade GPU/ops cost vs API cost; privacy is not the driver since sources are public) · caching with version-aware keys · early stopping · batch/offline graph extraction.

## 4. Budgets and guardrails

Per-request hard cap (agent stops before the next paid call), per-principal daily cap, global alert threshold. Values are config, set after baselines. Eval runs record total cost so ablation arms are compared on quality **and** cost.

## 5. Parameter table (to be filled by measurement)

| Parameter | Source | Value |
|---|---|---|
| p_in / p_out per model | Provider price sheet (date-stamped) | TBD |
| tokens per route (mean, p95) | `llm.call` spans | TBD |
| embedding tokens per corpus | Ingestion run | TBD |
| calls per agentic run | Traces | TBD |
| cache hit rate | `cache.lookup` | TBD |
