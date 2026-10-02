# ADR-006: Model routing and provider abstraction

**Status:** DEFERRED for concrete model selection; PROPOSED for the interface and routing policy

## Context
The research input compares hosted and local LLMs with illustrative latency/accuracy/cost tables. Those tables are unsourced, name older model generations, and contain figures that appear inconsistent with public model descriptions (e.g., the stated parameter count for Mistral 8x7B). They are not usable as evidence. Data is public, so hosted models are permissible (assumption A-07); user-supplied private documents would need a new decision.

## Problem
Decide how models are selected and routed without hard-coding vendors or unmeasured claims.

## Decision
- Provider-agnostic interfaces: `LLMProvider`, `Embedder`, `Reranker`, `Classifier`, each with a **deterministic stub** and a recorded `model_id`/`version` on every artifact.
- **Task-based routing** in config: classification, plan decomposition, draft generation, claim extraction, entailment, graph extraction, impact drafting — each independently assignable (hosted or local).
- Selection by **benchmark per task**, not by reputation; quality and cost reported together ([evaluation](../evaluation.md), [cost-model](../cost-model.md)).
- No model names, prices, or latencies are hard-coded in code or docs; configuration only (`.env.example` placeholders).
- Fallbacks: deterministic extractive path when the model is unavailable or budget is exhausted.

## Alternatives considered
1. Single hosted model for everything — simple, costly, unvalidated per task.
2. Local-only models — cost control, quality/ops unknown for legal text.
3. Fine-tuning on regulatory corpora — out of scope; revisit only with benchmark evidence.

## Tradeoffs
+ Vendor flexibility, testability, evidence-based choice. − Interface discipline and per-task evaluation effort.

## Consequences
Stub-first development; first real-model selection happens in Phase 5 (embedding) and Phase 9–10 (generation/verification) with recorded runs.

## Migration path
New provider = new adapter + benchmark run; routing changes are config changes with an evaluation record attached.
