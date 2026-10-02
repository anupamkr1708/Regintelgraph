# Phase 1A — Source Access Report (SEBI Mutual Funds)

Status: DRAFT FOR HUMAN REVIEW. This is an **engineering access assessment, not a legal opinion** (H18). It does not classify any activity as legally permitted.

**Overall classification: `AMBIGUOUS_REQUIRES_REVIEW`**
Reconnaissance of public SEBI pages was technically uneventful, but robots.txt could not be retrieved, the copyright policy requires permission for reproduction, and the website policy is silent on automated retrieval. Per the brief's stop rule, ambiguity is **not** read as permission. `ingestion_authorized` is `false` in the [manifest](../../data/manifests/sebi-mutual-funds.yaml) and Phase 1B code must refuse to fetch while it is false.

Label legend: `VERIFIED` read from the primary SEBI source in this session · `OBSERVED` seen via non-canonical copy / search excerpt / pointer · `INFERRED` engineering deduction · `UNVERIFIED` not checked. Evidence detail: [recon-findings](recon-findings.md).

## 1. Official source pages

| Role | URL | Status |
|---|---|---|
| Legal → Master Circulars listing ("Updated List"; separate "Historical Data" mode not opened) | `https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=6` | `VERIFIED` fetched; 134 records over 6 pages; page 1 read. A Sep-2026 entry on page 1 shows the listing is **current** |
| Title-search listing for the Mutual Funds Master Circular series | `…/HomeAction.do?doListingAll=yes&search=Circular+For+Mutual+Funds` | `VERIFIED` fetched; 15/15 records on one page; includes the full 2011–2026 series |
| Keyword listing "Mutual Funds" (window enumeration) | `…/HomeAction.do?doListingAll=yes&search=Mutual+Funds` | `VERIFIED` fetched; 486 records, 20 pages; page 1 read (current to Aug 2026) |
| Master Circular for Mutual Funds — current (2026-03-20) | `https://www.sebi.gov.in/legal/master-circulars/mar-2026/master-circular-for-mutual-funds_100491.html` | `VERIFIED` |
| Master Circular for Mutual Funds — predecessor (2024-06-27) | `…/legal/master-circulars/jun-2024/master-circular-for-mutual-funds_84441.html` | `VERIFIED` |
| SEBI (Mutual Funds) Regulations, 2026 | `…/legal/regulations/apr-2026/securities-and-exchange-board-of-india-mutual-funds-regulations-2026_100744.html` | `VERIFIED` as a listing URL; page not fetched |
| SEBI (Mutual Funds) Regulations, 2026 [last amended July 7, 2026] | `…/legal/regulations/jul-2026/…-last-amended-on-july-7-2026-_102780.html` | `VERIFIED` as a listing URL; not fetched |
| SEBI (Mutual Funds) Regulations, 1996 [last amended Nov 1, 2025] | `…/legal/regulations/nov-2025/…-1996-last-amended-on-november-01-2025-_99120.html` | `VERIFIED` as a listing URL; not fetched |
| Website Policy (terms of use, copyright, privacy, linking) | `https://www.sebi.gov.in/website-policy.html` | `VERIFIED` fetched |

"Current regulation" answer: the listing shows **two regimes** in the window — the 1996 Regulations (consolidated page last amended 2025-11-01) and the 2026 Regulations (listed 2026-04-01, consolidated page updated 2026-07-07). Which is "current" on a given date is exactly the temporal question the system must answer; the Master Circular itself states the 2026 Regulations come into force on April 01, 2026 (`OBSERVED`, third-party copy; recon-findings R1).

Full candidate inventory: the manifest (21 entries; 16 with listing-verified canonical detail URLs). Listing status: all three listing URLs are query-string endpoints — **stability `UNVERIFIED`** (not documented as stable interfaces).

## 2. Access findings

| Finding | Label |
|---|---|
| Listing and detail pages returned HTML without any challenge, login wall or CAPTCHA on the content pages fetched (a login/CAPTCHA widget exists for site sign-in only) | `OBSERVED` (the fetch tool is not representative of a bespoke client; absence of anti-bot controls is not established for a rate-sensitive crawler) |
| One canonical PDF (2026-05-19 circular) was retrievable as `application/pdf` from `www.sebi.gov.in` | `VERIFIED` |
| Pagination of listings is driven by JavaScript functions (`searchFormNewsList(...)`, form posts), not by documented links | `OBSERVED` |
| The sandbox shell cannot reach `sebi.gov.in` (egress proxy denial `host_not_allowed`); the fetch tool can, but only for URLs already surfaced in the session | `VERIFIED` (environment fact; no bypass attempted) |
| Technical appropriateness of automated retrieval for **listing page 1 / detail pages / attachment PDFs by URL** | `INFERRED` plausible (static, public, ordinary GET) — pending the policy items below |
| Technical appropriateness of enumerating **listing pages 2…N** | `UNVERIFIED` and **not recommended** until reviewed: it appears to require replicating JavaScript form posts, i.e. an endpoint not offered as an API — the brief forbids using undocumented private endpoints |
| Attachment links go through a viewer wrapper (`/web/?file=<pdf url>`) | `OBSERVED` — the viewer must never be fetched; the inner URL is extracted and validated (source-safety-contract §3) |

