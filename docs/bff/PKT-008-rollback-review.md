# PKT-008 Governance Rollback Review BFF Contract

## Purpose

Provide a BFF-composed rollback review payload for the Governance Rollback Review screen so the UI does not reconstruct position impact, binding scope, or rollback authority state client-side.

## Primary Read Route

- `GET /api/v1/operator/rollback-review/{rollback_id}`
- Path parameter: `rollback_id` — the rollback request under review

Required response fields:

- `rollback_id`
- `target_plan_id` (deployment plan to roll back to)
- `trigger_reason` (human-readable backend-supplied trigger summary)
- `requested_at`
- `requested_by`
- `rollback_scope` (`full` | `partial`)
- `affected_persona_count` (integer)
- `affected_binding_count` (integer)
- `target_stage` (deployment stage the rollback would revert to)
- `position_impact[]` (one entry per affected binding)
  - `binding_id`
  - `persona_id`
  - `current_stage`
  - `target_stage`
  - `position_impact_summary` (human-readable; null when position data is stale)
  - `position_data_stale` (boolean)
- `affected_bindings[]`
  - `binding_id`
  - `persona_id`
  - `capital_pool_id`
  - `current_stage`
- `trigger_evidence`
  - `trigger_reason`
  - `evidence_refs[]` (each with `ref_id`, `type`, `url`)
  - `linked_incident_id` (nullable)
- `allowedActions`
  - `canApproveRollback` (boolean)
  - `canRejectRollback` (boolean)
- `meta.snapshot_at`
- `meta.surfaces` (per-surface `status`; must include `position_data` and `rollback_review`)

## Write Actions

All write actions use `POST /api/v1/operator/commands`.

### Approve Rollback

```json
{
  "command": "ApproveRollback",
  "target": { "type": "Rollback", "id": "{rollback_id}" },
  "action": "approve",
  "params": { "rollback_id": "{rollback_id}", "approval_notes": "optional" },
  "audit_context": { "reason": "operator rationale (required)", "timestamp": "RFC3339" }
}
```

### Reject Rollback

```json
{
  "command": "RejectRollback",
  "target": { "type": "Rollback", "id": "{rollback_id}" },
  "action": "reject",
  "params": { "rollback_id": "{rollback_id}", "rejection_reason": "required" },
  "audit_context": { "reason": "operator rationale (required)", "timestamp": "RFC3339" }
}
```

## Design Rules

- The BFF composes position impact from runtime binding state and position data. The UI must not derive position impact from raw binding or telemetry fields.
- All CTA-facing fields (`allowedActions.*`) must be backend-shaped.
- When `meta.surfaces.position_data` is `degraded` or `unavailable`, the Approve CTA must be disabled regardless of `allowedActions.canApproveRollback`. The stale-data warning must appear on all affected position_impact rows.
- When `position_data_stale` is `true` on a row, `position_impact_summary` will be null. The UI renders the stale-data message for that row.
- Rollback action semantics (safe rollback targets, position settlement rules, and authority boundaries) are governed by `ROLLBACK_AND_POSITION_SEMANTICS.md`. This contract does not redefine those rules; it exposes them as backend-shaped fields.
- Inherits `meta.surfaces.*` degradation semantics from `PKT-005 Degradation Banner`.

## Example Payload

- `docs/examples/PKT-008-rollback-review.json`
