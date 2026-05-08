# BFF-LUV-GAP-005 - Governance, Runtime, Risk, Incident, Audit Compatibility

Priority: P0

Area: Operator control, governance review, risk, and audit routes

## Goal

Fill the route gap between the rich existing `/api/v1/operator/*` Pantheon BFF surface and the `/bff/*` route names expected by `execute-plans`.

## Missing Routes

Governance:

- `GET /bff/reviews`
- `POST /bff/reviews`
- `GET /bff/reviews/{reviewId}`
- `POST /bff/reviews/{reviewId}/actions/{actionId}`
- `GET /bff/reviews/{reviewId}/validators`
- `GET /bff/reviews/{reviewId}/audit`
- `GET /bff/approvals/{approvalId}/evidence`

Deployment and runtime:

- `GET /bff/deployments`
- `GET /bff/deployments/{deploymentId}`
- `POST /bff/deployments/{deploymentId}/actions/{actionId}`
- `GET /bff/runtimes`
- `GET /bff/runtimes/{runtimeId}`
- `POST /bff/runtimes/{runtimeId}/actions/{actionId}`

Risk and incident:

- `GET /bff/risk/alerts`
- `GET /bff/risk/alerts/{alertId}`
- `POST /bff/risk/alerts/{alertId}/actions/{actionId}`
- `GET /bff/incidents`
- `POST /bff/incidents`
- `GET /bff/incidents/{incidentId}`
- `POST /bff/incidents/{incidentId}/actions/{actionId}`
- `GET /bff/alerts` source-reference compatibility

Audit and commands:

- `GET /bff/audit/events`
- `GET /bff/audit/entities/{entityType}/{entityId}`
- `GET /bff/audit/export`
- `POST /bff/command-confirmations`

## Implementation Notes

- Existing routes such as `/api/v1/operator/governance/review-queue`, `/api/v1/operator/governance/approval-queue`, `/api/v1/operator/deployment-plans`, `/api/v1/operator/runtime-state`, `/api/v1/operator/alerts`, and incident streams should be adapted rather than duplicated.
- Command confirmation must align with final precondition errors and confirm-token semantics.

## Acceptance Criteria

- Exact `/bff/*` routes above exist and are tested.
- Governance and approval evidence surfaces include correlation IDs and audit references.
- Risk/action routes use final command envelope and precondition error semantics.
- Existing `/api/v1/operator/*` behavior remains backward compatible.

## Implementation Status

Status: implemented; pending reviewer approval.

Delivered in `services/control-plane/bff/main.py`:

- Added `/bff/reviews`, `/bff/approvals/{approvalId}/evidence`, `/bff/deployments`, `/bff/runtimes`, `/bff/risk/alerts`, `/bff/incidents`, `/bff/alerts`, `/bff/audit/*`, and `/bff/command-confirmations` compatibility routes.
- Action routes submit through `command_store` and return the final command envelope with `Idempotency-Key` replay/conflict semantics.
- Approval evidence responses provide `correlation_id` and an `audit_ref` fallback to `/bff/audit/entities/ApprovalDecision/{approvalId}`.
- Incident create responses are retained in a BFF overlay so create/detail/action compatibility works within the process when no backend incident writer exists.
- New `CommandType` / `ObjectType` values and action catalog entries cover review, deployment, runtime, risk-alert, and incident actions.
- `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json` now marks the BFF-LUV-GAP-005 family as `implemented`.

Verification:

- `pytest -q services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py services/control-plane/bff/test_execute_plans_contract_registry.py`
- `python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/models.py services/control-plane/bff/read_store.py services/control-plane/bff/action_catalog.py`

Known adjacent failure:

- `pytest -q services/control-plane/bff/test_action_catalog.py` still fails because pre-existing `AgoraSignalFeedback`, `AgoraMessageAction`, `AgoraInsightAction`, and `AgoraMemoryAction` `CommandType` values have no catalog entries; this predates the GAP-005 action catalog rows and belongs to the Agora route family follow-up.
