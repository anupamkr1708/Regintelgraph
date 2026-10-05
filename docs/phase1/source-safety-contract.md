# Phase 1 — Source Safety Contract (no implementation)

Status: **ACCEPTED as the Phase 1C safety contract**; implemented and tested offline (`packages/ingestion/{urlpolicy,ipcheck,egress,validate,blobstore,logsafe,net}.py`, `tests/security/`) — see [ingestion-contract](ingestion-contract.md) §9 for deviations and what is not implemented. This contract implements, for the ingestion path, the controls already decided in [security](../security.md) (T-04 impersonation, T-05 malformed PDFs, T-06 path traversal, T-07 SSRF, T-08 malicious URLs/redirects, T-14 oversize, T-16 secret leakage) and [ingestion-design](../ingestion-design.md) §5 ("Politeness & safety"). It introduces **no new service, dependency or architectural decision** (H5, H15). Companion: [ingestion-contract](ingestion-contract.md).

**Numeric limits are deliberately not set here (H14).** Every numeric parameter below (timeouts, size caps, delays, retry counts, redirect limit, circuit-breaker threshold) is read from the manifest `crawl` block. **If any required parameter is unset, the fetcher fails closed** (`ABORTED(POLICY_VIOLATION)`); there are no code defaults. Values are chosen by a human after the access review and recorded in the manifest.

Trust model: **everything crossing the egress boundary is untrusted data** — response headers, HTML, PDF bytes, URLs found in HTML (H4). Nothing fetched can change policy, allowlists, paths, or code.

## 1. One egress path

- All outbound HTTP goes through a single `EgressClient` (the only module allowed to open sockets; enforced by an import check and a test that scans for other network libraries in `packages/ingestion`). Tests inject a fake transport and fake resolver; **a test that touches the real network fails** ([testing-strategy](../testing-strategy.md) §4.5).
- Environment proxy variables and system certificate overrides are ignored unless set explicitly in configuration; no automatic proxy discovery.
- Allowed methods: `GET`, `HEAD`. No request bodies, no cookies, no authentication headers, no form submission, no uploads, no calls that mutate the source. (Brief §3.)

## 2. Host allowlist (T-04, T-07)

- The allowlist is the manifest `allowed_hosts`: **exact hostnames, no wildcards, no suffix matching**; currently `www.sebi.gov.in` only.
- Comparison is on the hostname after lowercasing, IDNA/punycode normalisation and removal of a trailing dot; hostnames containing non-ASCII after normalisation are rejected.
- Port: **443 only** (explicit `:443` tolerated; any other port rejected).
- The allowlist is applied to the **initial URL, every redirect hop, and every URL extracted from a page**. A host that is not on the list is never contacted.
- Third-party hosts that mirror SEBI documents are **not** allowlisted. Authority comes from the manifest and the allowlisted host, never from document text (T-04). A document that presents another authority's cover page or is a bundle of two authorities' documents is quarantined `AUTHORITY_MISMATCH` (observed in Phase 1A: a depository communique prepended to a SEBI circular — [recon-findings](recon-findings.md) §5).

## 3. URL validation (T-06, T-08)

Applied before any DNS lookup, to every candidate URL, with unit tests for each rejected class:

| Rule | Reject when |
|---|---|
| Scheme | not `https` (no `http`, `file`, `ftp`, `data`, `javascript`, `gopher`, protocol-relative) |
| Userinfo | contains `user:pass@` |
| Host | not exactly allowlisted; IP literals (v4, v6, decimal/octal/hex forms); empty |
| Path | contains `..` segments after normalisation, encoded slashes/backslashes (`%2f`, `%5c`), double-encoding, control characters, or does not start with an allowed `path_prefixes` family for its `purpose` (manifest) |
| Query | present on non-listing purposes; on listing purpose, parameters outside the manifest's reviewed list |
| Fragment | stripped before use, never sent |
| Length | exceeds the manifest URL length limit |
| Viewer wrapper | path is the SEBI viewer form (`/web/` with a `file=` parameter): **never fetched**. The inner `file=` value is extracted *as data*, validated through this table as a new URL, and only then planned as an `ATTACHMENT` fetch. A mismatch between the wrapper's host and the inner host is a rejection (`VIEWER_WRAPPER_URL`) |

