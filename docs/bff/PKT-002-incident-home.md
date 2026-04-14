# PKT-002 Incident Home BFF Contract

## Purpose

Provide a list-shaped payload for open and in-progress incidents, and a kill switch status payload for the control rail badge, so the UI does not need to join incident state or derive emergency status client-side.

## Primary Read Routes

### List incidents

- `GET /api/v1/incidents`
- Query parameters: `status` (comma-separated: `open`, `in_progress`, `resolved`), `page_token`, `page_size`

Required response fields per item:

- `incident_id`
- `title`
- `severity` (`sev1`, `sev2`, `sev3`)
- `status` (`open`, `in_progress`, `resolved`)
- `artifact_id`
- `opened_at`

Required list-level fields:

- `page_info.next_page_token` (nullable)
- `meta.snapshot_at`
- `meta.surfaces.incident_list` — `ok`, `degraded`, or `unavailable`

### Get kill switch status

- `GET /api/v1/kill-switch/status`

Required response fields:

- `kill_switch.status` (`armed`, `triggered`, `cooling_down`)
- `kill_switch.last_triggered_at` (nullable RFC3339)
- `kill_switch.last_confirmed_at` (RFC3339)
- `kill_switch.active_commands[]` — list of active emergency commands in effect (may be empty)
- `meta.snapshot_at`
- `meta.surfaces.kill_switch` — `ok`, `degraded`, or `unavailable`

## Degraded-State Rules

- When `meta.surfaces.incident_list = degraded`, return as many items as are available and include a `meta.degradation.reason` string.
- When `meta.surfaces.incident_list = unavailable`, return an empty `items[]` and a `meta.degradation` object. The UI must render an explicit unavailable state, not an empty list.
- When `meta.surfaces.kill_switch = degraded`, return the last known `kill_switch` state with `last_confirmed_at` set to the timestamp of that state. The UI must render the badge with the staleness caveat.
- When `meta.surfaces.kill_switch = unavailable`, return `kill_switch.status = null` and `meta.surfaces.kill_switch = unavailable`. The UI must render "Kill switch status unavailable" — it must not assume any kill switch state.

## Design Rules

- The kill switch status and incident list are independent reads. The BFF must not block one on the other.
- The UI must not derive kill switch eligibility or emergency command authority locally.
- All CTA-facing fields on downstream screens originate from `allowedActions` on the composed view (Incident Detail), not from the list view.
- Downstream failure in the kill switch service must surface through degradation metadata, never by silently returning a default state.

## Example Payload

- `docs/examples/PKT-002-incident-home.json`
