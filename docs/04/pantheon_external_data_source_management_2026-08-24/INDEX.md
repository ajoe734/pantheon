# Pantheon External Data Source Management — 2026-08-24

This package defines the current gap truth and phase-1 architecture for
completing Pantheon external data coverage and making every supported source
independently manageable from the Management product.

## Documents

Read and implement in this order:

1. [`CURRENT_GAP_2026-08-24.md`](CURRENT_GAP_2026-08-24.md) — code, product,
   provider, runtime, search, memory, Management UI, and acceptance gaps.
2. [`SA_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24.md`](SA_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24.md)
   — target architecture, authority boundaries, lifecycle, Management
   experience, provider waves, work packages, dependencies, and rollout plan.
3. [`SD_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24.md`](SD_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24.md)
   — file-level implementation design, schemas, persistence, command effects,
   BFF routes, frontend components, provider adapters, search, memory, tests,
   migration, and hosted acceptance.

## Phase boundary

### Phase 1 — this package

- complete the external source inventory and provider coverage truth;
- separate deployed connector definitions from configured instances and runtime
  observations;
- support create-disabled, validate, bounded canary, enable, disable, degrade,
  resume, schedule, replace, and retire per source;
- expose governed BFF commands and extend the existing
  `/management/data-sources` page into a control center;
- complete as-of/hybrid/structured-alpha search;
- prove evidence-to-research/seed and reviewed-memory lineage; and
- accept the flow on an exact hosted FE/BFF/source deployment.

### Phase 2 — deferred

- OpenClaw-assisted provider discovery;
- LLM source-change proposals in the product workflow;
- connector-development request generation;
- governed local development-tooling handoff; and
- AI-assisted source optimization, replacement, or retirement proposals.

Phase 2 consumes phase-1 contracts. It must not make the product BFF a
repository/task/worktree authority or let an LLM approve and apply its own
source changes.

## Baseline

| Repository | Audited ref | SHA |
|---|---|---|
| `ajoe734/pantheon` | `origin/dev` | `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0` |
| `ajoe734/execute-plans` | `origin/dev` | `5447d2a09b5c83a4f9ee2d405f57c642913e0055` |

The frontend remains a separate repository. All `execute-plans/...` paths in
this package refer to `ajoe734/execute-plans`; none may be created inside a
Pantheon checkout.

## Phase-1 Delivery & Hosted Acceptance Status

| Work Package | Task ID | Spec Section | Delivery Outcome |
|---|---|---|---|
| Command Engine & Store | `SRCM-P1-SOURCE-COMMANDS-20260824` | SD-SRCM-01/02 | Done (PR #5197) |
| BFF Management Facade | `SRCM-P1-BFF-FACADE-20260824` | SD-SRCM-03 | Done (PR #5202) |
| Management Control UI | `SRCM-P1-MGMT-UI-20260824` | SD-SRCM-04 | Done (`execute-plans`) |
| Provider Adapter Coverage | `SRCM-P1-PROVIDER-COVERAGE-20260824` | SD-SRCM-05 | Done (PR #5201) |
| Governed Search & Alpha | `SRCM-P1-SEARCH-ALPHA-20260824` | SD-SRCM-06 | Done (PR #5198) |
| Reviewed Memory Writeback | `SRCM-P1-MEMORY-WRITEBACK-20260824` | SD-SRCM-07 | Done (PR #5207) |
| Hosted Acceptance & Closeout | `SRCM-P1-HOSTED-ACCEPTANCE-20260824` | SD-SRCM-08 | Done (This Task) |

- **Hosted Acceptance Evidence Package**: [`docs/deployment/evidence/external-source-management-phase1/`](../../deployment/evidence/external-source-management-phase1/)
- **Verification Script**: `python3 scripts/verify_external_source_management_acceptance.py`

