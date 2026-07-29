# Track C Go/No-Go Packet: `persona-tw-equity` Paper Activation

Task: `P0-TW-PAPER-ACTIVATE-001`
Status: decision and contract packet only; this task executed none of the
production calls below.

> Track C changes registry, governance, capital, deployment, runtime, and
> signal-routing truth. The current decision is **NO-GO**. Do not copy these
> examples into a production shell until every blocker and operator gate in
> this packet is closed.

## Decision

The recommendation remains **NO-GO**. All of the following must be true before
an operator can authorize paper activation:

- `SRCLIVE-005` proves a governed TW dataset and active source path.
- A real Qlib LightGBM run replaces `qlib-stub-62f94503afa2`; its evaluation
  evidence binds the run, dataset, checksum, StrategySpec, costs, and risk
  thresholds.
- The result is packaged as a schema-valid **StrategyArtifact** execution
  bundle. A generic `model_artifact` RegistryEntry is not deployable by the
  current Runtime Manager authority verifier.
- The StrategyArtifact has an independently approved zero-capital rollback
  target.
- Registry and Governance ApprovalDecision writes have an authenticated,
  tenant-bound production mutation boundary. Their canonical lifecycle routes
  currently lack that enforcement; route existence is not write authority.
- Capital, Deployment, and Runtime Manager use verified bearer identities,
  exact tenant/service headers, and Runtime Manager MFA.
- An operator approves the paper budget, registry transitions, approval
  decision, capital binding activation, DeploymentPlan dispatch, and the
  separate TW signal-route change.
- Paper fleet reconciliation and telemetry ingest are healthy.

The display id `qlib-tw-cross-sectional-alpha-model-draft-v1` is not a
deployable RegistryEntry id. The production flow must create a new immutable
StrategyArtifact id/version from the real run.

### Schema blocker that must not be hidden

`services/registry/strategy_artifact.schema.json` currently fixes
`strategy_logic.kind` to `close_to_close_momentum`. A Qlib cross-sectional
alpha cannot be relabelled as that strategy. Before GO, the production owner
must either:

1. produce a truthful LEAN execution wrapper that genuinely conforms to the
   current StrategyArtifact contract; or
2. land and independently review a schema/loader extension for the Qlib
   execution semantics.

The deterministic contract proof in this task deliberately uses the existing
TW momentum fixture only to prove identity and authority wiring. It is not
training or admission evidence for the Qlib strategy.

## Canonical owner routes and readbacks

| Object / transition | Canonical write route | Required authoritative readback |
|---|---|---|
| StrategyArtifact register | `POST /api/registry/strategy-artifacts` | `GET /api/registry/strategy-artifacts/{registry_id}` |
| StrategyArtifact state | `POST /api/registry/strategy-artifacts/{registry_id}/advance` | same StrategyArtifact GET |
| CapitalPool | `POST /api/capital-pools` | `GET /api/capital-pools/{pool_id}` |
| pending PersonaCapitalBinding | `POST /api/bindings` | `GET /api/bindings/{binding_id}` |
| canonical ApprovalDecision | `POST /api/governance/approvals` | `GET /api/governance/approvals/{decision_id}` |
| accept ApprovalDecision review | `POST /api/governance/approvals/{decision_id}/review` | same ApprovalDecision GET |
| decide ApprovalDecision | `POST /api/governance/approvals/{decision_id}/decide` | same ApprovalDecision GET |
| activate PersonaCapitalBinding | `POST /api/bindings/{binding_id}/activate` | binding GET plus `GET /api/bindings/admissibility` |
| validate DeploymentPlan without persistence | `POST /api/deployment/plans/validate` | response `ok=true`; no plan is stored |
| create DeploymentPlan | `POST /api/deployment/plans` | `GET /api/deployment/plans/{plan_id}` |
| dispatch DeploymentPlan | `POST /api/deployment/plans/{plan_id}/dispatch` | `GET /api/deployment/plans/{plan_id}/saga-progress` |
| RuntimeBinding execution cut | normally the Deployment outbox consumer; direct route is `POST /api/runtimes/deploy` | `GET /api/runtime-bindings/{binding_id}` and `GET /api/runtimes/{pool_id}/active` |

The deprecated BFF-local routes `/api/v1/deployment-plans`,
`/api/v1/approval-decisions`, and `/api/v1/bindings` are not the canonical
authority chain for Runtime Manager admission. `POST /api/registry/entries`
with `artifact_type=model_artifact` is also insufficient: Runtime Manager
requires the StrategyArtifact facade to produce
`artifact_type=execution_bundle` and `metadata.strategy_artifact`.

