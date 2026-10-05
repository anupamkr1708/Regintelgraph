# Phase 1 — Crawl-Safety Parameters (the 16 mandatory limits)

**Status:** specification of the *implemented* behaviour as of the Phase 1C closeout. It refines [source-safety-contract](source-safety-contract.md) §6–§8 and [ingestion-contract](ingestion-contract.md) §3.2; it does not loosen either. Facts here were read from the code and are pinned by tests (listed in §6). **No parameter has a value in this repository, and none may be given a default.**

## 1. Rules that apply to every parameter

1. **Source of values:** only the manifest `crawl` block (a human-reviewed file, see [`data/manifests/sebi-mutual-funds.yaml`](../../data/manifests/sebi-mutual-funds.yaml)). The same names are the model fields of `SafetyLimits` and the keys of `CRAWL_PARAMETER_NAMES` in `packages/domain/manifest.py`.
2. **Missing means refused, never defaulted (H14).** `CrawlConfig.limits()` returns `None` unless `crawl.status == CONFIGURED` *and* all 16 names are present; a `LIVE` run then ends `ABORTED(POLICY_VIOLATION)` with the missing names in `stats.policy_violation`, before any DNS/TCP/HTTP activity. No model field has a default value (tested).
3. **Order of refusal:** the source-access gate is evaluated first (`ABORTED(GATE_CLOSED)`); only if the gate is open are the parameters checked (`ABORTED(POLICY_VIOLATION)`); then the crawler contact environment variable and the human authorisation reference. The dry-run limits object is *never* consulted for a `LIVE` run.
4. **Value validation:** every parameter must be a finite number `> 0`; booleans, strings, `NaN`, `±inf`, zero and negatives are rejected; integer-typed parameters reject fractions; `backoff_max_seconds >= backoff_base_seconds`. Consequence worth knowing: `max_redirects` cannot be `0` ("follow no redirect" is not expressible; unwanted redirects are stopped by the exact host/path allowlist, which validates every hop).
5. **No value is invented here.** The numbers used in tests (`tests/support/builders.py`) are synthetic and prove nothing about any real source. Choosing production values is a human decision in the manifest review.
6. **What each change means for versions** — two different identifiers are involved:
   - *Changing a value in the manifest* is recorded through the run's `manifest_hash` / `manifest_version` (provenance on every `ingest_run`). It does not change `safety_policy_version`.
   - *Adding, removing or re-defining a parameter, or changing how it is enforced* is a rule change: per contract §12 it bumps `safety_policy_version` (the `SAFETY_POLICY_VERSION` constant in `packages/ingestion/pipeline.py`, recorded on every run, `fetch_request` row and quarantine record) and loosening needs a written reason and a matching test change (H6).
   - **Open decision (not made here):** the constant still reads `source-safety-contract@phase-1b`, although Phase 1C introduced `max_expansion_ratio` and `pdf_eof_tail_bytes` and the closeout adds the request audit. Contract §12 therefore calls for a bump (suggested value `source-safety-contract@phase-1c`). It was *not* changed here because `tests/integration/test_pipeline_contract.py` pins the literal and editing that expectation was outside the reviewed change set. No live run has ever recorded the old value, so the bump is cheap and should be taken in a reviewed change before the first live run.
7. **Unit conventions:** seconds are real-valued; bytes and counts are integers; `max_expansion_ratio` is dimensionless.

## 2. The parameters

Retry column: *yes* = the failure it produces is retried (only `HTTP_TRANSIENT`, `TIMEOUT`, `TLS_ERROR` ever are); *no* = never retried; *shapes* = it changes retry timing or count.

