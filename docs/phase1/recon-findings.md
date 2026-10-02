# Phase 1A — Reconnaissance Findings (SEBI Mutual Funds)

Status: DRAFT FOR HUMAN REVIEW. Read-only reconnaissance + a three-document parse-quality sample. Not legal advice (H18). No raw documents are stored in this repository; this file contains analysis, locators and (at most) one short quoted fragment per source document.

Companion documents: [source-access-report](source-access-report.md) · [manifest](../../data/manifests/sebi-mutual-funds.yaml) · [ingestion-contract](ingestion-contract.md) · [source-safety-contract](source-safety-contract.md).

**Labels (never blurred):** `VERIFIED` read from the primary SEBI artifact in this session · `OBSERVED` seen in a non-canonical copy, a search-index excerpt, a third-party summary, or as a pointer inside another document · `INFERRED` engineering deduction (reasoning given) · `UNVERIFIED` not checked. Every relationship below separates **OBSERVED_SOURCE_TEXT** from **ENGINEERING_INTERPRETATION**.

## 0. Provenance and limits of this sample (read first)

1. **The two Master Circulars were NOT read from canonical SEBI bytes.** The fetch tool in this environment only opens URLs that appeared in earlier search/fetch results. The Master Circular PDF URLs appear on SEBI's detail pages only as relative viewer links, so they could not be fetched directly; the sandbox shell cannot reach `sebi.gov.in` at all (egress proxy: `host_not_allowed`). I did not work around either control. The text of the two Master Circulars was therefore read from **third-party-hosted copies** (an AMC-hosted copy of the 2024 circular; a depository-communique bundle containing the 2026 circular). Findings from those copies are `OBSERVED`, and any statement that they equal the canonical file is `UNVERIFIED`.
2. The **2026-05-19 circular was read from the canonical host** (`www.sebi.gov.in`, `application/pdf`) and is the only `VERIFIED` document text.
3. Extraction was performed by the fetch tool's PDF text extractor, **not** by a candidate parser from OD-03. No PDF bytes were available in the sandbox: no checksum, no page-image inspection, no font/image-layer inspection. Every "born-digital / no OCR" statement is therefore `INFERRED` from the presence of clean extracted text.
4. The tool truncated long PDFs: roughly the first 68 of 748 printed pages of the 2026 Master Circular and roughly the first 80 pages of the 2024 Master Circular were seen. Appendices (lists of rescinded circulars), annexures and formats were **not** read.
5. Nothing was retained as project data. Reproduction requires re-fetching the same URLs; per-request timestamps were not captured by the tool (session window ≈ 2026-10-01 20:45–21:00 UTC, from the sandbox clock).

### 0.1 Request log (read-only; no bulk activity)

| # | Target (host `www.sebi.gov.in` unless stated) | Purpose | Result |
|---|---|---|---|
| 1 | Legal → Master Circulars listing (`doListing=yes&sid=1&ssid=6`) | Identify official source area | 200, page 1 of 6 (25 of 134 records) read |
| 2 | Detail page, MC 2026-03-20 | Canonical page + attachment pointer | 200, no redirect observed |
| 3 | Title-search listing `search=Circular+For+Mutual+Funds` | Master Circular series | 200, 15 of 15 records (single page) |
| 4 | Detail page, MC 2024-06-27 | Canonical page + attachment pointer | 200 |
| 5 | Keyword listing `search=Mutual+Funds` | Window enumeration | 200, page 1 of 20 (25 of 486 records) read |
| 6 | `website-policy.html` | Terms / copyright | 200 |
| 7 | PDF `…/may-2026/1779193408747.pdf` | Sample #3 (canonical) | 200, `application/pdf` |
| 8 | `robots.txt` (sandbox shell, one HEAD) | robots check | Blocked by the sandbox egress proxy before reaching SEBI → **robots.txt UNVERIFIED** |
| — | `ricago.com` (1 PDF), `taurusmutualfund.com` (1 PDF) | Non-canonical copies of the two MCs | Read via the fetch tool; **third-party hosts, not part of any future ingestion** |

