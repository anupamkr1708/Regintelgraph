# Phase 1 — Tooling Bootstrap Plan

Status: **DECIDED for Phase 1C** — OD-02 and open questions OQ-T1…OQ-T4 are resolved (§3, §8) and the result is recorded in §9. The text below keeps its original Phase 1A/1B reasoning. **Nothing has been installed or changed on the machine in Phase 1A** (H15; no global developer tooling is installed). This plan names what Phase 1B would set up, with the reasoning, so each dependency can be approved before it is added ([AGENTS.md](../../AGENTS.md) "Dependencies": why needed, why stdlib/existing is insufficient, licence, maintenance, offline-test impact).

Facts about the environment come from `scripts/inspect_env.sh` and read-only package queries run in Phase 1A (`VERIFIED`): Ubuntu 24.04, 1 CPU, ~4 GB RAM; Python 3.12.3; pip 24.0; uv 0.11.7; Node 22; git 2.43; **no Docker/Podman, no PostgreSQL client or server**; `pdftotext`/`qpdf` binaries present; `apt-cache` shows `postgresql-16` 16.13 and `postgresql-16-pgvector` 0.6.0 as installable candidates from the Ubuntu archive. Licence and maintenance statements below are **general knowledge, `UNVERIFIED`**, and must be checked at adoption time.

## 1. Principles

1. Python 3.12, one backend, one Postgres (ADR-001, ADR-002). No new service, database, broker or framework (H15).
2. Standard library first. Every non-stdlib dependency needs a written justification in the change that adds it.
3. Core tests are **offline and deterministic** (H13): unit/security/regression need no network and no Postgres; integration needs only a local Postgres.
4. Reproducible installs: lockfile with hashes, pinned Python (T-18).
5. Tools are chosen to be replaceable; none leaks into `packages/domain` beyond type definitions.

## 2. Toolchain table

| Tool | Role | Advantages | Disadvantages | Why (eventual selection) | Offline-test implications | Maintenance implications |
|---|---|---|---|---|---|---|
| **Python 3.12** | language | already in repo docs and environment (3.12.3) | none new | decided in README/architecture | none | pin `requires-python` to 3.12.x |
| **uv** | env + lockfile + runner | present (0.11.7); fast; lockfile with hashes; one tool for venv/deps/scripts; offline-capable with a warm cache | single-vendor tool; younger than pip/poetry | gives hash-locked reproducible installs (T-18) with minimal ceremony; `uv sync --frozen` in CI | CI can cache; no network needed once locked and cached | commit `uv.lock`; bump deliberately (H17) |
| **pytest** | test runner | de facto standard; fixtures and markers (e.g. `network`, `postgres`, `paid_api` — excluded by default) | plugin sprawl risk | already planned in roadmap Phase 1 | a conftest guard can **fail any test that opens a socket** (testing-strategy §4.5) | keep plugins minimal |
| **Hypothesis** (dev) | property tests | roadmap/testing-strategy already require idempotency and anchor round-trip properties | adds a dev dependency; nondeterminism unless seeded | idempotency (G3) and URL-validation properties are natural fits | fixed seed + bounded examples ⇒ deterministic CI | dev-only |
| **ruff** (dev) | lint + format | one fast tool replaces several; roadmap-planned | rule churn between versions | pin version; select a small explicit rule set (incl. import-boundary rules where expressible) | none | pin; review rule changes |
| **mypy** (dev) | type check | roadmap-planned; strict mode on `packages/*` matches the complete-type-hints expectation for `packages/*` | slower; occasional false positives | stick to the roadmap's choice; **pyright is not added** (two checkers = noise) | none | pin version |
| **Import-boundary check** | enforce layering | AGENTS.md layering is currently unenforced (stage-1-review) | custom code (small) | stdlib script (AST import scan) first; adopt a tool only if it outgrows that | pure static check | extend with each package |
| **YAML parser (PyYAML)** | read manifests | manifests are YAML by design (ingestion-design §3); PyYAML is already importable here | **must use `safe_load` only** (arbitrary-object construction otherwise) | needed in Phase 1B; add a test that asserts no unsafe loader is used | none | pin |
| **HTTP transport** | egress | stdlib `http.client` + `ssl` allow what the safety contract needs (connect to a validated IP with original SNI/Host, manual redirects, hard byte caps, no env proxy) with **zero dependencies** | more code than using a library; HTTP/2 absent | prefer stdlib behind the `HttpTransport` interface; revisit a library **only** if the pinned-IP + manual-redirect requirements can be met without wrapping its internals | fake transport + fake resolver ⇒ no network | small, well-tested module |
| **PostgreSQL 16** | system of record | ADR-002; available from the Ubuntu archive here | not installed; needs a one-time setup | see [local-postgres-plan](local-postgres-plan.md) | integration tests only; unit/security independent | pin major version; backup/restore later |
| **psycopg 3** | Postgres driver | maintained successor of psycopg2; sync API suffices for Phase 1B; supports server-side features needed (transactions, `COPY`, later `SKIP LOCKED`) | needs libpq (binary wheel option exists) | the one driver needed for Postgres; async not required in Phase 1B | use only in `integration` marker tests | pin; check wheel/libpq on target platform |
| **pgvector** | dense vectors | ADR-002 | the Ubuntu archive version (0.6.0) may lag features relevant to filtered search (A-05) and index choice (OD-05) — **`UNVERIFIED` general knowledge; check release notes at Phase 4** | install in Phase 1B only to prove `CREATE EXTENSION` works; **no vector tables until Phase 4** | extension needed only by retrieval integration tests | pin version; record in migration |
| **Local blob store** | raw files | content-addressed filesystem behind `BlobStore` (ADR-002); stdlib only | single node | exactly as decided; safety rules in [source-safety-contract](source-safety-contract.md) §10 | temp-dir fixture ⇒ hermetic | S3-compatible adapter later (ADR migration path) |
| **Secret scanning (CI)** | T-16 | required by security.md | tool adoption decision | start with the existing stdlib `check_foundation.py` patterns; add a dedicated scanner as a reviewed CI step | none | — |

