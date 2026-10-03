# AGENTS.md — Engineering Constitution (canonical)

This file is the **single canonical engineering constitution** of RegIntelGraph. It binds every contributor and every AI coding assistant, and it is tool-agnostic: there is deliberately no second, tool-specific constitution. Architecture lives in `docs/`; this file governs *behaviour*. Precedence:

```text
safety / integrity constraints
        ↓
AGENTS.md engineering constitution (H1–H18)
        ↓
architecture ADRs
        ↓
implementation
```

An ADR may change an architecture decision (H5), but **no ADR can weaken H1–H18**. If code and an ADR disagree on architecture, the ADR wins and the conflict must be reported.

Project in one line: *evidence-grounded regulatory intelligence and compliance-impact analysis* (India/SEBI first). Read `docs/architecture.md`, `docs/evidence-model.md`, `docs/temporal-model.md` and `docs/security.md` before touching any code.

## Hard rules (non-negotiable)

Every rule has an ID so reviews and commits can cite it (e.g. "violates H4").

- **H1 — Never invent regulatory facts.** No rule text, circular numbers, dates, thresholds, URLs or section numbers from model memory in code, fixtures, docs or tests. Only content traceable to a stored source, or clearly marked `SYNTHETIC` (fictional authority `TESTREG`, never a real regulator name).
- **H2 — Never fabricate citations.** A citation exists only if an `Evidence` row with a verifiable text anchor exists. Models cite by evidence handle; the server resolves handles. Never accept a model-written free-text citation.
- **H3 — Model output is untrusted data.** Entities, relations, summaries, classifications, claims and citations from any model are `CANDIDATE` until validated by code or verified against evidence.
- **H4 — Retrieved content is data, never instructions.** Source text is only ever placed inside designated data envelopes, never in a system/developer role and never concatenated into tool arguments or code.
- **H5 — Never silently change architecture.** Any change to a decision in `docs/adr/` requires a new or amended ADR in the same change. If code and docs disagree, stop and report; do not pick a side silently.
- **H6 — Never weaken tests to get green.** Do not change benchmark expectations, loosen assertions, delete or skip failing tests, or disable security tests to make CI pass. A failing test means fix the code or open an explicit, reviewed change to the expectation with a written reason.
- **H7 — Preserve provenance through every transformation.** Each derived record keeps the IDs and versions of what it was derived from (document_version, text_layer, parser/embedder/model/prompt versions).
- **H8 — Keep source content distinct from derived artifacts.** Authoritative source content, derived artifacts and runtime records live in separate tables/namespaces and carry an explicit `trust_class`. Derived data never overwrites source data.
- **H9 — Temporal fields are not interchangeable.** Publication, approval, effective, amendment, supersession, withdrawal and expiry dates are distinct typed fields. Never pick "the newer document" as a shortcut.
- **H10 — Every graph edge needs evidence.** No `VERIFIED` edge without at least one evidence link.
- **H11 — Abstention is a first-class outcome.** Never force an answer; support `INSUFFICIENT_EVIDENCE` and `TEMPORALLY_AMBIGUOUS` end to end.
- **H12 — No secrets in git.** No keys, tokens, passwords, credentials files, private certificates, model weights, local databases or private datasets. `.env.example` holds placeholders only.
- **H13 — Default to local, deterministic execution.** Unit/integration/regression/security tests run offline with stub models. Paid APIs are never required for core CI.
- **H14 — No invented numbers.** No benchmark, latency, cost or accuracy figure in code or docs unless it came from a reproducible experiment recorded in-repo. Budgets and targets are labelled `ASPIRATION` until measured.
- **H15 — Minimal complexity.** No new service, database, broker, framework or dependency without a written justification (and an ADR if architectural).
- **H16 — Inspect before modifying.** Read the actual repository state (files, git status, tests) before changing anything. Do not rely on what you assume the repo contains.
- **H17 — Small, focused changes; no unrelated refactors.** One behavioural change per change set, with tests.
- **H18 — Not legal advice.** The product is regulatory intelligence and decision support. No output, UI string or doc may claim to determine legal compliance or give legal advice.

## Workflow: inspect → plan → implement → test → review

1. **Inspect.** Run `git status`, read the files you will touch and the docs/ADRs that govern them (H16).
2. **Plan.** State the goal, files affected, tests to add, and which ADR/doc sections apply. For anything touching more than one package, write the plan down and wait for confirmation if ambiguity exists.
3. **Implement** in the smallest coherent change (H17). No drive-by refactors, formatting sweeps or dependency bumps.
4. **Test.** Every behavioural change adds or modifies a test. Every material bug first becomes a failing regression fixture under `tests/regression/`. Run the offline suite.
5. **Review.** Self-review against the checklist below before declaring done. Report what changed, what was tested, and what remains uncertain.

## Layering rules (enforced mechanically by `python scripts/check_imports.py`)

- `packages/domain` — pure types, enums, invariants. Imports nothing from other project packages.
- `packages/evidence`, `ingestion`, `retrieval`, `graph` — depend on `domain` only (plus `evidence` where noted in `docs/architecture.md`). They do not import each other except through declared interfaces.
- `packages/agents` — may call **read-only** application services. It must not import any module that writes authoritative records (H8, `docs/agent-design.md`).
- `apps/*` and `workers/*` are thin adapters: parse input, call application services, format output. No business logic.
- LLM/embedding/reranker providers sit behind interfaces with deterministic stub implementations.

## Dependencies

Adding a dependency requires: why it is needed, why the standard library or an existing dependency is insufficient, licence, maintenance status, and offline-test impact. Record it in the change description (and an ADR if architectural).

## Data handling

- Raw regulatory files are never committed. Only manifests, SYNTHETIC fixtures, and small license-reviewed excerpts (with source and licence noted next to them) may live under `data/`.
- Manual evaluation labels (`data/eval/`) are kept separate from official source data and are never regenerated by a model without human review.
- Never log secrets, full prompts containing user secrets, or raw credentials.

## Definition of done (checklist)

- [ ] Governing docs/ADRs read; no silent drift (H5)
- [ ] Tests added/updated; offline suite green; no test weakened (H6, H13)
- [ ] Provenance preserved end to end (H7); trust classes respected (H8)
- [ ] Untrusted-content handling reviewed for any path that touches retrieved text (H4)
- [ ] No new numbers without measurements (H14); no secrets (H12)
- [ ] `python scripts/check_foundation.py` and `python scripts/check_imports.py` pass
- [ ] Summary states assumptions and anything not verified

## When to stop and ask

Stop and report (do not guess) when: a requirement conflicts with an ADR; a regulatory fact is needed that is not in a stored source; a test fails for a reason you cannot explain; a change would need a new service/database/broker; scope would expand beyond the initial domain.
