# Phase 1 — Local PostgreSQL + pgvector Development Plan

Status: PROPOSED for review. **Plan only — nothing was installed or started in Phase 1A.** The absence of Postgres in the Stage 1 environment is an environment fact, not an architecture failure ([stage-1-review](../stage-1-review.md) §1). This plan makes the first database reproducible without cloud services: **no AWS, no Supabase, no managed service** is on the core development path ([ADR-002](../adr/ADR-002-primary-storage.md): one Postgres, modular monolith). Related: [tooling-bootstrap-plan](tooling-bootstrap-plan.md), [ingestion-contract](ingestion-contract.md).

Environment facts (`VERIFIED` in Phase 1A): no `psql`/`pg_config`, no Docker/Podman; Ubuntu 24.04; Ubuntu archive candidates `postgresql-16` 16.13 and `postgresql-16-pgvector` 0.6.0; the sandbox network allowlist includes the Ubuntu archive but not the PostgreSQL project's own apt repository (`UNVERIFIED` whether a newer pgvector is reachable by other means).

## 1. What the first database must support

| Requirement | How it is proven (integration tests, marker `postgres`) |
|---|---|
| **Migrate from empty** | create a fresh database → apply every migration in order → compare the resulting catalog (tables, columns, constraints, indexes) against the expected schema snapshot; re-running the runner is a no-op; an edited, already-applied migration file is rejected (checksum) |
| **Schema constraints** | every table's PK/FK/UNIQUE/CHECK/enum has a test that a violating insert fails (data-model §6) |
| **Provenance constraints** | `NOT NULL`/FK for `ingest_run_id`, `content_hash`, `canonical_url`, `retrieved_at`, `trust_class`; no `document_version` without a raw artifact and a run; no derived row without a provenance chain (H8, H10) |
| **Source-layer immutability** | `raw_artifact`, `document_version` are insert-only: privileges revoked for `UPDATE`/`DELETE` on the application role plus a trigger that raises on update/delete (except the single allowed `last_seen` refresh on location rows). Test: attempt UPDATE/DELETE as the app role and as owner-through-app-code path → fails |
| **Transactional writes** | the artifact + version + location + `ingest_result` writes for one candidate commit atomically; a fault injected between statements leaves **zero** rows |
| **Idempotency under concurrency** | two concurrent writers of the same `content_hash` ⇒ one `raw_artifact` (`INSERT … ON CONFLICT`); concurrent runs for one `source_id` are prevented (G3) |
| **Quarantine rule** | publishing a version whose `content_hash` has a quarantine record is rejected by constraint/trigger (G4) |
| **Extensions** | `CREATE EXTENSION vector` succeeds on the target server (smoke test only — **no vector tables before Phase 4**, OD-05/A-05) |
| **Integration tests** | run against a disposable database; CI uses the same migrations |

Not in scope for Phase 1: embeddings, HNSW/IVFFlat indexes, FTS configuration, graph tables, pg_trgm — all arrive with their phases ([implementation-roadmap](../implementation-roadmap.md)).

## 2. Setup paths (no Docker required)

### Path A — project-local cluster, no system service (recommended for developers)

One-time prerequisite (root): install `postgresql-16` and `postgresql-16-pgvector` from the Ubuntu archive. A package install also registers a default system cluster; **the project does not use it** and the setup script must not touch it.

Planned helper `scripts/pg-dev.sh` (to be written and tested in Phase 1B; commands shown are the intended shape, **not run in Phase 1A**):

| Subcommand | Intended action |
|---|---|
| `init` | `initdb` into `local_data/postgres` with UTF-8 encoding, a deterministic locale (`C.UTF-8`), `--auth-local=trust` limited to a Unix socket in a `0700` directory, `--auth-host=reject`; write `postgresql.conf` overrides: `listen_addresses = ''` (**no TCP listener**), `unix_socket_directories` pointing at the project socket dir, `timezone = 'UTC'`, defaults for everything else (no tuning numbers are invented; tune later by measurement, H14) |
| `start` / `stop` | `pg_ctl` against that data directory using the Debian/Ubuntu binary path (`/usr/lib/postgresql/16/bin/`), logs to the git-ignored `/logs/` |
| `reset` | stop → delete **only** `local_data/postgres` (guarded: path must resolve inside the repo) → `init` → `start` |
| `url` | print a socket-style `RIG_DATABASE_URL` for `.env` (placeholders only appear in `.env.example`; real values live in the git-ignored `.env`, H12) |
| `psql` | open a client on the project socket |

