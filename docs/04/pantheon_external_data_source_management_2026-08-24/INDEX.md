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