Seven fetches reached SEBI's site (6 HTML pages, 1 PDF). Requests 8 and the two third-party fetches did not touch SEBI. All other lookups were web-search queries (search-index excerpts are used only as pointers and are labelled `OBSERVED`).

## 1. Document identities observed

| Document | Listing date | Printed id / date | Where read | Label |
|---|---|---|---|---|
| Master Circular for Mutual Funds (current) | 2026-03-20 (listing + detail page) | `HO/24/13/11(1)2026-IMD-POD-1/I/7602/2026`, March 20, 2026 | third-party bundle | listing date `VERIFIED`; id/date `OBSERVED` |
| Master Circular for Mutual Funds (predecessor) | 2024-06-27 | `SEBI/HO/IMD/IMD-PoD-1/P/CIR/2024/90`, June 27, 2024 | third-party AMC copy | listing `VERIFIED`; id/date `OBSERVED` |
| Circular — Revision of Monthly Cumulative Report (MCR) Format | not on listing page 1 | `HO/24/11/24(62)2026-IMD-RAC4/I/11872/2026`, May 19, 2026 | **canonical PDF** | `VERIFIED` |

Series listing (official title-search page, 15 records): ten Master Circulars for Mutual Funds dated 2011-01-07, 2012-05-11, 2013-09-11, 2014-10-01, 2016-09-14, 2018-07-10, 2020-08-24, 2023-05-19, 2024-06-27, 2026-03-20, plus five 2010–2012 circulars (`VERIFIED`, single page, complete for that title query only). Consequence (`INFERRED`): the series is a chain of distinct, separately numbered instruments, each with its own detail page — **not** successive versions of one URL.

**URL identity observation (`INFERRED`).** The numeric stems of the attachment filenames decode as epoch-millisecond timestamps whose calendar dates match the corresponding listing dates (e.g. the stem of the 2026 MC PDF decodes to 2026-03-20; the 2024 MC stem to 2024-06-27; the MCR circular stem to 2026-05-19). They look like upload times, not identifiers. A re-upload would produce a new URL with identical content, which is exactly the "different URL, same bytes → same version, extra location" case in [ingestion-design](../ingestion-design.md) §4. Filenames must stay attributes, never identity (ADR/ingestion rule already says this).

## 2. Temporal validation against real SEBI text

<a id="r1"></a>
### R1 — Master Circular dated 2026-03-20 (current) · locators refer to the printed front matter of the third-party copy; page = printed page via the stamp rule in §5

**OBSERVED_SOURCE_TEXT**

- **Para 1 (p1):** states that the SEBI (Mutual Funds) Regulations, 2026 have been notified and come into force on April 01, 2026.
- **Para 2–3 (p1):** the 2024 Master Circular incorporated circulars issued up to March 31, 2024; the present one is updated in line with the 2026 Regulations and covers circulars issued up to March 20, 2026.
- **Para 4 (p2):** the circulars listed at **Sr. Nos. 1 to 34 of this document's own Appendix**, to the extent they relate to Mutual Funds, are rescinded upon the issuance of this circular; it also recalls that the 2024 Master Circular's Appendix list had been rescinded earlier.
- **Para 5 (p2):** other directions or guidance issued by the Board that are *specifically applicable to Mutual Funds* remain in force alongside it. Para 6.1–6.3 (p2) contain savings language (actions taken, pending applications, accrued rights deemed under or unaffected by the rescinded circulars).
- **Para 8 (p3):** one sentence says the circular comes into force with effect from April 01, 2026 — `"This Master Circular shall come into force with effect from April 01, 2026."` — and the next sentence says it **replaces** the Master Circular for Mutual Funds dated June 27, 2024. **No date is attached to the replacement sentence.**
- **Running header (every page):** an as-on date of March 20, 2026.
- **Index (p4–5):** the Appendix "List of Rescinded Circulars" starts at printed page 316; Annexures at 336; Formats at 464. The list content was **not read**.

**ENGINEERING_INTERPRETATION**

