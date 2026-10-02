# Retrieval Design

Status: PROPOSED ([ADR-003](adr/ADR-003-retrieval-strategy.md)). All numeric parameters below are **initial configuration defaults, unmeasured** (H14); they are set in config and tuned only via the benchmark.

## 1. Pipeline

`QUERY → normalization → classification → metadata/temporal filtering → sparse ∥ dense (∥ identifier lookup) → fusion → reranking → evidence selection`

1. **Normalization.** Unicode NFKC, whitespace, case-folded copy for lexical search. Identifiers (circular numbers, section labels, dates, numbers with units) are extracted by deterministic patterns and preserved verbatim. Abbreviation/synonym expansion uses a **curated glossary in git**, never a model; every expansion is recorded in the run.
2. **Classification (router).** Deterministic rules first (identifier present, "as of/on <date>", "between versions/what changed", relationship words), classifier fallback (model or small local; stub in tests). Output: `route` ∈ {IDENTIFIER_LOOKUP, FACTUAL, TEMPORAL, COMPARISON, MULTI_HOP, IMPACT, OUT_OF_SCOPE} + confidence + rule/classifier version. Low confidence → fall back to FACTUAL (hybrid) and say so in the trace.
3. **Filtering.** Hard filters from query/context: authority, document type, status = `PUBLISHED`, date window, and — for as-of queries — the temporal resolver's candidate version set ([temporal-model](temporal-model.md)). Filters are applied **before ranking** in SQL; they are never left to the model.
4. **Sparse retrieval.** Postgres FTS over `chunk.tsv` plus the identifier index (deterministic exact match gets top priority within its own result list).
5. **Dense retrieval.** Embedding of the (normalized) query; nearest neighbours over chunk embeddings for the pinned `embedding_version`. Chunk embeddings may be computed from *contextualized* text (hierarchy path + chunk text); **evidence anchors always refer to the original text layer**, never the contextualized string.
6. **Fusion.** Reciprocal Rank Fusion as baseline (rank-based, no score calibration needed between FTS and cosine); weighted-score fusion is an ablation arm. Per-list candidate limit `N_candidates` (default placeholder 50).
7. **Reranking.** Optional cross-encoder on top `N_rerank` fused candidates (default placeholder 30), behind a `Reranker` interface; `none` and a deterministic stub exist for CI. Reranking only reorders; it cannot add items or alter text/provenance.
8. **Evidence selection.** Choose a bounded, de-duplicated, diverse set (`K_final`, placeholder 8) with a token budget; attach structural neighbours (parent heading path) as context-only items, flagged `role=CONTEXT`.

## 2. Why hybrid

- **Sparse** is preferable for identifiers, defined terms, exact phrases, numbers, section labels — frequent in regulatory queries; deterministic and explainable.
- **Dense** is preferable for paraphrased/conceptual queries ("limits on investing in unrated securities" vs the text's own wording), and cross-vocabulary matches.
- Neither dominates; the benchmark's Dense/Sparse/Hybrid ablation ([evaluation](evaluation.md)) decides whether hybrid earns its cost. This is a hypothesis, not a result.

## 3. Metadata filters with vector search

Selective filters can make approximate vector indexes under-return results. Strategy: (a) when the filtered candidate set is small (e.g., an as-of version set), do filtered **exact** distance over that set; (b) otherwise use the ANN index with filter and verify returned count ≥ requested, falling back to (a) if not. Behaviour must be verified on the chosen pgvector version (**A-05**). Retrieval records which path was taken.

## 4. When graph traversal is invoked

Not part of the baseline. Invoked by the router for MULTI_HOP / IMPACT and by the planner when the evidence pack mentions entities with `VERIFIED` edges. Traversal returns *edges + their evidence*; text is then fetched through the same evidence pipeline. See [graph-schema](graph-schema.md) §4 for when NOT to use it.

## 5. Provenance survives every stage

Each candidate carries `candidate_id`, `chunk_id`, `text_layer_id`, offsets, `document_version_id` and a `trace[]` of `{stage, rank_before, rank_after, score, params_version}`. Stages may only reorder/filter/annotate; none may rewrite `text`. A final check asserts `sha256(slice of text_layer) == quote_hash` before an item is eligible to become `Evidence`.

## 6. Diversity and duplicates

- **Exact duplicates** (same `text_hash`, e.g. a clause unchanged across versions): grouped, not discarded. If the query has an as-of date, only the applicable version's chunk is returned; otherwise the group is shown once with `appears_in_versions[]`, and the group's representative is the latest *applicable* version, never merely the newest.
- **Near duplicates** (high lexical overlap, different documents, e.g. master circular vs original circular): kept as separate items because their authority/status differ; the temporal resolver labels each.
- **Diversity:** per-document cap (default placeholder 3 of `K_final`) and optional MMR over embeddings; evaluated via ablation, not assumed.

## 7. Result contract

```json
{
  "retrieval_run_id": "uuid",
  "results": [{
    "candidate_id": "uuid",
    "chunk_id": "uuid",
    "text": "<verbatim from text layer — UNTRUSTED SOURCE TEXT>",
    "location": {
      "document_id": "uuid", "document_version_id": "uuid", "text_layer_id": "uuid",
      "node_path": ["Part II", "3", "3.2(a)"], "start_offset": 0, "end_offset": 0,
      "page_start": 0, "page_end": 0, "canonical_url": "https://…"
    },
    "temporal": {"status": "APPLICABLE|NOT_YET_EFFECTIVE|SUPERSEDED|UNKNOWN|CONFLICTED", "as_of": "date|null", "basis": ["assertion_id…"]},
    "scores": {"sparse": null, "dense": null, "fused": 0.0, "rerank": null},
    "role": "PRIMARY|CONTEXT",
    "trace": [{"stage": "sparse", "rank": 3, "params_version": "…"}],
    "trust_class": "SOURCE",
    "content_trust": "UNTRUSTED_SOURCE_TEXT"
  }],
  "versions": {"embedding": "…", "parser": "…", "fts_config": "…", "fusion": "…", "reranker": "…"},
  "filters_applied": {}, "route": {"name": "…", "confidence": 0.0, "by": "rule|classifier"},
  "limits": {"n_candidates": 0, "n_rerank": 0, "k_final": 0}
}
```

Scores are retained for diagnosis; they are **not** presented to users as confidence in correctness.
