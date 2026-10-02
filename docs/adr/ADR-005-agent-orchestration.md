# ADR-005: Agent orchestration — bounded orchestrator, read-only tools

**Status:** PROPOSED (framework adoption DEFERRED)

## Context
Complex questions (impact, multi-hop) need multi-step retrieval. Risks: prompt injection, runaway cost, unverifiable output. The research input shows a plan→tool-loop→generate pseudocode that concatenates top results into the generator.

## Problem
Define the orchestration approach and its safety boundaries.

## Decision
A **hand-written state machine** (`ROUTE→PLAN→EXECUTE→ASSEMBLE→DRAFT→EXTRACT_CLAIMS→VERIFY→COMPOSE`) with typed steps, a static read-only tool registry, budgets (iterations, calls, time, tokens, cost), loop prevention, and deterministic fallbacks. Deterministic code for routing rules, retrieval, graph, comparison, evidence building, deterministic verification; models only for classification residue, plan decomposition on complex routes, drafting, claim extraction, entailment. **No multi-agent swarm**; no agent write path to any record; no free-text citations.

**Disagreement with research input:** pseudocode that extends evidence with `results[:5]` and generates directly is replaced by: evidence packs with handles → verification gate → composition. ReAct/LangChain-style free tool loops are not adopted as the control structure.

## Alternatives considered
1. Agent framework (LangGraph/LlamaIndex agents) — deferred: may help state/persistence but adds dependency and opaque control flow; evaluate in a later spike against H15.
2. Autonomous ReAct loop — rejected: weaker budget/loop control, harder to test.
3. Multi-agent roles — rejected: no benefit demonstrated; more attack surface.

## Tradeoffs
+ Testable, bounded, auditable. − Less flexible than open-ended agents; plans for novel question types need code/templates.

## Consequences
Agent package forbidden from write-capable imports; DB role for the query path is read-only on authoritative tables; ablation arms A5/A6 measure whether iteration pays off.

## Migration path
Orchestrator steps map onto any workflow framework later; tool registry and contracts remain the stable surface (also reused by MCP).