## Authentication and MFA truth

The following is the current implementation contract, not an aspirational
claim:

| Boundary | Current enforcement | GO requirement |
|---|---|---|
| Registry StrategyArtifact lifecycle | The routes in `services/registry/service.py` have no bearer/tenant mutation middleware. | Add or route through a verified authenticated, tenant-bound owner boundary before production use. Until then, **NO-GO**. |
| Governance ApprovalDecision lifecycle | `propose`, `review`, and `decide` do not call `_authenticate_governance_write`; `actor_role` and `actor_id` arrive in the body. The existing authenticated helper protects other governance surfaces, not these three routes. | Bind identity/role to a verified bearer token and require the operator's MFA policy before production use. Until then, **NO-GO**. |
| Capital | Mutations require `Authorization`, `X-Tenant-Id`, and `X-Pantheon-Service`; body `actor_id`/`actor_role` must match verified authority. | Use an allowed service identity and exact tenant. CapitalPool create needs an allowed capital role; binding activation needs `persona.admin`. |
| Deployment | All `/api/deployment/*` routes require a verified bearer role and `X-Tenant-Id`; server identity replaces caller `created_by` and binds the tenant. | Use the same tenant as the decided ApprovalDecision. Current Deployment middleware does not itself enforce MFA, so do not claim it does. |
| Runtime Manager | `/api/runtimes/deploy` requires an operator-role bearer and is marked `mfa_required=True`; MFA is enforced when `PANTHEON_RUNTIME_MFA_REQUIRED=true`. | Production/dev activation configuration must keep that flag true and present accepted IdP MFA proof or a valid `X-MFA-Token` per the deployed policy. |

Do not use a permissive structured token as production evidence. Capture the
verified actor, roles, tenant, MFA proof, request idempotency keys, and all
readback digests in the activation bundle.

## Identity set

All calls and readbacks must use one immutable identity set:

```text
strategy_artifact_id        = <new-production-strategy-artifact-id>
strategy_id                 = tw-cross-sectional-equity-alpha
artifact_version            = <production-semver>
approval_decision_id        = <governance-decision-id>
capital_pool_id             = pool-tw-equity-paper
persona_capital_binding_id  = binding-tw-equity-paper
persona_id                  = persona-tw-equity
deployment_plan_id          = plan-tw-equity-paper
runtime_id                  = runtime-tw-equity-paper
tenant_id                   = <authorized-tenant>
```

`strategy_artifact_id` must equal both
`StrategyArtifact.artifact_id` and `RegistryEntry.registry_id`.

## Exact dependency sequence

Values in angle brackets come from the real production run, an approved
baseline, verified identity claims, or the immediately preceding response.

### C1-C2 — real training and evaluation

Run real LightGBM training against the governed TW dataset and evaluate it. Do
not reuse the stub's run id, checksum, object-store path, or metrics. Produce a
truthful StrategyArtifact execution bundle and an independently approved
rollback baseline before any write below.

### C3.1 — register a draft StrategyArtifact

The request envelope is:

```http
POST /api/registry/strategy-artifacts
Content-Type: application/json

{
  "registry_id": "<new-production-strategy-artifact-id>",
  "artifact_state": "draft",
  "strategy_artifact": {
    "artifact_schema_version": "1.0",
    "artifact_id": "<new-production-strategy-artifact-id>",
    "strategy_id": "tw-cross-sectional-equity-alpha",
    "version": "<production-semver>",
    "algorithm_ref": {
      "engine": "lean",
      "repository": "<approved-repository>",
      "commit": "<40-hex-reviewed-commit>",
      "path": "<normalized-repo-relative-path>",
      "entrypoint": "<python-module:object>",
      "signal_interface": "<python-module:object>",
      "signal_schema_version": "<version>",
      "logic_interpreter": "<python-module:object>"
    },
    "strategy_logic": "<complete schema-valid execution logic>",
    "parameters": "<complete governed parameter object>",
    "mutation_surface": "<complete controls + immutable parameter partition>",
    "lineage": {
      "source_run_ids": ["<production-run-id>"],
      "source_dataset_refs": ["<governed-tw-dataset-ref>"],
      "source_strategy_spec_id": "<approved-strategy-spec-id>"
    },
    "binding_intent": {
      "persona_id": "persona-tw-equity",
      "persona_capital_binding_id": "binding-tw-equity-paper"
    },
    "provenance_refs": ["<training-and-evaluation-evidence-ref>"]
  },
  "producer_run_id": "<production-run-id>",
  "evaluation_summary": "<measured production evaluation>",
  "rollback_target": "<approved-zero-capital-baseline-artifact-id>",
  "metadata": {
    "framework": "qlib",
    "training_backend": "lightgbm"
  }
}
```