Brief items A–I:

| Brief item | Result | Label |
|---|---|---|
| A canonical SEBI source pages | §1 | `VERIFIED` |
| B robots.txt | §3 — **not retrieved** | `UNVERIFIED` |
| C website policy / terms | §4 | `VERIFIED` |
| D copyright / reproduction | §5 | `VERIFIED` (text); applicability to our use `UNVERIFIED` |
| E direct PDF links | exist on detail pages as viewer links; attachment host paths `/sebi_data/attachdocs/` and `/sebi_data/commondocs/` | `OBSERVED` |
| F canonical URL redirects | none observed on the fetched pages (final URL equalled requested URL) | `OBSERVED` |
| G anti-bot mechanisms | none observed on content pages; login CAPTCHA only | `OBSERVED` |
| H automated retrieval technically appropriate | see table above | `INFERRED` / `UNVERIFIED` |
| I documents linked from stable official pages | detail pages have per-document URLs (month-year path + numeric id) that appear stable in form; **no stability guarantee found** | `OBSERVED` / `UNVERIFIED` |

## 3. robots.txt findings

- **Not retrieved.** The sandbox shell was blocked by the egress proxy before the request reached SEBI, and the fetch tool refused the URL because it had not appeared in a prior result; searches for it returned other sites' robots files. I did not attempt to reconstruct the URL, use a proxy, or read a cached copy as authority.
- Consequence: crawl-path restrictions, crawl-delay, and any rules for generic or AI user agents are **`UNVERIFIED`**. Under the brief this is an ambiguity → stop and report.
- Human action required (cannot be done from this environment): retrieve `https://www.sebi.gov.in/robots.txt` from an unrestricted network, store its full text and retrieval time in the review record, and record whether the listing, detail and attachment path families in the manifest are allowed for the intended user agent.

## 4. Website policy findings (`VERIFIED` text; interpretation `INFERRED`)

The Website Policy page (Terms of Use, Copyright, Privacy, Linking) was read in full.

- **Terms of Use:** content is stated not to be a statement of law or to be used for legal purposes; users are told to verify against print versions and to obtain professional advice; SEBI disclaims responsibility for accuracy and completeness; Indian law and courts govern.
- **Automated access:** the policy text contains **no statement permitting or prohibiting automated retrieval, scraping, rate, or user-agent use.** Silence is neither permission nor prohibition (`INFERRED`).
- **Privacy:** the site logs IP address, browser, pages visited. (An identifying crawler User-Agent is therefore also a transparency measure.)
- **Linking:** direct linking is welcome; framing is not permitted. Not a constraint on storing documents, but relevant to the UI (open source documents in a new window; never embed SEBI pages in frames).
- **Implication for H18/product positioning (`INFERRED`):** SEBI's own caution that web copies are not authoritative aligns with the product rule that outputs are decision support, cite the source URL, and surface the "verify against official print/gazette copy" caveat in the evidence viewer.

## 5. Copyright / reproduction findings (`VERIFIED` text; applicability `UNVERIFIED`)

- The Copyright Policy says material may be reproduced free of charge **after taking proper permission by sending a mail**; reproduction must be accurate and not misleading or derogatory; the **source must be prominently acknowledged** where the material is published or issued to others; third-party-copyright material is excluded from that permission.
- **Gaps (all `UNVERIFIED`, human/legal review items — not decided here):**
  1. Whether **internal storage and indexing** of public circulars is "reproduction" under this policy.
  2. Whether **displaying exact quoted clause text** to product users (the evidence viewer, [evidence-model](../evidence-model.md) §3) needs the permission described.
  3. Whether the permission mail must be sent before any bulk retrieval, and to which address (the address was not captured).
  4. Whether any listed attachments are marked as third-party-copyright (not checked).
- Engineering posture until resolved: nothing is committed to git beyond manifests and analysis (already required by AGENTS.md "Data handling"); quotations in docs are limited to short fragments with source and locator.
- I did **not** send, draft-send, or contact SEBI. Whether and how to request permission is a human decision.

## 6. Direct-document access findings

| Item | Finding | Label |
|---|---|---|
| Detail pages expose attachments only through a relative viewer link | confirmed for the 2026 and 2024 Master Circular pages | `VERIFIED` |
| Canonical Master Circular PDFs fetched | **No.** Tool constraints (§3 of recon-findings) prevented it; the two MCs were read from third-party copies | `VERIFIED` (limitation) |
| Canonical PDF fetched | one: 2026-05-19 circular, `application/pdf` | `VERIFIED` |
| Third-party mirrors of SEBI documents exist (AMC sites, depository communiques, aggregators) and one is a **bundle** with another authority's cover page | sampled | `OBSERVED` — these are non-canonical and must be quarantined if ever fetched by the ingestion service (security T-04) |
| Content hashes / byte sizes of any canonical Master Circular | not obtained | `UNVERIFIED` |

