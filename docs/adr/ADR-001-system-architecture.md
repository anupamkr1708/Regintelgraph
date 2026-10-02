# ADR-001: System architecture — modular monolith

**Status:** ACCEPTED (subject to the Stage 1 review)

## Context
Single team (initially one engineer + AI assistants), one initial domain, a small-to-moderate corpus (assumption A-03), and a hard requirement for provenance consistency across ingestion, retrieval, graph and verification. Principle K forbids unnecessary infrastructure.

## Problem
Choose a deployment/code architecture that keeps boundaries enforceable, tests deterministic and local, and avoids distributed-systems cost before it is justified. The Executive Summary recommends a microservices architecture (FastAPI services for search and graph, Redis, Docker/Kubernetes-or-compose, Airflow/Prefect).

## Decision
A **modular monolith**: one Python codebase organised as `packages/*` with enforced dependency direction; thin `apps/*` (API, web) and `workers/*` (entry points running package jobs); **one PostgreSQL** and a content-addressed blob directory. Docker is optional convenience; no Kubernetes, message broker, or service mesh. Boundaries are interfaces (`BlobStore`, `Embedder`, `Reranker`, `LLMProvider`, `GraphReader`, `JobQueue`) so extraction later is mechanical.

**Disagreement with research input:** microservices/Kubernetes/Airflow/Redis are **not adopted initially**. Reason: no measured need; cross-service consistency would endanger provenance invariants (e.g., edge ↔ evidence in one transaction).

## Alternatives considered
1. Microservices (search service, graph service, agent service) — rejected: operational burden, distributed consistency, slower local testing.
2. Serverless functions — rejected: poor fit for long parses and stateful workflows.
3. Unstructured monolith — rejected: invites architecture drift; boundaries matter for AI-assisted development.

## Tradeoffs
+ Simplicity, atomic transactions, easy local CI, one deployment. − Single scaling unit; parse load can contend with queries (mitigated by separate worker process and later extraction).

## Consequences
Import-boundary checks in CI (Phase 1+); package APIs treated as contracts; a service split requires a new ADR with measured triggers ([architecture](../architecture.md) §7).

## Migration path
Extract in this order if measurements demand: parse/index workers → search/vector engine → graph engine → inference service → API gateway. Each is already behind an interface.
