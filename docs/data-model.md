# Data Model (initial relational design)

Status: PROPOSED; storage decision in [ADR-002](adr/ADR-002-primary-storage.md), graph in [ADR-004](adr/ADR-004-graph-strategy.md). DDL below is **illustrative**; the authoritative DDL is produced in Phase 2 with migrations and constraint tests.

## 1. Storage options evaluated

| Option | Role | Assessment for this project | Verdict |
|---|---|---|---|
| PostgreSQL | System of record: source layer, structure, graph tables, runtime/audit | One engine for transactions, FTS, vectors, jobs; strong constraints (FK, CHECK, partial unique) enforce provenance rules; easy local dev | **Adopt** |
| pgvector (extension) | Dense vectors in the same DB | Keeps filters + vectors + joins in one query/transaction; scale for a focused corpus is expected to be modest (assumption **A-03**, to validate) | **Adopt** (index type chosen after measurement) |
| PostgreSQL FTS (`tsvector`, GIN) | Sparse retrieval | Good baseline; `ts_rank` is not BM25. If the benchmark shows lexical weakness, evaluate a BM25 extension or app-side BM25 (decision **OD-04**) | **Adopt as baseline** |
| `pg_trgm` / identifier index | Exact/fuzzy lookup of circular numbers, section labels | Regulatory queries often hinge on identifiers; deterministic path before any model | **Adopt** |
| Object/blob storage | Raw PDFs/HTML | Raw bytes do not belong in rows; content-addressed files by sha256 behind a `BlobStore` interface; S3-compatible later | **Adopt (filesystem first)** |
| Redis | Cache, queues, memory | Adds a second stateful system. Not needed before caching/queue needs are measured (see [performance](performance.md)) | **Defer** |
| Dedicated vector DB / Elasticsearch | Retrieval at larger scale | Operational burden unjustified for the initial corpus | **Defer** |
| Neo4j / graph DB | Graph storage and traversal | See §2 | **Defer with explicit triggers** |

## 2. Graph storage: Option A vs Option B

| Criterion | A: Postgres + pgvector + relational graph | B: Postgres + pgvector + Neo4j |
|---|---|---|
| Project scale | Initial subset: entities/edges expected in the thousands–hundreds of thousands (assumption **A-04**) — comfortably relational | Designed for much larger/denser graphs |
| Local development | `postgres` only | Second server, driver, Cypher, separate backup/ops |
| Simplicity | One transaction boundary; evidence FK enforced by DB | Cross-store consistency problem: edge in Neo4j, evidence in Postgres (dual-write/sync, drift risk) |
| Query needs | Expected patterns are shallow: 1–3 hops (obligation→applies_to→entity; amends/supersedes chains). Recursive CTEs handle these | Path queries/variable-depth are more natural and faster at depth |
| Graph complexity | Small typed schema (~10 predicates) | Overkill; ontology is intentionally small |
| Provenance enforcement | CHECK/trigger: VERIFIED edge requires ≥1 `kg_edge_evidence` row; same-DB FK | Application-level enforcement only |
| Operational burden | Minimal | Licensing/edition, memory tuning, upgrades, security surface (principle K) |
| Migration path | Edge tables map 1:1 to property graph; export to Neo4j/AGE later is mechanical; traversal sits behind a `GraphReader` interface | n/a |

**Recommendation: Option A**, with a `GraphReader` interface and explicit revisit triggers recorded in ADR-004 (traversal p95 over budget at required depth on real data; need for graph algorithms the SQL layer cannot support; graph size beyond measured comfortable limits). The Executive Summary's "Neo4j/graph tables" is therefore resolved to *graph tables*.

## 3. Schema groups and key tables

