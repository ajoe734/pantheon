# Capital Service Contract

Last updated: 2026-07-13
Task: `PPL-ALLOC-011`

## Purpose

`services/capital/` is the deployable service boundary for the Capital Pool Plane.
It turns the canonical governance objects into a real API surface:

- `CapitalPool`
- `RiskPolicy`
- `RiskPolicyEvaluation`
- `PersonaCapitalBinding`
- authoritative capital allocations
- rebalance proposals and apply receipts
- emergency containment records

The service owns:

- governed write paths for pools and bindings
- the executable RiskPolicy evaluator contract consumed by optimizer,
  promotion/deployment, and runtime-manager gates
- append-only audit logging for pool/binding mutations
- durable, idempotent rebalance creation and apply commands
- atomic all-or-none allocation mutation and authoritative readback
- risk-decreasing emergency containment
- read paths used by runtime-manager, persona flows, and BFF projections

It does **not** own deployment execution or `RuntimeBinding` writes.

## Write Ownership

| Object | Operation | Authorized role |
|---|---|---|
| `CapitalPool` | create / update status | `capital.admin` |
| `PersonaCapitalBinding` | create / activate / update status | `persona.admin` |
| `Rebalance` | create / apply | `operator`, `approver`, `admin`, `capital.operator`, `capital.admin` |
| `Containment` | create | `operator`, `approver`, `reviewer`, `admin`, `capital.operator`, `capital.admin`, `risk.admin` |

BFF and other callers remain façades or consumers. They must not mutate the
underlying JSON stores directly.

## API Surface

### Capital pools

- `POST   /api/capital-pools`
- `GET    /api/capital-pools`
- `GET    /api/capital-pools/{pool_id}`
- `PATCH  /api/capital-pools/{pool_id}/status`
- `GET    /api/capital-pools/{pool_id}/live-owner`

### Persona bindings

- `POST   /api/bindings`
- `GET    /api/bindings`
- `GET    /api/bindings/{binding_id}`
- `POST   /api/bindings/{binding_id}/activate`
- `PATCH  /api/bindings/{binding_id}/status`
- `GET    /api/bindings/admissibility?persona_id=...&capital_pool_id=...&target_stage=...`

### Allocation and rebalance authority

- `POST   /api/rebalances`
- `GET    /api/rebalances?capital_pool_id=...&status=...`
- `GET    /api/rebalances/{rebalance_id}`
- `POST   /api/rebalances/{rebalance_id}/apply`
- `GET    /api/allocations?capital_pool_id=...&persona_id=...`
- `GET    /api/capital-pools/{pool_id}/allocations?persona_id=...`
- `POST   /api/containments`
- `GET    /api/containments?persona_id=...`

### Governance support

- `RiskPolicyEvaluator.evaluate(policy, context)` (Python contract)
- `GET    /api/capital/write-authority`
- `GET    /api/capital/audit`
- `GET    /health`

## Service Invariants

1. `PersonaCapitalBinding` writes must reference an existing `CapitalPool`.
2. Archived pools reject new bindings.
3. Binding activation requires the referenced pool to be `active`.
4. `risk_policy_ref` must resolve to an executable `RiskPolicy` before a target
   can progress into optimizer synthesis, DeploymentPlan creation, promotion,
   RuntimeBinding creation, or runtime launch.
5. `RiskPolicyEvaluator` returns a `RiskPolicyEvaluation` with one of:
   `allowed`, `allowed_with_conditions`, or `rejected`. Rejected evaluations
   are hard vetoes and must fail closed before downstream writes.
6. Binding admissibility is computed from:
   - binding status
   - effective validity window
   - role deployment ceiling
   - `allowed_deployment_scope`
   - pool governance status
7. Only one active `live_owner` binding may exist per pool.
8. BFF/runtime read models consume the service's persisted snapshots:
   - `capital_pools.json`
   - `persona_capital_bindings.json`
9. Rebalance proposals persist their normalized allocation lines. Apply uses only
   those server-owned lines; callers cannot replace target weights at apply time.
10. Apply verifies every persisted `current_weight` against authoritative state
    before changing any allocation. A stale line fails the proposal without a
    partial allocation mutation.
11. A live-running allocation increase requires an approval reference. The
    service derives that requirement from the persisted proposal; non-live and
    risk-decreasing proposals do not require approval.
12. `idempotency_key`, `request_hash`, the server-computed payload hash, and
    `command_id` are persisted. An exact replay returns the durable result and a
    conflicting reuse fails closed.
13. Successful apply receipts and allocation records set
    `authoritative_capital_readback=true`; receipts also set
    `authoritative_capital_state_applied=true` and
    `live_capital_side_effects=false`.
14. Containment can only preserve or reduce the authoritative capital baseline.
    Promotion, canary/live stage creation, and allocation increases are rejected.
    A freeze record emits both `state` and `containment_state` as `frozen`.
15. JSON allocation authority writes use one aggregate document, an in-process
    `RLock`, and atomic temporary-file replacement. With
    `CAPITAL_STORE_BACKEND=postgres`, the same aggregate is persisted through
    `PostgresJsonOwnerStore`.

## Downstream Read Paths

- Runtime-manager checks `/api/bindings/admissibility` before creating a `RuntimeBinding`.
- BFF creates and applies rebalance proposals through this service, then reads
  `/api/allocations` (or the pool-scoped route) for authoritative readback.
- BFF and other read surfaces load the canonical snapshots emitted by this service.
- Persona session/bootstrap flows treat this service as the source of truth for pool/binding governance, while `RuntimeBinding` remains owned by runtime-manager.