Dependencies proposed for Phase 1B (each to be approved when added): **runtime** — PyYAML, psycopg 3; **dev** — pytest, Hypothesis, ruff, mypy. No HTTP library, no ORM, no web framework, no queue library.

Domain types in Phase 1B: **frozen stdlib dataclasses/enums** in `packages/domain` (no dependency). Pydantic is referenced by [agent-design](../agent-design.md) for tool I/O; adopting it at the API/agent boundary later is compatible. *(Open question OQ-T1: confirm.)*

## 3. Migration tool — OD-02 (**RESOLVED**: option A, minimal in-repository forward-only SQL runner)

**Decision (Phase 1C):** option A below. Semantics: numbered `NNNN_name.sql` files applied in ascending, gap-free order; **one migration per transaction** (together with its `schema_migration` row); the file checksum (SHA-256) is stored; an already-applied migration that was edited (or is missing) is a hard failure and is never repaired; re-running applied migrations is a no-op; a session advisory lock serialises concurrent runners; no down migrations; no autogeneration; no Alembic; no SQLAlchemy. Implemented in `scripts/migrate.py` (repository tooling: it depends only on `psycopg`, so no package imports another package's infrastructure to reach it).

Constraints from [data-model](../data-model.md) §6: versioned, **forward-only SQL**, tested apply-from-empty path, no trusted auto-generated schema, every migration ships with a constraint test. Phase 1 needs the first migrations (roadmap), so the choice cannot wait until Phase 2.

| Candidate | Advantages | Disadvantages | Offline-test implications | Maintenance implications |
|---|---|---|---|---|
| **A. Minimal in-repo SQL runner** (numbered `.sql` files; `schema_migration` table storing version + file checksum; each file applied in one transaction; refuses edited-after-apply files; forward-only) | no new dependency; exactly the semantics in data-model §6; trivially auditable; checksum guards against silent edits (supports H5/H6 spirit) | we own ~100–200 lines of code and its tests; no autogenerate (which the project does not want); no down-migrations (also not wanted) | pure Python + Postgres; the runner's own tests need a throwaway database | low, but it is ours; must stay small |
| **B. Alembic** | widely used; revision graph; scriptable raw-SQL ops | brings SQLAlchemy as a dependency (not otherwise needed); tempts autogenerate; Python-revision files can carry logic that hides schema changes | needs SQLAlchemy engine in tests | dependency chain upgrades |
| **C. Standalone SQL-file migrator binary** (e.g. a Go single-binary tool) | plain SQL files; small footprint; language-agnostic | second toolchain to install and pin outside `uv`; not available offline unless vendored | binary must be present in CI image | version pinning outside Python lockfile |

**Leaning (not a decision):** A, because it adds no dependency, matches the stated semantics, and the project explicitly rejects autogeneration; reconsider B only if the schema's evolution needs outgrow plain ordered SQL. Selection and an entry in the decision log are requested **before** the first migration is written.

## 4. PostgreSQL and pgvector plan (summary)

Detailed, reproducible steps and the integration-test contract are in [local-postgres-plan](local-postgres-plan.md). Two constraints decided there: no Docker requirement (it is absent here), and no vector tables before Phase 4.

## 5. CI (implemented in Phase 1C)

Implemented as `.github/workflows/foundation-checks.yml` with exactly these jobs: `foundation` (stdlib) → `lint-types` (`uv lock --check`, ruff check + format check, strict mypy, `scripts/check_imports.py`) → `offline-tests` (`pytest -m "not postgres"`, socket guard on) → `integration` (PostgreSQL 16 service container with pgvector, `pytest -m postgres`, `RIG_REQUIRE_POSTGRES=1` so a missing database fails instead of skipping); `secret-scan` (`scripts/check_secrets.py`, stdlib) runs independently. Third-party actions are pinned to full commit SHAs resolved from upstream tags; the token is `contents: read`; no job crawls a real source and no secrets or paid APIs are used. **Closeout update:** the test-count ratchet (`scripts/check_test_ratchet.py`, a step of the `foundation` job; testing-strategy §4.3) and image-digest pinning of the PostgreSQL service container (record: [local-postgres-plan](local-postgres-plan.md) §2 *CI image pin record*; evidence level `observed`, not pull-tested) are now implemented. The first observed GitHub run of the Phase 1C commit failed in the `integration` job (a timezone assertion, since fixed); the workflow is not considered validated until a green run is observed. The original proposal follows.

Extend `.github/workflows/foundation-checks.yml` into: `foundation` (unchanged, stdlib) → `lint+types` (ruff, mypy) → `unit+security+regression` (offline; socket guard on) → `integration` (Postgres service container, `CREATE EXTENSION vector` smoke) → `secret-scan`. Full benchmark/performance jobs stay manual (testing-strategy §5). The ratchet check on counts of security tests/regression fixtures is introduced here as testing-strategy §4.3 states ("implemented from Phase 1").

## 6. PDF parsing — OD-03 spike plan (not part of Phase 1B coding; **no parser is chosen here**)

Phase 1A produced the requirements ([recon-findings](recon-findings.md) §5), not a winner, because no parser was run on canonical bytes. When access is approved and three canonical files exist, run a bounded spike (Phase 3 per the roadmap) comparing **at most three** practical candidates, chosen for what the sample demands (tables, footnote markers, hyphen loss, reading order near tables, header/footer stripping, stable character offsets):

1. a layout-preserving text extractor already on the machine (`pdftotext` from poppler, in `-layout` and default modes);
2. a pure-Python extractor with positional output (pdfminer-style), for word coordinates;
3. a table-aware extractor built on (2) for the table pages only, *only if* (1)/(2) fail the table criteria.

Candidates with a copyleft or commercial-dual licence must pass a licence review before adoption (`UNVERIFIED` here; check at selection). **Spike acceptance criteria (set before running):** (a) every evidence offset round-trips (slice re-hashes) after header/footer removal via an offset map; (b) page boundaries recover the printed page numbers at all checkable index points; (c) clause labels (`n.n.n`, `(a)`, `(i)`) recovered with recorded precision/recall on a hand-checked page sample; (d) footnotes separable from body text with markers un-fused; (e) tables either structurally recovered or reliably flagged `TABLE` with flat-text fallback; (f) runtime and memory bounded under the sandbox limits (OD-14); (g) deterministic output across runs for a pinned version. Parsing runs in a sandboxed subprocess with no network (T-05, OD-14).

## 7. What Phase 1B adds to the repository (proposal; **implemented in Phase 1C** except where §5/§9 say otherwise)

`pyproject.toml` (project + tool config), `uv.lock`, `.python-version`, `tests/` conftest with the socket guard and markers, `scripts/check_imports.py`, CI jobs above, `migrations/0001_*.sql` + runner (per §3), `packages/domain` types, `packages/ingestion` per the two contracts. Each as a separate focused change (H17).

## 8. Open questions

| ID | Question |
|---|---|
| ID | Question | Resolution |
|---|---|---|
| OQ-T1 | Frozen stdlib dataclasses in `domain` for Phase 1B, Pydantic only at API/agent boundaries — acceptable? | **Accepted**: frozen stdlib dataclasses/enums in `packages/domain`; no Pydantic in the domain layer. |
| OQ-T2 | Migration runner: approve option A (or choose B/C)? (OD-02) | **Option A** (§3). |
| OQ-T3 | Approve the stdlib `http.client` transport behind `HttpTransport`, or require a library? | **Accepted**: stdlib `http.client` + `ssl` behind `HttpTransport`; no HTTP dependency. |
| OQ-T4 | CI Postgres: service container only (no Docker locally), or also support a documented local container path for developers who have Docker? | **Service container in CI; Docker is not a local prerequisite.** Local development uses the project-local cluster ([local-postgres-plan](local-postgres-plan.md)). |

## 9. Implementation record (Phase 1C)

Tooling that now exists: `pyproject.toml`, `uv.lock` (hash-locked; 20 packages resolved), `.python-version` (3.12), the `ruff`/`mypy`/`pytest` configuration, `scripts/{migrate.py,check_imports.py,check_secrets.py,pg-dev.sh}` and the CI workflow (§5). The dependency set is exactly: runtime **PyYAML**, **psycopg 3** (`psycopg[binary]`); development **pytest**, **Hypothesis**, **ruff**, **mypy**. No versions are pinned by hand in `pyproject.toml`; the resolver and `uv.lock` record them. `yaml.safe_load` is the only YAML loader and a test enforces it.

Package licences as read from the installed package metadata at lock time (`observed`; this is metadata, not legal advice, and no licence review was performed): PyYAML — MIT; psycopg / psycopg-binary — **LGPL-3.0-only**; pytest — MIT; Hypothesis — **MPL-2.0** (dev only); ruff — MIT; mypy — MIT. The LGPL (runtime) and MPL (dev) entries are the ones a licence review should look at before any distribution of built artefacts (`unverified`).

Python package layout: the repository root is the import root (`packages.domain`, `packages.ingestion`; `uv` `package = false`), so code runs in place with no install step. This follows the existing top-level layout and needed no new architectural decision.
