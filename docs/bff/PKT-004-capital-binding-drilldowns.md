# PKT-004 Module B — Capital / Binding Drilldowns BFF Contract

## Purpose

Define the four BFF read surfaces for capital pool and binding drilldowns so the UI can navigate pool and binding context without client-side joins.

## Read Routes

### CP-01 — Capital Pool List

```
GET /api/v1/capital-pools
Query params: status (optional), page_token, page_size
```

Required response fields:

- `data[].id`
- `data[].name`
- `data[].status`
- `data[].owner_id`
- `data[].owner_type`
- `data[].single_runtime_enforced`
- `data[].risk_policy_ref`
- `meta.total`
- `meta.staleness` (nullable)

### CP-02 — Capital Pool Detail

```
GET /api/v1/capital-pools/{pool_id}
```

Required response fields:

- `data.id`
- `data.name`
- `data.status`
- `data.owner_id`
- `data.owner_type`
- `data.single_runtime_enforced`
- `data.risk_policy_ref`
- `meta.staleness` (nullable)

### CP-03 — Binding List

```
GET /api/v1/bindings
Query params: persona_id (optional), capital_pool_id (optional), validity (optional), page_token, page_size
```

Required response fields:

- `data[].id`
- `data[].persona_id`
- `data[].capital_pool_id`
- `data[].role`
- `data[].validity`
- `data[].status`
- `data[].allowed_deployment_scope`
- `meta.total`
- `meta.staleness` (nullable)

### CP-04 — Binding Detail

```
GET /api/v1/bindings/{binding_id}
```

Required response fields:

- `data.id`
- `data.persona_id`
- `data.capital_pool_id`
- `data.role`
- `data.validity`
- `data.status`
- `data.allowed_deployment_scope`
- `meta.staleness` (nullable)

## Design Rules

- All four surfaces are read-only. No write actions are defined in this module.
- Filters must be sent as query parameters; the BFF applies them. No client-side filtering.
- `viewer` role tokens are rejected: `operator`, `approver`, `admin`, or `reviewer` token required.
- When `meta.staleness` is non-null, display a staleness notice but do not hide content.

## Non-Blocking Caveats

- `viewer` role is rejected on all CP surfaces.

## Example Payload

- `docs/examples/PKT-004-capital-binding-drilldowns.json`
