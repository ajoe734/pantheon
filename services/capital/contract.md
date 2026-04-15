# Capital Service Contract

Last updated: 2026-04-15  
Task: `BP5-SVC-006`

## Purpose

`services/capital/` is the deployable service boundary for the Capital Pool Plane.
It turns the canonical governance objects into a real API surface:

- `CapitalPool`
- `PersonaCapitalBinding`

The service owns:

- governed write paths for pools and bindings
- append-only audit logging for pool/binding mutations
- read paths used by runtime-manager, persona flows, and BFF projections

It does **not** own deployment execution or `RuntimeBinding` writes.

## Write Ownership

| Object | Operation | Authorized role |
|---|---|---|
| `CapitalPool` | create / update status | `capital.admin` |
| `PersonaCapitalBinding` | create / activate / update status | `persona.admin` |

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

### Governance support

- `GET    /api/capital/write-authority`
- `GET    /api/capital/audit`
- `GET    /health`

## Service Invariants

1. `PersonaCapitalBinding` writes must reference an existing `CapitalPool`.
2. Archived pools reject new bindings.
3. Binding activation requires the referenced pool to be `active`.
4. Binding admissibility is computed from:
   - binding status
   - effective validity window
   - role deployment ceiling
   - `allowed_deployment_scope`
   - pool governance status
5. Only one active `live_owner` binding may exist per pool.
6. BFF/runtime read models consume the service's persisted snapshots:
   - `capital_pools.json`
   - `persona_capital_bindings.json`

## Downstream Read Paths

- Runtime-manager checks `/api/bindings/admissibility` before creating a `RuntimeBinding`.
- BFF and other read surfaces load the canonical snapshots emitted by this service.
- Persona session/bootstrap flows treat this service as the source of truth for pool/binding governance, while `RuntimeBinding` remains owned by runtime-manager.

