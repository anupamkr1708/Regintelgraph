# Contributing

Thank you for helping. This repository is a security-sensitive regulatory-intelligence system, so the rules are strict but simple.

## Before you change anything

1. **Read [AGENTS.md](AGENTS.md).** It is the single canonical engineering constitution for humans and AI assistants (hard rules H1–H18). Do not add a second instruction file with different rules.
2. **Inspect the repository and the documents that govern the area you will touch**: the relevant [ADRs](docs/adr/), [docs/architecture.md](docs/architecture.md), and for ingestion the [ingestion contract](docs/phase1/ingestion-contract.md), [source-safety contract](docs/phase1/source-safety-contract.md) and [crawl-safety parameters](docs/phase1/crawl-safety-parameters.md). Do not rely on assumptions about what the code does.
3. **No silent architectural changes.** A change to a decision in `docs/adr/` needs a new or amended ADR in the same change (H5). No ADR may weaken H1–H18. New services, databases, brokers, frameworks or dependencies need a written justification (H15); update [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) when dependencies change.

## Workflow: inspect → plan → implement → test → review

Keep each change **small, focused and tested** (H17): no unrelated refactors, formatting sweeps or dependency bumps. State your plan and assumptions, implement the smallest coherent change, add or update tests, run the checks below, and ask for review. If code and a document disagree, stop and report it; do not pick a side silently.

## Tests

- **Never weaken a test to get green** (H6): no loosened assertions, deleted or skipped failing tests, or disabled security tests. If an expectation must change, say why in the change, and make it a reviewed edit.
- A **test-count ratchet** (`python scripts/check_test_ratchet.py`) fails if the number of security, regression or integration tests drops below `tests/ratchet-baseline.json` or skip/xfail markers appear. Raising the baseline is `--update`; lowering it needs `--update --reason "<written justification>"` and is reviewed like any other change.
- Development is **deterministic and local-first** (H13): tests run offline (socket guard on), with fake clocks/resolvers/transports and synthetic fixtures. No paid APIs, no live URLs, no real source in any test.
- Prove a new safety test can fail: temporarily break the behaviour it guards and confirm the test catches it.

## Checks to run before opening a pull request

Python 3.12 and `uv` (the version in `.github/workflows/foundation-checks.yml`); dependencies come only from the lockfile.

```bash
python scripts/check_foundation.py
python scripts/check_test_ratchet.py
python scripts/check_secrets.py
uv sync --frozen && uv lock --check
uv run --frozen python scripts/check_imports.py
uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen mypy
uv run --frozen pytest -m "not postgres"          # offline suite
```

PostgreSQL integration tests need a local database (no Docker required; see [local-postgres-plan](docs/phase1/local-postgres-plan.md)):

```bash
scripts/pg-dev.sh init && scripts/pg-dev.sh start
RIG_TEST_DATABASE_URL="$(scripts/pg-dev.sh test-url)" RIG_REQUIRE_POSTGRES=1 uv run --frozen pytest -m postgres
```

CI runs the same checks plus the PostgreSQL job; a pull request is not done until CI is observed green, not merely passing locally.

## Data and source material

- **No raw regulatory source files in Git** (PDFs, HTML, scraped text), and no secrets, `.env`, local databases, logs or model weights (H12). Use small synthetic fixtures.
- **Never invent regulatory facts** (H1): no rule text, circular numbers, dates, thresholds or URLs from memory in code, fixtures, docs or tests. Do not invent numbers either (H14): crawl-safety limits and benchmark figures come from a human decision or a recorded experiment.
- **Source-specific behaviour is data, not code.** Hosts, path prefixes and entry points belong in the manifest or declared policy; code hard-enforces only universal security invariants (HTTPS, port 443, host allowlist, TLS verification, private-IP blocking, redirect validation, byte/time limits, fail-closed). Do not add rules that special-case a domain, title, filename or page number.
- **Respect the source.** No stealth, proxy rotation, CAPTCHA or challenge solving, fingerprint spoofing or other evasion; if a source blocks the client, stop and report. The live-retrieval gate stays closed until a human opens it. See [SECURITY.md](SECURITY.md).

## Database changes

Migrations are forward-only SQL files in `migrations/`, checksum-verified once applied: add a new file, never edit an applied one. Tables of immutable provenance or audit data stay append-only (trigger plus least-privilege grants), with tests for the constraints.

## Not legal advice

Documentation, UI strings and outputs must not claim to determine legal compliance or give legal advice (H18).
