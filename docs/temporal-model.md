# Temporal Model (critical subsystem)

Status: PROPOSED ([ADR-007](adr/ADR-007-temporal-and-supersession-policy.md)). Authority-specific rules (SEBI first) are added in Phase 6 from **read source text, not assumed**; this document defines the generic machinery.

## 1. Two time axes

| Axis | Question | Fields |
|---|---|---|
| **Legal (valid) time** | When did the rule apply in the world? | The date kinds below |
| **Knowledge (system) time** | When did *we* know/ingest it? | `retrieved_at`, `ingested_at`, `published_to_index_at` |

Queries carry `as_of_legal` (default: today, stated explicitly in the answer) and optionally `known_as_of` (for audit reproduction: "what would the system have said then?"). Retrospective documents (published after date X but effective before X) are flagged `RETROSPECTIVE` and never presented as having been publicly known at X.

## 2. Date kinds (never interchangeable — H9)

| Kind | Meaning | Notes |
|---|---|---|
| `PUBLICATION` | Date the authority issued/published the document | Often printed on the document; ≠ effective |
| `APPROVAL` | Date of approval by a board/authority/government, if stated | Informational; does not start effect by itself |
| `EFFECTIVE_FROM` | Date the provisions take effect | May be explicit, relative ("within N days of this circular"), "immediate", or absent |
| `AMENDMENT_ISSUED` | Publication date of an amending instrument | Distinct from the amendment's own effective date |
| `AMENDMENT_EFFECTIVE` | Date an amendment takes effect on its target | Defaults to nothing; see missing-date rules |
| `SUPERSESSION_EFFECTIVE` | Date a supersession/replacement takes effect | Explicit text only |
| `WITHDRAWAL` | Date the document is withdrawn/rescinded | |
| `EXPIRY` | Stated sunset/end date | |
| `CONSOLIDATED_AS_OF` | "As on" date of a consolidated text, when the document states one | Not an effective date |

Each is stored as a `date_assertion` with: `value`, `precision` (DAY/MONTH/YEAR), `basis` ∈ {`EXPLICIT_TEXT`, `RELATIVE_TEXT` (computed, formula stored), `IMMEDIATE_EFFECT_TEXT` (equals publication, flagged derived), `MANUAL`, `UNKNOWN`}, `evidence_id` (required unless `UNKNOWN`), `status` (CANDIDATE/VERIFIED), `extraction_version`. **Missing stays missing**: `UNKNOWN` is a value, not an error.

## 3. State of a document or clause

Computed (never stored as a mutable field) for `(document_version or clause scope, as_of)`:

`ISSUED_NOT_EFFECTIVE` · `IN_FORCE` · `IN_FORCE_AMENDED` (in force; amendments listed, each with its own state) · `PARTIALLY_SUPERSEDED` · `SUPERSEDED` · `WITHDRAWN` · `EXPIRED` · `UNKNOWN_EFFECTIVE_DATE` · `CONFLICTED`.

## 4. Answering "What was applicable on date X?"

Input: topic/scope candidates (from retrieval/graph), `as_of=X`. Output: `TemporalResolution { as_of, applicable[], excluded[{item, reason, basis}], ambiguous[{item, missing}], rules_version }`.

1. **Candidates**: all documents/versions in scope that could bear on the topic (never only the newest).
2. **Effective period** per candidate/clause: `from` = verified `EFFECTIVE_FROM`; if none → `IMMEDIATE_EFFECT_TEXT` publication-derived only when the text says so, flagged `DERIVED_FROM_TEXT`; else `UNKNOWN`. `to` = earliest of explicit `EXPIRY`, `WITHDRAWAL`, effective `SUPERSESSION_EFFECTIVE` (scope-aware).
3. **Status at X**: `from` unknown → `UNKNOWN_EFFECTIVE_DATE`; `from > X` → `ISSUED_NOT_EFFECTIVE`; `from ≤ X < to` → `IN_FORCE` (with applied amendments whose effective date ≤ X); `X ≥ to` → `SUPERSEDED/WITHDRAWN/EXPIRED` by cause.
4. **Overlap resolution**: only via **explicit** `SUPERSEDES`/`AMENDS` relations with scope. No relation + overlapping topic → both retained; contradiction check on their obligations: complementary ⇒ both apply; conflicting ⇒ `CONFLICTED`.
5. **Verdict**: any decisive candidate that is `UNKNOWN_EFFECTIVE_DATE`, `CONFLICTED`, or has unclear supersession ⇒ dependent claims become `TEMPORALLY_AMBIGUOUS`; the answer lists candidates and exactly what is missing.

The resolver is **deterministic code** with no model calls; given the same assertions it returns the same result regardless of input order (property-tested).

## 5. Hard cases

| Situation | Behaviour |
|---|---|
| Two documents overlap on the same topic, no explicit relation | Report both; `CONFLICTED` if obligations conflict, else both applicable; never choose by recency |
| Amendment published but not yet effective at X | Original text is `IN_FORCE` at X; amendment shown as `ISSUED_NOT_EFFECTIVE` with its effective date (or unknown) |
| Effective date missing | `UNKNOWN_EFFECTIVE_DATE`; answer states it; allowed fallback only if document text says "with immediate effect" (flagged derived) |
| Supersession unclear (e.g., "this replaces earlier guidance" without naming it) | No `SUPERSEDES` edge is created automatically; candidate relation goes to review; until verified → `TEMPORALLY_AMBIGUOUS` |
| Partial supersession (some paras only) | Edge `scope=PARTIAL` with target clause refs; unaffected clauses stay `IN_FORCE` |
| Master/consolidating document lists superseded circulars | Verified explicit supersessions apply from their stated effective date; the consolidation's `CONSOLIDATED_AS_OF` is not used as effective date |
| Amendment effective but no official consolidated text | Cite original + amendment; any "as amended" rendering is a **derived, non-authoritative view** labelled as such, with per-span evidence to both sources; never presented as official text |
| Two authorities issue conflicting guidance | No hierarchy of norms is assumed or invented; show both with authority/type; `CONFLICTED`; decision left to human |
| Retrospective effect | Applicable at X in legal time; flagged `RETROSPECTIVE` w.r.t. knowledge time |
| Corrigendum / correction | Modelled as `AMENDS` with `operation=UNSPECIFIED` pending review (**OD-06**: dedicated predicate?) |

## 6. Testing (see [testing-strategy](testing-strategy.md))

Property tests: permutation invariance; adding an explicit supersession never makes a document applicable earlier; removing the only effective-date assertion can only move a result toward `UNKNOWN_EFFECTIVE_DATE`; no input exists for which "newer" alone changes the outcome. Fixtures use SYNTHETIC `TESTREG` documents. Benchmark category *temporal* includes missing/ambiguous dates and expects abstention ([benchmark-spec](benchmarks/benchmark-spec.md)).
