# PKT-003 Post-Incident Review Console

## Classification

- Workbench: Operator Console
- Screen ID: `screen-operator-post-incident-review`
- Feature ID: `PKT-003-post-incident-review`
- Packet status: ready

## User Goal

Give an operator a single surface to review a resolved incident end-to-end: the incident record, the postmortem findings, associated evolution decisions, artifact lineage, and telemetry performance — without joining these surfaces client-side.

## Page Sections

- **Incident list panel**: paginated list of resolved incidents. Each row shows `incident_id`, `title`, `status`, `artifact_id`, and `resolved_at`. Source: `GET /api/v1/incidents?status=resolved`.
- **Post-Incident Review detail panel**: opens on row selection using the composed view. Shows:
  - Incident summary: `incident_id`, `title`, `status`, `artifact_id`, `artifact_version`, `runtime_id`, `trace_id`.
  - Postmortem panel: `postmortem_id`, `status`, `root_cause`, `action_items[]`. Renders "Postmortem pending" when `meta.surfaces.postmortem = degraded`.
  - Evolution decisions panel: list of linked `evolution_decisions[]` with `action_type`, `risk_level`, `status`, and `artifact_id`.
  - Lineage edges panel: `lineage_edges[]` showing `from_artifact_id`, `to_artifact_id`, `relationship`. Renders "No lineage evidence" when empty.
  - Telemetry performance panel: `telemetry_performance` with `window` and `summary` containing `total_pnl`, `max_drawdown`, and `sharpe_ratio`. Renders "No telemetry evidence" when empty.
- **Degradation banner**: when any `meta.surfaces` entry has `status != "ok"`, a non-dismissable banner names the degraded panel and prevents that panel from showing empty data silently.
- **Loading, empty, degraded, and error states**: explicit and visually distinct with no mock fallback.

## Interaction Rules

- All production data comes from Pantheon BFF routes only.
- The detail panel uses `GET /api/v1/operator/post-incident-review/{incident_id}` as the primary source — do not re-fetch individual surfaces separately.
- Add `?snapshot=preferred` to the composed view request to trigger staleness metadata.
- When `meta.surfaces.postmortem = degraded`, display the "Postmortem pending" panel with incident ID. Do not hide the panel or show an empty state.
- When `meta.surfaces.lineage = degraded` or `lineage_edges` is empty, display "No evidence yet" with a staleness note.
- If a required field is absent from the BFF response, the UI must emit a `bff-gap` handoff instead of inventing local state.
- No write actions on this screen — all actions originate from Incident Response Console (`PKT-002`).

## Acceptance

- List panel renders resolved incidents from real BFF data with no mock rows.
- Detail panel opens from list row selection and renders all required fields from the composed view.
- Postmortem panel shows "Postmortem pending" copy when `meta.surfaces.postmortem = degraded`.
- Evolution decisions, lineage edges, and telemetry performance panels each handle empty and degraded states explicitly.
- Degradation banner renders when any `meta.surfaces` entry is not `ok`.
- Loading, empty, degraded, and error states are explicit and visually distinct.
- Front-end emits a `bff-gap` handoff if any expected `meta.surfaces` key is absent from the BFF response.
