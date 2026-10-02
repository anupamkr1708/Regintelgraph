# Evidence Model

Status: PROPOSED ([ADR-008](adr/ADR-008-evidence-anchoring-and-citation-handles.md)).

## 1. Definitions

- **Evidence** — an immutable, verifiable text span in a `TextLayer`. `evidence_id = H(text_layer_id, start, end)`; stores `quote_hash`, node/page info, `document_version_id`, `canonical_url`. Trust class `SOURCE`.
- **Claim** — one atomic assertion from a draft answer/report: `text`, `claim_type` ∈ {REQUIREMENT, DATE, NUMERIC, ENTITY_SCOPE, RELATION, CHANGE, OTHER}, `as_of`, `criticality` ∈ {CORE, SUPPORTING}. Trust class `RUNTIME/MODEL`.
- **Citation** — link `Claim → Evidence` with `role` ∈ {SUPPORTS, CONTRADICTS, CONTEXT} and a user-visible handle. Exists only if the Evidence row exists and the claim–evidence pairing was produced by the matcher.
- **VerificationResult** — per claim: `state` ∈ {`SUPPORTED`, `PARTIALLY_SUPPORTED`, `CONTRADICTED`, `INSUFFICIENT_EVIDENCE`, `TEMPORALLY_AMBIGUOUS`}, per-check outcomes, verifier versions, `gap` text (for partial), model rationale (explanatory only).

## 2. Minimum provenance for a valid citation

`citation-valid` requires **all** of:

1. `document_id` + `document_version_id` (immutable snapshot)
2. `text_layer_id` + `start_offset/end_offset` (character offsets) and `quote_hash`
3. `node_path` (section/clause label as printed) when the structure parse located one; else `null` explicitly
4. `page_start/page_end` when the source is paginated; else `null`
5. `canonical_url` (and `retrieved_at`) of the source location
6. Temporal status of that version for the claim's `as_of` ([temporal-model](temporal-model.md))
7. Integrity: `sha256(slice) == quote_hash`, version `PUBLISHED`, not quarantined

Missing optional locators (page, clause label) degrade display, not validity; missing 1, 2, 5 or 7 invalidates the citation.

## 3. Lifecycle

```
question → [retrieval/graph → evidence pack E1..En] → draft answer (model sees pack as data; cites by handle)
        → claim extraction (atomic claims; each tagged with handles the draft used)
        → targeted evidence retrieval for claims lacking support (bounded second pass)
        → evidence matching (claim ↔ candidate evidence)
        → verification (§4)
        → final answer composition (§5)
```

Citations are created by **handle resolution**: the model may only emit handles `E1..En` present in the pack; the server maps handle → `evidence_id`; unknown handles are discarded and logged as `CITATION_REJECTED`. Displayed quotes are sliced from the stored text layer, never copied from model output.

## 4. Verification checks (ordered)

| # | Check | Type | Fail effect |
|---|---|---|---|
| V1 | Anchor integrity (hash recompute) | deterministic | Evidence unusable; claim loses it |
| V2 | Version status (published, not quarantined) | deterministic | Evidence unusable |
| V3 | Temporal validity for `as_of` | deterministic (resolver) | → `TEMPORALLY_AMBIGUOUS` or evidence excluded with reason |
| V4 | Scope/authority match (claimed authority/document matches evidence) | deterministic | Evidence downgraded to CONTEXT |
| V5 | Literal grounding: numbers, dates, defined terms, identifiers in the claim must appear (normalized) in cited evidence | deterministic | Not `SUPPORTED`; at best `PARTIALLY_SUPPORTED` |
| V6 | Entailment of claim by evidence | model/NLI (stub in CI; calibrated on benchmark) | Lowers state |
| V7 | Counter-evidence scan (other pack items + targeted retrieval) | hybrid | `CONTRADICTED` if credible contradicting evidence in the *same temporal scope* |

A model check may only **lower** a state or confirm one that passed V1–V5; no model output can override a deterministic failure. Contradiction between *different versions* is a **change**, not a contradiction.

## 5. State semantics and composition

| State | Condition | In the final answer |
|---|---|---|
| `SUPPORTED` | V1–V5 pass, V6 pass, no V7 contradiction, temporal clear | Stated, with citation(s) |
| `PARTIALLY_SUPPORTED` | Some elements grounded, others not/unclear | Stated only for the supported part, with an explicit gap sentence |
| `CONTRADICTED` | Credible contrary evidence in same scope | Presented as a conflict with both citations; never as a fact |
| `INSUFFICIENT_EVIDENCE` | No qualifying evidence | Removed; if CORE, answer is downgraded/abstained |
| `TEMPORALLY_AMBIGUOUS` | Dates/supersession unresolved for a time-dependent claim | Candidates and missing information shown; no single assertion |

Answer disposition: `ANSWERED` (all CORE claims SUPPORTED), `PARTIALLY_ANSWERED` (some CORE partial/removed), `ABSTAINED` (no CORE claim supported), plus a `conflict_reported` flag. **Removed** = peripheral unsupported claims dropped; **downgraded** = hedged to what evidence covers; **abstention** = core claims unsupported. The disposition and per-claim states are always visible to the user and stored in `answer_record`.

## 6. Integrity & reproducibility

- A scheduled integrity job re-hashes a sample/all evidence; any mismatch raises a P1 alert and quarantines affected citations.
- `answer_record` stores: pack hash, handle map, claims, verification results, versions (parser, embedder, index, verifier, prompt, model), config hash → "why this answer?" is reconstructable ([observability](observability.md)).
- Citation precision/recall and claim-support rate are measured against labelled benchmark cases ([evaluation](evaluation.md)); an automated LLM judge is never the sole ground truth.