## 7. Canonical URL findings

- **Detail-page URLs** follow `/legal/<section>/<mon-yyyy>/<slug>_<numeric id>.html` (`OBSERVED`, 20+ examples, 4 section families: master-circulars, circulars, regulations, reports-and-statistics). The month in the path matches the listing date in every case checked.
- **Attachment URLs** use numeric filenames that decode as epoch-millisecond upload times matching the listing dates (`INFERRED`, three documents); they are not semantic identifiers.
- **Redirects:** none observed (`OBSERVED`); behaviour for HTTP→HTTPS, trailing slashes and case is `UNVERIFIED`.
- **Identity:** nothing here changes the Stage-1 rule: identity = manifest `document_key` (or anchored `source_reference`), URLs and filenames are attributes ([ingestion-design](../ingestion-design.md) §4).
- **Time-varying listing content:** the keyword listing's record count and ordering change over time (`INFERRED` from its daily-updated nature); a listing diff is an input to incremental ingestion, never an identity.

## 8. Risks

| ID | Risk | Evidence | Mitigation / owner |
|---|---|---|---|
| AR-1 | Unknown robots rules / no stated policy on automation → possible non-compliance | §3, §4 | Human retrieves robots; decide; manifest gate fail-closed |
| AR-2 | Copyright policy requires permission; unclear scope vs storage and quotation | §5 | Human/legal decision; request permission if advised; limit committed excerpts |
| AR-3 | Only JavaScript-driven pagination → temptation to call undocumented endpoints | §2 | Do not enumerate beyond page 1 without review; consider department-filtered listing or manual manifest curation (ingestion-design already allows a manual manifest fallback, RK-01) |
| AR-4 | Title-keyword listing under-enumerates (two cited in-window circulars missing from page 1) | manifest `known_gaps` | Seed-resolution step in Phase 1B using reviewed listing lookups, not crawling |
| AR-5 | Canonical Master Circular bytes not yet seen → every parse/date finding is non-canonical | recon-findings §0 | First Phase 1B step after access approval: fetch 3 canonical documents under the safety contract, re-run the sample |
| AR-6 | Listing/URL formats are not documented interfaces and may change | §1 | Recorded-HTTP fixtures; `QuarantineRecord` on unexpected shapes |
| AR-7 | Third-party copies are easy to mistake for canonical (one is a two-authority bundle) | §6 | Exact-host allowlist; `authority` from manifest; quarantine non-allowlisted hosts |
| AR-8 | Our own recon requests carried no configured contact identity | `.env.example` has `RIG_CRAWLER_CONTACT_EMAIL` unset | Set before any live fetch (manifest `crawl.user_agent_contact`) |

## 9. Unresolved questions

1. What does SEBI's robots.txt say, for which user agents and path families? (`UNVERIFIED`)
2. Is internal storage/indexing, and display of exact clause quotations, within the copyright policy as written, or is written permission needed first? Who sends the request, and to what address?
3. Is programmatic retrieval of listing page 1 plus per-document pages acceptable, and is any rate/identification expectation published elsewhere (e.g. a terms page not reached from this session)?
4. Is there a documented, stable SEBI listing/RSS/export that avoids JavaScript form replication? (Not found; not exhaustively searched.)
5. What are the canonical bytes, sizes, and page counts of the two Master Circulars, and do the third-party stamps ("Confidential", "Page N of 748") exist in them?
6. Notification numbers and exact titles of the Regulations (identity inputs) — not read.
7. Scope decision for borderline items (e.g. the Investment Advisers/Research Analysts circular that concerns MF schemes) under ADR-009's no-second-corpus rule.
8. Page 2–20 enumeration of in-window items: by what reviewed method?

## 10. Recommendation

1. **Do not start Phase 1B code that fetches.** Resolve §9 items 1–3 first (human actions: robots.txt retrieval, copyright-policy decision/permission, enumeration-method decision). Fill `access_review.reviewed_by/at` and flip `ingestion_authorized` only after that.
2. **Keep Mutual Funds as the domain.** No material corpus failure was found; the Investment Advisers fallback in ADR-009 is **not** triggered (recon-findings §6).
3. **Implement Phase 1B contract-first and offline** (ingestion-contract, source-safety-contract; recorded-HTTP fixtures; fake resolver), with the live fetch behind the manifest gate. The proposal is in the final report of this phase.
4. **First live step, once approved:** fetch only the three sampled canonical documents plus the two Regulations detail pages, at the configured delay, record hashes, and re-run the §5 parse-quality sample on canonical bytes before any wider window fetch.
5. **Keep all Stage-1 ADRs unchanged.** The findings in recon-findings §8 are later-phase gaps (temporal date kinds, a consolidation relation, regulator-published consolidated pages, footnote nodes), none blocking.
