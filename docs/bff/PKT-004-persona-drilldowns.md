# PKT-004 Module A — Persona Drilldowns BFF Contract

## Purpose

Define the six individual BFF read surfaces that back the Persona Drilldown module so the UI can navigate persona, session, teaching, and capability data without client-side joins.

## Read Routes

### PS-01 — Persona Catalog

```
GET /api/v1/personas
Query params: lifecycle_state (optional), page_token, page_size
```

Required response fields:

- `data[].id`
- `data[].name`
- `data[].lifecycle_state`
- `data[].mandate`
- `data[].strategy_family`
- `data[].created_at`
- `data[].last_active_at`
- `meta.total`
- `meta.staleness` (nullable)

### PS-02 — Persona Detail

```
GET /api/v1/personas/{persona_id}
```

Required response fields:

- `data.id`
- `data.name`
- `data.lifecycle_state`
- `data.mandate`
- `data.strategy_family`
- `data.created_at`
- `data.last_active_at`
- `data.bindings[]` (each binding: `id`, `capital_pool_id`, `validity`, `status`, `allowed_deployment_scope`)
- `meta.staleness` (nullable)

### PS-03 — Session List

```
GET /api/v1/personas/{persona_id}/sessions
Query params: status (optional), page_token, page_size
```

Required response fields:

- `data[].id`
- `data[].persona_id`
- `data[].session_type`
- `data[].status`
- `data[].started_at`
- `data[].last_heartbeat_at`
- `data[].tools_enabled[]`
- `data[].pool_scope`
- `data[].deployment_stage`
- `data[].runtime_binding_id`
- `meta.total`
- `meta.staleness` (nullable)

### PS-04 — Session Detail

```
GET /api/v1/sessions/{session_id}
```

Required response fields:

- `data.id`
- `data.persona_id`
- `data.session_type`
- `data.status`
- `data.started_at`
- `data.last_heartbeat_at`
- `data.tools_enabled[]`
- `data.pool_scope`
- `data.deployment_stage`
- `data.capital_pool_id`
- `data.runtime_binding_id`
- `data.capability_snapshot` (embedded)
  - `snapshot_id`
  - `effective_tools[]`
  - `effective_skills[]`
  - `effective_workflows[]`
  - `restrictions[]`
  - `generated_at`
  - `source_refs[]`
- `meta.staleness` (nullable)

### PS-05 — Teaching History

```
GET /api/v1/personas/{persona_id}/teaching
Query params: page_token, page_size
```

Required response fields:

- `data[].id`
- `data[].persona_id`
- `data[].status`
- `data[].started_at`
- `data[].completed_at` (nullable)
- `data[].topic`
- `data[].operator_id`
- `data[].outcomes[]`
- `data[].session_artifacts[]`
- `meta.total`
- `meta.staleness` (nullable)

### PS-06 — Capability Snapshot

```
GET /api/v1/personas/{persona_id}/capabilities
```

Required response fields:

- `data.snapshot_id`
- `data.persona_id`
- `data.effective_tools[]`
- `data.effective_skills[]`
- `data.effective_workflows[]`
- `data.restrictions[]`
- `data.generated_at`
- `data.source_refs[]`
- `meta.staleness` (nullable)

## Design Rules

- All six surfaces are read-only. No write actions are defined in this module.
- Filters must be sent as query parameters; the BFF applies them. No client-side filtering.
- `viewer` role tokens are rejected: `operator`, `approver`, `admin`, or `reviewer` token required.
- When `meta.staleness` is non-null, display a staleness notice but do not hide content.

## Non-Blocking Caveats

- `viewer` role is rejected on all PS surfaces.
- `meta.staleness` may be non-null for list surfaces when the backing store has not recently refreshed.

## Example Payload

- `docs/examples/PKT-004-persona-drilldowns.json`
