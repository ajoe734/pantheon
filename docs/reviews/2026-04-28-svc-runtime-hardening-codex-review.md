# SVC-RUNTIME-HARDENING Codex Review

Date: 2026-04-28
Reviewer: Codex
Disposition: changes requested

## Findings

1. `ApproveDeployment` is not wired to the canonical governance approval service URL.

   The service-family contract and compose wiring use `PANTHEON_GOVERNANCE_APPROVAL_API_URL` for the governance ApprovalDecision API. The new client in `services/control_plane/internal_api.py` only checks `PANTHEON_GOVERNANCE_API_URL` and `PANTHEON_DEPLOYMENT_API_URL`, and the runtime-manager compose service does not set either variable. In the default stack, BFF dispatches `ApproveDeployment` to runtime-manager, but runtime-manager will return `GOVERNANCE_API_UNCONFIGURED`. If `PANTHEON_DEPLOYMENT_API_URL` is set, the code still calls `/api/governance/approvals/...` against the deployment service URL, which is also not an authoritative governance endpoint.

   Required change: prefer `PANTHEON_GOVERNANCE_APPROVAL_API_URL` for ApprovalDecision calls, keep any legacy alias only as a fallback if still needed, and wire the runtime-manager service environment in compose. Do not send `/api/governance/approvals` traffic to `PANTHEON_DEPLOYMENT_API_URL` unless a real deployment-owned approval endpoint is implemented and tested.

2. `ApproveDeployment` forwards BFF/runtime auth roles that the governance API rejects.

   The new route derives `actor_role` as `approver`, `admin`, or `risk_owner`. The governance service request model accepts only `governance_reviewer`, `risk_owner`, `governance_committee`, or `automated_gate`. The current tests mock `_governance_request`, so they do not catch the real FastAPI/Pydantic 422 that an `approver` or `admin` token will produce. This means the common BFF token shape `Bearer <operator>:approver` cannot approve through the authoritative governance service.

   Required change: either require an explicit governance `actor_role` compatible with the governance API or map BFF/operator roles to valid governance roles before calling `/review` and `/decide`. Add a test that exercises the real governance request payload shape, not only a permissive mock.

3. The lookup assumes `ApprovalDecision.target_type=DeploymentPlan`, but governance cannot create that target type.

   `_find_open_deployment_decision` searches `target_type=DeploymentPlan`, while the governance service contract and enum support `registry_entry`, `strategy_spec`, `model_artifact`, `allocation_policy`, `persona_capital_binding`, and `evolution_proposal`. A normal governance-created ApprovalDecision cannot match this lookup, so the unqualified approval flow returns `NO_OPEN_APPROVAL` even when the canonical artifact approval exists. If `ApproveDeployment` is meant to approve the DeploymentPlan status, the deployment service owns `POST /api/deployment/plans/{plan_id}/status`; if it is meant to decide an ApprovalDecision, the BFF must pass the real decision id/target semantics.

   Required change: choose one authority path and align the route with it: governance ApprovalDecision by actual decision id/valid target type, or deployment plan status through the deployment API. Cover the real-service path in tests.

## Verification

- `python3 -m pytest services/runtime-manager/test_runtime_hardening.py services/runtime-manager/test_internal_api_routes.py services/control-plane/bff/test_command_executor.py services/governance/test_governance_api.py services/control-plane/governance/test_service_family_contract.py`
- Result: 69 passed, 1 warning.

The tests passing is not sufficient for approval because the failing behavior is at the service-family wiring and governance API contract boundary.
