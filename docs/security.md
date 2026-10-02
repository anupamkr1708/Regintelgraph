# Security

Status: PROPOSED. Security tests in `tests/security/` are part of the core offline suite and **may never be disabled to get CI green** (H6).

## 1. Trust boundary

```
┌──────────────── TRUSTED CONTROL PLANE ────────────────┐      ┌──── UNTRUSTED ────────────────────────┐
│ code · prompt templates · tool schemas · manifests ·  │      │ fetched files & HTML · extracted text │
│ authz policy · config · DB roles · verification logic │ ◄─── │ extracted metadata · user queries ·   │
│ (only source of instructions)                         │ data │ MCP arguments · ALL model output      │
└───────────────────────────────────────────────────────┘ only └───────────────────────────────────────┘
```

Rules: untrusted content crosses only as **data** through typed, validated interfaces; it never selects tools, builds SQL/paths/URLs, enters system prompts, or changes authorization. Every record carries `trust_class`. Model output is untrusted even though it originates inside the control plane's call.

**Defence order:** (1) structural controls (read-only agent, handle-resolved citations, allowlists, schema validation) → (2) deterministic verification → (3) prompt-level hardening and heuristic injection flags. Layer 3 is never relied on alone.

## 2. Threat catalogue

| ID | Threat | Attack surface | Risk | Mitigation | Test strategy |
|---|---|---|---|---|---|
| T-01 | Indirect prompt injection | Retrieved chunks, graph text, tool results placed in prompts | Model follows embedded instructions, leaks data, misuses tools | Read-only agent; no write/exec tools; source text only in boundary-tokenized data envelopes; static prompts; schema-validated outputs; tool args validated and never templated from source text; heuristic injection flagging (signal only); verification gate independent of the drafter | Corpus of injection fixtures (SYNTHETIC) embedded in documents; assert: no unregistered tool call, no handle outside pack, answer unchanged vs control, flagged in audit; stub-model adversarial mode that "obeys" injections to prove structural defences hold |
| T-02 | Malicious/untrusted document text (hidden text, homoglyphs, zero-width chars, fake "SYSTEM" blocks) | Parser output, chunks | Smuggled instructions or altered meaning | Unicode normalization + control/zero-width stripping in a *derived* view (raw offsets preserved); hidden-text (white/zero-size) detection in parse report; chunk flags | Fixture PDFs/HTML with hidden text; assert flags + no effect on prompts/tools |
| T-03 | Citation spoofing / fabricated citations | Model output; user-supplied "citations" | Claim shown as supported by evidence that does not say so | Citation-by-handle only; server-side resolution; quote sliced from stored text; V1–V7 checks; display of the exact span | Unit: unknown handle rejected; property: every rendered quote re-hashes; adversarial model stub emitting fake citations → zero rendered |
| T-04 | Source impersonation | Lookalike domains, mirrors, documents claiming to be from an authority | Non-authoritative text ingested as authoritative | Authority comes from manifest + allowlisted exact hosts, never from document text; `canonical_url` host check; TLS required; mismatch → quarantine | Fixtures: wrong host, redirect to other host, document claiming another authority → quarantined |
| T-05 | Malformed/malicious PDFs | Parser | Crash, memory exhaustion, parser exploits | Parse in sandboxed subprocess (resource limits, timeout, no network, no write outside temp); no JavaScript/launch actions/embedded-file execution; page/size caps; pinned parser versions; fuzz corpus | Corpus of truncated/bomb/recursive PDFs; assert bounded time/memory and `FAILED_PARSE` not crash; periodic fuzz run (non-blocking CI) |
| T-06 | Path traversal | Blob store, any file-referencing input | Read/write outside intended dirs | Content-addressed storage keyed by hash (no user-supplied paths); relative paths validated against root; MCP/API accept ids not paths | Unit: `../`, absolute paths, symlinks in all path-taking functions |
| T-07 | SSRF | Crawler, any fetch of URLs found in documents/manifests | Access internal services/metadata endpoints | Single egress component; exact-host allowlist from manifest; resolve DNS and reject private/loopback/link-local ranges; re-validate every redirect; no fetch of URLs from document content | Unit with fake resolver: private IPs, DNS rebinding sequence, redirect chains, IPv6, userinfo tricks |
| T-08 | Malicious URLs & redirects | Links in documents, listing pages | Fetch of hostile content, open redirects, scheme abuse | https only; no auto-follow of links outside the manifest scope; size/time caps; content-type sniffing | Fixtures: `file://`, `javascript:`, huge responses, wrong content-type |
| T-09 | Poisoned metadata | Titles, dates, reference numbers extracted from documents | Wrong identity/date drives wrong temporal answer | Extracted metadata is `CANDIDATE` + evidence-anchored; identity from manifest/review; date assertions verified; conflicts → review; manual overrides logged with reviewer | Fixtures with contradictory dates/ids; assert no auto-merge, status stays CANDIDATE/UNKNOWN |
| T-10 | Graph poisoning | Extraction of entities/edges from hostile text | False edges alter impact analysis | CANDIDATE→VERIFIED gate with evidence; review queue for AMENDS/SUPERSEDES/EXEMPTS; extraction rate/volume anomaly checks; edges never created from model "knowledge" | Fixtures with text asserting fake supersession; assert no VERIFIED edge without anchored explicit text; DB constraint test |
| T-11 | Retrieval data leakage | Cross-tenant/visibility (future), logs, caches | Exposure of non-public or other users' data | Stage-1 corpus is public only; visibility/tenant columns reserved; filters enforced in SQL layer; user-supplied-doc feature is out of scope until isolation design (ADR) exists; no user data in shared caches | Contract tests: visibility filter applied on every retrieval path; cache key includes principal scope |
| T-12 | Unauthorized access | API, MCP, admin | Unauthorized reads or job triggers | AuthN at edge, per-tool scopes, least-privilege DB roles (query path read-only), admin routes separated | Authz matrix tests per endpoint/tool; DB-role test that query role cannot write source tables |
| T-13 | Tool abuse (by agent or MCP client) | Tool arguments, call volume | Resource exhaustion, probing | Static tool registry; strict schemas; budgets (calls/tokens/cost/time); dedupe/loop prevention; per-principal rate limits | Tests: schema fuzzing, budget exhaustion paths, loop scenarios |
| T-14 | Oversized payloads | API/MCP requests, downloads, model contexts | Memory/cost blowup | Request size caps; download caps; pack token budgets; pagination | Boundary tests at cap ±1 |
| T-15 | Denial of service | Expensive queries, parse jobs | Availability loss, cost | Query budgets, timeouts, queue isolation of parse/index work from query path, rate limits, graph traversal caps | Load/budget tests; verify query path unaffected by worker load (Phase 14/15) |
| T-16 | Secret leakage | Repo, logs, prompts, traces, error messages | Credential exposure | `.env` ignored; placeholders only; secret scanning in CI and pre-commit; log redaction; no secrets in prompts; provider keys via env/secret store | CI secret-scan job; unit: redaction on logger; repo check script flags key-like strings |
| T-17 | Model-output manipulation | Draft, claims, classifications, extraction | Wrong/unsafe content presented as verified | Everything model-made is CANDIDATE/untrusted; schema validation; deterministic checks precede entailment; abstention path; no free-text citations | Stub models returning malformed/hostile outputs; assert fallbacks and abstention |
| T-18 | Supply-chain (dependencies, models) | Package installs, model downloads | Malicious/compromised code or weights | Dependency justification rule (H15); lockfiles with hashes; pinned versions; no model weights in git; verify checksums of downloaded models | CI: lockfile integrity check; dependency audit (non-blocking initially) |

## 3. Other controls

- **Transport & secrets:** TLS for all external traffic; secrets from environment/secret store only.
- **Logging:** structured, redacted; queries may be sensitive → retention policy ([observability](observability.md)).
- **Audit:** append-only `audit_event`; query-path DB role lacks UPDATE/DELETE on audit and source tables.
- **Disclosure/abuse:** the product never claims legal advice (H18); guardrails on out-of-scope requests return scope statements.
- **Review cadence:** threat model revisited at each phase gate (13 is the hardening phase, but tests are written from Phase 1).
