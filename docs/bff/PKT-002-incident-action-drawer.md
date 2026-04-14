# PKT-002 Incident Action Drawer BFF Contract

## Purpose

Provide the kill switch status read surface and the operator command write surface so the Incident Action Drawer can render emergency action authority, issue commands, and return command receipts without the UI deriving emergency state client-side.

## Primary Read Route

### Get kill switch status (fresh fetch on drawer open)

- `GET /api/v1/kill-switch/status`

Required response fields:

- `kill_switch.status` (`armed`, `triggered`, `cooling_down`)
- `kill_switch.last_triggered_at` (nullable RFC3339)
- `kill_switch.last_confirmed_at` (RFC3339)
- `kill_switch.active_commands[]` — list of active emergency commands in effect
- `allowedActions.canPause`
- `allowedActions.canRiskOff`
- `allowedActions.canLiquidateAll`
- `allowedActions.canHardRollback`
- `allowedActions.canIssueSafeMode`
- `allowedActions.secondaryPathAvailable` — `true` when the fallback routing path is reachable
- `meta.snapshot_at`
- `meta.surfaces.kill_switch` — `ok`, `degraded`, or `unavailable`
- `meta.surfaces.allowedActions` — `ok`, `degraded`, or `unavailable`

## Write Route

### Issue emergency command

- `POST /api/v1/operator/commands`

All emergency commands use this route. The `command` field discriminates the action type.

#### Pause Execution

```json
{
  "command": "PauseExecution",
  "target": { "type": "Runtime", "id": "{runtime_id}" },
  "params": {
    "pause_new_entries": true,
    "cancel_open_orders": false
  },
  "audit_context": {
    "reason": "operator rationale (required, non-empty)",
    "timestamp": "RFC3339",
    "incident_id": "{incident_id}"
  }
}
```

#### Issue Risk-Off

```json
{
  "command": "IssueRiskOff",
  "target": { "type": "Runtime", "id": "{runtime_id}" },
  "params": {
    "reduce_exposure_pct": 100
  },
  "audit_context": {
    "reason": "operator rationale (required, non-empty)",
    "timestamp": "RFC3339",
    "incident_id": "{incident_id}"
  }
}
```

#### Liquidate All

```json
{
  "command": "LiquidateAll",
  "target": { "type": "Runtime", "id": "{runtime_id}" },
  "params": {},
  "audit_context": {
    "reason": "operator rationale (required, non-empty)",
    "timestamp": "RFC3339",
    "incident_id": "{incident_id}"
  }
}
```

#### Hard Rollback

```json
{
  "command": "HardRollback",
  "target": { "type": "Runtime", "id": "{runtime_id}" },
  "params": {
    "target_artifact_id": "{rollback_artifact_id}"
  },
  "audit_context": {
    "reason": "operator rationale (required, non-empty)",
    "timestamp": "RFC3339",
    "incident_id": "{incident_id}"
  }
}
```

#### Issue Safe Mode

```json
{
  "command": "IssueSafeMode",
  "target": { "type": "Runtime", "id": "{runtime_id}" },
  "params": {
    "safe_mode_level": "soft"
  },
  "audit_context": {
    "reason": "operator rationale (required, non-empty)",
    "timestamp": "RFC3339",
    "incident_id": "{incident_id}"
  }
}
```

### Command Receipt

Required response fields from `POST /api/v1/operator/commands`:

- `receipt_id`
- `command`
- `status` (`accepted`, `queued`, `failed`)
- `accepted_at` (RFC3339)
- `routing_path` (`direct` or `fallback`)
- `expected_completion_at` (nullable RFC3339)
- `error_message` (string, present only when `status = failed`)

## Secondary Control Path Rules

- When `meta.surfaces.kill_switch = degraded` or `unavailable`, the BFF must return `allowedActions.secondaryPathAvailable` to indicate whether the fallback routing path is reachable.
- When `allowedActions.secondaryPathAvailable = true`, commands issued through the secondary path must return a receipt with `routing_path = fallback`.
- Only `PauseExecution` and `IssueRiskOff` are allowed through the fallback path. `LiquidateAll`, `HardRollback`, and `IssueSafeMode` require the primary path and must have their `allowedActions` flags set to `false` when the primary path is degraded.
- When `allowedActions.secondaryPathAvailable = false` and the primary path is also unavailable, return all `allowedActions` flags as `false`.

## Design Rules

- All CTA-facing fields must be backend-shaped in `allowedActions`.
- The UI must not compute emergency command eligibility or kill switch authority locally.
- Every command must include a non-empty `audit_context.reason`. The BFF must reject commands with an empty or missing `reason` with a 400 response.
- When `meta.surfaces.allowedActions = degraded`, return a conservative `allowedActions` with all action flags set to `false`.
- Downstream failure in the kill switch or runtime-manager service must surface through degradation metadata and receipt status, never by silently accepting commands that were not routed.

## Example Payload

- `docs/examples/PKT-002-incident-action-drawer.json`