Relative links found in HTML are resolved against the **page's validated final URL** and then validated as above; they are never trusted because of where they were found. **No URL taken from a document body is ever fetched**; only the single attachment link expected from a manifest-listed detail page is eligible, and only after validation. (Phase 1A saw the viewer link only in the fetch tool's markdown rendering; the raw HTML element that carries it is `UNVERIFIED` and must be observed on the first approved fetch.)

## 4. DNS and IP validation; private-network blocking (T-07)

1. Resolve the hostname with an injectable `Resolver`; collect **all** A/AAAA answers.
2. **Every** answer must be a globally routable address. Reject if any answer falls in: loopback; private (RFC 1918); link-local (incl. cloud metadata addresses); CGNAT shared space; unspecified; multicast; reserved/documentation ranges; IPv6 unique-local; IPv4-mapped/compatible/translated forms of any of the above; or any address the standard library classifies as non-global. (Fail if *any* record is bad — no "pick the good one" behaviour.)
3. **Pin the connection to the validated IP** (connect to the address, send the original hostname for SNI and `Host`), so a second resolution cannot rebind to an internal address. Tests include a DNS-rebinding sequence (public then private on the second lookup).
4. Re-run steps 1–3 for **every redirect hop**.
5. TLS: certificate and hostname verification always on; no downgrade; no `verify=False` code path exists, even behind a flag.

## 5. Redirect validation (T-07, T-08)

- Redirects are followed **manually**, never by library default. Maximum hops: manifest value (unset ⇒ fail closed).
- Each hop: parse `Location`, resolve relative to the current URL, run §2–§4 (host allowlist, URL rules, DNS/IP), and enforce the scheme (`https` only; an `https→http` hop is rejected).
- A redirect to a non-allowlisted host, to an IP literal, or into a loop is `REDIRECT_REJECTED`/quarantine `REDIRECT_OFF_ALLOWLIST`.
- The redirect chain is stored in the request log; `canonical_url` for a version is the **final validated URL** ([ingestion-design](../ingestion-design.md) §2). Phase 1A saw no redirects on the fetched pages; behaviour for other cases is unverified, which is why this is tested with fixtures.

## 6. Response limits, content-type and magic-byte verification (T-05, T-14)

**Timeouts** (manifest): connect, per-read, and total. Exceeding any ⇒ `TIMEOUT` (retryable). **Measurement boundaries as implemented:** `connect_timeout_seconds` covers TCP connect and the TLS handshake; `read_timeout_seconds` bounds each single socket operation; `total_timeout_seconds` is measured from the **start of body streaming** (not from the start of the request) and is checked between reads; the per-fetch `candidate_deadline_seconds` budget additionally covers pacing waits, attempts and backoff. The precise table, the consequences and the recommendation for review are in [crawl-safety-parameters](crawl-safety-parameters.md) §3. This is the authoritative statement of the semantics; "total-per-request" in earlier drafts meant body streaming, not the whole request lifecycle.

**Size:**
- Maximum bytes per `purpose` from the manifest (`LISTING`, `DETAIL_PAGE`, `ATTACHMENT`).
- If `Content-Length` exceeds the cap, abort before reading the body.
- **Never trust the header:** count bytes while streaming and abort the moment the cap is exceeded (`SIZE_EXCEEDED`; discard, do not store).
- If `Content-Encoding` is present, decompress with a hard cap on *decompressed* bytes (and an expansion-ratio guard, `max_expansion_ratio`) to defeat decompression bombs.
- Reject responses that disagree with a present `Content-Length` after full read (truncation) ⇒ `PDF_SANITY_FAILED`/`SIZE_EXCEEDED` as appropriate.

**Content-type (header):**
- `LISTING`, `DETAIL_PAGE`: `text/html`.
- `ATTACHMENT`: `application/pdf`. Other types (including `application/octet-stream`, archives, office files) are rejected by default; widening the set needs a manifest review. No archive extraction exists in Phase 1B.
- Mismatch ⇒ quarantine `CONTENT_TYPE_MISMATCH`.