**Source layer (append-only)**
- `authority(authority_id PK, name, jurisdiction, allowed_hosts[])`
- `source(source_id PK, authority_id FK, kind, manifest_ref, enabled)`
- `ingest_run(ingest_run_id PK, source_id FK, started_at, finished_at, status, stats jsonb, code_version)`
- `raw_artifact(raw_artifact_id PK, content_hash UNIQUE, size_bytes, media_type_sniffed, blob_path_rel)`
- `regulatory_document(document_id PK, authority_id FK, document_type, source_reference NULL, title, identity_key UNIQUE, created_at)`
- `document_version(document_version_id PK, document_id FK, raw_artifact_id FK, content_hash, retrieved_at, ingest_run_id FK, status, UNIQUE(document_id, content_hash))`
- `document_version_location(document_version_id FK, canonical_url, first_seen, last_seen, http_meta jsonb)` — many URLs may serve one version
- `date_assertion(assertion_id PK, document_version_id FK, target_node_id NULL, kind, value_date, precision, basis, evidence_id FK NULL, status)` — kinds per [temporal-model](temporal-model.md)

**Structure (derived deterministically; keyed by parser version)**
- `text_layer(text_layer_id PK, document_version_id FK, extractor_version, text_hash, text, page_map jsonb, UNIQUE(document_version_id, extractor_version))`
- `structure_node(node_id PK, text_layer_id FK, parent_id, kind, label, ordinal, start_off, end_off, page_start, page_end, parser_version)`
- `chunk(chunk_id PK, node_id FK, text_layer_id FK, start_off, end_off, text_hash, context_path, chunker_version, tsv tsvector)` — `tsv` GIN-indexed
- `chunk_embedding_<tag>(chunk_id PK/FK, embedding vector(D))` — one table per `embedding_version` (dimension fixed per table); `index_version` records the active pointer
- `definition(definition_id PK, text_layer_id, term, node_id FK)`

**Evidence & derived knowledge**
- `evidence(evidence_id PK = hash(text_layer_id,start,end), text_layer_id FK, start_off, end_off, quote_hash, node_id NULL, page_start, page_end)`
- `kg_entity(entity_id PK, entity_class, canonical_name, aliases[], external_ids jsonb, status, method, extraction_version)`
- `kg_edge(edge_id PK, predicate, src_kind, src_id, dst_kind, dst_id, status, confidence NULL, method, extraction_version, valid_from NULL, valid_to NULL, UNIQUE(predicate, src_id, dst_id, extraction_version))`
- `kg_edge_evidence(edge_id FK, evidence_id FK, role)` — **required for VERIFIED**
- `obligation`, `exception_rule`, `applicability_rule`, `amendment`, `supersession`, `effective_period`, `regulatory_change` (see [domain-model](domain-model.md)); each has `evidence_id`/`edge` links and version stamps

**Runtime/audit**
- `query`, `retrieval_run`, `retrieval_candidate(run_id, stage, rank, score, chunk_id, ...)`, `answer_record`, `claim`, `citation`, `verification_result`, `impact_assessment`
- `audit_event` (append-only; no UPDATE/DELETE grants), `job(job_id, kind, payload, idempotency_key UNIQUE, state, attempts, run_after, locked_by)`

## 4. Illustrative constraints

```sql
-- immutability: no UPDATE on source tables except status columns via dedicated function
-- provenance gate (enforced in publish function + deferred constraint trigger):
--   edge.status = 'VERIFIED'  =>  EXISTS (SELECT 1 FROM kg_edge_evidence e WHERE e.edge_id = edge.edge_id)
-- evidence integrity (verified by a scheduled check and in tests):
--   sha256(substr(text_layer.text, start_off+1, end_off-start_off)) = evidence.quote_hash
CREATE UNIQUE INDEX ON document_version (document_id, content_hash);
CREATE INDEX ON chunk USING gin (tsv);
```

## 5. Indexing & versioning rules

- FTS and embeddings are **rebuildable derived indexes**; their loss never loses authoritative data.
- Embedding/FTS/parser/extractor versions are columns or table suffixes; multiple versions may coexist; queries pin one `index_version` and record it in `retrieval_run`.
- Approximate vector index choice (HNSW vs IVFFlat vs exact) is deferred to measurement (**OD-05**).
- Reserved but unused in Stage 1: `tenant_id`/`visibility` columns on future user-supplied-document tables (see [security](security.md) §retrieval leakage).

## 6. Migrations

Versioned, forward-only SQL migrations in `migrations/` with a tested apply-from-empty path; tool choice recorded at Phase 2 (**OD-02**). No auto-generated schema trusted without review. Every migration ships with a constraint test.
