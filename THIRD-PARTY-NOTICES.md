# Third-party notices and dependency inventory

This is an **inventory**, not a legal opinion. It records what each dependency is, which version is locked, which licence it declares, where that statement came from, and whether a human should look at it again. It makes no determination about licence compatibility or obligations, and no dependency is replaced because of what is written here.

- **Authoritative version list:** [`uv.lock`](uv.lock) (hash-locked). Declared dependencies: [`pyproject.toml`](pyproject.toml). Why each *direct* dependency exists: [tooling-bootstrap-plan](docs/phase1/tooling-bootstrap-plan.md) §9.
- **Snapshot:** 19 third-party packages resolved by `uv.lock` at the Phase 1C closeout (the project itself is a virtual package and is not listed). A test (`tests/unit/test_third_party_notices.py`) fails if this table and `uv.lock` disagree on the package set or versions, or if a direct dependency is not classified as direct.
- **This repository's own licence:** Apache-2.0 (`LICENSE`, `pyproject.toml`).
- **Distribution:** the repository is source-only; no packaged or binary distribution of this project exists. Obligations that depend on distribution are therefore not analysed here.

## How to read the table

- **Scope:** `direct runtime` / `direct dev` = named in `pyproject.toml` (the `psycopg[binary]` extra makes `psycopg-binary` direct); `transitive runtime` / `transitive dev` = pulled in by another package. Runtime packages are those a deployed backend would import; dev packages are only used to lint, type-check and test.
- **Licence evidence** (never inferred from the package's name or reputation):
  - `metadata: expression` — the `License-Expression` field of the installed distribution;
  - `metadata: field` — the free-text `License` field of the installed distribution;
  - `metadata: classifier` — a `License ::` trove classifier only (weaker: no expression or text field);
  - `PyPI metadata` — read from the package's PyPI record for the locked version because the package is **not installed** on Linux (platform-marker dependency); weaker still.
  In every installed case a licence file was also present in the distribution's `dist-info`; its text was not reviewed.
- **Review:** `yes` = a human should look again before any redistribution decision (copyleft family, or the distribution bundles other code whose licences this inventory did not enumerate). `no flag` = nothing in the declared metadata raised a question *for this inventory*; it does not mean the licence was legally reviewed.

## Inventory

| Package | Version | Scope | Declared licence | Evidence | Purpose | Review |
|---|---|---|---|---|---|---|
| psycopg | 3.3.6 | direct runtime | LGPL-3.0-only | metadata: expression | PostgreSQL driver (sync API) used by the repository, migration runner and job queue | **yes** — LGPL family |
| psycopg-binary | 3.3.6 | direct runtime (extra `psycopg[binary]`) | LGPL-3.0-only | metadata: expression | Pre-built C implementation of psycopg; bundles libpq so no system libpq is required | **yes** — LGPL family; the binary wheel also bundles native libraries whose own licences are **not enumerated here** (unverified) |
| PyYAML | 6.0.3 | direct runtime | MIT | metadata: field + classifier | Manifest parsing (`yaml.safe_load` only; enforced by a test) | no flag |
| typing-extensions | 4.16.0 | transitive runtime (via psycopg; also mypy) | PSF-2.0 | metadata: expression | Backports of typing features required by psycopg | no flag |
| tzdata | 2026.4 | transitive runtime (via psycopg, Windows only; not installed on Linux) | Apache-2.0 | PyPI metadata | IANA time-zone database for psycopg on Windows | no flag |
| pytest | 9.1.1 | direct dev | MIT | metadata: expression | Test runner (markers `postgres`; offline socket guard fixtures) | no flag |
| hypothesis | 6.168.3 | direct dev | MPL-2.0 | metadata: expression | Property-based idempotency / invariant tests (`tests/unit/test_properties.py`) | **yes** — MPL-2.0 (file-level copyleft); dev/test only, not imported by runtime code |
| ruff | 0.16.10 | direct dev | MIT | metadata: expression | Linter and formatter | no flag |
| mypy | 2.4.0 | direct dev | MIT | metadata: expression | Strict static type checking | **yes** — the distribution bundles `typeshed` with its own licence file, not enumerated here (unverified) |
| ast-serialize | 0.11.2 | transitive dev (via mypy) | MIT | metadata: expression | Compiled component used by mypy | **yes** — ships a separate licence file for bundled crates, not enumerated here (unverified) |
| librt | 0.16.0 | transitive dev (via mypy, non-PyPy) | MIT | metadata: expression | Runtime support library used by mypy | no flag |
| mypy-extensions | 1.1.0 | transitive dev (via mypy) | MIT | metadata: expression | Extension types for mypy | no flag |
| pathspec | 1.1.1 | transitive dev (via mypy) | MPL-2.0 | metadata: classifier | Gitignore-style path matching used by mypy | **yes** — MPL-2.0 (classifier only; no expression field); dev only |
| iniconfig | 2.3.0 | transitive dev (via pytest) | MIT | metadata: expression | INI parsing for pytest | no flag |
| packaging | 26.3 | transitive dev (via pytest) | Apache-2.0 OR BSD-2-Clause | metadata: expression | Version/specifier handling for pytest | no flag |
| pluggy | 1.6.0 | transitive dev (via pytest) | MIT | metadata: field + classifier | Plugin system for pytest | no flag |
| pygments | 2.21.0 | transitive dev (via pytest) | BSD-2-Clause | metadata: expression | Syntax highlighting in pytest output | no flag |
| colorama | 0.4.6 | transitive dev (via pytest, Windows only; not installed on Linux) | BSD (classifier: BSD License) | PyPI metadata | Coloured terminal output for pytest on Windows | no flag |
| sortedcontainers | 2.4.0 | transitive dev (via hypothesis) | Apache 2.0 | metadata: field + classifier | Sorted collections used by hypothesis | no flag |

## Notes

- **Copyleft-family entries** (psycopg, psycopg-binary: LGPL-3.0-only; hypothesis, pathspec: MPL-2.0) are listed so they are visible, not because anything is wrong. Hypothesis and pathspec are development-only; psycopg and psycopg-binary are the only copyleft-family packages in the runtime path.
- **Not enumerated:** licences of code *bundled inside* binary wheels (libpq and its dependencies in `psycopg-binary`; bundled crates in `ast-serialize`; typeshed in `mypy`). Enumerating them would need the wheel contents; that is a review task, listed as open in the Phase 1C closeout report.
- **Not covered:** GitHub Actions used by CI (pinned by commit SHA in the workflow), the PostgreSQL/pgvector container image used by CI (pinned by digest; see [local-postgres-plan](docs/phase1/local-postgres-plan.md) §2), the operating-system PostgreSQL packages used for local development, and any regulatory source material (never stored in this repository).
- **Updating:** when `pyproject.toml` or `uv.lock` changes, update this table in the same change. Licence evidence is read with `importlib.metadata` from an environment created by `uv sync --frozen` (fields `License-Expression`, `License`, `Classifier`); never from memory.
