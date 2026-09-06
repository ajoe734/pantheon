# Deployment Service API Contract

Last updated: 2026-07-26
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

## Authentication and tenant isolation

All `/api/deployment/*` requests require a bearer-authenticated service or
operator identity and an explicit `X-Tenant-Id` header. The authenticated actor
is authoritative; caller-supplied `created_by` or dispatch `actor_id` values
cannot override it.

The tenant boundary is persisted on DeploymentPlan metadata and copied into the
DeploymentSaga. Plan, saga, projection, outbox, and inbox reads expose only the
request tenant. A cross-tenant point lookup behaves as `404 Not Found`, while a
mutation whose ApprovalDecision, DeploymentPlan, or saga belongs to another
tenant is rejected.

Authentication configuration uses `PANTHEON_DEPLOYMENT_AUTH_*`, falling back to
the shared BFF/runtime inbound-auth configuration. The accepted role family is
defined in `services/deployment/auth.py`.

Promotion `/api/v1/*` mutation and read routes use the same boundary with
`PANTHEON_PROMOTION_AUTH_*`; ApprovalDecision and promotion DeploymentPlan rows
are tenant-filtered before they can feed this service.

---

## Service Boundary

| Concern | Owner |
|---|---|
| DeploymentPlan create / validate / read | **Deployment Service** |
| Stage-transition validation | **Deployment Service** via canonical `StagePlanner` |
| DeploymentSaga bootstrap + local outbox append | **Deployment Service** via canonical `DeploymentSagaStore` |
| Inbox dedupe / per-saga ordering receipts | **Deployment Service** |
| Outbox exclusive lease / ack / idle recovery receipts | **Deployment Service** |
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

### `POST /api/deployment/stage-planner/check`

Focused DEP-002-RB stage-rule check for callers that need planner truth before
they have a full registry / approval payload.

Request body:

- `current_stage` (required; `none`, `paper`, `canary`, `live`, or `frozen`)
- `target_stage` (required; `none`, `paper`, `canary`, `live`, or `frozen`)
- optional `rollback_action` (`replace`, `pause_then_replace`, or
  `liquidate_then_replace`)
- optional `scale` override to check stage-specific scale caps

Response fields:

- `ok`
- `ruleset = DEP-002-RB-stage-planner-v1`
- `transition_type`
- `runtime_action`
- `rollback_required`
- `default_scale`
- `effective_scale`
- `errors[]`

Rules checked:

- forbidden transitions such as `none -> canary`, `paper -> live`, and no-op
  stage changes
- active targets (`paper`, `canary`, `live`) require rollback linkage
- rollback action controls rollback-transition runtime action
- paper / canary / live / frozen scale defaults and hard caps

This route is read-only and never creates a `DeploymentPlan`.

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

- CapitalPool lookup uses `PANTHEON_CAPITAL_POOL_STORE_PATH` when present;
  otherwise `capital_pools.json` comes from `CAPITAL_DATA_DIR`, then
  `DEPLOYMENT_DATA_DIR`, then `PANTHEON_GOVERNANCE_DATA_DIR`, then
  `/tmp/pantheon/governance`
- PersonaCapitalBinding lookup uses `PANTHEON_PERSONA_BINDING_STORE_PATH` when
  present; otherwise `persona_capital_bindings.json` uses the same directory
  precedence
- RuntimeBinding lookup uses `PANTHEON_RUNTIME_BINDING_STORE_PATH` when present,
  then `${PANTHEON_RUNTIME_DATA_DIR}/runtime_bindings.json`, then
  `/tmp/pantheon/runtime-manager/bindings.json`
- the default Compose deployment binds the Capital service's `capital-data`
  volume at `/data/capital:ro` and Runtime Manager's `runtime-data` volume at
  `/data/runtime:ro`; both are composition-only snapshots and remain owned by
  their source services

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

List outbox events visible to the authenticated tenant. Dispatch consumers
must use the claim endpoint rather than this inspection route.

Supported filters:

- `owner_service`
- `aggregate_id`
- `status`

Returned events are ordered by `(aggregate_id, sequence_no)`.

---

### `POST /api/deployment/outbox/claim`

Transactionally claim currently due, pending outbox events for one consumer.

Request body:

- `consumer_name` (required)
- `lease_seconds` (default `60`, range `1..3600`)
- `limit` (default `25`, range `1..250`)
- optional `aggregate_id`

Each returned record keeps the canonical outbox `status` and additionally
includes `tenant_id`, `claim_token`, `lease_status`, `claimed_at`,
`lease_expires_at`, and `recovery_count`.

Claim invariants:

- a process-safe file lock and atomic replace serialize lease changes
- no two consumers can hold an active lease for the same event
- events whose `next_retry_at` is in the future are not claimable
- an expired lease is released with
  `release_reason=lease_expired_idle_recovery` and may be reclaimed
