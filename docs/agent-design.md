# Agent Architecture

Status: PROPOSED ([ADR-005](adr/ADR-005-agent-orchestration.md)). A **controlled orchestrator**, not a swarm. One bounded state machine; model calls are used only where judgement over language is needed; everything else is deterministic code.

## 1. Logical capabilities

| Capability | Implementation | Why |
|---|---|---|
| Query Router | Deterministic rules → classifier fallback (model/local; stub in CI) | Cheap, explainable, testable; classifier only for residual ambiguity |
| Planner | Template plans per route (deterministic); model-assisted decomposition only for IMPACT/MULTI_HOP | Most queries need no free-form planning |
| Search Tool | Deterministic code (retrieval pipeline) | Reproducible |
| Graph Tool | Deterministic code (`GraphReader`) with limits | Reproducible, bounded |
| Document Comparison Tool | Deterministic structural diff; model only to *pair* obligations/summarize, output verified | Diff must be exact |
| Evidence Builder | Deterministic (dedupe, order, budget, handles) | Provenance integrity |
| Verification Tool | Deterministic checks V1–V5, V7 scan; model/NLI for V6 | See [evidence-model](evidence-model.md) |
| Impact Analysis | Orchestrated: graph + comparison + retrieval; model drafts, verification gates | Composite; every finding is a Claim |
| Answer drafter | Model, sees pack as **data** | Language synthesis |

## 2. Orchestrator state machine

`ROUTE → PLAN → EXECUTE_STEP (loop) → ASSEMBLE_PACK → DRAFT → EXTRACT_CLAIMS → VERIFY → [REPAIR (bounded)] → COMPOSE → DONE | ABSTAIN | FAILED_PARTIAL`

State (`AgentRun`): `run_id`, `request_id`, `query`, `route`, `plan` (list of typed steps), `step_results`, `evidence_pack`, `budget_used`, `tool_call_log`, `termination_reason`. State is persisted per step (audit + resume) and contains **references**, not copies, of source text.

## 3. Tool contracts

Tools are pure application-service calls with pydantic-typed input/output, registered statically. Common envelope: `ToolContext {principal, request_id, run_id, budget_remaining, as_of}`. Every tool is **read-only** and returns `{ok, data, truncated, warnings, cost}`; source text appears only in fields marked `content_trust: UNTRUSTED_SOURCE_TEXT`.

| Tool | Input (validated) | Output | Limits (initial, unmeasured) |
|---|---|---|---|
| `search` | query, filters, as_of, k | `RetrievalResult[]` | k ≤ cap; timeout |
| `graph_traverse` | start entity ids, predicates, depth | edges + evidence refs | depth ≤ 3, fan-out/node caps |
| `get_text` | evidence/node id | verbatim slice | size cap |
| `compare_versions` | document_id, version A/B (or dates) | `RegulatoryChange[]` | node cap |
| `resolve_temporal` | candidate set, as_of | `TemporalResolution` | — |
| `verify_claims` | claims, pack | `VerificationResult[]` | claim cap |

Argument validation rejects: unknown fields, free-form SQL/paths/URLs, ids not of the expected type. The model chooses *which registered step to run next* and parameters inside validated schemas — it cannot define new tools.

## 4. Hard prohibitions (enforced structurally, tested in `tests/security`)

The agent **cannot**: fabricate evidence (only `Evidence` rows with valid anchors enter packs; handles are server-resolved) · bypass authorization (principal flows through `ToolContext`; each tool re-checks scope) · mutate authoritative or derived records (agent package has no write-capable imports; DB role for query path has read-only grants plus insert on runtime/audit tables) · bypass verification (COMPOSE only accepts `VerificationResult`s; no code path from DRAFT to response) · treat retrieved text as instructions (source text is only placed in data envelopes; model output is parsed against a schema; any "tool call" requested by text content is ignored and logged).

## 5. Limits, loops, failure handling (initial defaults — `ASPIRATION`, tune from traces)

| Control | Default | On breach |
|---|---|---|
| Max planner iterations | 4 | Stop; compose from verified evidence so far; `termination_reason=ITERATION_LIMIT` |
| Max tool calls per run | 12 | Same |
| Per-tool timeout / run wall-clock | configured | Tool: step marked failed; run: return partial |
| Token budget (in/out per run) | configured | Truncate pack by priority; no extra model calls |
| Cost budget per run | configured | Hard stop before the next paid call |
| Loop prevention | Dedupe on `(tool, canonical_args_hash)`; stop if two consecutive steps add no new evidence ids | `termination_reason=NO_PROGRESS` |
| Early stop | Stop when pack covers all plan sub-questions with verified-eligible evidence | — |
| Failure | Tool errors retried once if idempotent; model failure → deterministic fallback (hybrid retrieval + extractive pack, no generated prose) or `ABSTAIN` | Partial results always labelled incomplete |

## 6. Prompt construction rules

- System/developer prompts are static templates in git, versioned (`prompt_version`).
- Source text appears only inside `<source_data id="E3" trust="untrusted">…</source_data>`-style envelopes with delimiters the content cannot forge (per-request random boundary token; escaping of the boundary).
- The model is told that envelope content is data and cannot change instructions; this is **defence in depth only** — the structural controls above are the real defence ([security](security.md) T-01).
- Output must be schema-valid JSON/structured output; invalid → one repair attempt, then deterministic fallback.