Why a socket-only cluster: no port conflicts, nothing reachable from the network, no credentials to leak, reproducible per checkout. `local_data/` is already git-ignored (`.gitignore`), as is `/logs/`.

### Path B — CI service container (mandatory for CI)

The CI `integration` job uses a Postgres service container with the pgvector extension available (an image such as `pgvector/pgvector` pinned by digest at adoption; tag/digest `UNVERIFIED` here). Same migrations, same tests. CI never uses cloud databases.

### Path C — optional Docker for developers who already have it

Documented as an alternative that must run the **same** migration runner and tests; not required and not the supported default (Docker is absent in the reference environment).

## 3. Roles and safety rails

- **Owner role** (runs migrations; owns objects) and **app role** (used by ingestion/workers; least privilege: `INSERT`/`SELECT` on source tables, no `UPDATE`/`DELETE` on immutable ones; `SELECT` on views; no `CREATE`, no superuser, no `COPY … PROGRAM`). Retrieval-time read-only role comes with Phase 4/5 (security T-12).
- **Destructive-operation guard:** the test harness and `reset` refuse to run unless `RIG_DATABASE_URL` points to a local socket/loopback **and** the database name starts with `rig_test_` (tests) or is the project dev database (`reset`). It must be impossible to point the test suite at a non-local database by accident.
- **Per-test isolation:** a session-scoped template database with migrations applied; each test module clones it (`CREATE DATABASE … TEMPLATE …`) and drops it afterward. Tests needing an empty database for the migrate-from-empty check create one explicitly.
- **Settings the tests assert:** UTF-8 encoding, `timezone = UTC`, `timestamptz` for all instants (temporal-model: dates vs timestamps kept separate), no reliance on locale-dependent ordering for correctness.
- **No secrets in the repo:** `.env.example` already carries placeholder credentials only; the socket path needs none.

## 4. Developer workflow (target)

1. `uv sync --frozen`
2. `scripts/pg-dev.sh init && scripts/pg-dev.sh start`
3. `python -m <migration-runner> apply` (OD-02 decides the runner)
4. `pytest -m "not postgres"` — offline unit/security/regression (no database)
5. `pytest -m postgres` — integration, needs the cluster
6. `scripts/pg-dev.sh stop`

`RIG_OFFLINE_TESTS=1` remains the default; the `postgres` marker is opt-out in environments without a database, and **CI always runs it** so a skipped suite cannot hide a regression.

## 5. Migration conventions (inputs to OD-02, not a decision)

Forward-only numbered SQL files in `migrations/`; one transaction per file; each file's checksum recorded; every migration lands with its constraint test; no auto-generated DDL (data-model §6). First migrations create only source-layer and run-layer tables: `source`, `ingest_run`, `raw_artifact`, `regulatory_document`, `document_version`, `document_version_location`, plus the two tables proposed by the ingestion contract (`ingest_result`, `quarantine_record`) and `job`/`job_event` skeletons. DDL for later phases (nodes, anchors, embeddings, relations) is **not** created in Phase 1B.

## 6. Risks

| ID | Risk | Mitigation |
|---|---|---|
| PG-1 | Ubuntu-archive pgvector (0.6.0) may lack features needed later (filtered-search behaviour; A-05/OD-05) — `UNVERIFIED` | smoke-test only now; decide the install route (newer package, build from source, container) at Phase 4 with a benchmark-backed reason |
| PG-2 | Package install needs root once; may be impossible on some dev machines | Path C (Docker) documented; CI path unaffected |
| PG-3 | Package install creates a default system cluster that could be confused with the project cluster | script never touches it; project cluster is socket-only under `local_data/` |
| PG-4 | Tests that mutate a shared database | per-module template clone + destructive-operation guard |
| PG-5 | Constraint/trigger logic drifting from the contract | schema snapshot test + one test per invariant G1–G6 |
| PG-6 | Single-CPU/4 GB machine slows integration tests | keep fixtures tiny; template-database cloning; heavy suites stay manual |

## 7. Exit criteria for "local Postgres ready" (Phase 1B)

- [ ] a new checkout reaches a migrated, empty database with the documented commands, offline apart from the one-time package install;
- [ ] migrate-from-empty, checksum-tamper and rerun-no-op tests pass;
- [ ] constraint, immutability, provenance and transaction-rollback tests pass;
- [ ] concurrent-writer idempotency test passes;
- [ ] `CREATE EXTENSION vector` smoke passes (or is recorded as a failed prerequisite with the reason);
- [ ] CI `integration` job green with the same suite;
- [ ] destructive-operation guard demonstrably refuses a non-local URL.
