# Documentation index

| Area | Document |
|---|---|
| Research input & reconciliation | [executive-summary](executive-summary.md) |
| Product | [product-spec](product-spec.md) |
| Architecture | [architecture](architecture.md) · [domain-model](domain-model.md) · [data-model](data-model.md) |
| Pipelines | [ingestion-design](ingestion-design.md) · [retrieval-design](retrieval-design.md) |
| Knowledge & time | [graph-schema](graph-schema.md) · [temporal-model](temporal-model.md) · [evidence-model](evidence-model.md) |
| Agents & interfaces | [agent-design](agent-design.md) · [mcp-design](mcp-design.md) |
| Security | [security](security.md) |
| Quality | [evaluation](evaluation.md) · [benchmark-spec](benchmarks/benchmark-spec.md) · [testing-strategy](testing-strategy.md) |
| Operations | [observability](observability.md) · [performance](performance.md) · [cost-model](cost-model.md) |
| Plan | [implementation-roadmap](implementation-roadmap.md) · [stage-1-review](stage-1-review.md) |
| Decisions | [ADR-001](adr/ADR-001-system-architecture.md) · [002](adr/ADR-002-primary-storage.md) · [003](adr/ADR-003-retrieval-strategy.md) · [004](adr/ADR-004-graph-strategy.md) · [005](adr/ADR-005-agent-orchestration.md) · [006](adr/ADR-006-model-routing.md) · [007](adr/ADR-007-temporal-and-supersession-policy.md) · [008](adr/ADR-008-evidence-anchoring-and-citation-handles.md) · [009](adr/ADR-009-initial-domain-scope.md) |

## Deviations from the requested conceptual structure (and why)

- Added ADR-007 (temporal/supersession policy), ADR-008 (evidence anchoring and citation handles), ADR-009 (initial domain scope): each records a decision that either contradicts the research summary or is too consequential to bury in another ADR.
- Added `docs/README.md`, `docs/stage-1-review.md`, `scripts/check_foundation.py`, `scripts/inspect_env.sh`, `.github/workflows/foundation-checks.yml` so the foundation is mechanically checkable (anti-drift) from day one.
- `workers/*` are **entry points over `packages/*`**, not separate services (see [architecture](architecture.md)).

Conventions: statuses are `ACCEPTED`, `PROPOSED`, `DEFERRED`. Numbers labelled `ASPIRATION` are unmeasured. Assumptions are tracked with IDs (`A-xx`) in [stage-1-review](stage-1-review.md); open decisions with IDs (`OD-xx`).
