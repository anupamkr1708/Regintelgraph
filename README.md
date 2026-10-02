# RegIntelGraph

**Evidence-grounded regulatory intelligence and compliance-impact analysis.**

> Status: **Stage 1 — architecture foundation.** No application code exists yet. Nothing here is production-ready, and no performance or quality numbers are claimed: every such number must come from a reproducible experiment (see [evaluation](docs/evaluation.md)).

## Overview

RegIntelGraph ingests authoritative public regulatory material, represents it structurally and temporally, retrieves evidence with hybrid search, traverses a provenance-backed knowledge graph where relationships matter, runs a tightly bounded agentic workflow for complex questions, verifies every material claim against source evidence, and exposes selected capabilities through MCP. It is a **regulatory intelligence and decision-support system**. It does not provide legal advice and does not determine legal compliance.

## Problem

Regulatory change is hard to follow: circulars amend, supersede and cross-reference each other; effective dates differ from publication dates; consolidated text may not exist officially. Generic RAG answers fluently but cannot show *which version of which clause* supports a statement, cannot say "applicable as of date X", and rarely abstains. RegIntelGraph is designed around those failure modes.

## Architecture

A **modular monolith** (one Python backend, one PostgreSQL) with strict internal boundaries; extraction into services is a later, evidence-driven option. See [architecture](docs/architecture.md) and the ADRs in [docs/adr/](docs/adr/).

```
sources → ingestion → canonical docs/versions → structure → clauses/chunks ─┐
                                                                            ├→ hybrid retrieval ─┐
                                      derived graph (entities/relations, evidence-bound) ───────┘    │
query → router → [retrieval | + temporal logic | + graph | + bounded agent planning] → evidence pack ┤
                                                          claim extraction → verification → answer / abstain
interfaces: API · Web UI · MCP (read-only domain tools)
```

## Core capabilities (target)

Current-requirement lookup · version/amendment comparison · "as of date X" applicability · affected entities/products · related-rule tracing · conflict reporting · explicit abstention · auditable impact reports · MCP tools for external agents.

## Initial scope

India · SEBI · one focused corpus (proposed: Mutual Funds — see [ADR-009](docs/adr/ADR-009-initial-domain-scope.md), pending your review). RBI, SEC/EDGAR and others are out of scope until the SEBI pipeline is measured.

## Technology direction (proposed, see ADRs)

Python 3.12 backend (FastAPI) · PostgreSQL with pgvector and full-text search as the single system of record · content-addressed local blob store (S3-compatible later) · relational graph tables with recursive queries · Postgres-backed job queue · provider-agnostic model interfaces with deterministic stubs · OpenTelemetry-compatible tracing · Next.js UI (late). No Neo4j, Redis, Kafka, Kubernetes or microservices at the start.

## Evaluation philosophy

Measurement before claims. A reproducible benchmark ("RegIntelBench") with ablations (dense / sparse / hybrid / hybrid+graph / agentic / agentic+verification) decides what earns its complexity. Official source data and human-authored labels are kept separate. See [evaluation](docs/evaluation.md) and [benchmark spec](docs/benchmarks/benchmark-spec.md).

## Security philosophy

Trusted control plane vs untrusted regulatory content. Retrieved text is data, never instructions; model output is untrusted; citations are resolved server-side from evidence handles; the agent is read-only. See [security](docs/security.md).

## Development

- Constitution for humans and AI assistants: [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md).
- Foundation checks (stdlib only): `python scripts/check_foundation.py`
- Read-only environment report: `./scripts/inspect_env.sh`
- Copy `.env.example` to `.env` for local settings (placeholders only; `.env` is git-ignored).
- Core tests must run offline with deterministic stubs ([testing strategy](docs/testing-strategy.md)).

## Roadmap

Phases 0–18 in [implementation-roadmap](docs/implementation-roadmap.md). Next: Phase 1 (source manifest + ingestion) after your review of [stage-1-review](docs/stage-1-review.md).

Documentation index: [docs/README.md](docs/README.md).
