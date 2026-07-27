# Track C Go/No-Go Packet: `persona-tw-equity` Paper Activation

Task: `P0-TW-PAPER-ACTIVATE-001`
Status: decision packet only; no call below was executed by this task.

> Track C mutates registry, governance, capital, runtime, and signal-routing
> state. Every write below requires explicit operator authorization and a
> fresh production evidence bundle. The 2026-05 `MGMT-QLIB-005` bundle is a
> deterministic stub and must not be promoted.

## Decision

The recommendation is **NO-GO** until all of these are true:

- `SRCLIVE-005` proves a governed TW dataset and active source path.
- A real Qlib LightGBM run replaces `qlib-stub-62f94503afa2`.
- Evaluation evidence names the real run, dataset, checksum, StrategySpec,
  costs, and risk thresholds.
- An operator approves the paper budget, registry transitions, deployment
  plan, PersonaCapitalBinding activation, RuntimeBinding deployment, and TW
  signal repoint.
- The paper fleet reconciler and telemetry ingest are healthy.

The BFF display id
`qlib-tw-cross-sectional-alpha-model-draft-v1` is not the registry id. The old
review-only packet requested
`qlib-alpha-tw-cross-sectional-equity-alpha-1.0.0`, but no registry write was
performed. The production run must supply its own version, checksum, storage
reference, and registry id.

## Verified write-owner routes

| Object / transition | Canonical route | Contract source |
|---|---|---|
| Registry entry | `POST /api/registry/entries` | `services/registry/service.py::register_entry` |
| `draft -> candidate -> approved` | `POST /api/registry/entries/{registry_id}/advance` | `services/registry/service.py::advance_state` |
| CapitalPool | `POST /bff/capital-pools` | `services/control-plane/bff/main.py::bff_create_capital_pool` |
| pending PersonaCapitalBinding | `POST /api/v1/bindings` | `services/control-plane/bff/main.py::create_binding` |
| DeploymentPlan | `POST /api/v1/deployment-plans` | `services/control-plane/bff/main.py::create_deployment_plan_v1` |
| ApprovalDecision | `POST /api/v1/approval-decisions` | `services/control-plane/bff/main.py::create_approval_decision` |
| activate PersonaCapitalBinding | `POST /api/bindings/{binding_id}/activate` | `services/capital/main.py::activate_binding` |
| create RuntimeBinding | `POST /api/runtimes/deploy` | `services/runtime-manager/main.py::deploy` |

`POST /api/v1/bindings` creates a **PersonaCapitalBinding** in `pending`
state. It does not create a RuntimeBinding. A RuntimeBinding exists only after
the approved plan and active capital binding pass Runtime Manager deployment.

## Exact dependency sequence and request contracts

Values in angle brackets must come from the new production run or the response
from the immediately preceding call. All BFF creates require authenticated
operator/approver roles and an `Idempotency-Key` header.

### C1-C2 — production training and evaluation

Run real LightGBM training against the governed TW dataset, then evaluate it.
Do not reuse the stub checksum, run id, object-store path, or evaluation
summary from `support/evidence/MGMT-QLIB-005`.

### C3 — registry admission

Register the production artifact as `draft`:

```http
POST /api/registry/entries
Content-Type: application/json

{
  "artifact_type": "model_artifact",
  "strategy_id": "tw-cross-sectional-equity-alpha",
  "version": "<production-semver>",
  "artifact_state": "draft",
  "lineage": {
    "source_run_ids": ["<production-run-id>"],
    "source_dataset_refs": ["<governed-tw-dataset-ref>"],
    "source_strategy_spec_id": "<approved-strategy-spec-id>"
  },
  "storage_ref": {
    "backend": "object_store",
    "path": "<production-artifact-path>"
  },
  "checksum": "sha256:<production-checksum>",
  "producer_run_id": "<production-run-id>",
  "evaluation_summary": {"<approved-metric>": "<measured-value>"},
  "metadata": {"framework": "qlib", "training_backend": "lightgbm"}
}
```

Advance one state at a time:

```http
POST /api/registry/entries/<registry-id-from-register>/advance
Content-Type: application/json

{"target_state": "candidate"}
```

```http
POST /api/registry/entries/<registry-id-from-register>/advance
Content-Type: application/json

{
  "target_state": "approved",
  "approver": "<authorized-human-approver>",
  "approval_decision_id": "<artifact-approval-evidence-id>"
}
```

### C5 prerequisite — pool and pending PersonaCapitalBinding

