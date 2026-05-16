# Deployment Service API Contract

Last updated: 2026-04-15
Status: canonical API contract for BP5-SVC-004 and BP5-SVC-005
Owner: Codex
Reviewer: Claude

---

## Purpose

This document is the authoritative contract for the deployable
`services/deployment/` service.

The service exposes two canonical surfaces:

- `DEP-001` `DeploymentPlan` create / validate / read / status APIs
- `DEP-002` deployment saga dispatch / progress / outbox / inbox APIs
- `DEP-003` deployment projection read model APIs
- `CAP-002-RB` pool/runtime compatibility preflight API

The governing semantics still come from:

- `services/control-plane/governance/deployment_plan.contract.md`
- `services/control-plane/governance/deployment_saga.contract.md`
- `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`
- `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`

This service owns the deployable HTTP surface and file-backed persistence only.

---

## Service Boundary

| Concern | Owner |
|---|---|
| DeploymentPlan create / validate / read | **Deployment Service** |
| Stage-transition validation | **Deployment Service** via canonical `StagePlanner` |
| DeploymentSaga bootstrap + local outbox append | **Deployment Service** via canonical `DeploymentSagaStore` |
| Inbox dedupe / per-saga ordering receipts | **Deployment Service** |
| Compensation decision derivation | **Deployment Service** via canonical DEP-002 policy logic |
| Deployment projection read model | **Deployment Service** derived-only composition |
| Pool/runtime compatibility preflight | **Deployment Service** read-only composition over capital and runtime snapshots |
| ApprovalDecision lifecycle | `services/governance/` |
| Registry artifact lifecycle | `services/registry/` |
| RuntimeBinding writes / execution | Runtime Manager / execution plane |
| Rollback command application | rollback controller + runtime manager |

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

### `POST /api/deployment/plans/compatibility-check`

Read-only preflight for DeploymentPlan approval. The route checks:

- `CapitalPool` exists and has governance `status = active`
- the sponsoring persona has an active `PersonaCapitalBinding` for the pool
- the binding's `allowed_deployment_scope` permits target `paper`, `canary`, or
  `live`
- the current RuntimeBinding snapshot does not violate the pool's
  `single_runtime_enforced` invariant

Request body:

- `capital_pool_id` (required)
- `target_stage` (required; `paper`, `canary`, or `live` for this preflight)
- `sponsor_persona_id` (required for compatibility truth; missing value returns
  `ok = false`)

Response fields:

- `ok`
- pool facts: `pool_found`, `pool_status`, `pool_active`,
  `single_runtime_enforced`
- persona facts: `persona_binding_found`, `persona_scope_ok`,
  `persona_binding_id`, `allowed_deployment_scope`
- runtime facts: `active_runtime_binding_count`,
  `active_runtime_binding_ids`, `single_runtime_ok`
- `errors[]` and `warnings[]`

Read-only rule:

- this route never writes `CapitalPool`, `PersonaCapitalBinding`,
  `DeploymentPlan`, or `RuntimeBinding`
- exactly one active RuntimeBinding is reported as compatible with the current
  pool invariant but emits a warning because dispatch must use the appropriate
  replace/freeze/resume/rollback path instead of creating a second active
  binding

Snapshot lookup:

- `capital_pools.json` and `persona_capital_bindings.json` come from
  `CAPITAL_DATA_DIR`, then `DEPLOYMENT_DATA_DIR`, then
  `PANTHEON_GOVERNANCE_DATA_DIR`, then `/tmp/pantheon/governance`
- RuntimeBinding lookup uses `PANTHEON_RUNTIME_BINDING_STORE_PATH` when present,
  then `${PANTHEON_RUNTIME_DATA_DIR}/runtime_bindings.json`, then
  `/tmp/pantheon/runtime-manager/bindings.json`

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

### `POST /api/deployment/plans/{plan_id}/dispatch`

Bootstrap the canonical DEP-002 deployment saga for an approved plan.

Dispatch behavior:

- resolves the plan's registry entry from the request body or registry snapshot
- builds the canonical execution projection from `StagePlanner`
- creates `DeploymentSaga` plus the first `runtime.binding.requested` outbox
  event atomically
- returns the canonical deployment request envelope used by downstream
  orchestration

Request body:

- optional `trace_id`
- optional `saga_id`
- optional `workflow_id`
- optional `source_task_id`
- optional `metadata`
- optional `registry_entry` override when snapshot lookup is unavailable

Response fields:

- `plan`
- `strategy_id`
- `version`
- `target_stage`
- `execution_context`
- `artifact_loader_contract = EX-001`
- `deployment_contract = DEP-001`
- `consistency_contract = DEP-002`
- `execution_projection`
- `deployment_saga`
- `replayed`

Idempotency rule:

- if the same `plan_id` / `saga_id` has already been dispatched, the service
  returns the existing saga bootstrap with `replayed = true`
- no duplicate bootstrap outbox event is appended

Errors:

