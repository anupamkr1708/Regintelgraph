# Evaluation Framework

Status: PROPOSED. **No score in this repository may be asserted without a reproducible run record** (H14). This document defines *what* is measured and *how*; it contains no results.

## 1. Benchmark categories (RegIntelBench, see [benchmark-spec](benchmarks/benchmark-spec.md))

factual · temporal · multi-hop · amendment · applicability · impact · contradiction · citation · abstention · adversarial

## 2. Metrics

| Layer | Metric | Definition (summary) |
|---|---|---|
| Retrieval | Recall@K, Precision@K | Fraction of labelled relevant evidence in top-K / fraction of top-K that is relevant (evidence unit = clause span) |
| | MRR | Mean reciprocal rank of first relevant item |
| | nDCG@K | Graded relevance (0/1/2) |
| | Context Precision / Context Recall | Share of the final evidence pack that is relevant / share of required evidence present in the pack |
| | Reranker quality | nDCG@K and MRR delta vs the fused list, on the same candidates |
| Citations | Citation precision | Cited spans that truly support the attached claim ÷ all cited spans |
| | Citation recall | Required supporting spans cited ÷ required supporting spans |
| | Citation validity | Fraction of citations passing `citation-valid` (hash/version/URL) — expected 100% by construction |
| Claims | Claim support rate | Claims in final answers whose human/gold label is supported ÷ all stated claims |
| | Faithfulness | Stated claims entailed by cited evidence (gold labels; LLM-judge only as a calibrated auxiliary) |
| | Answer relevancy | Human/gold-rubric judgement of responsiveness |
| Reasoning | Temporal accuracy | Correct applicable set and correct abstention on ambiguous cases (exact-match on `TemporalResolution` labels) |
| | Graph traversal accuracy | Precision/recall of returned edges vs labelled edges for the query |
| | Contradiction detection accuracy | Precision/recall/F1 on labelled conflict vs change vs no-conflict cases |
| Behaviour | Abstention accuracy | Correct ANSWER/ABSTAIN/AMBIGUITY decision; report false-answer rate and false-abstain rate separately |
| Agent | Agent task success | Task-level success per labelled criteria |
| | Tool-call efficiency | Useful tool calls ÷ total; calls/run distribution |
| System | p50/p95/(p99) latency | Per subsystem and end-to-end ([performance](performance.md)) |
| | Cost/query | Tokens × price + compute, per route ([cost-model](cost-model.md)) |
| | Cache hit rate | Per cache layer |
| Extraction | Entity/relation precision, recall | Against manual gold subset ([benchmark-spec](benchmarks/benchmark-spec.md)) |

Reporting: point estimate + bootstrap 95% CI; per-category breakdown; macro and micro averages; paired comparisons between arms on identical cases.

## 3. Ablation study

| Arm | Configuration | Question answered |
|---|---|---|
| A1 | Dense only | Baseline semantic retrieval |
| A2 | Sparse only (FTS + identifier index) | Baseline lexical retrieval |
| A3 | Hybrid (RRF) | Does fusion beat the best single mode? |
| A3r | Hybrid + reranker | Does reranking justify its latency? |
| A4 | Hybrid + Graph (router-gated) | Does the graph help multi-hop/amendment/impact categories? |
| A5 | Agentic Hybrid (planner, no graph) | Does iteration help beyond A3? |
| A6 | Agentic Graph + Verification (full) | Full-system quality, abstention, cost |

Protocol: same cases, same corpus snapshot, same generator (or no generator for retrieval-only metrics), fixed seeds, temperature 0 where applicable, all versions recorded. Verification on/off is a separate toggle on A5/A6 to isolate its effect on claim support and abstention. **Decision rule:** a component stays only if it improves its target categories with a paired CI excluding zero and its cost/latency increase is within budget; otherwise it is demoted to optional/removed via ADR. This is how "do not add complexity for appearances" is enforced.

## 4. Targets vs measurements

| Kind | Content | Status |
|---|---|---|
| **Hard gates** (invariants, not performance) | Citation validity = 100%; no VERIFIED edge without evidence; security suite 100% pass; offline suite green; no un-flagged benchmark case deletions | Enforced from the phase that introduces each |
| **Hypotheses** (relative, no numbers) | Hybrid ≥ best single mode on Recall@K; graph arm > hybrid on multi-hop/amendment; verification reduces unsupported stated claims; abstention false-answer rate on ambiguous-temporal cases is lower with the resolver than without | `ASPIRATION` until tested |
| **Absolute targets** (recall, accuracy, latency, cost) | Set in `data/eval/targets.yaml` **after baseline runs (Phase 5)** with CI widths known; frozen by human sign-off before Phase 17 | `ASPIRATION` → `FROZEN` |
| **Measured results** | Stored only as run records: `data/eval/runs/<run_id>/{config,versions,metrics,per_case}.json` | Facts |

The Executive Summary's "≥80% of benchmark queries" and "<2 s average" are **not adopted** as targets (no baseline, no benchmark, no definition of "correct", unrealistic for verified agentic paths); see [executive-summary](executive-summary.md).

## 5. Integrity of the evaluation

- Dev/test split fixed before tuning; test split run sparingly, runs logged.
- LLM-as-judge is auxiliary and must be validated against a human-labelled subset (agreement reported); never the only judge for faithfulness or citation metrics.
- Labels are human-authored, double-annotated for the adjudicated subset; model-assisted drafting of labels requires human review.
- Benchmarks and expectations are versioned; changing an expectation requires a written reason and reviewer (H6).
- Official source data and manual labels live in separate paths; labels reference sources by `document_key` + node label + `quote_hash`, not by copying text.
