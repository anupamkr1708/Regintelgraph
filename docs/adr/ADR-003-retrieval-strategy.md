# ADR-003: Retrieval strategy — structure-aware hybrid retrieval with RRF

**Status:** PROPOSED (hybrid benefit is a hypothesis to be confirmed by ablation; embedder/reranker models DEFERRED)

## Context
Regulatory queries mix exact identifiers/terms and conceptual phrasing; structure (clauses) and time matter; provenance must survive each stage.

## Problem
Define the baseline retrieval pipeline and how it is validated without assuming hybrid is better.

## Decision
Pipeline: normalize → route → hard filters (incl. temporal candidate set) → sparse (FTS) ∥ dense (pgvector) ∥ identifier lookup → **Reciprocal Rank Fusion** → optional cross-encoder rerank → bounded, de-duplicated evidence selection. Clause-aligned chunks; embeddings may use contextualized text but anchors always use original text. Graph retrieval is additive and router-gated ([ADR-004](ADR-004-graph-strategy.md)). Weighted-score fusion and no-rerank are ablation arms. All limits are config defaults, tuned only from benchmark runs.

**Disagreement with research input:** the summary proposes a "weighted sum or ML reranker" over vector+sparse+graph scores. We start with rank-based fusion (no cross-modal score calibration) and treat graph as a separate evidence source, not a score component.

## Alternatives considered
1. Dense only — simpler, but weak on identifiers/exact terms (to be tested as arm A1).
2. Sparse only — strong on exact terms, weak on paraphrase (arm A2).
3. Weighted score fusion — needs calibration; arm for comparison.
4. LLM-based query rewriting by default — rejected as default: non-deterministic, provenance risk; curated glossary expansion instead.

## Tradeoffs
+ Explainable, testable, deterministic stubs. − More moving parts than single-mode; fusion/rerank cost must be justified by A3/A3r.

## Consequences
Retrieval contract fixed ([retrieval-design](../retrieval-design.md)); embedder/reranker are swappable interfaces; benchmark-first tuning (Phase 5).

## Migration path
Swap FTS for BM25 extension (OD-04), vector index type (OD-05), or an external engine behind the same `Retriever` interface.
