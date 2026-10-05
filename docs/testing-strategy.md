# Testing Strategy

Status: PROPOSED. Principles: **deterministic, offline, local by default** (H13). The benchmark and tests are never weakened to obtain a green build (H6).

## 1. Test types

| Type | Scope | Dependencies | Runs |
|---|---|---|---|
| Unit | Pure logic: normalization, identifier extraction, temporal resolver, fusion, hashing, anchors, state composition | None | Every commit |
| Integration | Package + PostgreSQL (migrations from empty, constraints, FTS, pgvector, job queue, publish gate) | Local Postgres (service container in CI) | Every commit |
| Regression | One fixture per material bug | As needed | Every commit |
| Evaluation (smoke) | Small deterministic RegIntelBench subset on synthetic corpus with stub models | Postgres | Every commit |
| Evaluation (full) | Full benchmark/ablations, optionally real models | May need keys | On demand; never required for CI |
| Security | Injection, SSRF, path traversal, authz, malformed inputs, redaction | Fakes | Every commit; **non-skippable** |
| Property-based | Temporal resolver invariants, anchor round-trips, idempotency of ingestion, RRF properties | Hypothesis-style | Every commit (bounded examples) |
| Performance | Latency/throughput baselines | Pinned env | Scheduled; non-blocking until baselines exist |
| Adversarial | Hostile documents/queries/model outputs | Stub "obedient" model | Every commit |

## 2. Deterministic doubles

`HashEmbedder` (stable pseudo-embedding from token hashes), `StubReranker`, `ScriptedLLM` (fixed responses by prompt hash; includes an **obedient-to-injection** mode to prove structural defences), `FakeResolver` (DNS), fake clock, seeded randomness, fixture blob store. Paid-API tests live behind an explicit marker and are excluded from CI defaults.

## 3. Fixtures

Synthetic `TESTREG` documents (marked `SYNTHETIC`) are the default. Real regulatory excerpts only if small, license-reviewed, with source/licence recorded beside them (H1, H12). Real PDFs are never committed.

## 4. Rules

1. **Every material bug becomes a regression fixture** before the fix.
2. Behavioural change ⇒ test change in the same change set.
3. **Ratchets:** counts of security tests, regression fixtures and benchmark cases may not decrease without a justified entry; CI fails on silent deletion/skip. **Implemented** for security, regression and integration tests by `scripts/check_test_ratchet.py` against the checked-in `tests/ratchet-baseline.json` (stdlib `ast` count of test functions per directory; skip/xfail markers counted against a ceiling; a lowered baseline needs `--update --reason` and is recorded in the file's history, which is self-validating). It runs in the CI `foundation` job. Benchmark cases are not yet covered because no benchmark cases exist (`tests/evaluation/` is empty); they join the ratchet when they do. Review checklist remains in [AGENTS.md](../AGENTS.md).
4. Expected values in benchmark/test data change only with a written reason and reviewer (H6).
5. No network access in unit/integration/security tests; a test that touches the network fails.
6. Flaky tests are fixed or removed with a recorded reason, never retried into green.
7. Coverage is a diagnostic, not a target.

## 5. CI layout

Now: `foundation-checks` (stdlib `scripts/check_foundation.py`). Phase 1+: lint + type check → unit → integration (Postgres) → security → eval-smoke → secret scan. Full benchmark and performance jobs are scheduled/manual.
