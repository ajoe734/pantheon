# SVC-RUNTIME-HARDENING Codex Re-review

Date: 2026-04-28
Reviewer: Codex
Disposition: approved

## Scope

Re-reviewed the handoff that moved `ApproveDeployment` to deployment-plane
authority via `POST /api/deployment/plans/{plan_id}/status`, removed the
invalid governance `DeploymentPlan` target lookup, and wired the deployment and
governance approval service URLs into compose.

## Findings

No blocking findings remain.

The prior review findings are addressed:

- `ApproveDeployment` now calls the authoritative deployment service plan-status
  endpoint instead of fabricating placeholder approval ids.
- The invalid governance `target_type=DeploymentPlan` lookup has been removed.
- Runtime-manager compose wiring includes `PANTHEON_DEPLOYMENT_API_URL` and
  `PANTHEON_GOVERNANCE_APPROVAL_API_URL`.
- A contract test validates the route's wire payload against
  `services.deployment.models.UpdatePlanStatusRequest`.

During re-review I found and fixed one additional auth hardening gap: in
permissive mode, a JWT-shaped bearer token without a configured JWT secret was
being treated as a legacy structured token and granted the default role. The
shared inbound auth helper now rejects unverifiable JWT-shaped tokens with
`AUTH_JWT_UNVERIFIED` while preserving legacy `actor:role` tokens, including
dotted actor ids. Coverage was added in
`services/runtime-manager/test_runtime_hardening.py`.

## Verification

Command:

```bash
python3 -m pytest services/runtime-manager/test_runtime_hardening.py services/runtime-manager/test_internal_api_routes.py services/control-plane/bff/test_command_executor.py services/governance/test_governance_api.py services/control-plane/governance/test_service_family_contract.py
```

Result:

- 72 passed
- 4 warnings, all existing `datetime.utcnow()` deprecation warnings in
  `services/control_plane/internal_api.py`

