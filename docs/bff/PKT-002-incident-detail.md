# PKT-002 Incident Detail BFF Contract

## Purpose

Provide a page-shaped composed view for the Incident Detail screen so the UI does not need to join incident records, affected bindings, kill switch state, or action authority client-side.

## Primary Read Route

### Get incident response composed view

- `GET /api/v1/operator/incident-response/{incident_id}`
- Optional query parameter: `snapshot=preferred` — triggers `meta.staleness` metadata when BFF read surfaces are not fresh

Required top-level fields:

- `data.incident.incident_id`
- `data.incident.title`
- `data.incident.severity` (`sev1`, `sev2`, `sev3`)
- `data.incident.status` (`open`, `in_progress`, `resolved`)
- `data.incident.artifact_id`
- `data.incident.artifact_version`
- `data.incident.runtime_id`
- `data.incident.trace_id`
- `data.incident.opened_at`
- `data.affected_bindings[]` — list of affected binding records; may be empty but must not be absent
- `data.kill_switch.status` — current kill switch state (`armed`, `triggered`, `cooling_down`)
- `data.kill_switch.last_triggered_at` (nullable RFC3339)
- `data.kill_switch.last_confirmed_at` (RFC3339)
- `data.kill_switch.active_commands[]`
- `allowedActions.canPause`
- `allowedActions.canRiskOff`
- `allowedActions.canLiquidateAll`
- `allowedActions.canHardRollback`
- `allowedActions.canIssueSafeMode`
- `allowedActions.canOpenActionDrawer`
- `meta.snapshot_at`
- `meta.surfaces.incident` — `ok`, `degraded`, or `unavailable`
- `meta.surfaces.affected_bindings` — `ok`, `degraded`, or `unavailable`
- `meta.surfaces.kill_switch` — `ok`, `degraded`, or `unavailable`
- `meta.surfaces.allowedActions` — `ok`, `degraded`, or `unavailable`

Required fields per `data.affected_bindings[]` item:

- `binding_id`
- `persona_id`
- `capital_pool_id`
- `stage` (`paper`, `live`)
- `binding_status`

## Degraded-State Rules

- When `meta.surfaces.kill_switch = degraded`, return the last known `data.kill_switch` state with `last_confirmed_at` set to the timestamp of that state. Include a `meta.degradation.kill_switch_reason` string.
- When `meta.surfaces.kill_switch = unavailable`, return `data.kill_switch.status = null`. The UI must not assume any kill switch state.
- When `meta.surfaces.affected_bindings = degraded`, return as many binding records as are available and include a `meta.degradation.affected_bindings_reason` string.
- When `meta.surfaces.allowedActions = degraded`, return a conservative `allowedActions` with all action flags set to `false`. The UI must not enable CTAs when action authority cannot be confirmed.
- When `meta.surfaces.allowedActions = unavailable`, set all `allowedActions` flags to `false` and include a `meta.degradation.allowedActions_reason` string.

## Staleness Handling

- Request with `?snapshot=preferred` to receive `meta.staleness` when `BFF_READ_SURFACE_STATE != fresh`.
- When staleness metadata is present, show a non-dismissable staleness banner on the kill switch status panel.

## Error Handling

- 404 on `{incident_id}`: return a 404 response. The UI must render "Incident not found" with the ID and a back action.
- Any `meta.surfaces` key absent from the response: the UI must emit a `bff-gap` handoff. Do not render the screen with assumed surface states.

## Write Actions

All write actions originate from the Incident Action Drawer screen (`PKT-002-incident-action-drawer`). This composed view is read-only.

## Example Payload

- `docs/examples/PKT-002-incident-detail.json`