**PDF magic bytes and sanity (attachments):**
- The first bytes must be the `%PDF-` signature at offset 0 (stricter than the lenient 1024-byte rule; fail closed). Failure ⇒ `MAGIC_BYTES_MISMATCH`. (The header is checked independently of the declared content type: a PDF served as HTML, and HTML served as PDF, are both caught.)
- A trailer marker (`%%EOF`) must appear near the end — within the last `pdf_eof_tail_bytes` bytes (truncated downloads ⇒ `PDF_SANITY_FAILED`).
- Bounded byte-level probe, **no rendering, no JavaScript, no embedded-file extraction, no execution**: flag the presence of encryption and of the names for JavaScript, launch actions, open-actions and embedded files ⇒ quarantine `PDF_SANITY_FAILED` with the specific flag; a reviewer may release. A byte-level token scan can miss objects inside compressed object streams; it is a coarse gate, **not** a safety proof. The real defence is the sandboxed parse in Phase 3 (T-05, OD-14), which is out of scope here.
- Page-count and structure checks are **not** done at ingestion (parsing is later).

**HTML (listing/detail):** treated as untrusted. Extraction is by a strict, bounded procedure (no script execution, no resource loading, no following links); text from HTML is never placed in prompts, SQL, paths or URLs except as validated URL candidates per §3.

## 7. Rate limiting, politeness, retries

- All numeric limits in this section are the 16 mandatory manifest parameters specified in [crawl-safety-parameters](crawl-safety-parameters.md); an unset parameter refuses a `LIVE` run and is never defaulted.
- **One in-flight request per host** (concurrency 1) and a **minimum delay between request starts** from the manifest (`crawl.min_delay_seconds`). A token-bucket/clock interface is injected so tests run without sleeping.
- Honour `Retry-After` for 429/503; a longer server-specified wait overrides the configured delay.
- **Identification:** a descriptive User-Agent naming the project plus a contact reference taken from `RIG_CRAWLER_CONTACT_EMAIL` (unset ⇒ `LIVE` runs refuse to start). No browser-UA spoofing, no header tricks, no IP/UA rotation.
- **Do not defeat blocks.** `403`, `429`, challenge pages or login redirects are *signals*, not obstacles: no retry with altered identity, no alternative endpoints. They are recorded and counted toward the **circuit breaker**: after the manifest-configured number of consecutive refusals the run ends `ABORTED(CIRCUIT_OPEN)` and requires a human restart.
- **Retry rules:** retry only `HTTP_TRANSIENT` (429/5xx), `TIMEOUT`, `TLS_ERROR`; attempts bounded by the manifest; **exponential backoff with full jitter**, never below the configured minimum delay and never shorter than `Retry-After`; a total per-candidate deadline applies. `HTTP_PERMANENT` (e.g. 404/410, and 403 unless the manifest says otherwise), and any `*_REJECTED`, are not retried. Poison candidates end `FAILED_*` for manual review.
- **Conditional requests:** use stored validators (`ETag`, `Last-Modified`) to avoid re-downloading unchanged content; a 304 yields `UNCHANGED_SKIPPED`.
- **No enumeration beyond the manifest:** the fetcher visits only candidate-derived requests and the manifest's reviewed listing entry points. It does not paginate listings or discover documents by crawling unless a later reviewed manifest explicitly adds a method (see [source-access-report](source-access-report.md) AR-3).

## 8. Quarantine behaviour

- Any validation failure produces a `QuarantineRecord` ([ingestion-contract](ingestion-contract.md) §3.4) with the **first** failing reason code, and stops that candidate; other candidates continue.
- Quarantined bytes (only if fully received and within the cap) are stored in a **quarantine directory separate from the content-addressed store**, hash-named, owner-only, **no execute bit**. They are never indexed, parsed, or returned by retrieval.
- A version cannot be published while a quarantine record exists for its content hash (DB constraint + publish-gate check, G4).
- Quarantine is append-only. Release is a new, reviewed record plus an explicit manifest override with reviewer and reason (T-09: manual overrides logged).
- Quarantine counts by reason code are part of every ingest report; **any `VERIFY_AFTER_WRITE_FAILED` or hash-collision event is a P1 alert**.

## 9. Checksum generation

- `content_hash` = sha256 of the exact received (decoded per `Content-Encoding`) bytes, computed incrementally while streaming into a temp file and re-verified by re-reading after the final write.
- Hash is lowercase hex, 64 characters, validated by a strict pattern **before it is used to build any path**.
- Hash is computed **after** size and content checks but over the whole body; a quarantined body still gets a hash recorded on the quarantine record when fully received.
- Hash algorithm is fixed by the contract; a change is a versioned migration, not a config toggle.

## 10. No arbitrary file writes; no symlink escapes (T-06)

