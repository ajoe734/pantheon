# CapitalPool and PersonaCapitalBinding Governance Contract

Last updated: 2026-04-10
Task: `CAP-001`
Owner: Claude
Reviewer: Codex
Status: review_requested

---

## 1. Purpose

`CapitalPool` and `PersonaCapitalBinding` are the two canonical governance
objects that anchor the Capital Pool Plane.

Together they answer:
- what capital pools exist, who owns them, and what runtime rules apply
- which personas are admissible to a given pool, and up to what deployment scope

These objects sit at the entry to the deployment chain:

```
CapitalPool + PersonaCapitalBinding
        |
        v
ApprovalDecision -> DeploymentPlan -> RuntimeBinding
```

---

## 2. CapitalPool

### 2.1 Purpose

`CapitalPool` is the canonical governance record for a unit of deployable
capital. It is NOT a runtime object. It represents the ownership, risk-policy
anchor, and single-runtime configuration of a pool.

### 2.2 Write Owner

- **Capital Pool Plane** (write authority)
- Operator Console / Governance Workbench (proposal only)

### 2.3 Canonical Fields

Machine-readable schema:

- `services/control-plane/governance/capital_pool.schema.json`

Python implementation:

- `services/control-plane/governance/capital_pool.py`

| Field                    | Required | Meaning |
|---|---|---|
| `pool_id`                | yes | immutable unique identifier |
| `name`                   | yes | human-readable display name |
| `owner_id`               | yes | ID of the owning entity |
| `owner_type`             | yes | `org`, `fund`, `desk`, or `operator` |
| `status`                 | yes | `active`, `suspended`, or `archived` |
| `created_at`             | yes | ISO-8601 UTC creation timestamp |
| `currency`               | no  | ISO 4217 code (default `USD`) |
| `budget`                 | no  | allocated budget in pool currency units |
| `risk_policy_ref`        | no  | reference to the active risk policy |
| `single_runtime_enforced`| no  | single-runtime rule flag (default `true`) |
| `description`            | no  | free-form description |
| `updated_at`             | no  | last update timestamp |
| `metadata`               | no  | consumer metadata |

### 2.4 Status Lifecycle

```
active <-> suspended -> archived
```

- `active`: pool is open for binding and deployment
- `suspended`: temporarily paused; no new deployments; existing runtime may continue
- `archived`: permanently closed; no new bindings or deployments

### 2.5 Single-Runtime Rule

`single_runtime_enforced = true` (default) means:

> At most one live `RuntimeBinding` may be active for this pool at any time.

Callers creating a `RuntimeBinding` must call
`CapitalPoolStore.is_single_runtime_enforced(pool_id)` and enforce this
constraint before proceeding.

Multi-persona use of the same pool must be resolved upstream via judge /
committee / approved unified artifact before deploying to the runtime.

---

## 3. PersonaCapitalBinding

### 3.1 Purpose

`PersonaCapitalBinding` is the governance object that expresses the admissibility
of a persona within a capital pool.

**Key invariant:** binding is governance, not deployment.

> A binding authorises. A `DeploymentPlan` deploys.

### 3.2 Write Owner

| Action           | Owner | Notes |
|---|---|---|
| Propose          | Operator Console / Governance Workbench | submits binding request |
| Validate + write | Governance Plane | checks lifecycle, policy, and role scope |
| Persist          | Capital Pool Plane (Binding Registry) | canonical store |

### 3.3 Canonical Fields

Machine-readable schema:

- `services/control-plane/governance/persona_capital_binding.schema.json`

Python implementation:

- `services/control-plane/governance/persona_capital_binding.py`

| Field                     | Required | Meaning |
|---|---|---|
| `binding_id`              | yes | immutable unique identifier |
| `persona_id`              | yes | the persona being bound |
| `capital_pool_id`         | yes | the pool being bound to |
| `role`                    | yes | `advisor`, `paper_owner`, or `live_owner` |
| `allowed_deployment_scope`| yes | deployment scope upper-bound (`none`/`paper`/`canary`/`live`) |
| `status`                  | yes | `pending`, `active`, `suspended`, `revoked`, or `expired` |
| `created_at`              | yes | ISO-8601 UTC creation timestamp |
| `mandate`                 | no  | free-form mandate description |
| `budget`                  | no  | capital budget allocated under this binding |
| `effective_from`          | no  | inclusive start of validity window |
| `effective_to`            | no  | exclusive end of validity window |
| `approval_decision_id`    | no* | required before status can be `active` |
| `created_by`              | no  | actor who created this binding |
| `updated_at`              | no  | last update timestamp |
| `metadata`                | no  | consumer metadata |

### 3.4 `role` Semantics

| Value         | Meaning |
|---|---|
| `advisor`     | can provide recommendations; cannot sponsor deployment |
| `paper_owner` | can sponsor paper-stage deployments |
| `live_owner`  | can sponsor paper, canary, and live deployments |

