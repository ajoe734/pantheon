# Review: BFF-MGMT-DELTA-008 — GET /bff/management/cost-attribution

Reviewer: Claude
Owner: Claude2
Reviewed at: 2026-05-24T15:00:00Z
Commit: 27679b9280dc67331f9c223a4b86473ec15c9828
Status: **approved**

## Scope Verified

- `services/control-plane/bff/main.py` — `_management_cost_attribution_rows()`,
  `_management_cost_attribution_response()`, `bff_management_cost_attribution` route
- `execute-plans/src/lib/bff-v1/management.ts` — `ManagementCostAttributionQuery/Row/Summary/Response` types,
  `managementCostAttributionPath`, `fetchManagementCostAttribution`
- `execute-plans/src/lib/bff-v1/paths.ts` — `managementCostAttribution` entry
- `services/control-plane/bff/test_bff_management_delta_routes.py` — 3 pytest cases
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py` — contract assertions
- `docs/04/pantheon_bff_api_gap_2026-05-24_delta/BFF_API_GAP_delta_audit_spec.md` — BFF-MGMT-DELTA-008 section
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md` — BFF-MGMT-DELTA-008 section

## Findings

### Backend

- Route `GET /bff/management/cost-attribution` is correctly wired.
- Helper functions compose from existing PM-12 telemetry, capital-pool,
  runtime-binding, and persona-binding surfaces — no new cost ledger introduced.
- Cost components: commission (3 bps × total_trades), slippage
  (|avg_slippage_bps| × total_notional / 10000), infrastructure (0.01% × allocated_capital).
  All are None-safe with explicit guards; `total_cost` is the sum of available parts.
- Pagination via `_page_slice`, surface status via `_aggregate_group_surface`, and sorting
  by descending `total_cost` are consistent with the governance-ledger and risk-radar patterns.
- Filter params (persona_id, strategy_id, capital_pool_id) are validated and applied correctly.
- `_require_read_role` gate enforced (401 for anonymous verified by test).

### TypeScript Client

- Query/Row/Summary/Response interfaces carry both camelCase and snake_case fields,
  matching backend output and following the established dual-key convention.
- `managementCostAttributionPath` and `fetchManagementCostAttribution` match the
  pattern established by other management endpoints.
- `paths.managementCostAttribution` correctly points to `/bff/management/cost-attribution`.

### Tests

- `test_cost_attribution_success`: verifies 200 response, envelope shape, summary fields,
  policy, meta surfaces, and composition_sources list.
- `test_cost_attribution_filter_by_persona`: verifies empty result for non-existent persona.
- `test_cost_attribution_cors_preflight_and_openapi`: verifies CORS pre-flight and OpenAPI registration.
- All 22 focused tests pass: `pytest -q test_bff_management_delta_routes.py
  test_execute_plans_final_live_wiring_contract.py → 22 passed`.

### Audit Docs

- Both `BFF_API_GAP_delta_audit_spec.md` and `bff-backend-gap-2026-05-24-delta.md` have
  correct BFF-MGMT-DELTA-008 sections describing scope, JSON contract envelope, TS surface,
  and composition sources.

### Pre-existing Dirty Index

The working tree has stale staged deletions in `.git/index` for all 7 task files (the
inverse of the task diff). This is a shared-index artifact from a prior interrupted worker —
not introduced by this commit. HEAD 27679b92 is clean. The owner should be aware of this
during closeout and use `worker_commit.py --scope` to prevent absorbing the stale staging.

## Decision

**Approved.** Implementation is complete, correct, and consistent with the BFF management
route pattern. Tests pass. Docs updated. Ready for owner closeout via `task_finalize.sh`.
