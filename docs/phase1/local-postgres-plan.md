# Phase 1 — Local PostgreSQL + pgvector Development Plan

Status: **IMPLEMENTED in Phase 1C** (helper `scripts/pg-dev.sh`, runner `scripts/migrate.py`, harness `tests/support/pgfixtures.py`; see the checked criteria in §7). Originally a Phase 1A plan: **nothing was installed or started in Phase 1A.** The absence of Postgres in the Stage 1 environment is an environment fact, not an architecture failure ([stage-1-review](../stage-1-review.md) §1). This plan makes the first database reproducible without cloud services: **no AWS, no Supabase, no managed service** is on the core development path ([ADR-002](../adr/ADR-002-primary-storage.md): one Postgres, modular monolith). Related: [tooling-bootstrap-plan](tooling-bootstrap-plan.md), [ingestion-contract](ingestion-contract.md).

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

Helper `scripts/pg-dev.sh` (implemented and exercised in Phase 1C: `init`, `start`, `stop`, `status`, `reset`, `url`, `test-url`, `psql`). It refuses to run as root, never touches a system cluster, installs no operating-system packages (it reports clearly if the PostgreSQL binaries are absent), configures the cluster socket-only (`listen_addresses = ''`), UTC and UTF-8, and `reset` deletes only `local_data/postgres` after proving it resolves inside the repository and contains `PG_VERSION`. Original intended shape:

| Subcommand | Intended action |
|---|---|
| `init` | `initdb` into `local_data/postgres` with UTF-8 encoding, a deterministic locale (`C.UTF-8`), `--auth-local=trust` limited to a Unix socket in a `0700` directory, `--auth-host=reject`; write `postgresql.conf` overrides: `listen_addresses = ''` (**no TCP listener**), `unix_socket_directories` pointing at the project socket dir, `timezone = 'UTC'`, defaults for everything else (no tuning numbers are invented; tune later by measurement, H14) |
| `start` / `stop` | `pg_ctl` against that data directory using the Debian/Ubuntu binary path (`/usr/lib/postgresql/16/bin/`), logs to the git-ignored `/logs/` |
| `reset` | stop → delete **only** `local_data/postgres` (guarded: path must resolve inside the repo) → `init` → `start` |
| `url` | print a socket-style `RIG_DATABASE_URL` for `.env` (placeholders only appear in `.env.example`; real values live in the git-ignored `.env`, H12) |
| `psql` | open a client on the project socket |

Why a socket-only cluster: no port conflicts, nothing reachable from the network, no credentials to leak, reproducible per checkout. `local_data/` is already git-ignored (`.gitignore`), as is `/logs/`.

### Path B — CI service container (mandatory for CI)

The CI `integration` job uses a Postgres service container with the pgvector extension available, **pinned by immutable digest** (see the pin record below). Same migrations, same tests. CI never uses cloud databases.

#### CI image pin record

| Field | Value | Evidence |
|---|---|---|
| Image reference in CI | `pgvector/pgvector@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a` | **observed** |
| What the digest is | the multi-platform *index* digest of the `pg16` tag, observed on Docker Hub on 2026-10-04 | **observed** — identical on the `linux/amd64` and `linux/arm64` image pages |
| `linux/amd64` image digest | `sha256:a97d77306ff47cc1d8add4d000cec2207da2e110746318df3137ce53c337ca30` | **observed** (the platform GitHub-hosted runners use) |
| PostgreSQL | 16.15 (`PG_VERSION=16.15-1.pgdg12+2`, Debian bookworm) | **observed** in the image metadata page |
| pgvector | v0.8.7 (built from the upstream `v0.8.7` tag); the `0.8.7-pg16` tag carried the same digest | **observed** |
| Reason selected | the digest the `pg16` tag resolved to when the pin was introduced, i.e. the image the previously mutable reference meant, frozen. The tag is mutable (an earlier search-engine snapshot listed a different `pg16` digest), so an unpinned reference can change the test environment without any commit | reasoning |
| Pull-tested / executed by the author | **no** — the sandbox has no Docker and cannot reach any registry host (`host_not_allowed`), so neither `docker buildx imagetools inspect` nor a run of the suite against this exact image was possible | **unverified** |
| Local development PostgreSQL | 16.15 + pgvector 0.6.0 (Ubuntu archive) — same PostgreSQL minor, **different pgvector version**; the suite only runs `CREATE EXTENSION vector` against it, but a green run on the pinned image is what actually verifies that | **observed** |

Failure mode if the recorded digest were wrong: the `integration` job fails at image pull. It cannot silently run a different image.