1. `INFERRED` — This is an **explicit** replacement statement naming the predecessor by title and date (not by circular number). It satisfies ADR-007 item 4 ("explicit text") for a `SUPERSEDES` edge MC-2026 → MC-2024 at document scope. Scope `FULL` is itself `INFERRED`: the text does not say the replacement is total, and para 5 preserves some non-consolidated guidance, so record scope as `FULL (as to the 2024 Master Circular as a document)` with the para-5 caveat and send it to human review.
2. `INFERRED` — Three different dates coexist in one document and are **not** interchangeable (H9): printed/listing date 2026-03-20 (`PUBLICATION`), "as on" date 2026-03-20 (`CONSOLIDATED_AS_OF`), and coming-into-force 2026-04-01 (`EFFECTIVE_FROM`, `EXPLICIT_TEXT`). A "newest wins" or "published date wins" rule would be wrong for 2026-03-20 → 2026-03-31.
3. `INFERRED` — **Open temporal tension.** The rescission in para 4 is tied to the circular's *issuance*; the force-date in para 8 is April 01; the replacement sentence carries no date. The text does not say what governs 2026-03-20 … 2026-03-31. Under ADR-007 (`SUPERSESSION_EFFECTIVE` from explicit text only) the superseding effective date is not derivable, so an as-of query inside that interval should be `TEMPORALLY_AMBIGUOUS` rather than guessed. This is document-structure analysis, not a conclusion about legal effect (H18). Good benchmark material (category *temporal*).
4. `INFERRED` — The rescinded-circular list is **inside the same PDF** (Appendix, p316+). Supersession/rescission edges for those 34 circulars are `UNVERIFIED` until that list is read and each entry anchored.

<a id="r2"></a>
### R2 — Master Circular dated 2024-06-27 (predecessor) · third-party AMC copy

**OBSERVED_SOURCE_TEXT**

- **Para 1–2 (p1):** the 2023-05-19 Master Circular incorporated circulars up to March 31, 2023; this one updates it to circulars issued on or before March 31, 2024. Para 2 says this circular `supersedes` the Master Circular for Mutual Funds dated May 19, 2023 — `"supersedes the Master Circular for Mutual Funds dated May 19, 2023"`.
- **Para 3 (p2):** circulars at **Sr. Nos. 1 to 16** of this document's Appendix, to the extent they relate to Mutual Funds, stand rescinded; the earlier Appendix (2023) had rescinded its own list. Para 5.1–5.3 (p2–3): savings language equivalent to the 2026 text.
- **Para 4 (p2):** other Mutual-Fund-specific SEBI guidance continues in force. **Para 6–8 (p3):** reporting continues; issued under section 11(1) of the SEBI Act, 1992; available under "Legal → Master Circulars".
- **No sentence in paras 1–8 states a date on which the circular comes into force.** Running header: an as-on date of March 31, 2024. Appendix starts at printed page 434 per the index (not read).

**ENGINEERING_INTERPRETATION**

1. `INFERRED` — Supersession wording differs between consecutive Master Circulars (`supersedes` here; `replace` in R1). A pattern list must be keyword-*family*-based and always yield `CANDIDATE`, never auto-`VERIFIED` (T-09, T-10).
2. `INFERRED` — Effective date for the 2024 circular is **absent** from the front matter. ADR-007 allows a publication-derived fallback only when the text says "with immediate effect"; it does not, so `EFFECTIVE_FROM = UNKNOWN` (not defaulted). Contrast: R1 has an explicit force date. Therefore assumption **A-08** ("effective dates are usually explicit") is **mixed**: explicit in 2026, absent in 2024 in the sampled front matter. A third-party summary of the **2023** circular (`OBSERVED`, search excerpt only) reports a force date equal to its issue date with one paragraph effective later — i.e. a third pattern (document date + clause-scoped later date). Not read from primary text.
3. `INFERRED` — "As on March 31, 2024" (`CONSOLIDATED_AS_OF`) predates issuance by about three months. Circulars issued between those dates are not consolidated in this document. A resolver must not treat the as-on date as an effective date (temporal-model §5).
4. `INFERRED` — **Clause-level dates inside a consolidated document can predate its own publication** (e.g. a bracketed insertion in Chapter 1, carried from an earlier circular, states an adoption date of June 01, 2024, i.e. before 2024-06-27). The effective date belongs to the *source circular + clause*; the Master Circular only restates it. This is a `RETROSPECTIVE`-flag candidate relative to *this document's* publication and argues for clause-scoped `date_assertion.target_node_id` (already in the data model).

