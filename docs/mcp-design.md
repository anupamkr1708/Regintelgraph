# MCP Design

Status: PROPOSED (Phase 12). MCP here is the **Model Context Protocol** (the Executive Summary's "Model-Chain-Protocol"/"Multi-Chain Protocol" naming is incorrect; recorded in [executive-summary](executive-summary.md)). The MCP server is a **thin adapter** over the same application services as the API: no business logic, no separate data access path.

## 1. Principles

Domain-capability tools only · read-only · typed input/output schemas · bounded results · every output item carries provenance · source text marked untrusted · authorization checked per call · full audit.

**Never exposed:** SQL execution, shell, filesystem traversal, arbitrary HTTP fetch, arbitrary Python, any write to authoritative/derived data, any tool that accepts URLs or paths.

## 2. Tools

| Tool | Input | Output | Notes |
|---|---|---|---|
| `search_regulations` | query, filters{authority, doc_type, date_window}, as_of?, k≤cap | `RetrievalResult[]` (contract in [retrieval-design](retrieval-design.md)) | Baseline hybrid |
| `get_document` | document_id or document_version_id | metadata + version list + structure outline (no full text by default) | Full text only via sections |
| `get_section` | document_version_id, node ref | verbatim section text + location | Size-capped |
| `get_clause` | document_version_id, node ref | verbatim clause + evidence handle | |
| `find_related_documents` | document_id, relation kinds | documents + edges + evidence refs | `VERIFIED` edges only |
| `trace_amendments` | document_id, window? | ordered Amendment/Supersession records with date assertions + evidence | Reports unknowns explicitly |
| `compare_versions` | document_id, version A/B or dates | `RegulatoryChange[]` with before/after evidence | |
| `find_applicable_rules` | topic/entity, as_of | `TemporalResolution` + evidence | May return `TEMPORALLY_AMBIGUOUS` |
| `find_affected_entities` | change id or document/clause | entity types/products/activities + edges + evidence | Graph-backed; coverage warning if sparse |
| `build_evidence_pack` | query/claims, as_of, budget | evidence pack with handles, temporal status | Pack is hash-stamped |
| `verify_claim` | claim text, evidence handles or pack id, as_of | `VerificationResult` | Deterministic checks always run; model check optional |
| `generate_impact_report` | change set / scope, as_of | `ImpactAssessment` (claims + verification + disposition) | May be async: returns report id |

All outputs include: `request_id`, `versions`, `truncated`, `warnings`, `disclaimer: decision-support, not legal advice`, and per-item `content_trust`.

## 3. Output contract excerpt

```json
{
  "request_id": "…",
  "ok": true,
  "data": {"items": [{"evidence_id": "…", "handle": "E1",
     "text": "…", "content_trust": "UNTRUSTED_SOURCE_TEXT",
     "location": {"document_version_id": "…", "node_path": ["…"], "page_start": 0, "canonical_url": "…"}}]},
  "truncated": false, "warnings": [], "versions": {"index": "…"}
}
```

Tool *descriptions and schemas* are static trusted strings; no source text or metadata is ever interpolated into them.

## 4. Authorization expectations

- Transport-level authentication for any non-local transport; local stdio mode runs under the invoking user. Tokens/credentials never in tool arguments or logs.
- Per-principal scopes (e.g., `read:search`, `read:graph`, `read:reports`, `run:verify`); each call checks scope and applies rate/size/cost limits. Corpus visibility (`authority`, future `tenant_id`) enforced in the query layer, not the adapter.
- Quotas and per-client budgets; denial returns a typed error, never partial leakage.
- Every call → `audit_event` (principal, tool, args hash, result ids, decision).

## 5. Risks specific to MCP

Result content may carry injected instructions aimed at the *consuming* agent: mitigated by `content_trust` marking, delimiting, and docs instructing clients to treat it as data — we cannot control the client, so outputs avoid imperative framing and strip control characters. Oversized/abusive calls: caps. Confused-deputy: scope checks per call, no ambient authority. See [security](security.md).