**Re-verification (human, with Docker):** `docker buildx imagetools inspect pgvector/pgvector:pg16` must still list the index digest above while the tag has not moved; for the pinned digest itself run `docker buildx imagetools inspect pgvector/pgvector@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a` and confirm it resolves to a `linux/amd64` manifest `sha256:a97d7730…`, then pull it and run the PostgreSQL suite.

**Update procedure:** a digest change is a reviewed change — resolve the new digest with the command above, run the full PostgreSQL suite against exactly that image, update this record and the workflow together. `tests/security/test_ci_image_pinned.py` fails if the workflow references any service/container image by tag instead of digest, or if the workflow and this record disagree. Do not move to `pg17` or another variant here; that is an architecture/ADR matter, not a CI refresh.

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
3. `python scripts/migrate.py apply --dsn "$(scripts/pg-dev.sh url)"` (the OD-02 runner)
4. `pytest -m "not postgres"` — offline unit/security/regression (no database)
5. `RIG_TEST_DATABASE_URL="$(scripts/pg-dev.sh test-url)" pytest -m postgres` — integration, needs the cluster (the harness creates and drops only `rig_test_*` databases and refuses any non-local URL)
6. `scripts/pg-dev.sh stop`

`RIG_OFFLINE_TESTS=1` remains the default; the `postgres` marker is opt-out in environments without a database, and **CI always runs it** so a skipped suite cannot hide a regression.

## 5. Migration conventions (OD-02 **resolved** — see [tooling-bootstrap-plan](tooling-bootstrap-plan.md) §3)

Forward-only numbered SQL files in `migrations/`; one transaction per file; each file's checksum recorded; every migration lands with its constraint test; no auto-generated DDL (data-model §6). First migrations create only source-layer and run-layer tables: `source`, `ingest_run`, `raw_artifact`, `regulatory_document`, `document_version`, `document_version_location`, plus the two tables proposed by the ingestion contract (`ingest_result`, `quarantine_record`) and `job`/`job_event` skeletons. DDL for later phases (nodes, anchors, embeddings, relations) is **not** created in Phase 1B.

## 6. Risks

| ID | Risk | Mitigation |
|---|---|---|
| PG-1 | Ubuntu-archive pgvector (0.6.0) may lack features needed later (filtered-search behaviour; A-05/OD-05) — `UNVERIFIED` | smoke-test only now; decide the install route (newer package, build from source, container) at Phase 4 with a benchmark-backed reason |
| PG-2 | Package install needs root once; may be impossible on some dev machines | Path C (Docker) documented; CI path unaffected |
| PG-3 | Package install creates a default system cluster that could be confused with the project cluster | script never touches it; project cluster is socket-only under `local_data/` |
| PG-4 | Tests that mutate a shared database | template clone + destructive-operation guard. **Refinement made in Phase 1C:** each *test* (not each module) clones the migrated template, which is stricter isolation by the same mechanism |
| PG-5 | Constraint/trigger logic drifting from the contract | schema snapshot test + one test per invariant G1–G6 |
| PG-6 | Single-CPU/4 GB machine slows integration tests | keep fixtures tiny; template-database cloning; heavy suites stay manual |

## 7. Exit criteria for "local Postgres ready" (Phase 1B → checked in Phase 1C)

Checked means exercised in the Phase 1C sandbox (`observed`/`tested`); it is not a claim about any other machine.

- [x] a checkout reaches a migrated, empty database with the documented commands (`pg-dev.sh init/start`, `migrate.py apply`); the one-time package install was `postgresql-16` + `postgresql-16-pgvector` from the Ubuntu archive;
- [x] migrate-from-empty, checksum-tamper and rerun-no-op tests pass;
- [x] constraint, immutability, provenance and transaction-rollback tests pass (including that a migration and its bookkeeping row commit atomically);
- [x] concurrent-writer idempotency test passes (and concurrent migration runners, concurrent run starts and concurrent job claims);
- [x] `CREATE EXTENSION vector` smoke passes — **observed**: PostgreSQL 16.15 and pgvector 0.6.0 from the Ubuntu archive in this sandbox. No vector table, HNSW or IVFFlat index exists, and no index type is claimed optimal (OD-05 stays open);
- [ ] CI `integration` job green with the same suite — **not met**. The first observed GitHub run (`a90f5df`) *failed* in this job: `test_database_settings_the_project_relies_on` compared `SHOW timezone` with the literal `UTC`, while a Docker-style server reports `Etc/UTC` (reproduced locally under CI-equivalent conditions; the CI log itself was not accessible). The test now asserts the required behaviour (zero UTC offset in winter and summer). This stays unchecked until a green GitHub run is actually observed after the fix;
- [x] destructive-operation guard demonstrably refuses a non-local URL and any database name outside `rig_test_*` (`tests/security/test_pg_harness_guard.py`).
