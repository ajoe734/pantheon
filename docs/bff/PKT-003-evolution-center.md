# PKT-003 Evolution Center BFF Contract

## Purpose

Provide list and detail read surfaces for evolution decisions, freeze orders, and rollback records so the Evolution Center can render the current evolution state without client-side joins.

## Primary Read Routes

### List evolution decisions (EV-01)

- `GET /api/v1/evolution-decisions`
- Query parameters: `action_type`, `risk_level`, `status`, `page_token`, `page_size`

Required response fields per item:

- `id`
- `action_type`
- `risk_level`
- `status`
- `incident_ref`
- `artifact_id`

Required list-level fields:

- `page_info.next_page_token` (nullable)
- `meta.snapshot_at`

### Get evolution decision detail (EV-02)

- `GET /api/v1/evolution-decisions/{decision_id}`

Required response fields:

- All EV-01 item fields
- `created_at`
- `updated_at`
- `notes`
- `meta.snapshot_at`

### List freeze orders (EV-03)

- `GET /api/v1/freeze-orders`
- Query parameters: `status`, `scope`

Required response fields per item:

- `freeze_order_id`
- `status`
- `scope`
- `issued_at`

### List rollbacks (EV-04)

- `GET /api/v1/rollbacks`
- Query parameters: `runtime_id`, `action_type`
- Note: `time_range` parameter is accepted but not applied in v1 store — do not expose as a UI control.

Required response fields per item:

- `rollback_id`
- `action_type`
- `runtime_id`
- `executed_at`

## UI Gating Rules

- When `BFF_READ_SURFACE_STATE != fresh`, render a staleness banner on all panels.
- Render empty states explicitly for each panel — do not collapse panels that return zero rows.
- Only `operator`, `approver`, `admin`, and `reviewer` role tokens are accepted. Viewer tokens are rejected at the BFF; surface this as a "permission required" state, not a data-loading error.

## Error Handling

- 404 on `{decision_id}`: render "Evolution decision not found" with the ID.
- Any missing required field in the list response: emit a `bff-gap` handoff.

## Write Actions

None. Evolution decision mutations require the Mutation Review screen (`PKT-003-mutation-review`), which is blocked pending EVO-004 execution boundary settlement.