- the former claim token cannot acknowledge a recovered lease

The canonical outbox remains in `deployment_sagas.json`. The lease ledger adds
exclusive delivery ownership in `deployment_outbox_leases.json`; it does not
become a second event source.

---

### `GET /api/deployment/outbox/lease-health`

Return lease counts and recovery state for the authenticated service/operator:

- `active_claim_count`
- `acknowledged_claim_count`
- `released_claim_count`
- `recovered_claim_count`
- `recovered_this_check`
- `oldest_active_claimed_at`
- `updated_at`

The general service health dependency exposes the same ledger and the
`outbox_lease_recovered_count` metric.

---

### `POST /api/deployment/outbox/{event_id}/consume`

Apply the DEP-002 inbox rule to one outbox event for one consumer.

Request body:

- `consumer_name` (must own the active lease)
- `claim_token` (required when lease enforcement is enabled, which is the
  default)

Consumer behavior:

- duplicate `event_id` / `idempotency_key` -> receipt `duplicate`
- sequence gap -> receipt `out_of_order`
- next expected sequence -> receipt `applied`

An `applied` or `duplicate` inbox receipt marks the canonical outbox record
published and acknowledges the lease. A non-applied receipt releases the lease.
If the canonical receipt/publish commit succeeds but the response or lease ack
is lost, the published outbox record is no longer claimable and duplicate inbox
handling remains idempotent.

---

### `POST /api/deployment/outbox/{event_id}/failure`

Persist retry, dead-letter, and operator-visible delivery state for the active
claim, then release its lease.

Request body:

- `consumer_name`
- `claim_token`
- `reason`
- `retryable`
- optional `max_attempts`
- optional `retry_delay_seconds`

The canonical outbox exposes `delivery_attempts`, `last_error`,
`next_retry_at`, `blocked_reason`, `dlq_at`, and retry policy fields.

---

### `POST /api/deployment/outbox/{event_id}/replay`

Operator-governed replay of a dead-lettered event. Replay is tenant-scoped and
does not bypass inbox ordering or downstream authoritative readback.

---

### `GET /api/deployment/inbox`

List inbox receipts written by consumers.

Supported filters:

- `consumer_name`
- `aggregate_id`
- `status`

---

### Default outbox dispatcher convergence contract

The supervised `deployment-outbox-consumer` owns the apply side effect for
`runtime.binding.requested`, `runtime.load.requested`, and
`deployment.compensation.requested`.

It must be configured with a non-empty `PANTHEON_RUNTIME_MANAGER_URL` and send
all RuntimeBinding commands/readbacks to that remote authority.  Missing remote
configuration is fail-closed; the consumer must not instantiate an in-process
Runtime Manager or fall back to a local binding store.

The worker additionally requires `PANTHEON_DEPLOYMENT_SERVICE_TOKEN` and
`PANTHEON_DEPLOYMENT_TENANT_ID` for every Deployment API call. Claim duration
and batch size use `DEPLOYMENT_OUTBOX_CONSUMER_LEASE_SECONDS` and
`DEPLOYMENT_OUTBOX_CONSUMER_CLAIM_LIMIT`. The later manifest integration must
wire these values before accepting the dispatcher as active.

Compose startup waits only for the unconditional Deployment and Runtime
Manager authorities. Deployment completes a paper plan from the authoritative
RuntimeBinding and DEP-003 readback; the Capital paper-fleet reconciler is the
next consumer and owns worker-start readback. Incident service reachability is
required only for the safe-mode compensation path. An unavailable conditional
target fails the affected event closed and follows its retry policy; it does
not prevent the consumer process from starting or handling unrelated events.
At retry exhaustion, the worker first persists saga compensation and then
acknowledges the failed predecessor so the compensation successor can run. It
never DLQs a side-effect predecessor while leaving its successor
sequence-blocked.

Forward dispatch invariants:

- caller- and DeploymentPlan-authored `loader_checks_passed` values are not
  admission proof and are not propagated as authority
- before dispatch, the consumer reads four canonical owners and requires exact,
  target-bound agreement:
  - Deployment: the fetched DeploymentPlan is approved/executing and matches
    the saga's plan, strategy, artifact/version, approval, pool, persona, and
    paper target
  - Registry: the exact entry is an approved execution bundle, carries the
    same approval and identity, and embeds a schema-valid StrategyArtifact whose
    computed checksum matches the recorded checksum
  - Governance: the exact ApprovalDecision is decided/approved, unconditional,
    unrevoked, unexpired, and bound to the artifact version, pool, and persona
  - Capital: the exact active pool enforces one runtime, admissibility is
    permitted, and the exact active PersonaCapitalBinding and scope permit paper
