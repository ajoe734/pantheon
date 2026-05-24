# BFF-MGMT-DELTA-003 Review — GET /bff/management/strategy-allocation

Reviewer: Claude
Task: BFF-MGMT-DELTA-003
Owner: Codex
Commit: c477b3bf (merged PR #521 → dev at e8f5c83d)
Date: 2026-05-24

## Decision: APPROVED

## Scope Verified

- `GET /bff/management/strategy-allocation` route registered in `services/control-plane/bff/main.py`
- Query parameters: `strategy_id`, `capital_pool_id`, `deployment_stage`, `drift_status`, `page_token`, `page_size`
- Composes runtime bindings, deployment plans, persona-capital bindings, capital pools, strategy summaries, telemetry snapshots, and paper/live drift reports
- Auth enforcement via `_require_read_role(identity)` (HTTP 401 on anonymous)
- CORS preflight returns HTTP 204
- Path registered in `execute-plans/src/lib/bff-v1/paths.ts` as `managementStrategyAllocation`
- TypeScript types in `management.ts`: `ManagementStrategyAllocationQuery`, `ManagementStrategyAllocationRow`, `ManagementStrategyAllocationSummary`, `ManagementStrategyAllocationResponse`
- Fetch helper `fetchManagementStrategyAllocation` in `management.ts`
- Path included in wiring contract test known-paths list

## Review Findings

### Route Implementation

- `_management_strategy_allocation_runtime_facts` correctly filters runtime bindings to active statuses only, resolving strategy/pool IDs from runtime, plan, and persona-binding with a three-way fallback chain.
- `_management_strategy_allocation_rows` groups by `(strategy_id, capital_pool_id)`, aggregates allocation amounts, computes risk-budget utilization, and synthesizes per-runtime drift via `_management_strategy_allocation_drift_summary`.
- `_management_strategy_allocation_runtime_drift` wraps `read_store.get_paper_live_drift_report` with a structured unavailable fallback — no hard failures on missing drift data.
- `_management_strategy_allocation_drift_summary` correctly rolls up `breached > watch > unavailable > degraded > ok > mixed` priority ordering across runtime drifts.
- Response surface degradation logic is correct: if `drift_available_count < active_runtime_count`, the `paper_live_drift` surface is marked `degraded`.
- `policy=read_only_strategy_allocation` correctly asserted throughout.
- Canonical aggregate envelope (`data`, `items`, `rows`, `summary`, `page_info`, `meta`) matches the spec contract.

### Dual-key Fields

Both camelCase and snake_case variants are present for all row and summary fields, consistent with the other management routes.

### Test Coverage

Three focused tests:
- `test_strategy_allocation_returns_active_strategy_allocations_with_drift` — full happy path including drift fields, summary fields, links, and surface metadata
- `test_strategy_allocation_requires_read_auth` — HTTP 401 without auth
- `test_strategy_allocation_cors_preflight` — HTTP 204 with `Access-Control-Allow-Origin`

OpenAPI schema check verifies path registration.

Focused pytest suite: **49 passed, 3 existing `datetime.utcnow()` deprecation warnings** (pre-existing in `read_store.py`, not introduced by this task).

### Frontend Contract

TypeScript interfaces and fetch helper align with the backend response shape. `ManagementStrategyAllocationResponse.meta.surfaces` correctly names `strategy_allocation` as the aggregate surface with the supporting source surfaces typed as optional.

## All Acceptance Criteria Met

| # | Criterion | Result |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | Verified |
| 2 | Active strategy allocation slice across capital pools | Verified |
| 3 | Includes paper/live drift status and metric counts | Verified |
| 4 | Anonymous request returns HTTP 401 | Verified |
| 5 | Authenticated request returns HTTP 200 | Verified |
| 6 | Response keeps canonical aggregate envelope | Verified |
| 7 | CORS preflight returns HTTP 204 | Verified |
| 8 | Focused pytest case covers `strategy_allocation` | Verified |
| 9 | execute-plans exposes typed path and fetch helpers | Verified |

## No Required Changes

Implementation is complete and correct. Returning to owner Codex for closeout.
