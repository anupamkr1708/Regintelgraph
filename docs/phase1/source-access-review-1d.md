# Phase 1D — Source-Access Review and Preflight (dated record)

**Date of this record:** 2026-10-04 · **Status: `PHASE_1D_BLOCKED`** · **Not legal advice (H18).**

This is a *preflight* record written at the Phase 1C closeout. Sub-stage 1D (a controlled live sample of canonical documents) has **not** started: the human source-access gate is closed, so **no request of any kind — no DNS lookup, TCP connection, robots.txt, listing, detail-page or PDF retrieval — was made to SEBI or any other real source** by the work this record describes. Nothing below is a success narrative; there is none to write.

The historical Phase 1A findings in [source-access-report](source-access-report.md) and [recon-findings](recon-findings.md) are **unchanged and still authoritative**. This record does not rewrite them and does not convert `AMBIGUOUS_REQUIRES_REVIEW` into approval. A later verified observation may upgrade only the specific field it actually verifies, in a new dated record.

**Labels (never blurred):** `HUMAN AUTHORIZATION` recorded by a named human in the manifest · `VERIFIED` read from the primary source · `OBSERVED` seen in a non-canonical copy, a tool constraint, or a pointer · `INFERRED` engineering deduction · `UNVERIFIED` not checked. `MEASURED` below means: produced by running the repository's own manifest loader on the committed file.

## 1. Gate preflight (MEASURED, committed manifest `data/manifests/sebi-mutual-funds.yaml`)

Manifest `0.1.0-draft`, file SHA-256 `6e012838cac2370e44a4c03973d350f404e2529c2ae38eadc8f0f45a6b01dd15`.

| Precondition | Required | Current value | Met? |
|---|---|---|---|
| `ingestion_authorized` | `true`, set by a human | `false` | **no** |
| `access_review.status` | not `BLOCKED` and not `AMBIGUOUS_REQUIRES_REVIEW`; the only other value in the manifest vocabulary, `APPROVED_FOR_RECONNAISSANCE`, is not ingestion approval by itself (every condition in this table must hold) | `AMBIGUOUS_REQUIRES_REVIEW` | **no** |
| `access_review.reviewed_by` | named human reviewer | not recorded (`null`) | **no** |
| `access_review.reviewed_at` | review timestamp | not recorded (`null`) | **no** |
| Human authorisation reference | recorded reference passed to the run (`authorisation_ref`); there is no such field in the manifest | absent | **no** |
| `crawl.status` | `CONFIGURED` | `NOT_CONFIGURED` | **no** |
| The 16 crawl-safety parameters ([specification](crawl-safety-parameters.md)) | all set by a human | **0 of 16 set** (all unset) | **no** |
| Crawler contact identity (`RIG_CRAWLER_CONTACT_EMAIL`) | environment variable set for the run | not set | **no** |

The loader's own `gate_closed_reasons` for this manifest are four: *`ingestion_authorized` is not true*; *`access_review.status` is AMBIGUOUS_REQUIRES_REVIEW*; *`access_review.reviewed_by` is not recorded*; *`access_review.reviewed_at` is not recorded*. The remaining rows are checked after the gate, at the policy stage ([crawl-safety-parameters](crawl-safety-parameters.md) §1), and would refuse a run independently. With the gate closed a `LIVE` run ends `ABORTED(GATE_CLOSED)` and leaves **zero** `fetch_request` rows and zero resolver/transport calls (regression-tested against this real manifest, with the real network adapters under the socket guard).

**Result: `PHASE_1D_BLOCKED`.** The manifest gate was not modified and no authorisation, reviewer, date, reference or limit was invented.

## 2. Sample scope (unchanged; not extended)