### 3.5 `allowed_deployment_scope` Semantics

| Value    | Meaning |
|---|---|
| `none`   | governance association only; no deployment allowed |
| `paper`  | may deploy up to paper stage |
| `canary` | may deploy up to canary stage |
| `live`   | may deploy up to live stage |

`allowed_deployment_scope` is the **permission ceiling**, not the current
deployment state. A binding may exist with `live` scope even when nothing is
deployed.

### 3.6 `allowed_deployment_scope` vs `RuntimeBinding.deployment_mode`

These two fields are semantically independent:

| Field | Object | Answers |
|---|---|---|
| `allowed_deployment_scope` | `PersonaCapitalBinding` | what the persona is *allowed* to deploy to |
| `deployment_mode`          | `RuntimeBinding`        | what is *actually running* right now |

The binding's scope constrains the plan's target stage:

```
binding.allowed_deployment_scope >= DeploymentPlan.target_stage
```

### 3.7 Binding Status Lifecycle

```
pending -> active -> suspended -> revoked / expired
pending -> revoked
active  -> revoked / expired
```

An `approval_decision_id` must be set before transitioning to `active`.

### 3.8 Single-Live-Owner Rule

The store enforces:

> At most one `active` `live_owner` binding may exist per capital pool.

Activating a second `live_owner` binding for the same pool raises an error.
The existing `live_owner` binding must first be revoked or suspended.

This rule implements the platform invariant from
`BINDING_AND_DEPLOYMENT_SEMANTICS.md` §9:
> 預設：一個 capital pool = 一個 LEAN runtime.

Advisors and paper_owners are not subject to this constraint.

---

## 4. Relationship to Other Canonical Objects

```
CapitalPool                     ← governs budget / risk / single-runtime rule
    |
PersonaCapitalBinding           ← governs admissibility / scope ceiling
    |
ApprovalDecision                ← governs artifact approval
    |
DeploymentPlan                  ← governs stage transition intent
    |
RuntimeBinding                  ← records actual running state
```

- `BINDING_AND_DEPLOYMENT_SEMANTICS.md` — L1 doc covering binding vs deployment
- `PERSONA_RUNTIME_MODEL.md`           — L1 doc covering persona registry / session / runtime layers
- `services/control-plane/governance/contract.md` (ApprovalDecision)
- `services/control-plane/governance/deployment_plan.contract.md`

---

## 5. Validation Rules

### CapitalPool

| Rule | Where enforced |
|---|---|
| `pool_id`, `name`, `owner_id` must not be empty | `validate_pool()` |
| `owner_type` must be one of the enum values | `CapitalPool.__post_init__` |
| `status` must be one of the enum values | `CapitalPool.__post_init__` |
| `budget >= 0` | `CapitalPool.__post_init__` |
| Status transitions follow the allowed graph | `_validate_status_transition()` |

### PersonaCapitalBinding

| Rule | Where enforced |
|---|---|
| `binding_id`, `persona_id`, `capital_pool_id` must not be empty | `validate_binding()` |
| `role` must be one of the enum values | `PersonaCapitalBinding.__post_init__` |
| `allowed_deployment_scope` must be one of the enum values | `PersonaCapitalBinding.__post_init__` |
| `status` must be one of the enum values | `PersonaCapitalBinding.__post_init__` |
| `approval_decision_id` required before `active` | `validate_binding()` + `activate()` |
| `budget >= 0` | `PersonaCapitalBinding.__post_init__` |
| Single live_owner per pool | `_check_single_live_owner()` in store |
| Status transitions follow the allowed graph | `_validate_binding_status_transition()` |

---

## 6. API Sketch

### CapitalPool APIs
- `POST   /api/capital-pools` — create pool
- `GET    /api/capital-pools/{pool_id}` — get pool
- `GET    /api/capital-pools?owner_id=...&status=...` — list pools
- `PATCH  /api/capital-pools/{pool_id}/status` — update status

### PersonaCapitalBinding APIs
- `POST   /api/bindings` — propose binding
- `POST   /api/bindings/{binding_id}/activate` — activate (requires approval_decision_id)
- `PATCH  /api/bindings/{binding_id}/status` — suspend / revoke / expire
- `GET    /api/bindings/{binding_id}` — get binding
- `GET    /api/bindings?persona_id=...&capital_pool_id=...&status=...` — list bindings
- `GET    /api/capital-pools/{pool_id}/live-owner` — current live_owner binding

---

## 7. Relationship to Downstream Tasks

- **RUN-001**: `RuntimeBinding` must check `PersonaCapitalBindingStore.persona_may_deploy_to()`
  and `CapitalPoolStore.is_single_runtime_enforced()` before creating a binding.
- **CAP-002**: multi-persona synthesis in optimizer-svc requires active advisor bindings
  for each persona being synthesised.
- **DEP-002**: saga compensation must consider binding status when deciding rollback scope.
