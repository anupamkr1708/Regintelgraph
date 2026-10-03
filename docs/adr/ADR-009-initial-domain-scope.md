# ADR-009: Initial domain scope — India / SEBI / Mutual Funds

**Status:** ACCEPTED (decision recorded for Phase 1C: initial scope is **India · SEBI · Mutual Funds**). This is a corpus-selection decision for an engineering project; it is not a legal-compliance determination, and it does not resolve the source-access questions below.

## Context
The brief fixes India/SEBI as primary and asks for a justified first domain among Mutual Funds, AIF, Investment Advisers, or another. The research input spans SEC/EDGAR, RBI, FINRA, gazettes, XBRL, FCA/ESMA.

## Problem
Pick a corpus small enough to label and verify manually, but rich enough to exercise amendments, supersession, applicability and multi-hop questions.

## Decision
**SEBI Mutual Funds**: the governing regulations, the current master circular(s), and circulars/notifications that amend or supersede them within a bounded date window fixed in the manifest. Rationale (hypotheses to validate, not facts): long-lived instruments with frequent circular-level change and consolidation → strong temporal and amendment test material; clear entity types (fund, AMC, trustee, investor classes) and products (schemes) → applicability and impact questions.

**Validation tasks (A-01, A-02):** confirm the documents are publicly downloadable with acceptable terms/robots; count documents and pages in the window; sample parse quality (born-digital vs scanned); check how dates/supersession are actually stated in a sample; estimate labelling effort.

**Status of the validation tasks (Phase 1C):** the source-access questions — robots.txt, permission for automated retrieval, copyright/reproduction scope, the listing pagination method and byte-level verification of the canonical Master Circular — remain **UNRESOLVED** and gate live ingestion (`ingestion_authorized: false` in the manifest). Accepting this scope does not open that gate; see [source-access-report](../phase1/source-access-report.md).

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
