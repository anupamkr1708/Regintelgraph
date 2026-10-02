# ADR-004: Graph strategy — relational graph tables, evidence-bound edges

**Status:** ACCEPTED, with explicit revisit triggers

## Context
Needed traversals are shallow (1–3 hops over amendment/supersession/applicability), the ontology is deliberately small, and every edge needs verifiable provenance. See [data-model](../data-model.md) §2 for the A/B comparison.

## Problem
Choose graph storage and semantics.

## Decision
- Store the graph in PostgreSQL: `kg_entity`, `kg_edge`, `kg_edge_evidence`; traversal via recursive CTEs behind a `GraphReader` interface.
- Edges have lifecycle `CANDIDATE → VERIFIED/REJECTED`; **VERIFIED requires ≥1 evidence link** (DB-enforced).
- Relation set per [graph-schema](../graph-schema.md); `SUPPORTED_BY` is the evidence join, not an edge. `SUPERSEDES` is created only from explicit text or manual curation.
- GraphRAG is router-gated and never replaces baseline retrieval.

**Disagreement with research input:** the summary recommends populating Neo4j ("or similar") and includes relations such as `ASSOCIATED_WITH`, `EFFECTIVE_DATE` edges to Date nodes, entity NER for persons/organisations. We adopt "graph tables" (the summary's own alternative), model dates as `date_assertion`, and omit relations without a defined query pattern.

## Alternatives considered
1. Neo4j + Postgres — rejected for now: dual-store consistency, ops burden, no demonstrated need.
2. Postgres AGE/graph extension — deferred: adds extension risk; reconsider with triggers.
3. In-memory graph (NetworkX) built from tables — possible utility, not a store.

## Tradeoffs
+ Constraint-enforced provenance, one store. − Deep/variable-length path queries and graph algorithms are clumsier.

## Consequences
Graph quality depends on verification gating and review queues (cost: human time). Coverage checks prevent traversal on sparse graph regions.

## Migration path / revisit triggers
Revisit if, on real data: traversal p95 exceeds budget at required depth; needed analyses require graph algorithms; or graph size/density exceeds measured comfortable limits. Migration: export edges + evidence refs to Neo4j/AGE; keep Postgres authoritative for provenance.