The angle-bracket objects must be replaced with complete JSON conforming to
`services/registry/strategy_artifact.schema.json`; they are not literal
strings. The registration response and GET readback must prove:

```text
entry.registry_id              == strategy_artifact.artifact_id
entry.artifact_type            == "execution_bundle"
entry.artifact_state           == "draft"
entry.metadata.strategy_artifact is the exact validated loader payload
entry.storage_ref              == {"backend":"inline","path":"$.entry.metadata.strategy_artifact"}
entry.checksum                  == sha256(canonical StrategyArtifact JSON)
deployment_stage               == "none"
```

Advance one state at a time:

```http
POST /api/registry/strategy-artifacts/<registry-id>/advance
Content-Type: application/json

{"target_state": "candidate"}
```

Read back `artifact_state=candidate`. Do not request `approved` yet; the
canonical ApprovalDecision id does not exist until C3.3.

### C3.2 — create the paper pool and pending capital binding

These are Capital service routes, not BFF-local `/api/v1` stores.

```http
POST /api/capital-pools
Authorization: Bearer <verified-capital-service-token>
X-Tenant-Id: <authorized-tenant>
X-Pantheon-Service: <allowed-caller-service>
Content-Type: application/json

{
  "actor_id": "<verified-or-delegated-actor-id>",
  "actor_role": "capital.admin",
  "idempotency_key": "<unique-key>",
  "request_hash": "<hash-bound-to-the-semantic-request>",
  "pool_id": "pool-tw-equity-paper",
  "name": "TW Equity Paper Capital Pool",
  "owner_id": "<approved-desk-id>",
  "owner_type": "desk",
  "status": "active",
  "currency": "TWD",
  "budget": "<operator-approved-paper-budget>",
  "risk_policy_ref": "<approved-tw-paper-risk-policy>",
  "single_runtime_enforced": true,
  "metadata": {"capital_mode": "paper", "live_capital_enabled": false}
}
```

`idempotency_key` and `request_hash` must be supplied together or both omitted;
the production packet must supply both.

```http
POST /api/bindings
Authorization: Bearer <verified-capital-service-token>
X-Tenant-Id: <authorized-tenant>
X-Pantheon-Service: <allowed-caller-service>
Content-Type: application/json

{
  "actor_id": "<verified-or-delegated-actor-id>",
  "actor_role": "persona.admin",
  "idempotency_key": "<unique-key>",
  "request_hash": "<hash-bound-to-the-semantic-request>",
  "binding_id": "binding-tw-equity-paper",
  "persona_id": "persona-tw-equity",
  "capital_pool_id": "pool-tw-equity-paper",
  "role": "paper_owner",
  "allowed_deployment_scope": "paper",
  "budget": "<operator-approved-paper-budget>",
  "metadata": {"market_scope": ["TW"], "live_capital_enabled": false}
}
```

Read back pool `status=active`, `single_runtime_enforced=true`, and binding
`status=pending`.

### C3.3 — propose, review, and decide the canonical ApprovalDecision

These are the required request models. They are **not authorized for production
execution until the auth blocker above is fixed**.

```http
POST /api/governance/approvals
Content-Type: application/json

{
  "decision_id": "<governance-decision-id>",
  "target_type": "registry_entry",
  "target_id": "<new-production-strategy-artifact-id>",
  "target_version": "<production-semver>",
  "risk_level": "medium",
  "capital_pool_id": "pool-tw-equity-paper",
  "persona_id": "persona-tw-equity",
  "tenant_id": "<authorized-tenant>",
  "owner_user_id": "<authenticated-proposer-id>"
}
```

Read back `decision_state=proposed`.

```http
POST /api/governance/approvals/<decision-id>/review
Content-Type: application/json

{
  "actor_role": "governance_reviewer",
  "actor_id": "<authenticated-reviewer-id>"
}
```

Read back `decision_state=under_review`.