| # | Parameter (unit) | Meaning | Enforced at | On violation | Retry |
|---|---|---|---|---|---|
| 1 | `min_delay_seconds` (s) | Minimum interval between **request starts** to one host; one request in flight per host. Also the floor of every backoff delay. | `HostPacer.slot` (`egress.py`), before each hop's request is opened | Not a failure: the client waits. Never skips or reorders. | **shapes** (floor under the backoff delay) |
| 2 | `connect_timeout_seconds` (s) | Bound on TCP connect **and** the TLS handshake (the handshake runs under the same socket timeout). | `_PinnedHTTPSConnection.connect` (`net.py`) | `TIMEOUT` | yes |
| 3 | `read_timeout_seconds` (s) | Bound on **each** blocking socket operation after connect: sending the request, reading status/headers, reading each body chunk. Not cumulative. | `StdlibTransport.open` sets it on the socket; body reads via `_stream` | `TIMEOUT` | yes |
| 4 | `total_timeout_seconds` (s) | Wall-clock bound on **streaming the response body** of one attempt. See §3 — it does *not* start at request start. | `EgressClient._stream`, checked before every read | `TIMEOUT` ("total timeout exceeded while reading body") | yes (the next attempt restarts the clock) |
| 5 | `max_bytes_listing` (bytes) | Size cap for `LISTING` responses. | `_finish` (declared length), `_stream` (running decoded size), `validate_response` (final size); selected by `SafetyLimits.max_bytes(purpose)` | `SIZE_EXCEEDED`, quarantine reason `SIZE_EXCEEDED`; body discarded or quarantined, never published | no |
| 6 | `max_bytes_detail_page` (bytes) | Same, for `DETAIL_PAGE`. | same | same | no |
| 7 | `max_bytes_attachment` (bytes) | Same, for `ATTACHMENT` (the only purpose the Phase 1C pipeline requests). | same | same | no |
| 8 | `max_url_length` (characters) | Longest URL accepted — both for URLs from the manifest and for redirect `Location` values. | `validate_url` (before any parsing); `_next_location` (before resolving the reference) | Manifest URL: pre-flight rejection, quarantine reason `MANIFEST_SCOPE_MISMATCH`, no request sent. Redirect: `REDIRECT_REJECTED` | no |
| 9 | `max_redirects` (count) | Number of redirects followed; `N` allows `N` redirects, i.e. `N+1` requests. Every hop is fully re-validated (URL, allowlist, DNS/IP) before it is requested. | `EgressClient._attempt` hop loop | `REDIRECT_REJECTED` ("too many redirects"); no body is read | no |
| 10 | `max_attempts` (count) | Total attempts for one fetch, including the first. `1` means never retry. | `EgressClient.fetch` retry loop | After the last allowed attempt the last error is raised; candidate ends `FAILED_TRANSIENT` | **shapes** (bounds retries) |
| 11 | `backoff_base_seconds` (s) | Base of the exponential backoff ceiling: `min(backoff_max, base · 2^(attempt−1))`. | `backoff_ceiling` / `EgressClient.fetch` | Not a failure: delay before a retry, drawn uniformly from `[0, ceiling]` (jitter from the injected RNG), then raised to at least `min_delay_seconds` and to at least the server's `Retry-After` | **shapes** |
| 12 | `backoff_max_seconds` (s) | Cap of that ceiling; must be `>= backoff_base_seconds`. | same | same | **shapes** |
| 13 | `candidate_deadline_seconds` (s) | Absolute time budget for **one `EgressClient.fetch` call**: all attempts, backoff sleeps and pacing waits. See §3 for what is and is not checked. | `EgressClient.fetch` (a retry whose sleep would pass the deadline is not taken); `_stream` (checked before every read) | `TIMEOUT` raised; no further retry | **shapes** (stops retries) |
| 14 | `circuit_breaker_threshold` (count) | Number of **consecutive** refusal responses (HTTP 401, 403, 429) after which the run stops. Any 200/304/redirect response resets the count. One counter per `EgressClient` (one run). | `_note_refusal` / `_note_success` (`egress.py`); handled in `run_ingest` | `CircuitOpenError` → the current candidate ends `FAILED_TRANSIENT(CIRCUIT_OPEN)` and the run ends `ABORTED(CIRCUIT_OPEN)`; later candidates are never requested; a human restarts | stops (no further requests at all) |
| 15 | `max_expansion_ratio` (dimensionless) | Ceiling on *decoded bytes ÷ on-the-wire bytes* for a response with `Content-Encoding` gzip/deflate: guards decompression bombs. The client requests `Accept-Encoding: identity`, so the ratio is only exercised if a server compresses anyway. | `_stream.emit`: after each decoded piece, `decoded_so_far > ratio · max(raw_so_far, 1)` | `SIZE_EXCEEDED`, quarantine reason `SIZE_EXCEEDED` | no |
| 16 | `pdf_eof_tail_bytes` (bytes) | Size of the window at the **end** of an attachment in which the `%%EOF` marker must appear (`min(size, N)` last bytes): the contract's "near the end". Too small ⇒ valid PDFs with trailing data are quarantined; too large ⇒ the "truncated download" check weakens. | `_check_pdf_bytes` (`validate.py`), `ATTACHMENT` only, after the download and before the bytes are stored as published (the hash is computed while streaming) | Quarantine reason `PDF_SANITY_FAILED` ("no %%EOF marker within the tail window") | no |

**Parameters 15 and 16** were named during Phase 1C. They implement rules [source-safety-contract](source-safety-contract.md) §6 already required in prose ("an expansion-ratio guard"; "a trailer marker (`%%EOF`) must appear near the end") but left unnamed and unbounded. They are now first-class mandatory parameters like the other fourteen.