- `404 Not Found` when the plan does not exist
- `400 Bad Request` when the plan is not dispatchable or the registry entry
  cannot be resolved

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

### `GET /api/deployment/projections`

List DEP-003 deployment projection read models.

Supported filters:

- `strategy_id`
- `capital_pool_id`
- `target_stage`
- `status`

Response shape:

- `projection_contract = DEP-003`
- `derived_only = true`
- plan identity, artifact, approval, stage, status, and lifecycle summary fields
- `source_status` for `deployment_plan`, `approval_decision`, `registry_entry`,
  `execution_projection`, `deployment_saga`, and `runtime_binding`
- embedded `plan`
- optional embedded `approval_decision`, `runtime_binding`, `deployment_saga`,
  and `execution_projection`

Read-model rule:

- `DeploymentPlan` remains the only deployment-plan truth
- `ApprovalDecision`, registry snapshot, runtime binding, and saga state are
  joined read-only when their stores are available
- `actual_stage` comes from RuntimeBinding when present, otherwise from executed
  plan state, otherwise from `DeploymentPlan.current_stage`
- `projected_stage` is always `DeploymentPlan.target_stage`
- the projection never writes plan, approval, runtime, registry, or saga records

RuntimeBinding lookup uses `PANTHEON_RUNTIME_BINDING_STORE_PATH` when present,
then `${PANTHEON_RUNTIME_DATA_DIR}/runtime_bindings.json`, then
`/tmp/pantheon/runtime-manager/bindings.json`.

### `GET /api/deployment/projections/{plan_id}`

Fetch one DEP-003 projection by deployment plan id.

Alias:

- `GET /api/deployment/plans/{plan_id}/projection`

Errors:

- `404 Not Found` when the plan does not exist

---

### `GET /api/deployment/sagas`

List stored deployment sagas.

Supported filters:

- `plan_id`
- `status`

Returns newest-first.

---

### `GET /api/deployment/sagas/{saga_id}`

Fetch one stored saga by id.

Errors:

- `404 Not Found`

---

### `POST /api/deployment/sagas/{saga_id}/binding-created`

Record that the runtime binding exists and append the next outbox event:

- saga moves to `awaiting_runtime_load`
- outbox emits `runtime.load.requested`

Request body:

- `binding_id` (required)
- optional `runtime_id`
- optional `note`

---

### `POST /api/deployment/sagas/{saga_id}/runtime-active`

Record successful activation and append the terminal success outbox event:

- saga moves to `completed`
- outbox emits `deployment.saga.completed`

Request body:

- optional `binding_id`
- optional `runtime_id`
- optional `note`

---

### `POST /api/deployment/sagas/{saga_id}/failure`

Record a saga failure and emit the compensation-request outbox event.

Request body:

- `reason` (required)
- optional `failed_step`

Response:

- canonical `CompensationDecision`

Compensation uses DEP-002's owner-scoped matrix:

- binding create failure -> `abort_plan`
- runtime load failure -> `mark_binding_failed_inactive`
- post-activation failure -> `request_rollback`
- failed compensation / non-converging rollback -> `enter_safe_mode_and_raise_incident`

---

### `POST /api/deployment/sagas/{saga_id}/compensation/finalize`

Finalize the compensation path and append the terminal failure / abort event.

Request body:

- optional `note`
- optional `terminal_status`

Default terminal status:

- `aborted` for `abort_plan`
- `failed` for all other compensation commands

---

### `GET /api/deployment/outbox`

List pending outbox events.

Supported filters:

- `owner_service`
- `aggregate_id`

Returned events are ordered by `(aggregate_id, sequence_no)`.

---

### `POST /api/deployment/outbox/{event_id}/consume`

Apply the DEP-002 inbox rule to one outbox event for one consumer.

Request body:

- `consumer_name`

Consumer behavior:

- duplicate `event_id` / `idempotency_key` -> receipt `duplicate`
- sequence gap -> receipt `out_of_order`
- next expected sequence -> receipt `applied`

The endpoint writes the durable inbox receipt but does not mark the outbox
record as published.

---

### `GET /api/deployment/inbox`

List inbox receipts written by consumers.

Supported filters:

- `consumer_name`
- `aggregate_id`
- `status`

---

### `GET /health`

Liveness probe.

Response:

```json
{"status": "ok", "service": "pantheon-deployment"}
```

---

## Storage

The service persists files to:

1. `DEPLOYMENT_DATA_DIR`
2. else `PANTHEON_GOVERNANCE_DATA_DIR`
3. else `/tmp/pantheon/governance`

Files:

- `deployment_plans.json`
- `deployment_sagas.json`
- `approval_decisions.json` (lookup only unless upstream governance writes it)

This aligns with the shared file-backed baseline contract used by the operator
BFF for canonical snapshots.

---

## Acceptance Anchors

BP5-SVC-005 closes the deployable gap when:

1. deployment dispatch uses an explicit transactional outbox path
2. outbox / inbox receipts make duplicate replay and out-of-order delivery
   observable
3. compensation paths are exposed and tested through the deployable API
