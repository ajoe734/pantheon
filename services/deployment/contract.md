# Deployment Service API Contract

Last updated: 2026-04-15
Status: canonical API contract for BP5-SVC-004
Owner: Codex
Reviewer: Claude

---

## Purpose

This document is the authoritative contract for the deployable
`services/deployment/` service.

The service exposes the canonical `DeploymentPlan` planner as an HTTP API so
callers can create, validate, list, read, and advance deployment plans without
importing the control-plane domain module directly.

The policy and stage semantics still come from:

- `services/control-plane/governance/deployment_plan.contract.md`
- `PAPER_CANARY_LIVE_POLICY.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`

This service owns the API surface and file-backed persistence only.

---

## Service Boundary

| Concern | Owner |
|---|---|
| DeploymentPlan create / validate / read | **Deployment Service** |
| Stage-transition validation | **Deployment Service** via canonical `StagePlanner` |
| Rollback linkage enforcement | **Deployment Service** via canonical `DeploymentPlan` validation |
| ApprovalDecision lifecycle | `services/governance/` |
| Registry artifact lifecycle | `services/registry/` |
| RuntimeBinding writes / execution | Runtime Manager / execution plane |

---

## Routes

### `POST /api/deployment/plans`

Create and persist a `DeploymentPlan`.

Request body:

- `approval_decision_id` (required)
- `target_stage` (required)
- either:
  - `registry_entry`, or
  - `registry_id` that resolves from `PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH`
- optional `approval_decision`; otherwise the service resolves the decision from
  `${DEPLOYMENT_DATA_DIR|PANTHEON_GOVERNANCE_DATA_DIR}/approval_decisions.json`
- optional planner overrides: `current_stage`, `schedule_window`, `scale`,
  `rollback`, `pre_checks`, `post_checks`, `metadata`, `supersedes_plan_id`

Response:

- `201 Created` with the stored `DeploymentPlan`

Errors:

- `422 Unprocessable Entity` for invalid stage transitions, missing rollback
  linkage, approval mismatches, or duplicate `plan_id`

---

### `POST /api/deployment/plans/validate`

Dry-run validation for a would-be `DeploymentPlan`.

Uses the same request shape as `POST /api/deployment/plans` but does not persist
the plan.

Response:

```json
{
  "ok": true,
  "plan": { "...would-be DeploymentPlan..." },
  "errors": []
}
```

When validation fails, `ok = false`, `plan = null`, and `errors[]` contains the
planner / validation failures.

---

### `GET /api/deployment/plans`

List stored plans. Supported filters:

- `strategy_id`
- `capital_pool_id`
- `target_stage`
- `status`

Returns newest-first.

---

### `GET /api/deployment/plans/{plan_id}`

Fetch one stored plan by id.

Errors:

- `404 Not Found`

---

### `POST /api/deployment/plans/{plan_id}/status`

Advance only the `status` field of a stored plan.

Allowed transitions:

- `draft -> approved | rejected | aborted`
- `approved -> executing | rejected | aborted`
- `executing -> executed | failed | aborted`

Terminal states do not allow further transitions.

Errors:

- `404 Not Found` when the plan does not exist
- `400 Bad Request` for invalid status transitions

---

### `GET /api/deployment/strategies/{strategy_id}/read-model`

Return a strategy-scoped deployment read model.

Supported query parameter:

- `capital_pool_id`

Response fields:

- `strategy_id`
- `capital_pool_id`
- `current_stage`
- `latest_plan_id`
- `active_plan_id`
- `latest_target_stage`
- `latest_transition_type`
- `latest_status`
- `plan_count`
- `plans[]` (summary rows, newest-first)

Read-model rule:

- if at least one plan is `executed`, `current_stage` becomes the newest
  executed plan's `target_stage`
- otherwise `current_stage` reflects the newest plan's `current_stage`

---

### `GET /health`

Liveness probe.

Response:

```json
{"status": "ok", "service": "pantheon-deployment"}
```

---

## Storage

The service persists `deployment_plans.json` to:

1. `DEPLOYMENT_DATA_DIR`
2. else `PANTHEON_GOVERNANCE_DATA_DIR`
3. else `/tmp/pantheon/governance`

This aligns with the shared file-backed baseline contract used by the operator
BFF for canonical snapshots.