```http
POST /api/governance/approvals/<decision-id>/decide
Content-Type: application/json

{
  "actor_role": "risk_owner",
  "outcome": "approved",
  "rationale": "Approve governed TW paper activation only.",
  "actor_id": "<authenticated-risk-owner-id>",
  "conditions": [],
  "evidence_refs": [
    {
      "ref_type": "activation_bundle",
      "ref_id": "<immutable-evidence-id>",
      "storage_ref": {
        "backend": "<approved-backend>",
        "path": "<immutable-evidence-path>"
      }
    }
  ]
}
```

The proposer, reviewer, and deciding actor must be distinct according to the
operator policy even though the current request model does not enforce that
separation. Runtime admission only accepts an unconditional readback with:

```text
decision_state == "decided"
decision       == "approved"
target_type    == "registry_entry"
target_id/version, capital_pool_id, and persona_id match the activation
conditions      is null or []
revoked_at      is empty
expires_at      is absent or still in the future
```

### C3.4 — bind the governance decision to registry approval

```http
POST /api/registry/strategy-artifacts/<registry-id>/advance
Content-Type: application/json

{
  "target_state": "approved",
  "approver": "<authenticated-risk-owner-id>",
  "approval_decision_id": "<governance-decision-id>"
}
```

Read back:

```text
entry.artifact_state        == "approved"
entry.approval_decision_id  == <governance-decision-id>
entry.artifact_type         == "execution_bundle"
deployment_stage            == "none"
```

### C4 — activate the capital binding

```http
POST /api/bindings/binding-tw-equity-paper/activate
Authorization: Bearer <verified-capital-service-token>
X-Tenant-Id: <authorized-tenant>
X-Pantheon-Service: <allowed-caller-service>
Content-Type: application/json

{
  "actor_id": "<verified-or-delegated-actor-id>",
  "actor_role": "persona.admin",
  "approval_decision_id": "<governance-decision-id>"
}
```

Read back binding `status=active` and the same `approval_decision_id`, then:

```http
GET /api/bindings/admissibility?persona_id=persona-tw-equity&capital_pool_id=pool-tw-equity-paper&target_stage=paper
```

The response must have `permitted=true`, `binding_status=active`,
`allowed_deployment_scope=paper`, and `single_runtime_enforced=true`.

### C5 — validate and create the canonical DeploymentPlan

First submit the complete body to the non-persisting validation route:

```http
POST /api/deployment/plans/validate
Authorization: Bearer <verified-deployment-token>
X-Tenant-Id: <authorized-tenant>
Content-Type: application/json

{
  "plan_id": "plan-tw-equity-paper",
  "approval_decision_id": "<governance-decision-id>",
  "capital_pool_id": "pool-tw-equity-paper",
  "target_stage": "paper",
  "current_stage": "none",
  "registry_entry": "<exact approved StrategyArtifact entry readback>",
  "approval_decision": "<exact decided ApprovalDecision readback>",
  "sponsor_persona_id": "persona-tw-equity",
  "scale": {"capital_scale_pct": 0.0, "gross_scale_pct": 100.0},
  "rollback": {
    "target_artifact_id": "<approved-zero-capital-baseline-artifact-id>",
    "target_version": "<approved-baseline-version>",
    "action_type": "replace",
    "reason": "Fail closed to the approved zero-capital paper baseline."
  },
  "pre_checks": ["registry", "governance", "capital", "source_readiness"],
  "post_checks": ["runtime_binding", "fleet_worker", "telemetry_identity"],
  "metadata": {
    "tenant_id": "<authorized-tenant>",
    "market_scope": ["TW"],
    "live_write_enabled": false
  },
  "status": "approved"
}
```

Angle-bracket readbacks above are complete JSON objects. `ok=true` is required.
Then send the byte-equivalent semantic body to:

```http
POST /api/deployment/plans
Authorization: Bearer <verified-deployment-token>
X-Tenant-Id: <authorized-tenant>
Content-Type: application/json
```

The created plan must read back with:

```text
artifact_type       == "execution_bundle"
current_stage       == "none"
target_stage        == "paper"
transition_type     == "activate"
runtime_action      == "deploy_new_binding"
status              == "approved"
approval/artifact/pool/persona identities match exactly
metadata.tenant_id  == authenticated tenant
```

### C6 — dispatch the DeploymentPlan

```http
POST /api/deployment/plans/plan-tw-equity-paper/dispatch
Authorization: Bearer <verified-deployment-token>
X-Tenant-Id: <authorized-tenant>
Content-Type: application/json

{
  "idempotency_key": "<unique-dispatch-key>",
  "source_task_id": "<operator-approved-activation-task-id>",
  "registry_entry": "<exact approved StrategyArtifact entry readback>",
  "metadata": {
    "tenant_id": "<authorized-tenant>",
    "operator_authorization_ref": "<immutable-signoff-ref>"
  }
}
```