The DeploymentPlan API requires `binding_id` and `capital_pool_id`, so create
these pending governance objects before C4:

```http
POST /bff/capital-pools
Idempotency-Key: <unique-key>
Content-Type: application/json

{
  "pool_id": "pool-tw-equity-paper",
  "name": "TW Equity Paper Capital Pool",
  "status": "active",
  "owner_id": "<operator-or-desk-id>",
  "owner_type": "operator",
  "currency": "TWD",
  "budget": "<operator-approved-paper-budget>",
  "risk_policy_ref": "<approved-tw-paper-risk-policy>",
  "single_runtime_enforced": true,
  "metadata": {"capital_mode": "paper", "live_capital_enabled": false}
}
```

```http
POST /api/v1/bindings
Idempotency-Key: <unique-key>
Content-Type: application/json

{
  "binding_id": "binding-tw-equity-paper",
  "persona_id": "persona-tw-equity",
  "capital_pool_id": "pool-tw-equity-paper",
  "role": "paper_owner",
  "allowed_deployment_scope": "paper",
  "budget": "<operator-approved-paper-budget>",
  "metadata": {"market_scope": ["TW"], "live_capital_enabled": false}
}
```

### C4 — DeploymentPlan and human ApprovalDecision

```http
POST /api/v1/deployment-plans
Idempotency-Key: <unique-key>
Content-Type: application/json

{
  "plan_id": "plan-tw-equity-paper",
  "binding_id": "binding-tw-equity-paper",
  "artifact_id": "<approved-registry-id>",
  "capital_pool_id": "pool-tw-equity-paper",
  "deployment_mode": "paper",
  "locked": false,
  "params": {"market_scope": ["TW"], "live_write_enabled": false}
}
```

```http
POST /api/v1/approval-decisions
Idempotency-Key: <unique-key>
Content-Type: application/json

{
  "plan_id": "plan-tw-equity-paper",
  "decision": "approve",
  "memo": "Approve governed TW paper activation only."
}
```

The approval route accepts `approve` or `reject`; it does not accept
`approved`. `memo` must contain at least eight characters.

### C5-C6 — activate capital binding and deploy RuntimeBinding

The Capital service is the write owner for activation:

```http
POST /api/bindings/binding-tw-equity-paper/activate
Content-Type: application/json

{
  "actor_id": "<authorized-operator-id>",
  "actor_role": "<authorized-role>",
  "approval_decision_id": "<approval-decision-id-from-C4>"
}
```

After loader checks pass, Runtime Manager creates the actual RuntimeBinding:

```http
POST /api/runtimes/deploy
Content-Type: application/json

{
  "plan_id": "plan-tw-equity-paper",
  "plan_status": "approved",
  "target_stage": "paper",
  "artifact_id": "<approved-registry-id>",
  "artifact_version": "<production-semver>",
  "strategy_id": "tw-cross-sectional-equity-alpha",
  "approval_decision_id": "<approval-decision-id-from-C4>",
  "sponsor_persona_id": "persona-tw-equity",
  "capital_pool_id": "pool-tw-equity-paper",
  "persona_capital_binding_id": "binding-tw-equity-paper",
  "persona_capital_binding_status": "active",
  "allowed_deployment_scope": "paper",
  "loader_checks_passed": true,
  "runtime_id": "runtime-tw-equity-paper",
  "metadata": {"market_scope": ["TW"], "live_write_enabled": false}
}
```

Runtime Manager independently re-resolves the registry, deployment,
governance, and capital authorities. A caller-supplied `approved` string is
not sufficient.

### C7 — TW signal source

Only after the active RuntimeBinding is readable should the separately
approved `SRCLIVE-005` change repoint `tw_signal_producer` from the retired
queue to the binding's real signal path. Verify:

- the producer emits governed TW symbols, never the AAPL smoke strategy;
- the reconciler spawns `runtime-tw-equity-paper`;
- telemetry and holdings carry the same persona, plan, capital binding,
  RuntimeBinding, strategy, and artifact identities;
- `live_write_enabled` remains false.

## Operator sign-off record

- [ ] Approve governed TW production data use and real LightGBM training.
- [ ] Approve registry `draft -> candidate -> approved`.
- [ ] Approve paper budget, CapitalPool, and PersonaCapitalBinding.
- [ ] Approve DeploymentPlan and ApprovalDecision.
- [ ] Approve RuntimeBinding deployment.
- [ ] Approve the separate SRCLIVE signal-route change.
- [ ] Record rollback/containment owner and evidence locations.
