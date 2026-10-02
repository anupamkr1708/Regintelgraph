# ADR-009: Initial domain scope — India / SEBI / Mutual Funds (proposed)

**Status:** PROPOSED — requires your decision before Phase 1

## Context
The brief fixes India/SEBI as primary and asks for a justified first domain among Mutual Funds, AIF, Investment Advisers, or another. The research input spans SEC/EDGAR, RBI, FINRA, gazettes, XBRL, FCA/ESMA.

## Problem
Pick a corpus small enough to label and verify manually, but rich enough to exercise amendments, supersession, applicability and multi-hop questions.

## Decision (proposed)
**SEBI Mutual Funds**: the governing regulations, the current master circular(s), and circulars/notifications that amend or supersede them within a bounded date window fixed in the manifest. Rationale (hypotheses to validate, not facts): long-lived instruments with frequent circular-level change and consolidation → strong temporal and amendment test material; clear entity types (fund, AMC, trustee, investor classes) and products (schemes) → applicability and impact questions.

**Validation tasks before Phase 1 (A-01, A-02):** confirm the documents are publicly downloadable with acceptable terms/robots; count documents and pages in the window; sample parse quality (born-digital vs scanned); check how dates/supersession are actually stated in a sample; estimate labelling effort.

**Disagreement with research input:** SEC/EDGAR, FINRA, gazettes, XBRL, RBI and others are out of scope (principle: do not expand scope in Stage 1).

## Alternatives considered
1. **AIF** — smaller, possibly easier first corpus; weaker amendment history (unverified).
2. **Investment Advisers** — smallest and cleanest; may under-exercise temporal/supersession logic (unverified).
3. Multi-domain SEBI — rejected: labelling cost, scope creep.

## Tradeoffs
Larger corpus than IA/AIF (more labelling, more parse variety) in exchange for stronger stress on the hardest subsystems.

## Consequences
Manifest, fixtures, and benchmark labels target this domain; synthetic `TESTREG` corpus mirrors its structural patterns without copying text.

## Migration path
Switch domain by changing the manifest and labels; no architecture change. Fallback if validation tasks fail: Investment Advisers.
