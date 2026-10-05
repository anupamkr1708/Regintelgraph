# Security policy

RegIntelGraph ingests untrusted public regulatory documents and turns them into structured, evidence-linked records. Its security posture is "untrusted source content, trusted control plane": see [docs/security.md](docs/security.md) and the enforceable rules in [docs/phase1/source-safety-contract.md](docs/phase1/source-safety-contract.md). This project is regulatory *intelligence and decision support*; nothing here, including this policy, is legal advice or a determination of legal compliance.

## Supported versions

The project is pre-release. Only the `main` branch is supported; there are no released versions.

## Reporting a vulnerability

- **Do not open a public issue or pull request containing exploit details, credentials, tokens, keys, personal data or any regulatory source file.**
- Preferred route: GitHub's **private vulnerability reporting** (repository *Security* tab → *Report a vulnerability*), if it is enabled for the repository. Whether it is enabled is a repository setting that this file cannot guarantee.
- If that option is not available: open a public issue that says only *"security contact requested"* — **no technical detail** — and the maintainer will arrange a private channel. No dedicated security e-mail address is published in this repository.
- Please include: affected file/commit, what an attacker could do, a minimal reproduction using synthetic data, and whether any secret may already have leaked.
- This is a small research project: reports are handled on a best-effort basis and no response-time or fix-time commitment is made. Please allow reasonable time before public disclosure (responsible disclosure) and coordinate the date with the maintainer.

## What is in scope

Anything that weakens a documented safety property, for example: bypass of the source-access gate or the host/path allowlist (SSRF-style egress), TLS or private-network protections; redirect handling; path traversal or symlink escape in the blob/quarantine stores; SQL injection or unsafe dynamic SQL; unsafe YAML or deserialisation; secret, cookie, contact-identity or response-body leakage into logs, records or Git; log injection; a way to modify or delete an immutable provenance record; a way to publish quarantined content; or tests that quietly stop checking one of these.

## Source-access authorization

Live retrieval from any real source is **disabled by default and gated by a human decision** recorded in the source manifest (`ingestion_authorized`, a reviewed `access_review`, a recorded authorisation reference and a fully configured set of crawl-safety parameters — see [crawl-safety-parameters](docs/phase1/crawl-safety-parameters.md)). Until that gate is deliberately opened by a human, the system makes no DNS, TCP or HTTP request to any source, and tests are required to prove it. The current state and the open questions (robots.txt, permission for automated retrieval, copyright and storage scope) are recorded in [source-access-review-1d](docs/phase1/source-access-review-1d.md).

- Do not open the gate, invent an authorisation reference or reviewer, or set crawl limits on someone else's behalf.
- Do not run any retrieval against a real source from a test, a script or a CI job.
- **No access-control or anti-bot evasion, ever:** no stealth browsers, proxy or IP rotation, CAPTCHA or challenge solving, fingerprint spoofing, browser-identity impersonation, alternate endpoints chosen to avoid a control, or rate-limit circumvention. If a source blocks, challenges or rate-limits the client, the correct behaviour is to stop, record the outcome and ask a human. Contributions that add evasion will be rejected.

## Provenance and integrity expectations

Raw artifacts are content-addressed (SHA-256) and immutable; derived records keep the identifiers and versions they came from; request attempts, quarantine events and ingest runs are append-only. Please do not edit these tables by hand, rewrite published Git history, or "repair" a record in place — add a new, reviewed record instead. Anything that breaks traceability from a stored version back to its run, manifest, code version and request attempts is a security-relevant bug.

## Handling sensitive and untrusted source artifacts

- Raw regulatory files (PDFs, HTML) are **never committed to Git** and never used as repository fixtures; tests use small synthetic documents only.
- Downloaded files are untrusted bytes. Quarantined files in particular may be malformed or hostile: keep them in the quarantine directory, do not open them in a normal viewer, and do not copy them elsewhere. Text taken from a document is data and never instructions.
- Never put secrets in the repository, issues, pull requests, logs or test output. `.env` is git-ignored; `.env.example` holds placeholders only. The contact identity used by the crawler is read from an environment variable and is never stored or logged. If you believe a secret was committed, report it privately and treat it as compromised.
