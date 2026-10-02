# Observability

Status: PROPOSED (Phase 14 builds dashboards; **instrumentation starts in Phase 1**). Goal: for any answer, reconstruct *why the system produced it*.

## 1. Telemetry vs audit

| | Telemetry (traces/metrics/logs) | Audit / explanation records |
|---|---|---|
| Purpose | Operate and debug | Accountability, reproducibility |
| Storage | OpenTelemetry-compatible spans + JSON logs (stdout locally; exporter pluggable, **OD-07**) | Postgres append-only tables (`audit_event`, `answer_record`, `retrieval_run`, `verification_result`) |
| Sampling | Configurable | **100%** of answers and material actions |
| Retention | Short, configurable | Policy-defined; includes versions and hashes |

## 2. Correlation IDs

- `request_id` (UUIDv7): created at the edge (API/MCP/job); client-supplied IDs are accepted only as `client_request_id` attribute, never trusted as the primary key.
- `trace_id`/`span_id`: W3C Trace Context.
- Derived IDs persisted together: `query_id`, `run_id` (agent), `retrieval_run_id`, `answer_id`, `claim_id`; background work: `job_id`, `ingest_run_id`, `document_version_id`.
- Propagation: context variables inside the process; passed explicitly into jobs/subprocess; **every log line and span carries `request_id`**.

## 3. Span taxonomy

| Span | Key attributes (never raw secrets) |
|---|---|
| `request` | route, principal id (hashed), status, disposition |
| `query.classify` | route, confidence, by=rule/classifier, classifier_version |
| `retrieval.run` | filters, index_version, N per stage, fallback path (exact vs ANN) |
| `retrieval.sparse` / `.dense` / `.fuse` / `.rerank` | counts, latency, params_version, candidate-set hash |
| `graph.traverse` | start ids, predicates, depth, nodes/edges visited, truncated |
| `temporal.resolve` | as_of, applicable/excluded/ambiguous counts, rules_version |
| `agent.iteration` / `agent.tool_call` | step, tool, args hash, result ids, budget remaining, termination_reason |
| `evidence.build` | pack hash, items, tokens, dropped counts |
| `claims.extract` / `claims.verify` | claims, states, checks failed |
| `llm.call` | provider, model id, prompt_version, input/output tokens, latency, cost estimate, cache hit, retry count |
| `cache.lookup` | layer, hit/miss |
| `ingest.stage` | stage, document_version_id, duration, outcome |

## 4. Logs

Structured JSON: `ts, level, request_id, trace_id, component, event, ids…, duration_ms, outcome`. Prompts and source text are **not** logged by default; store `prompt_version` + hashes, with an explicit debug flag (off in production) to capture bodies. Redaction filter for secrets/tokens. Errors carry stable codes (`CITATION_REJECTED`, `INJECTION_FLAGGED`, `BUDGET_EXHAUSTED`, …).

## 5. The explanation record (`answer_record`)

Per answer: `request_id` → `query` (normalized, parsed filters/as_of) → route decision → retrieval runs (candidates and scores per stage) → temporal resolution → graph traversal (edges used) → evidence pack (hash, handle map) → draft prompt/template version + model id → claims → verification results (per-check) → composition decisions (kept/removed/downgraded and why) → final text → disposition → versions (parser, embedder, index, extractor, verifier, config hash) → budget used. "Why this answer?" is a query over these rows.

## 6. Metrics (names indicative)

Latency histograms per span; error rates by code; abstention rate by reason; claim-state distribution; tool calls/run; tokens and cost per route; cache hit rate per layer; queue depth and job age; ingestion success/quarantine rates; evidence-integrity check failures (alert P1); injection-flag counts.

## 7. Privacy

Queries may be sensitive: retention limit, access restriction, optional hashing of query text in telemetry. No secrets in telemetry; tests assert redaction.