<a id="r3"></a>
### R3 — Circular dated 2026-05-19, "Revision of Monthly Cumulative Report (MCR) Format" · **canonical PDF (`VERIFIED`)**

**OBSERVED_SOURCE_TEXT (verified)**

- Header line carries the circular number and the date (May 19, 2026) on the same line; addressed to Mutual Funds, AMCs, trustees and AMFI. The extractor stamps "Page 1 of 1" on the letter page; annexure tables follow.
- **Para 1:** refers to clause **6.20** of the SEBI Master Circular for Mutual Funds dated March 20, 2026.
- **Para 2:** a new scheme-category framework introduced by a SEBI circular dated February 26, 2026 is described as having been consolidated into clause **3.7** of the Master Circular; hence the MCR format is to be modified — `"it has been decided to modify MCR format from June 2026 onwards"`. The revised formats are Annexure A (MCR) and Annexure B (MCR for SIF).
- **Para 3:** all other conditions of the referenced clause remain unchanged. **Para 4:** issued under section 11(1) of the SEBI Act read with the SEBI (Mutual Funds) Regulations 2026. **Para 5:** available under "Legal → Circulars".

**ENGINEERING_INTERPRETATION**

1. `INFERRED` — This is a **partial modification** of one Master Circular clause: no "supersede/replace/rescind" keyword appears; the verbs are "modify" and "remain unchanged". A keyword-only supersession detector would miss it; amendment detection needs the `(refers-to-clause) + (modify|revise|substitute|partial modification) + (remain unchanged)` pattern family. Map to `AMENDS`, `scope=PARTIAL`, target = MC-2026 clause 6.20, `operation=UNSPECIFIED/SUBSTITUTE` pending review.
2. `INFERRED` — Effective date expression has **MONTH precision** ("June 2026 onwards"); the temporal model's `precision ∈ {DAY, MONTH, YEAR}` is justified by real text. A reasoned lower bound (first day of the month) is a derived value and must not be silently stored as DAY precision.
3. `INFERRED` — The later circular cites the Master Circular **by title + date + clause number**, not by circular number. It cites the earlier Feb-2026 circular **by date and subject only** (no number in this text). Reference resolution therefore needs `(title, date)` as a lookup key in addition to circular numbers.
4. `INFERRED` — The text asserts that a clause was "consolidated" into the Master Circular. That is a *consolidation* relation (circular → MC clause), distinct from `AMENDS` and `SUPERSEDES`; the graph schema should be checked for it in Phase 7 (see §7, gap G-3).

### R4 — Other relationships seen but NOT established from primary text

| Observation | Source type | Status |
|---|---|---|
| A February-2026 categorisation circular is described as superseding clause 2.6 of the 2024 MC and as effective immediately | third-party group post (search excerpt) | `OBSERVED`; primary text not read |
| A May-2026 draft circular proposes partial modification of para 17.4.3 of the 2026 MC | search-index excerpt of a canonical-host PDF (not fetched) | `OBSERVED`; draft ≠ issued, must not create a relationship edge |
| The 2026 Regulations are the successor of the 1996 Regulations ("erstwhile") | wording in a SEBI listing title for an informal-guidance item | `OBSERVED` (title text only); relationship not read from the Regulations |
| SEBI publishes consolidated "[Last amended on <date>]" pages of both the 1996 and 2026 Regulations alongside the amending regulations | official listing | `VERIFIED` (existence); text unread |

## 3. Catalogue of real date expressions (sampled text only)

Interpretations are **not** normalised into facts here; each needs an evidence-anchored `date_assertion` later.

