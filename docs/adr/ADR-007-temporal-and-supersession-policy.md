# ADR-007: Temporal and supersession policy

**Status:** ACCEPTED

## Context
Regulatory validity depends on distinct dates (publication, approval, effective, amendment, supersession, withdrawal, expiry) and on explicit relations between instruments. The research input suggests tracking versions "by document date or gazette number", reporting a circular as updated if superseded by dates, and, on conflict, to "preferentially use the latest rule".

## Problem
Define how the system decides what applied when, and what it does when information is missing or conflicting.

## Decision
1. Typed `date_assertion`s with basis, evidence and status; missing dates stay `UNKNOWN`.
2. Legal-time vs knowledge-time axes; as-of queries are first-class.
3. Document state is **computed** by a deterministic resolver from assertions and explicit relations; never stored as a mutable flag.
4. **Supersession/amendment only from explicit text or manual curation** (scope FULL/PARTIAL). **Recency alone never decides** applicability or conflict resolution.
5. Unresolved cases yield `TEMPORALLY_AMBIGUOUS` with the candidates and what is missing; conflicting authorities are reported, no hierarchy of norms invented.
6. Any "as amended" consolidated rendering is derived and labelled non-authoritative.

**Disagreement with research input:** "latest rule wins" and "version tree by date" are rejected as defaults; version identity is content-hash based ([ADR-008](ADR-008-evidence-anchoring-and-citation-handles.md), [ingestion-design](../ingestion-design.md)).

## Alternatives considered
1. Newest-wins heuristic — rejected: silently wrong when the newer document is not yet effective, partial, or unrelated.
2. Model-inferred supersession — rejected: unverifiable; allowed only as CANDIDATE for review.
3. Single "valid date" field — rejected: conflates distinct concepts (H9).

## Tradeoffs
+ Correct by construction, auditable, testable properties. − More abstentions; more review work to verify supersession; slower to cover a corpus.

## Consequences
Phase 6 must read real SEBI text to define authority-specific patterns (not assumed here); benchmark includes missing/ambiguous-date cases that require abstention.

## Migration path
Rules versioned (`temporal_rules_version`); new authorities add pattern modules and fixtures; the generic resolver is unchanged.