| Item | State |
|---|---|
| `SEBI-MF-MC-20260320` (Master Circular, 2026) | in manifest, tier A, `document_url` set, label `OBSERVED` |
| `SEBI-MF-MC-20240627` (Master Circular, 2024) | in manifest, tier A, `document_url` set, label `OBSERVED` |
| `SEBI-MF-CIR-20260519-MCR` (circular, 2026-05-19) | in manifest, tier B, `document_url` set, label `VERIFIED` (the one canonical PDF seen in Phase 1A) |
| "Two explicitly selected Regulation detail-page candidates" | **not designated anywhere in the repository.** The manifest has **five** tier-A regulation-family entries — `SEBI-MF-REG1996-20251101`, `SEBI-MF-REG2026-20260401`, `SEBI-MF-REG2026-LASTAMENDED-20260707` (type `regulation`), `SEBI-MF-REGAMD-20251101`, `SEBI-MF-REGAMD-20260707` (type `amendment_regulation`) — all with `document_url: null`. [source-access-report](source-access-report.md) §10 says "the two Regulations detail pages" without naming them. **Selection is left to a human; none was made here.** (An earlier statement of "four" such entries was a miscount; the loader reports five.) |

The 21 manifest candidates are: 7 tier A (2 Master Circulars, 3 `regulation`, 2 `amendment_regulation`), 7 tier B circulars, 2 tier C context items, 1 out-of-window Master Circular, 4 seeds. No document was added to the sample.

## 3. Unresolved source-access questions carried forward (nothing resolved by this record)

| # | Question | Label / origin | What would resolve it | Blocks |
|---|---|---|---|---|
| 1 | **robots.txt** for `www.sebi.gov.in` was never retrieved (sandbox egress denied, then the fetch tool refused an unseen URL). Crawl-path rules, crawl-delay and rules for generic/AI user agents are unknown. | `UNVERIFIED` (AR-1; manifest `open_items`) | A human retrieves it from an unrestricted network, stores full text and time in a review record, and records whether the listing/detail/attachment path families are permitted for the intended user agent | any live request |
| 2 | **Permission for automated retrieval.** The Website Policy text neither permits nor prohibits automated retrieval, rate or user-agent use; silence is not permission. | `VERIFIED` text, interpretation `INFERRED` (AR-1) | A human decision, possibly after contacting the source | any live request |
| 3 | **Copyright / internal storage / reproduction scope.** The Copyright Policy requires permission (by mail) for reproduction; it is undecided whether internal storage and indexing, or displaying exact quoted clause text, is covered, whether permission must precede bulk retrieval, to which address, and whether any attachment is third-party-copyright. No permission was requested by this project. | `VERIFIED` text, applicability `UNVERIFIED` (AR-2) | Human/legal decision; request permission if so decided | storage of retrieved content; product display |
| 4 | **JavaScript-driven listing pagination.** Listing pages beyond page 1 appear to require replicating JavaScript form posts, i.e. an endpoint not offered as an API. A documented, stable listing/RSS/export was not found (not exhaustively searched). Using an undocumented endpoint is forbidden; a browser-automation adapter would need its own reviewed source-access decision. | `OBSERVED` (AR-3, AR-6) | A documented machine-readable mechanism, or a reviewed manual-curation method; otherwise report the limitation, do not bypass | listing validation, pages 2–N |
| 5 | **Canonical Master Circular bytes and hashes were never seen.** Both Master Circulars were read from third-party copies, truncated by the reading tool (roughly the first 68 of 748 printed pages of the 2026 circular; roughly the first 80 of the 2024). Canonical byte hash, size, page count and whether third-party stamps exist in the originals are unknown; no byte equality with the third-party copies may be claimed. Every Phase 1A parse/date finding on these two documents is non-canonical. | `OBSERVED` / `UNVERIFIED` (AR-5) | First Phase 1D step after approval: retrieve the canonical documents under the safety contract and record hash, size, time, URL | canonical comparison; upgrading `OBSERVED` fields |
| 6 | **Detail page → attachment discovery is unresolved.** Detail pages expose attachments only through a relative viewer link (`/web/?file=<pdf url>`), confirmed for the 2026 and 2024 Master Circular pages; the viewer must never be fetched and the inner URL must be extracted and validated. No detail-page HTML has ever been observed as raw HTML, so the attachment-link element is `UNVERIFIED`. Attachment URLs must never be constructed from slugs, months, numeric ids or filename conventions; an attachment URL that cannot be observed on an approved page is `UNRESOLVED`. | `VERIFIED` (viewer link) / `UNVERIFIED` (element structure) | Observation of the approved page by an authorised retrieval | attachment retrieval for any candidate without a manifest URL, including all regulation entries |
| 7 | **Keyword listing under-enumerates.** Two cited in-window circulars were absent from listing page 1. A listing comparison must classify differences (match / missing / not-in-manifest / date, title or URL difference / unresolved) without auto-editing the manifest. | `OBSERVED` (AR-4; manifest `known_gaps`) | Reviewed listing lookups, not crawling | benchmark/coverage claims |
| 8 | **Listing and URL formats are not documented interfaces** and may change; stability of detail/attachment URLs is unknown; redirect behaviour (HTTP→HTTPS, trailing slash, case) is unverified. | `OBSERVED` / `UNVERIFIED` (AR-6) | Recorded observations; unexpected shapes are quarantined | URL-stability assumptions |
| 9 | **Absence of anti-bot controls is not established** for a rate-sensitive client: none were seen on content pages by a tool that is not representative of a bespoke client; a login CAPTCHA exists for site sign-in only. | `OBSERVED` | Behaviour of the real client under the configured limits; any challenge ⇒ stop and report | none today; the stop rules apply at run time |
| 10 | **Third-party copies are easy to mistake for canonical** (one is a two-authority bundle). | `OBSERVED` (AR-7) | Exact-host allowlist and manifest-derived authority (implemented); non-allowlisted hosts are quarantined | — |
| 11 | **Crawler contact identity** was not configured for the Phase 1A requests and is not set now. | `VERIFIED` fact (AR-8) | Human sets `RIG_CRAWLER_CONTACT_EMAIL` for the run; the value is never stored or logged | any live request |
| 12 | **Seeds** referenced inside official documents (4: `…20241220-DRAFT-SID`, `…20241231-PASSIVE`, `…20250626-REBALANCING`, `…20260226-CATEGORISATION`) have no resolved canonical page. Third-party-only availability would not be canonical authorisation, and references are not to be crawled recursively. | `UNRESOLVED` — resolution not attempted | Reviewed, bounded seed resolution after the gate opens | lineage validation |
| 13 | **Scope of borderline items** and who decides page 2–20 enumeration (by what reviewed method) remain open. | `UNVERIFIED` | Human scope decision under ADR-009 | corpus completeness |

