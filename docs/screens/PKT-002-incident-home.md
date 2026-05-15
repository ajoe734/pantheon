# PKT-002 Incident Home

## Classification

- Workbench: Operator Console
- Screen ID: `screen-operator-incident-home`
- Feature ID: `PKT-002-incident-home`
- Packet status: ready

## User Goal

Give an operator a single entry point for all active and recent incidents, with the kill switch status always visible as a control rail badge. The operator must be able to triage open incidents and enter the detail view without navigating away from the console home.

## Page Sections

- **Kill Switch Control Rail**: a persistent badge at the top of the screen showing `kill_switch.status` (`armed`, `triggered`, `cooling_down`). Sourced from `GET /api/v1/kill-switch/status`. When `meta.surfaces.kill_switch = degraded`, a non-dismissable warning banner replaces the badge and shows the last known state with `last_confirmed_at` timestamp. When `meta.surfaces.kill_switch = unavailable`, a non-dismissable "Kill switch status unavailable" banner is shown instead; no inferred state or `last_confirmed_at` is displayed.
- **Incident List panel**: paginated list of incidents filtered by `status=open,in_progress`. Each row shows `incident_id`, `title`, `severity`, `status`, `artifact_id`, and `opened_at`. Source: `GET /api/v1/incidents`.
- **Resolved Incidents tab**: secondary tab showing `status=resolved` incidents for reference. Same row shape as the open list.
- **Degradation banner**: when any `meta.surfaces` entry has `status != "ok"`, a non-dismissable banner names the affected surface. A degraded `kill_switch` surface triggers the banner even when the incident list itself is healthy.
- **Loading, empty, and error states**: explicit and visually distinct with no mock fallback.

## Interaction Rules

- All production data comes from Pantheon BFF routes only.
- The kill switch status is fetched independently from the incident list and must not block incident list rendering.
- Filtering by `status` is passed as a query parameter to `GET /api/v1/incidents`; the UI does not filter client-side.
- Row selection navigates to the Incident Detail screen (`screen-operator-incident-detail`).
- If `meta.surfaces.kill_switch = degraded`, render the last known state with `last_confirmed_at`. Do not hide the control rail.
- If `meta.surfaces.kill_switch = unavailable`, render "Kill switch status unavailable" banner. Do not assume any state.
- If a required field is absent from the BFF response, the UI must emit a `bff-gap` handoff instead of inventing local state.

## Acceptance

- Incident list renders open and in-progress incidents from real BFF data with no mock rows.
- Kill switch status badge renders from `GET /api/v1/kill-switch/status`.
- Degraded kill switch surface triggers the non-dismissable warning banner with last known state.
- Resolved incidents tab renders from the same route with `status=resolved` filter.
- Loading, empty, degraded, and error states are explicit and visually distinct.
- Front-end emits a `bff-gap` handoff if any `meta.surfaces` key is absent from either BFF response.