| # | Expression type | Where | Temporal-model kind | Precision | Notes |
|---|---|---|---|---|---|
| D1 | Coming-into-force of the circular | R1 para 8 | `EFFECTIVE_FROM` | DAY | explicit; separate from issue date |
| D2 | Coming-into-force of *another* instrument (2026 Regulations) reported inside a circular | R1 para 1 | `EFFECTIVE_FROM` of the Regulations, evidence is in the circular | DAY | do not attach to the circular |
| D3 | Replacement of predecessor | R1 para 8 | `SUPERSESSION_EFFECTIVE` | — | **no date stated** → `UNKNOWN` |
| D4 | "As on" consolidation date | running headers (R1, R2) | `CONSOLIDATED_AS_OF` | DAY | R2: three months before issue |
| D5 | Coverage cut-off ("issued till/up to X") | R1 para 3, R2 para 2 | `CONSOLIDATED_AS_OF` (closest) | DAY | states which circulars are included |
| D6 | Compliance deadline for existing schemes | R1 clause 3.8.9 | **none fits** | DAY | see gap G-1 |
| D7 | Relative period anchored to "the date of this circular" inside a consolidated text | R1 clause 3.8.1(g) | `RELATIVE_TEXT` | — | "this circular" most plausibly means the *source* circular (Feb 2026, per the footnote on the enclosing Part IV heading); the anchor is **not** the Master Circular's date → anchor ambiguity |
| D8 | Clause-level adoption date inside an MC dated later | R2 clause 1.1.2C | `EFFECTIVE_FROM` (clause scope) | DAY | predates document publication → `RETROSPECTIVE` candidate |
| D9 | "From <Month> <Year> onwards" | R3 para 2 | `EFFECTIVE_FROM` | **MONTH** | quoted in §2 R3 |
| D10 | Issue date on the header line | R2, R1, R3 | `PUBLICATION` | DAY | |
| D11 | Dates of *cited* circulars (reference dates, not assertions about this document) | footnotes in R1, R2 | none (reference attribute) | DAY | must not enter the document's own date assertions |
| D12 | Durations that are not dates ("N working days", "N calendar days", "N years") | throughout | none | — | naive extractors will mistake these for dates |

**Surface-format variability observed:** `Month DD, YYYY` with and without a space after the comma; leading-zero days; per-page footnote dates; one date **corrupted by extraction** inside a footnote of the 2024 copy (a month name is split by stray punctuation characters), while the same footnote is clean in the 2026 copy. Date extraction must therefore keep the raw span, report a normalisation confidence, and never "repair" a month name silently.

## 4. Reference and amendment patterns (actual forms)

**4.1 How SEBI documents refer to things**

| Reference target | Observed pattern family | Examples of form (as printed) |
|---|---|---|
| Earlier circular, modern style | `SEBI/HO/<dept>/<division>/P/CIR/<year>/<n>` + "dated <date>" | e.g. 2023-era `SEBI/HO/IMD/IMD-RAC-2/P/CIR/2023/60` |
| Earlier circular, 2026 style | `HO/<nn>/<nn>/<nn>(<n>)<year>-<dept>-<division>/I/<n>/<year>` | `HO/24/13/15(2)2026-IMD-RAC4/I/5764/2026` — a different grammar from the pre-2026 one; one regex will not cover both |
| Earlier circular, legacy style | e.g. `IIMARP/MF/CIR/01/428/97`, `MFD/CIR No.22/2311/03`, `SEBI/IMD/CIR No.5/126096/08`, `CIR/IMD/DF/21/2012` | prefixes and separators vary; some include spaces inside the id or "No" with/without dot |
| Circular without number | "SEBI Circular dated <date>" (+ subject) | R3 para 2 |
| Master Circular | "Master Circular for Mutual Funds dated <date>" + `clause|paragraph <n.n.n>` | R3 paras 1–2; R1 internal "Paragraph 9.4.3 of this Master Circular" |
| Non-circular communications | "Refer SEBI letter No. … dated …", "Refer SEBI email dated …" | in footnotes of R1/R2; also "Policy related letters/emails … refer the attachment" in the index |
| Regulations | "Regulation <n>(<n>)(<x>)" scoped by a **defined term** | R1 abbreviation table defines "Regulation XX" as a regulation of the **2026** Regulations unless stated; R2 defines it against the **1996** Regulations. The same words resolve to different instruments |
| Gazette notification | "notification No. … dated …", "Gazette Notification No. S.O. … dated …" | R2 clause 1A.1.2, footnotes |
| Internal structure | "Paragraph/Clause <n.n.n>", "Annexure <n><letter>", "Format No. <n><letter>", Appendix "Sr. No." ranges | R1/R2/R3 |
| External | e.g. a Ministry of Finance scheme by name and year | R1 clause 3.10.2 |

