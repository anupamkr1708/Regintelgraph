# Regulatory Knowledge Graph Schema

Status: PROPOSED ([ADR-004](adr/ADR-004-graph-strategy.md)). Small on purpose: a relation is included only if a defined query pattern needs it. Stored relationally (`kg_entity`, `kg_edge`, `kg_edge_evidence`).

## 1. Node and edge rules

- **Structural nodes** (Document, Version, Section, Clause) are the relational structure tables, exposed to the graph reader as nodes. **Semantic nodes** (Term, Obligation, Exception, EntityType, Product, Activity, RegulatoryChange) live in `kg_entity`/domain tables.
- Every edge has: `predicate`, `src`, `dst`, `status` (`CANDIDATE | VERIFIED | REJECTED`), `method` (`PARSER | RULE | MODEL | MANUAL`), `confidence` (extractor-reported, **uncalibrated**, nullable), `extraction_version`, optional `valid_from/valid_to` (legal-time scope), and ≥1 evidence link when `VERIFIED` (H10).
- `CONTAINS` edges from the parser need no model evidence: provenance = the parse run (`method=PARSER`, evidence = node span).
- `SUPPORTED_BY` is **not** an edge predicate; it is the `kg_edge_evidence` join (a design decision: modelling support as an edge would let "support" edges themselves be unsupported). Deviation from the requested list, recorded in [stage-1-review](stage-1-review.md).

## 2. Relation catalogue

| Predicate (src → dst) | Semantics | Source | Provenance | Confidence | Temporal behaviour | Expected query patterns |
|---|---|---|---|---|---|---|
| `CONTAINS` (Document→Section→Clause) | Structural containment in a version | PARSER | Parse run + node span | n/a (deterministic) | Inherits version; no own validity | Get clause, list section contents |
| `DEFINES` (Clause→Term) | Clause defines a term for a scope | RULE (definition patterns) → MODEL fallback → verify | Exact defining span | Rule: n/a; model: reported | Scoped to version; changes via Amendment | "What does X mean in <doc>?" |
| `REQUIRES` (Clause→Obligation) | Clause states an obligation | MODEL → verify | Exact normative span; modality recorded | Reported, uncalibrated | Obligation validity = clause's effective period | Obligations of a clause/document; diff of obligations |
| `APPLIES_TO` (Obligation/Exception→EntityType) | Obligation's subject class | MODEL → verify (curated vocab preferred) | Span naming the subject/scope | Reported | Same as obligation; may carry own `valid_from/to` if text differs by date | "Which entity types does X apply to?" |
| `APPLIES_TO` (Obligation→Product) / (→Activity) | Obligation relates to a product/activity | MODEL → verify | Span naming the product/activity | Reported | Same | "Obligations touching product P" |
| `EXEMPTS` (Exception→EntityType/Obligation) | Carve-out removes scope | MODEL → verify; high-impact → review queue | Exception span | Reported | Own `valid_from/to` if stated | "Who is exempt from X?" |
| `AMENDS` (Document→Document; clause-level via `amendment` record) | A modifies target T (insert/substitute/delete) | RULE (explicit verbs/refs) → MODEL → verify; review queue | Operative span of the amending text | Reported | Edge validity = amendment's *effective* date (not publication) | "What amended X?", "What changed in X in period P?" |
| `SUPERSEDES` (Document→Document, scope FULL/PARTIAL) | A replaces/withdraws T (whole or part) | Explicit text or MANUAL only — never inferred from recency | Span stating supersession | Reported | Effective date of supersession; `scope` | "Is X still in force?", "What did Y replace?" |
| `REFERENCES` (Clause/Document→Document/Clause) | Explicit citation of another instrument/clause | RULE (reference patterns) → resolve against identifiers | Citing span; resolution status (`RESOLVED/AMBIGUOUS/UNRESOLVED`) | Resolution: deterministic or `AMBIGUOUS` | None by itself (a mention is not an effect) | "Related documents", cross-reference tracing |
| `CHANGES` (RegulatoryChange→Obligation) | A change record adds/removes/modifies an obligation | DET diff + MODEL pairing → verify | Before/after evidence | Reported | Change's effective date | "Obligations added/removed between A and B" |

`REFERENCES` is deliberately weaker than `AMENDS`/`SUPERSEDES`: a citation implies relatedness, never legal effect.

Excluded on purpose (no query pattern yet): `ISSUED_BY` (a column), `HAS_EFFECTIVE_DATE` (a `date_assertion`), `ASSOCIATED_WITH` (undefined semantics), person/organisation NER relations.

## 3. Lifecycle

`extract → CANDIDATE → validate (schema, anchor, quote hash, predicate type rules, vocabulary) → [review queue for AMENDS/SUPERSEDES/EXEMPTS] → VERIFIED | REJECTED`. Retrieval and traversal use `VERIFIED` by default; `CANDIDATE` edges are only visible to evaluation tooling and are never cited. Re-extraction produces new rows under a new `extraction_version`; promotion is a reviewed diff.

## 4. Router and when NOT to use GraphRAG

| Question type | Route | Components |
|---|---|---|
| Simple factual ("what is the requirement for X") | FACTUAL | hybrid retrieval |
| Identifier/clause lookup | IDENTIFIER_LOOKUP | identifier index (+ hybrid fallback) |
| Temporal ("applicable on date X", "current") | TEMPORAL | retrieval + temporal resolver |
| Comparison ("what changed between versions") | COMPARISON | version selection + structural diff + retrieval |
| Multi-hop relationship ("which circulars amend rules that apply to Y") | MULTI_HOP | graph + retrieval |
| Regulatory impact | IMPACT | graph + retrieval + bounded agentic planning |

**Do NOT use graph traversal when:** the answer is in a single clause or definition; the query is an exact identifier or phrase lookup; the entities of interest have no `VERIFIED` edges (coverage check fails) — then say graph coverage is insufficient rather than traverse sparse data; traversal depth needed exceeds the configured limit; edges needed are only `CANDIDATE`; cost/latency budget is tight and hybrid already returns high-agreement evidence. Misrouting is safe by construction because **baseline hybrid retrieval always runs** and graph results only add evidence that is itself verified.

Traversal limits (initial defaults, unmeasured): depth ≤ 3, fan-out cap per node, total node cap; exceeded → return partial with `truncated=true`.
