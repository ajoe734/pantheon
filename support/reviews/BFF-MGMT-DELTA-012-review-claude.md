# Review: BFF-MGMT-DELTA-012 — GET /bff/management/intervention-stream

Reviewer: Claude
Task: BFF-MGMT-DELTA-012
Owner: Codex
Date: 2026-05-24
Status: APPROVED

## Scope

Review of the `GET /bff/management/intervention-stream` BFF Management route
implementation for Management Console strict-live rendering.

## Reviewed Artifacts

- `services/control-plane/bff/main.py` — backend route and composition helpers
- `execute-plans/src/lib/bff-v1/management.ts` — TypeScript interfaces and fetch helpers
- `services/control-plane/bff/test_bff_management_delta_routes.py` — focused route tests
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py` — live-wiring contract tests
- `docs/04/pantheon_bff_api_gap_2026-05-24_delta/BFF_API_GAP_delta_audit_spec.md` — audit spec (DELTA-012 section)
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md` — delta audit record

## Acceptance Criteria Verification

| # | Criterion | Verdict |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | PASS — `@app.get("/bff/management/intervention-stream")` at line 27382 |
| 2 | Composes recent intervention records and intervention audit events | PASS — `_intervention_stream_record_event` and `_intervention_stream_audit_event` compose v5 interventions and governance audit events |
| 3 | Default window is 24 hours; summary groups events by persona | PASS — `window_hours=24` default, `by_persona` dict in summary |
| 4 | Accepts `persona_id`, `status`, `kind`, `q`, `window_hours`, `page_token`, `page_size` | PASS — all parameters present in route signature with camelCase aliases |
| 5 | Anonymous request returns HTTP 401 | PASS — `_require_read_role(identity)` enforced before any data access |
| 6 | Authenticated request returns HTTP 200 | PASS — confirmed via test suite |
| 7 | Response keeps canonical aggregate envelope | PASS — `data`, `items`, `rows`, `events`, `stream`, `summary`, `page_info`, `meta` with `policy: read_only_intervention_stream` |
| 8 | CORS preflight returns HTTP 204/200 with matching allow-origin | PASS — shared BFF CORS middleware applies; confirmed via broader auth suite |
| 9 | execute-plans exposes typed path and fetch helpers | PASS — `ManagementInterventionStreamQuery`, `ManagementInterventionStreamItem`, `ManagementInterventionStreamSummary`, `ManagementInterventionStreamResponse`, `managementInterventionStreamPath`, `fetchManagementInterventionStream` all exported |

## Test Results

```
Focused suite (31 tests):
  python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q
  → 31 passed, 3 warnings

Broader suite (93 tests):
  python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py -q
  → 93 passed, 3 warnings
```

All 3 warnings are pre-existing `datetime.utcnow()` deprecation warnings in
`read_store.py` — not introduced by this task.

## Implementation Quality

- Composition helpers (`_intervention_stream_record_event`,
  `_intervention_stream_audit_event`, `_intervention_stream_item_matches`,
  `_intervention_stream_sort_key`) follow the same patterns as adjacent
  BFF-MGMT-DELTA tasks in this sprint.
- `window_hours` bounded 1–720 with `ge=1, le=720` query constraints; default
  24 hours.
- No mutation of interventions, audit records, approvals, or capital — read-only
  aggregate only.
- Source-of-truth boundary respected: composed from v5 interventions and audit
  surfaces without introducing a new intervention stream store.
- Dual snake_case/camelCase field aliasing is consistent with the rest of the
  BFF Management aggregate layer.
- TypeScript interfaces are complete and match the backend response shape.

## Decision

APPROVED. All acceptance criteria satisfied. Test suite clean. No regressions
in the broader BFF delta/auth suite. Implementation scope is correctly bounded
to the read aggregate layer without touching write paths, SSE substrate, or
canonical L1 policy docs.