- the canonical report records those identities plus SHA-256 digests of the
  DeploymentPlan, Registry entry, ApprovalDecision, CapitalPool, admissibility
  response, and PersonaCapitalBinding; Runtime Manager independently repeats
  the four-owner reads before accepting the write and persists the report as
  `metadata.authoritative_loader_attestation`
- binding-created response-loss recovery permits only the canonical
  `approved -> executing` plan lifecycle change.  `current_stage` and every
  immutable authority field remain digest-covered; the current plan's
  `binding_id` and `metadata.runtime_lifecycle` must exactly match the recovered
  RuntimeBinding before the predecessor receipt can be written
- tenant and the dispatch foundation correlation id are copied into
  RuntimeBinding request metadata, and must agree with the authenticated
  dispatcher tenant before a side effect is attempted
- every newly created RuntimeBinding is paper-only; canary/live requires a
  separate target-bound governed promotion/cutover verifier with the required
  MFA/two-person proof
- forward replace, evolution, rollback, kill-switch fallback, replay, and
  response-loss recovery must preserve exact canonical paper lineage or fail
  closed; a reference string, copied metadata, or self-authored fallback proof
  cannot authorize a new binding
- Runtime Manager POST bodies are submission receipts only
- success requires a separate GET of the exact active RuntimeBinding, including
  plan, pool, artifact/version, deployment/execution mode, and governance
  binding identity, plus exact canonical authority identities and digests
- paper success additionally requires a completed fleet reconciliation cycle
  and exactly one matching running worker with a live process id
- the worker then requires a joined DEP-003 projection whose plan, saga, and
  runtime sources are canonical and terminal before it writes the inbox receipt

Compensation invariants:

- the applied inbox sequence is read before any side effect; an earlier DLQ
  must be replayed first and the blocked event does not consume retry budget
- a terminal binding/load failure is handed off durably to saga compensation
  before its predecessor receipt is written.  If receipt persistence or its
  response fails after handoff, replay retries the receipt and never converts
  the predecessor to a DLQ record
- `abort_plan` proves no RuntimeBinding exists, then writes only
  `DeploymentPlan.status = aborted`
- `mark_binding_failed_inactive` writes only the exact RuntimeBinding status and
  requires authoritative `failed` readback
- `request_rollback` uses only `DeploymentPlan.rollback` and exact prior
  fallback binding/plan/artifact lineage.  The fallback must resolve to one
  retired paper RuntimeBinding with a matching persisted four-authority
  `authoritative_loader_attestation`; plan-authored/self-attested loader proof
  is ignored.  Response-loss recovery finds the unique rollback child before
  any new POST, and missing/ambiguous proof routes to containment rather than a
  blind replacement
- `enter_safe_mode_and_raise_incident` requires acknowledged kill-switch
  follow-through, paused safe-mode/binding GET readback, and an exact stable
  IncidentCase
- finalize happens before consume; replay of a completed runtime-load event
  revalidates the active binding, paper fleet when applicable, and terminal
  DEP-003 projection before writing its receipt, without repeating mutation
- a crash after RuntimeBinding creation and saga `binding-created` state but
  before claim acknowledgement is recovered by lease expiry; the next
  dispatcher reads the recorded binding and uses the idempotent readback path,
  never a second `deploy`

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
- `deployment_outbox_leases.json`
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
4. L12-DEP-001 adds authenticated tenant ownership plus exclusive claim,
   acknowledgement, idle recovery, and crash-after-side-effect replay proof

## First-release owner approval references

Plan creation accepts `registry_id` and `approval_decision_id`. Caller Registry
or ApprovalDecision objects are rejected, including dispatch `registry_entry`.
Deployment reads the exact scoped Registry owner and the shared Governance
ApprovalReader; local Registry/approval JSON files are not production authority
or projection sources. Dispatch rechecks immutable version, tenant, approved
Registry linkage and current Governance validity before creating any saga or
outbox event. A historical approved Registry entry cannot override a revocation.
Projection reads those same owners and reports missing sources honestly.

Configure `DEPLOYMENT_REGISTRY_BASE_URL`, `DEPLOYMENT_REGISTRY_SERVICE_TOKEN`,
`DEPLOYMENT_REGISTRY_TIMEOUT_SECONDS`, `DEPLOYMENT_GOVERNANCE_BASE_URL`,
`DEPLOYMENT_GOVERNANCE_SERVICE_TOKEN` and `DEPLOYMENT_GOVERNANCE_TIMEOUT_SECONDS`.
Tokens are scoped principals supplied by deployment configuration; no token is
stored in the plan, receipt, audit or evidence. Missing configuration fails
closed. The isolated dispatcher test exercises real Governance/Registry HTTP,
Deployment stores and the shared Runtime verifier; capital/lifecycle doubles
are explicitly identified and do not establish a hosted trading lifecycle.