**4.2 How amendments are marked inside consolidated text**

- Footnote pattern "Inserted vide <circular id> dated <date>" (R1, R2).
- Footnote pattern "Substituted vide <circular id> … Prior to substitution, clause read as under" **followed by the old clause text** (R1 footnote near clause 1.1.2(c)). **Hazard:** superseded wording is present verbatim *inside the current document*. A chunker that does not mark footnote nodes would index historic text as a live requirement (T-09; evidence-model V3).
- In the 2024 copy, inserted text is wrapped in square brackets with a footnote; the bracket convention is **not** used in the 2026 copy (same content, different markup).
- Provenance footnotes cite *lists* of circulars and letters for one clause (multiple `vide` references per footnote), so one clause has several source circulars.
- Footnote numbers run **sequentially across pages and chapters** (observed up to the 80s in the first ~68 pages of R1), not restarting per page.
- Clause numbers are **not stable across Master Circular versions**: e.g. categorisation is clause 2.6 (2024) vs 3.7 (R3 and R1); fundamental attributes 1.14 → 1.9; NFO 1.10 → 1.7; regulation citations also renumber with the regime change (e.g. fundamental-attribute regulation 18(15A) in the 1996 Regulations vs 22(9)(c) in the 2026 Regulations, per the two documents' own footnotes). A reference `"clause 3.7"` is meaningless without the target document version.

**4.3 Source typos/oddities preserved in the text (observed in both Master Circular copies):** a circular id spelled with digit zeros in place of letter O (`SEBI/H0/0W/…`), a table whose old paragraph numbers survive as spaced digits, and a stray auto-number inside a table cell. Reference extraction must tolerate source errors and keep the raw span.

## 5. Parse-quality sample results

Evaluation flags are those requested in the brief. **Confidence** reflects the §0 limits (non-canonical copies, text-extractor only).

| Flag | R1 — MC 2026 (copy, first ~68/748 pp) | R2 — MC 2024 (copy, first ~80 pp) | R3 — MCR circular (**canonical**) |
|---|---|---|---|
| Format | PDF (a depository communique page precedes the SEBI text; **bundle of two authorities' documents in one file**) | PDF | PDF |
| TEXT_USABLE | **Yes** (clean English text layer) | **Yes** | **Yes** |
| Born-digital vs scanned | `INFERRED` born-digital (clean text layer, no OCR artefacts) | `INFERRED` born-digital | `INFERRED` born-digital |
| Image-only pages seen | none in sampled pages (unsampled pages `UNVERIFIED`) | none in sampled pages | none |
| OCR_REQUIRED | No for sampled pages (`INFERRED`); **A-06 provisionally supported, not proven** | No for sampled pages | No |
| STRUCTURE_USABLE | **Yes, with cleaning**: chapter/clause numbers (`1.1.`, `1.1.2.`, `(a)`, `(i)`) preserved as text. Noise: repeating running header + "Back to Index" + "Confidential" per page | **Yes, with cleaning**: running header per page; bracketed insertions | **Yes** (numbered paras 1–5) |
| PAGE_ANCHORS_USABLE | **Yes via heuristic.** A page stamp precedes each page's content; rule "stamp precedes content" matched the printed index at 4 of 4 checkable points (abbreviations p6, chapter 2 p15, chapter 3 p23, chapter 4 p44) — `INFERRED`. **Unknown whether the stamps belong to the canonical file or were added by the hosting party** (a "Confidential" footer on a public circular is suspicious) | **Yes via heuristic**: bare page number precedes each page's content; matched the index at 3 of 3 checkable points (1A p26, ch.2 p35, ch.3 p57) — `INFERRED` | **Weak**: stamp reads "Page 1 of 1" while annexure pages follow; page count `UNVERIFIED` |
| SECTION_ANCHORS_USABLE | **Yes** for decimal clause labels; sub-levels restart per clause; duplicated/stale numbers occur inside tables | **Yes**; same | **Yes** |
| TABLES_REQUIRE_SPECIAL_HANDLING | **Yes.** Multi-column category tables flatten to a cell-per-line stream; rows split across a page break; **reading order inverted** around a table spanning pages (notes that follow the table appear before its last rows — `INFERRED` from numbering); stale numerals split into single characters inside cells; footnote text interleaves with rows | **Yes**, same behaviours; header cell markers (`++`, `^^`, `@`) tie footnotes to rows | **Yes.** A 17-column template table flattens to header cells with no row/column alignment; values are empty (it is a format) |
| REFERENCES_EXTRACTABLE | **Yes by pattern, with normalisation**: footnote markers are **fused to the preceding word** (`Board1`, `Regulations2`); end-of-line hyphen loss corrupts identifiers and words (`IMDII`, `halfyear`, `UltraShort`, `TBills`); several id grammars (§4.1) | **Yes, same issues**; one date visibly corrupted by stray glyphs | **Yes** (no footnotes; clean ids) |
| Encoding / Unicode | Curly quotes and apostrophes present; no mojibake in sampled text; missing space after paragraph numbers in places | same; stray punctuation residue near some footnote markers | clean; a few source typos (e.g. duplicated "V" section numbering, a missing space) |
| Headers/footers | running header + "Back to Index" + "Confidential" + page stamp per page | running header + page number per page | one stamp |

### 5.1 What this does and does not say about OD-03 (parser choice)

- **Does say:** the three sampled documents are text-bearing (no OCR need seen in sampled pages), but **tables, footnote markers, hyphen loss, reading-order near tables, and running header/footer stripping** are real, recurring problems that any chosen parser must be tested against. Offsets anchored to a TextLayer (ADR-008) must survive header/footer removal — so the **raw extractor output must be stored un-cleaned, and cleaning must be a derived view with offset mapping** (security T-02 already requires a derived view).
- **Does not say:** which parser is better. No candidate parser ran on canonical bytes, so **OD-03 stays open**. A spike plan is in [tooling-bootstrap-plan](tooling-bootstrap-plan.md) §6; it requires access approval first.
- **Size evidence (A-03/A-01, `OBSERVED`):** the current Master Circular is stamped as 748 pages with 22 chapters; the predecessor's printed index runs to at least page 590. Page totals for canonical files are `UNVERIFIED`. I give **no labelling-effort estimate** (no basis; H14). Drivers are listed in [source-access-report](source-access-report.md) §9.

## 6. A-01 / A-06 / A-08 status after Phase 1A

| Assumption | Status | Basis |
|---|---|---|
| A-01 MF corpus suitable (size, structure, parse quality) | **Not falsified; not proven.** Temporal/amendment structure is real and rich; text extractable; tables/footnotes hard; very long documents | R1–R3, §5 |
| A-02 public + terms/robots permit automated retrieval | **Unresolved** (robots unverified; copyright requires permission; policy silent on automation) | [source-access-report](source-access-report.md) |
| A-06 mostly born-digital | **Provisionally supported** (3 of 3 sampled, partial pages) | §5 |
| A-08 effective dates usually explicit | **Mixed** (explicit in 2026 MC; absent in 2024 MC front matter; month-precision in R3) | R1–R3 |
| A-10 English only | Supported for all sampled text | §5 |

**ADR-009 fallback trigger:** not met. No material failure of the Mutual Funds corpus was found; the Investment Advisers fallback is **not** recommended on this evidence.

<a id="bounded-corpus-window"></a>
## 7. Bounded corpus window — proposal and rationale

**Proposed window: 2024-06-27 → 2026-07-10 (inclusive), anchor instruments plus in-window circulars.** Basis `INFERRED`:

1. **Contains the requested 2024 → 2026 transition** and its immediate cause: predecessor MC (2024-06-27) → current MC (2026-03-20, in force 2026-04-01).
2. **Contains a full regulatory-regime change**, not only a circular chain: the 1996 Regulations (consolidated page "last amended 2025-11-01") → the 2026 Regulations (listed 2026-04-01) → a 2026 amendment (listed 2026-07-07). This stresses version-scoped references (§4.1) far more than a circular-only window.
3. **Contains intervening amendment material**: an addendum issued five days after the master circular, circulars citing the master circular by clause (R3), and a draft that proposes a modification (must *not* create a relationship — H10/ADR-007).
4. **Bounded by evidence, not by guesswork:** the lower bound is the predecessor's own date; the upper bound is the latest Mutual Funds item seen on listing page 1.
5. **Small:** 20 candidate entries known (manifest tiers A 7, B 7, C 2, seed 4); plus 1 out-of-window lineage entry.

**Counts, stated honestly (H14):** 20 in-window candidates are *known*, which is a **floor, not the corpus size**. Listing pages 2–20 of the keyword listing were not read, and two in-window circulars cited by official documents (2026-02-26; 2026-05-19) are **absent from page 1 of that listing** — evidence that a title-keyword listing under-enumerates (`OBSERVED`). 16 of 21 manifest entries have a listing-verified canonical detail-page URL; 5 do not (the MCR circular's detail page and 4 seeds). Only 4 entries have an attachment URL.

**What is deliberately excluded:** earlier master circulars (2011–2023), recovery notices, informal guidance, speeches, and the Investment-Adviser/Research-Analyst circular's inclusion is a reviewer decision (scope note in manifest).

**Corpus-snapshot caveat:** the manifest is not a snapshot. A snapshot id is defined only after a reviewed ingest (Phase 1B).

## 8. Consistency check against Stage 1 documents

No genuine contradiction with an ADR and no safety flaw was found. Findings are **gaps for later phases**, not changes to any decision (H5). No ADR is modified.

| ID | Where | Observation | Smallest proposed change | Blocking? |
|---|---|---|---|---|
| G-1 | [temporal-model](../temporal-model.md) §2 date kinds | The date-kind list has no kind for a **compliance deadline / transition end** for existing entities (D6) or for a clause-scoped later effective date. | Phase 6: either add a `COMPLIANCE_DEADLINE` kind (docs + ADR-007 amendment) or define it as clause-scoped `EFFECTIVE_FROM`. Decide from more real text. | No |
| G-2 | [temporal-model](../temporal-model.md) §4 step 2 | Replacement statement without a date (D3) is a real case; ADR-007 already yields `UNKNOWN`/`TEMPORALLY_AMBIGUOUS`. | None; add a benchmark case in Phase 5. | No |
| G-3 | [graph-schema](../graph-schema.md) | Relation "consolidated into clause X" (R3 para 2) is neither `AMENDS` nor `SUPERSEDES`. | Phase 7: check schema; add predicate only if a query pattern exists (H15). | No |
| G-4 | [ingestion-design](../ingestion-design.md) §4, [domain-model](../domain-model.md) | SEBI publishes regulator-made **consolidated "last amended" pages** as separate listing entries; they are neither a new version of the original URL nor the amending instrument. | Phase 2: add a `document_type` value for regulator-published consolidations; keep ADR-007 item 6 (non-authoritative). | No |
| G-5 | [ingestion-design](../ingestion-design.md) §2 | `source_reference` is "nullable"; the Regulations have no circular number, and their identifying notification number was not read. | Phase 2: identity via manifest `document_key` (already rule 1). | No |
| G-6 | [security](../security.md) T-04 | The 2026 MC copy was a **bundle** containing another authority's document ahead of SEBI's. | None; supports host-allowlist + "authority from manifest" rule; add a security fixture (bundle/foreign-authority first page). | No |
| G-7 | [evidence-model](../evidence-model.md) | Footnote nodes can carry **verbatim superseded text** (§4.2). | Phase 3: structure kind `FOOTNOTE` with a `contains_superseded_text` flag; exclude from "current requirement" retrieval by default. | No |