**Purposes not yet exercised:** parameters 5 and 6 are enforced inside the egress client for any purpose, but the Phase 1C pipeline only requests `ATTACHMENT`; `LISTING` / `DETAIL_PAGE` requests arrive with Phase 1D.

## 3. Where time is actually measured (and what that implies)

The contract text says "total-per-request" timeout. The implementation is narrower, and the difference is recorded here instead of being papered over:

| Limit | Clock starts | Covers | Does **not** cover |
|---|---|---|---|
| `connect_timeout_seconds` | when the TCP connect begins | TCP connect + TLS handshake | — |
| `read_timeout_seconds` | after connect | each single socket operation (request send, header bytes, each body chunk) | the sum of operations |
| `total_timeout_seconds` | when **body streaming begins** (after the status line and headers have arrived), per attempt | reading the body | connect, TLS, request send, header wait, redirect hops, pacing waits, and the *duration of one single read* (the check runs before each read, so one read may overshoot it) |
| `candidate_deadline_seconds` | when `EgressClient.fetch` is entered | pacing waits, every attempt, backoff sleeps; *enforced* before a retry sleep and before each body read | the connect/header phase and redirect hops are not checked against it directly (each is bounded only by its own per-operation timeout) |

Consequences, stated plainly:

- A server can spend up to `connect_timeout + (per-read timeouts while sending headers)` per hop before the body clock starts, for up to `max_redirects + 1` hops. Those phases are bounded, but not by one wall-clock budget. `tests/security/test_timeout_boundary.py` pins this: 50 s of pre-body delay does not trip a 30 s `total_timeout`, while the same delay does trip the candidate deadline once the body is read.
- The `candidate_deadline_seconds` budget is **per fetch**, not per candidate in the sense of "detail page and attachment together". Phase 1C performs one fetch per candidate, so they coincide; Phase 1D's detail-page → attachment sequence will be two fetches with two budgets.
- **Is a later architectural change needed?** No new component is required. If a single hard wall-clock bound over the whole request lifecycle is wanted, it is a contained change inside `net.py`/`egress.py` (a monotonic watchdog around connect + headers + body). That would be a rule change (bump `safety_policy_version`, update these tests, state the new boundary here). It is a **recommendation for review, not implemented**, and not required to keep the current limits safe: every phase is individually bounded.
- Audit field: `fetch_request.elapsed_seconds` is the time spent holding the host's request slot (connect through end of body, summed over redirect hops, excluding pacing waits). It is a measurement, not a limit.

## 4. What is *not* a parameter (code-enforced security invariants)

These are not configurable and are not in the manifest; loosening any of them needs an ADR and a reviewed test change: HTTPS only; port 443 only; no userinfo; no fragment; exact host and path-prefix allowlist; TLS certificate and hostname verification with a TLS ≥ 1.2 floor; DNS answers validated (every address) before connecting and the connection pinned to a validated IP; private/loopback/link-local/metadata ranges refused; manual redirects only; `Accept-Encoding: identity`; refusal statuses never retried with a different identity, endpoint or address; no source-derived value reaches a file path; safe YAML loading only. Source-specific behaviour (which hosts, which path prefixes, which listing entry points) is manifest data, never code.

## 5. If the source blocks the client

A `401`, `403`, `429` or an unexpected challenge/login/redirect is a signal, not an obstacle: it is recorded as a `fetch_request` row with its status and outcome, counts toward `circuit_breaker_threshold`, and ends the candidate (and, at the threshold, the run). The client does not change identity, address, endpoint or timing strategy to get around it.

## 6. Tests that pin this document

| Claim | Test |
|---|---|
| Exactly 16 names, includes the two Phase 1C names; no field has a default | `tests/security/test_crawl_parameters_fail_closed.py` |
| Each single missing parameter refuses a live run, names the parameter, causes zero network and zero audit rows | `tests/integration/test_crawl_parameters_pipeline.py` (both repositories) |
| Value validation (zero/negative/NaN/inf/bool/string/None/fraction; backoff ordering; `max_redirects >= 1`) | `tests/security/test_crawl_parameters_fail_closed.py` |
| Where `total_timeout_seconds` and `candidate_deadline_seconds` are measured | `tests/security/test_timeout_boundary.py` |
| `elapsed_seconds` excludes pacing waits and sums redirect hops | `tests/security/test_fetch_audit_egress.py` |
| Retry only `HTTP_TRANSIENT` / `TIMEOUT` / `TLS_ERROR`; backoff monotone; `Retry-After`; circuit breaker; conditional GET | `tests/security/test_retry_and_politeness.py`, `tests/security/test_response_validation.py`, `tests/security/test_redirects.py` |
| Every refusal is audited with its status and outcome | `tests/integration/test_fetch_request_audit.py` |
