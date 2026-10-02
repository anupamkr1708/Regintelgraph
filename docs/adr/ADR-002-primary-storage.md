# ADR-002: Primary storage — PostgreSQL (+pgvector, FTS) and content-addressed blobs

**Status:** ACCEPTED (index types and BM25 extension deferred: OD-04, OD-05; migration tool: OD-02)

## Context
Provenance rules (immutability, evidence FKs, "VERIFIED edge requires evidence") are easiest to enforce with relational constraints. Retrieval needs filters + sparse + dense in one system for correct pre-filtering. Raw regulatory files must be immutable and hash-addressed.

## Problem
Pick storage for source records, structure, vectors, full-text, graph, jobs, audit — with minimal operational surface.

## Decision
- **PostgreSQL** is the single system of record (source, structure, graph tables, runtime, audit, job queue via `FOR UPDATE SKIP LOCKED`).
- **pgvector** for dense vectors; **Postgres FTS (`tsvector`) + `pg_trgm`/identifier index** for sparse and exact lookup.
- **Blob store**: content-addressed files (`sha256`) behind `BlobStore`; local filesystem first, S3-compatible later.
- **Deferred:** Redis, Elasticsearch/OpenSearch, dedicated vector DB, Airflow/Prefect/brokers.

**Disagreement with research input:** the summary lists Redis (cache/agent memory), Airflow/Prefect, and a possible Elastic/OpenSearch or vector DB. These are deferred until measurements (cache hit rate, throughput, retrieval ceiling) justify them; agent state lives in Postgres run records.

## Alternatives considered
1. Postgres + dedicated vector DB + Elasticsearch — rejected: three stores, sync drift, no demonstrated need.
2. SQLite + FAISS — rejected: weaker concurrency/constraints, harder path to production.
3. Raw documents in Postgres large objects — rejected: operational and backup awkwardness; hash-addressed files are simpler.

## Tradeoffs
+ One backup/restore, transactional provenance, simple local dev. − `ts_rank` ≠ BM25; ANN filtering caveats; Postgres scale limits are finite (revisit with data).

## Consequences
All derived indexes are rebuildable; embeddings versioned per table; no authoritative data outside Postgres + blob store; requires local Postgres for integration tests (not installed in the current environment — a Phase 0b/1 setup step).

## Migration path
`BlobStore`→S3; vector/FTS behind retrieval interfaces → swap engines; queue interface → broker; Postgres → managed Postgres.