- Writes occur **only** under two configured roots: `RIG_BLOB_ROOT` (published raw store) and the quarantine root. Both are validated at startup: exist, are directories, owned by the process user, not world-writable, and not symlinks.
- Destination paths are derived **only** from the validated hash (e.g. fan-out by hash prefix). **No source-supplied value ever contributes to a path**: not the URL, title, `Content-Disposition` filename, or any text from a document.
- Before every write: resolve the real path and assert it is inside the root (`commonpath`); `lstat` every component and **reject if any component is a symlink**; open with no-follow semantics where the platform supports it.
- Write procedure: create a temp file in the destination directory with exclusive creation and owner-only permissions, write, flush + fsync, verify the hash, then **atomically rename without replacing** an existing file (existing file with identical hash ⇒ no-op; existing file with different bytes ⇒ corruption alarm, never overwritten).
- No deletion of blobs by ingestion (garbage collection, if ever needed, is a separate reviewed job). No archive extraction. No executable bit on any stored file.
- Tests (T-06): `../`, absolute paths, symlinked root, symlinked fan-out directory, pre-existing different-bytes blob, concurrent writers of the same hash.

## 11. Logging restrictions (T-16)

- **Never log:** secrets, tokens, the contact email value, cookies, authorization headers, full request/response header sets, response bodies, document text excerpts, or userinfo.
- Log an **allowlisted header subset** only (`content-type`, `content-length`, `content-encoding`, `etag`, `last-modified`, `retry-after`, `location` after validation). The same allowlist is enforced by a database CHECK on `fetch_request.selected_headers`.
- URLs are logged after stripping userinfo and fragments; query strings are logged only for the reviewed listing parameters.
- Anything derived from remote content that reaches a log line (URLs, header values, error text) is **neutralised**: control characters and newlines escaped (no log injection), length-bounded, and treated as data. Exception messages from libraries are sanitised before logging.
- Structured logs carry `request_id`, `ingest_run_id`, `candidate_key`, `policy_ref`. Log files live under the git-ignored `/logs/` and are never committed (H12).

**Three kinds of record, three rules.** *Canonical/provenance data* (immutable database rows: `raw_artifact`, `document_version_location`, `quarantine_record`, `fetch_request`) keeps real request facts — an exact validated URL is provenance — but never persists unvalidated input verbatim, secrets, bodies or contact identity ([ingestion-contract](ingestion-contract.md) §3.4, *`requested_url` policy*). *Operational logging* is stricter again: neutralised, length-bounded, redacted, query-stripped, ephemeral and git-ignored. *Security telemetry* (alerts, quarantine counts by reason, circuit-breaker events) carries reason codes and counts only, never content.

## 12. Policy versioning and provenance

`safety_policy_version` is recorded on every `IngestRun`, request-log row and quarantine record. Changing any rule above bumps the version; loosening a rule requires a reviewed change with a written reason (H6) and a matching test change.

## 13. Required security tests (non-skippable; offline)

| Area | Fixtures / assertions |
|---|---|
| Host/scheme/URL | wrong host, lookalike host, userinfo trick, IP literals (all encodings), non-443 port, `file://`, `javascript:`, `data:`, encoded traversal, overlong URL, viewer-wrapper with mismatched inner host |
| DNS/IP | private, loopback, link-local, metadata address, CGNAT, IPv6 ULA, IPv4-mapped; **mixed good+bad answers**; **rebinding sequence**; resolver exception |
| Redirects | off-allowlist, `https→http`, loop, relative redirect, over-limit, redirect to private IP |
| Size/time | `Content-Length` over cap; unlimited stream; slow-loris (read timeout); decompression bomb; truncated body vs length |
| Type/magic | PDF served as HTML; HTML served as PDF; zero-byte; `%PDF` after offset 0; missing trailer; encrypted PDF; JavaScript/launch/embedded-file tokens |
| Authority | document claiming another authority; bundle with foreign first page |
| Rate/retry | min-delay respected with fake clock; `Retry-After`; backoff monotonicity; 403/429 not retried with altered identity; circuit breaker; unset parameter ⇒ fail closed |
| Files | traversal, symlink root/fan-out, different-bytes collision, crash between write and rename |
| Logging | secrets/contact email/bodies absent; control-char and newline neutralisation |
| Gate | `ingestion_authorized: false` ⇒ zero network calls |

The suite counts are ratcheted ([testing-strategy](../testing-strategy.md) §4.3).
