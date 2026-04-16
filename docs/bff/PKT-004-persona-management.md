# PKT-004 Persona Management Composed Screen — BFF Contract

## Purpose

Provide a page-shaped composed view for the Persona Management screen so the UI does not need to join persona, binding, capital-pool, session, or teaching data client-side.

## Primary Read Route

- `GET /api/v1/operator/persona-management/{persona_id}`
- Query parameters: `snapshot` (optional: `preferred`)
- Role requirement: `operator`, `approver`, `admin`, or `reviewer` token. Viewer-only tokens are rejected.

Required response fields:

- `data.persona`
  - `id`
  - `name`
  - `lifecycle_state`
  - `mandate`
  - `strategy_family`
  - `created_at`
  - `last_active_at`
- `data.bindings[]`
  - `id`
  - `persona_id`
  - `capital_pool_id`
  - `capital_pool.id` (embedded from CP-04)
  - `capital_pool.status`
  - `validity`
  - `status`
  - `allowed_deployment_scope`
- `data.sessions[]`
  - `id`
  - `persona_id`
  - `status`
  - `started_at`
  - `last_heartbeat_at`
  - `tools_enabled[]`
  - `pool_scope`
- `data.teaching_sessions[]`
  - `id`
  - `persona_id`
  - `status`
  - `started_at`
  - `completed_at`
  - `topic`
  - `operator_id`
  - `outcomes[]`
  - `session_artifacts[]`
- `data.allowedActions`
  - `canActivate`
  - `canEdit`
  - `canDelete`
  - `canRetire`
  - `canPause`
  - `canTerminateSession`
  - `canPauseSession`
  - `canViewTeachingHistory`
- `meta.snapshot_at`
- `meta.surfaces`
  - `persona_bindings.status` (`ok` | `degraded` | `unavailable`)
  - `capital_pool_bindings.status`
  - `persona_sessions.status`
  - `teaching_sessions.status`
  - `allowed_actions.status`

## Write Actions

All write actions use `POST /api/v1/operator/commands`.

### Edit Persona

```json
{
  "command": "EditPersona",
  "target": { "type": "Persona", "id": "{persona_id}" },
  "action": "edit",
  "params": { "persona_id": "{persona_id}", "updates": {} },
  "audit_context": { "reason": "operator rationale (required)", "timestamp": "RFC3339" }
}
```

### Retire Persona

```json
{
  "command": "RetirePersona",
  "target": { "type": "Persona", "id": "{persona_id}" },
  "action": "retire",
  "params": { "persona_id": "{persona_id}" },
  "audit_context": { "reason": "required", "timestamp": "RFC3339" }
}
```

### Terminate Session

```json
{
  "command": "TerminateSession",
  "target": { "type": "Session", "id": "{session_id}" },
  "action": "terminate",
  "params": { "session_id": "{session_id}", "persona_id": "{persona_id}" },
  "audit_context": { "reason": "required", "timestamp": "RFC3339" }
}
```

## Design Rules

- All CTA-facing fields (`allowedActions.*`) must be backend-shaped; the UI must not derive persona authority locally.
- When any surface in `meta.surfaces` is `degraded` or `unavailable`, the corresponding panel must show a degraded-panel placeholder and CTAs on that panel must be disabled; the global degradation banner must appear.
- Do not supplement the composed response with client-side joins or computed fields.

## Non-Blocking Caveats

- `snapshot=preferred` is accepted but not enforced: `meta.snapshot_at` is returned but sub-surface timestamps are not aligned in v1.
- Read-surface staleness is not tied to `BFF_READ_SURFACE_STATE`: degradation flags only when a sub-surface returns `None` or empty results.

## Example Payload

- `docs/examples/PKT-004-persona-management.json`