This creates the Deployment saga/outbox command. It does not let the caller
manufacture a RuntimeBinding. The outbox consumer normally submits the derived
request to Runtime Manager.

If the direct execution route is exercised for an explicitly authorized
diagnostic, its descriptor is:

```http
POST /api/runtimes/deploy
Authorization: Bearer <verified-operator-role-token>
X-MFA-Token: <valid-token-when-deployed-policy-uses-header-MFA>
Content-Type: application/json

{
  "plan_id": "plan-tw-equity-paper",
  "plan_status": "approved",
  "target_stage": "paper",
  "artifact_id": "<new-production-strategy-artifact-id>",
  "artifact_version": "<production-semver>",
  "strategy_id": "tw-cross-sectional-equity-alpha",
  "approval_decision_id": "<governance-decision-id>",
  "sponsor_persona_id": "persona-tw-equity",
  "capital_pool_id": "pool-tw-equity-paper",
  "persona_capital_binding_id": "binding-tw-equity-paper",
  "persona_capital_binding_status": "active",
  "allowed_deployment_scope": "paper",
  "runtime_id": "runtime-tw-equity-paper",
  "metadata": {"market_scope": ["TW"], "live_write_enabled": false}
}
```

Caller-supplied `plan_status`, binding status, deployment scope, and metadata
are descriptors, not proof. Before persistence, Runtime Manager performs these
authoritative GETs and requires an exact match:

```text
GET /api/deployment/plans/{plan_id}
GET /api/registry/strategy-artifacts/{artifact_id}
GET /api/governance/approvals/{approval_decision_id}
GET /api/capital-pools/{capital_pool_id}
GET /api/bindings/admissibility?...target_stage=paper
GET /api/bindings/{persona_capital_binding_id}
```

Only a passing
`authority=canonical_deployment_registry_governance_capital` report sets
`loader_checks_passed=true` and permits creation. The caller must not send or
claim that field as evidence.

### C7 — TW signal source and post-cut readbacks

Only after an active RuntimeBinding is authoritative may the separately
approved `SRCLIVE-005` change repoint `tw_signal_producer` from the retired
queue to the binding's real signal path. Verify:

- the producer emits governed TW symbols and never the AAPL smoke strategy;
- the reconciler spawns `runtime-tw-equity-paper`;
- telemetry and holdings carry the same persona, plan, capital binding,
  RuntimeBinding, strategy, artifact, and tenant identities;
- `live_write_enabled` remains false;
- the Deployment saga reaches its expected terminal state;
- rollback/containment can restore the independently approved baseline.

## Contract-only dry-run proof

The task-scoped proof is:

```bash
/home/lupin/pantheon/.venv/bin/python -m pytest \
  services/runtime-manager/test_p0_tw_paper_activate_authority_contract.py -q
```

Expected result: `1 passed`.

The test:

- validates the StrategyArtifact register and advance request models;
- validates Governance propose/review/decide request models;
- validates CapitalPool, pending binding, and activation request models;
- validates DeploymentPlan create and dispatch request models;
- creates a valid DeploymentPlan with `StagePlanner` in memory;
- feeds the six exact authoritative readbacks to
  `verify_deploy_authorities`;
- proves the resulting authority report passes with
  `transition_type=activate` and `runtime_action=deploy_new_binding`;
- uses conspicuous `contract-proof-*` identities and performs no HTTP call or
  authoritative store write.

This proves contract composition only. It does not close the production data,
Qlib training, StrategyArtifact schema, auth, human approval, deployment, or
signal-route gates.

## Operator sign-off record

- [ ] Approve governed TW production data use and real LightGBM training.
- [ ] Accept a truthful Qlib StrategyArtifact schema/loader contract.
- [ ] Close Registry and ApprovalDecision authenticated-write blockers.
- [ ] Approve the registry `draft -> candidate -> approved` sequence.
- [ ] Approve paper budget, CapitalPool, and PersonaCapitalBinding.
- [ ] Approve the canonical ApprovalDecision and DeploymentPlan.
- [ ] Approve DeploymentPlan dispatch and Runtime Manager MFA evidence.
- [ ] Approve the separate `SRCLIVE-005` signal-route change.
- [ ] Record rollback/containment owner, baseline, and immutable evidence.
