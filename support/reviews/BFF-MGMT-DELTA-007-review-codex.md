# Review: BFF-MGMT-DELTA-007 - GET /bff/management/governance-ledger

Reviewer: Codex
Owner: Codex2
Date: 2026-05-24
Status: approved

## Summary

Reviewed the governance ledger implementation merged by PR #541 at
`b8009c57f5bf183bb3c866076b604a70f2fa3b72`.

The implementation adds a read-only Management Console aggregate route that
composes approval queue/decision records, v5 interventions, and governance
audit events for approval, intervention, and override activity. It does not add
a new governance source of truth or any write path.

## Acceptance Criteria Verification

| # | Criterion | Result |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | Pass - `@app.get("/bff/management/governance-ledger")` is registered and the OpenAPI test checks the path. |
| 2 | Ledger unifies approvals, interventions, and override audit entries | Pass - `_management_governance_ledger_response()` composes `list_approval_queue_items()`, `list_approval_decisions()`, `_v5_intervention_records()`, and `_list_governance_audit_events()`. |
| 3 | Accepts `source_type`, `status`, `q`, `page_token`, and `page_size` | Pass - all five query params are present; filtering and pagination are applied before envelope construction. |
| 4 | Anonymous request returns HTTP 401 | Pass - covered by `test_governance_ledger_unifies_approval_intervention_and_override_sources`. |
| 5 | Authenticated request returns HTTP 200 | Pass - covered by the same success test with `HEADERS`. |
| 6 | Response keeps canonical aggregate envelope | Pass - response exposes `data`, top-level `items`/`entries`/`ledger`, `summary`, `page_info`, and `meta`; aliases point at the same page items. |
| 7 | CORS preflight returns HTTP 204/200 | Pass - `test_governance_ledger_cors_preflight_and_openapi` verifies the preflight response and allowed origin. |
| 8 | Focused pytest covers success, auth, preflight, OpenAPI, and execute-plans exports | Pass - route tests and live wiring contract checks are present. |
| 9 | execute-plans exposes typed path and fetch helpers | Pass - `ManagementGovernanceLedgerQuery`, `ManagementGovernanceLedgerResponse`, `managementGovernanceLedgerPath`, and `fetchManagementGovernanceLedger` are exported. |

## Verification

```bash
git diff --check b8009c57^1..b8009c57
```

Result: clean.

```bash
python3 -m pytest services/control-plane/bff/test_bff_management_delta_routes.py \
  services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py -q
```

Result: 81 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.

## Implementation Review

- `services/control-plane/bff/main.py` builds stable ledger entries with both
  camelCase and snake_case aliases for source, target, timestamps, evidence,
  audit context, and source record fields.
- Approval entries are projected from both queue items and decisions, keyed by
  decision id to avoid duplicate approval ledger rows.
- Intervention entries use the existing v5 intervention read surface and link
  to `/bff/v5/interventions/{id}`.
- Audit-derived entries are limited to approval, intervention, and override
  activity by action, target, or route metadata, which keeps the endpoint scoped
  to governance-ledger activity rather than a raw audit dump.
- Filtering is case-insensitive for `source_type` and `status`; `q` searches
  stable operator-facing fields.
- Pagination uses the existing integer-offset `page_token` helper and keeps
  summary counts based on the filtered full result set.
- Surface metadata records contributing approval, audit, intervention, and
  override surfaces, with `meta.policy == "read_only_governance_ledger"`.
- TypeScript client additions follow the existing management helper pattern:
  path builder, query type, response type, fetch helper, and `Accept:
  application/json`.
- Task commits include required trailers for `LLM-Agent: Codex2`,
  `Task-ID: BFF-MGMT-DELTA-007`, `Reviewer: Codex`, and `Verified`.

## Decision

Approved. All acceptance criteria are satisfied and focused validation passes.
Returning to Codex2 for owner finalization.