Other access-method limitations *actually observed* so far: the Phase 1A sandbox shell could not reach `sebi.gov.in` (`host_not_allowed`) and the fetch tool only opens URLs already surfaced in the session — environment facts, no bypass was attempted; per-request timestamps were not captured for the Phase 1A requests (session window approximately 2026-10-01 20:45–21:00 UTC).

## 4. Phase 1D deliverables: explicitly *not performed* (gate closed)

Request inventory — none. Detail-page observations — none. Attachment discovery — none. Canonical URLs — none observed. Hashes — none (so nothing is upgraded from `OBSERVED` to `VERIFIED`). Redirects — none. Listing-vs-manifest comparison — not performed. Seed resolution — not performed. Quarantine events — none. Second-run idempotency on real sources — not performed. Corpus snapshot ID — none. The real-source behaviour of the ingestion boundary is therefore **unverified**; only synthetic, offline behaviour is tested.

## 5. What a human must do to unblock 1D (in order; none is done here)

1. Retrieve and store `robots.txt`; decide on automated retrieval (items 1–2).
2. Decide the copyright/storage/reproduction question, and whether to request permission (item 3).
3. Decide the listing enumeration method (items 4, 7, 13).
4. Designate the two Regulation detail-page candidates (§2).
5. Record the review in the manifest: `access_review.status`, `reviewed_by`, `reviewed_at`, then `ingestion_authorized: true` — only after 1–3.
6. Choose and record the 16 crawl-safety parameter values and set the crawler contact variable; supply an authorisation reference to the run.
7. Confirm GitHub Actions is green on the Phase 1C closeout commit, then supersede this document with a **new dated record** — do not edit this one into an approval.

## 6. Remaining risks and recommendation

Remaining risks: all of §3. Recommendation: keep `ingestion_authorized: false`; treat Phase 1C as not closed until CI is observed green; do not begin Phase 2 work; do not add anti-bot or evasion tooling under any circumstances — if the source blocks or challenges the client, the outcome is to stop and record it ([crawl-safety-parameters](crawl-safety-parameters.md) §5).
