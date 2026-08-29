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
| `CapitalPool` | create / update / update status | `capital.admin` |
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
- `PATCH  /api/capital-pools/{pool_id}`
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
- `GET    /api/rebalances/receipts/{command_id}`
- `GET    /api/allocations?capital_pool_id=...&persona_id=...`
- `GET    /api/capital-pools/{pool_id}/allocations?persona_id=...`
- `POST   /api/containments`
- `GET    /api/containments?persona_id=...`
- `GET    /api/containments/receipts/{command_id}`

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
9. Pool and binding create accept optional paired `idempotency_key` and
   `request_hash`. The owner also persists a server-computed semantic payload
   hash; exact restart replays return the same resource and conflicting reuse is
   rejected. Owner-create idempotency is actor-scoped, and the complete
   reservation/check/create sequence is serialized inside the single Capital
   writer process. Bindings persist `capital_sleeve_id` as a top-level identity;
   a non-empty `(capital_pool_id, capital_sleeve_id)` pair is globally unique in
   the binding owner store.
10. Rebalance proposals persist their normalized allocation lines but do not
   create or mutate allocation state. Every risk-increasing line must name a
   non-empty capital sleeve and exactly match a durable persona/pool/sleeve
   binding. Its stage (including the corresponding `*_candidate` and
   `*_running` aliases) maps to `paper`, `canary`, or `live`, and both the binding
   role ceiling and `allowed_deployment_scope` must authorize that mapped scope.
   Pending and active bindings inside their effective window are accepted,
   while suspended, revoked, expired, or out-of-window bindings are rejected.
   First apply revalidates those conditions; a successful command receipt
   remains replayable after a later binding or pool lifecycle change. Binding
   lifecycle mutations, pool status mutations, and the first apply owner commit
   share an in-process critical section so a revoke or suspend cannot interleave
   after validation. Apply uses only those server-owned lines.
11. Apply verifies every persisted `current_weight` against authoritative state
    before changing any allocation. A stale line fails the proposal without a
    partial allocation mutation. Apply may bootstrap a missing sleeve allocation
    only when the persisted expected `current_weight` is exactly zero. Any
    risk-increasing proposal and its first apply require an `active` CapitalPool;
    risk-decreasing exits may proceed while the pool is suspended or archived.
12. A live-scoped allocation increase requires an approval reference. The
    service derives that requirement from the persisted proposal; non-live and
    risk-decreasing proposals do not require approval.
13. `idempotency_key`, `request_hash`, the server-computed payload hash, and
    `command_id` are persisted. An exact replay returns the durable result and a
    conflicting reuse fails closed.
14. Successful apply receipts and allocation records set
    `authoritative_capital_readback=true`; receipts also set
    `authoritative_capital_state_applied=true` and
    `live_capital_side_effects=false`.
15. Rebalance and containment receipts are durably addressable by `command_id`.
    External audit delivery is recorded as `pending` or `delivered`; an audit
    sink failure after the owner commit does not roll back or hide terminal
    capital state, and an idempotent replay retries pending delivery. Delivery is
    intentionally at-least-once: audit consumers must deduplicate retries by the
    stable `audit_ref`; this contract does not promise exactly-once audit append.
16. Containment can only preserve or reduce the authoritative capital baseline.
    Promotion, canary/live stage creation, and allocation increases are rejected.
    A freeze record emits both `state` and `containment_state` as `frozen`.
17. JSON pool, binding, and allocation writes use in-process `RLock` protection
    and atomic temporary-file replacement with file/directory `fsync`. With
    `CAPITAL_STORE_BACKEND=postgres`, the same aggregate is persisted through
    `PostgresJsonOwnerStore`. The aggregate remains a single-writer owner design:
    `PostgresJsonOwnerStore` does not provide cross-replica compare-and-swap, so
    Capital must run one writer until a transactional/CAS store replaces it.
18. General pool patch writes are owned by `services/capital/`, accept only
    `name`, `status`, `risk_policy_ref`, and legacy `params`, and persist
    `params` inside canonical metadata. Callers must fresh-read the owner API;
    no BFF overlay, cache, or submitted-response fallback is authoritative.

## Downstream Read Paths

- Runtime-manager checks `/api/bindings/admissibility` before creating a `RuntimeBinding`.
- BFF creates and applies rebalance proposals through this service, then reads
  `/api/allocations` (or the pool-scoped route) for authoritative readback.
- BFF and other read surfaces load the canonical snapshots emitted by this service.
- Persona session/bootstrap flows treat this service as the source of truth for pool/binding governance, while `RuntimeBinding` remains owned by runtime-manager.
