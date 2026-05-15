# PKT-003 Post-Incident Review Console BFF Contract

## Purpose

Provide a page-shaped composed view for the Post-Incident Review Console so the UI does not need to join incident, postmortem, evolution, lineage, or telemetry state client-side.

## Primary Read Routes

### List resolved incidents

- `GET /api/v1/incidents?status=resolved`
- Query parameters: `status` (comma-separated: `open`, `resolved`), `page_token`, `page_size`

Required response fields per item:

- `incident_id`
- `title`
- `status`
- `artifact_id`
- `resolved_at`

Required list-level fields:

- `page_info.next_page_token` (nullable)
- `meta.snapshot_at`

### Get post-incident review (composed view)

- `GET /api/v1/operator/post-incident-review/{incident_id}`
- Optional query parameter: `snapshot=preferred`

Required top-level fields:

- `data.incident.incident_id`
- `data.incident.title`
- `data.incident.status`
- `data.incident.artifact_id`
- `data.incident.artifact_version`
- `data.incident.runtime_id`
- `data.incident.trace_id`
- `data.postmortem` (may be `null` when pending; use `meta.surfaces.postmortem` to gate)
- `data.evolution_decisions[]`
- `data.lineage_edges[]`
- `data.telemetry_performance` — shape: `{ artifact_id, window, summary: { total_pnl, max_drawdown, sharpe_ratio } }`; may be `null` when no telemetry evidence exists; use `meta.surfaces.telemetry_performance` to gate
- `meta.snapshot_at`
- `meta.surfaces.postmortem` — `ok`, `degraded`, or `unavailable`
- `meta.surfaces.evolution_decisions` — `ok`, `degraded`, or `unavailable`
- `meta.surfaces.lineage` — `ok`, `degraded`, or `unavailable`
- `meta.surfaces.telemetry_performance` — `ok`, `degraded`, or `unavailable`

### Get postmortem index

- `GET /api/v1/postmortems`
- Used for navigation to incident; not the primary composed-view source.

## UI Gating Rules

- If `meta.surfaces.postmortem = degraded`, render "Postmortem pending" panel with `incident_id`. Never render an empty panel.
- If `meta.surfaces.lineage = degraded` or `data.lineage_edges` is empty, render "No lineage evidence yet" with a staleness note.
- If `meta.surfaces.telemetry_performance = degraded` or `data.telemetry_performance` is null, render "No telemetry evidence yet".
- Never render a generic "no data" state — always attribute the absence to a specific surface.

## Staleness Handling

- Request with `?snapshot=preferred` to receive `meta.staleness` when `BFF_READ_SURFACE_STATE != fresh`.
- When staleness is present, show a non-dismissable banner on the detail panel.

## Error Handling

- 404 on `{incident_id}`: render "Incident not found" with the ID and a back action.
- Any `meta.surfaces` key absent from the response: emit a `bff-gap` handoff — do not render the screen with assumed surface states.

## Write Actions

None. All incident response write actions originate from the Incident Response Console (`PKT-002`).
