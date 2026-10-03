# Product Specification

## 1. Problem

Regulated-sector teams must answer: *what is required, what changed, what applied on a given date, who is affected, and where is the proof.* Existing tools either search text (no structure/time/relationships) or generate fluent answers (no verifiable evidence, no abstention). Regulatory text also has structural traps: circulars that amend only parts of earlier instruments, consolidated "master" documents that supersede earlier ones, effective dates distinct from issue dates, and cross-references.

## 2. Positioning (non-legal-advice)

RegIntelGraph is a **regulatory intelligence, evidence-analysis and decision-support system**. It surfaces and organises authoritative text and the evidence for statements about it. It does **not** provide legal advice, does **not** certify compliance, and every user-facing output carries that framing (H18). Final judgement stays with qualified humans.

## 3. Target users

| Persona | Needs |
|---|---|
| Compliance analyst | Current requirement, applicable-on-date, evidence to attach to a memo |
| Regulatory researcher | Version/amendment history, related instruments, conflicts |
| Risk analyst | Which obligations changed and which products/entity types they touch |
| Legal/compliance engineering team | Machine-readable obligations, auditable impact reports, MCP/API access |
| AI/knowledge-platform engineer | Safe tool surface (MCP), reproducible evaluation, provenance guarantees |

## 4. Primary use cases

Mapped to the ten required question types (+ report): **UC1** current requirement for X · **UC2** diff between two versions · **UC3** obligations added/removed/modified · **UC4** affected entities/products/activities · **UC5** related prior rules/circulars · **UC6** applicable as of date · **UC7** evidence per material claim · **UC8** source conflicts · **UC9** correct abstention · **UC10** external agents via MCP · **UC11** auditable compliance-impact report.

## 5. Non-goals

Legal advice or compliance determination · coverage of every regulator · real-time/market-data features · ingestion of private/client documents (future, separate isolation design) · autonomous actions or writes by agents · fine-tuning models · building a generic legal ontology · mobile apps · multi-region deployment.

## 6. Initial domain

India / SEBI, one focused corpus. **Initial scope ([ADR-009](adr/ADR-009-initial-domain-scope.md)):** Mutual Funds (regulations, master circular(s), and circulars amending/superseding them within a bounded date window) — chosen because it has rich supersession and amendment structure to stress the temporal model. Alternatives: AIF, Investment Advisers. Decision record and validation tasks: [ADR-009](adr/ADR-009-initial-domain-scope.md). Corpus boundaries are fixed by a reviewed manifest, not by crawling "everything".

## 7. Future expansion (order, each gated by measured results)

RBI → SEC/EDGAR → others. Expansion requires: authority adapter, new source manifest, authority-specific temporal rules reviewed in an ADR, extended benchmark. Not before Phase 17 results exist for SEBI.

## 8. Core user journeys

1. **Ask & verify.** Analyst asks "What is the current requirement for X?" → router chooses hybrid retrieval → evidence pack → draft → claims verified → answer with citations that open to the exact clause text and source URL; or abstention with what was searched.
2. **As-of query.** "What applied on 2024-03-01?" → temporal resolver determines applicable versions with explicit reasons; if effective dates are missing or supersession unclear → `TEMPORALLY_AMBIGUOUS` with the competing candidates shown.
3. **Change analysis.** Pick two versions (or a date range) → structural diff → obligations added/removed/modified, each with before/after evidence → optional impact report.
4. **Agent consumption.** External agent calls MCP `search_regulations` → `build_evidence_pack` → `verify_claim`; gets typed, bounded, read-only results flagged as untrusted source text.

## 9. System capabilities (target)

Ingestion with immutable versions · structure-aware parsing · hybrid retrieval · temporal reasoning · evidence-bound knowledge graph · bounded agentic planner · claim verification · explicit abstention · API/UI/MCP · audit trail answering "why this answer?".

## 10. Acceptance criteria for the final system

Structural invariants (must hold 100%, verified by deterministic tests — these are guarantees by construction, not performance claims):

- Every citation in any answer resolves to a stored `Evidence` whose text slice re-hashes to its recorded hash.
- No claim is presented as fact unless its verification state is `SUPPORTED` or `PARTIALLY_SUPPORTED` (with the gap stated).
- Every `VERIFIED` graph edge has ≥1 evidence link.
- "As of date" answers state which date fields were used; missing/unclear dates yield `TEMPORALLY_AMBIGUOUS`, never a silent newest-wins.
- Retrieved text never alters control flow (security suite passes 100%).
- Core test suite passes offline.

Quality thresholds (recall, citation precision, abstention accuracy, etc.) are **not set in Stage 1**. They are set from baseline measurements in Phase 5 and frozen before Phase 17 ([evaluation](evaluation.md)).

## 11. Failure and abstention behaviour

| Condition | Behaviour |
|---|---|
| No relevant evidence retrieved | `INSUFFICIENT_EVIDENCE`; show queries/filters tried and corpus coverage boundary |
| Evidence exists but only partly covers the question | `PARTIALLY_SUPPORTED`; state what is and is not covered |
| Sources disagree | Report conflict with both citations; no silent resolution |
| Dates missing / supersession unclear / amendment not yet effective | `TEMPORALLY_AMBIGUOUS`; show candidates and what is unknown |
| Question outside corpus (other regulator, other period) | State corpus scope; abstain |
| Tool/model failure or budget exhausted | Return what is verified so far, labelled incomplete; never fill gaps with unverified text |
| Detected injection content in a source | Quarantine span from prompts, flag in audit, continue with other evidence |
| Request for legal conclusion ("are we compliant?") | Provide evidence and obligations; decline to conclude; restate decision-support scope |
