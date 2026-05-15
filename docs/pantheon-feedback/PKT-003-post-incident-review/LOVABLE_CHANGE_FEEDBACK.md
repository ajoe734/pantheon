# PKT-003 Post-Incident Review Console — Lovable Change Feedback

Reviewed the post-incident-review-console implementation in `ajoe734/front-ai-trading-system` against the PKT-003 BFF contract, screen spec, and example payload.

## Outcome

Pantheon review result: accepted for follow-up handoff.

The Post-Incident Review Console is implemented against the published PKT-003 contract. Two of the three allowed BFF endpoints are consumed through the existing BFF client (`GET /api/v1/incidents` and `GET /api/v1/operator/post-incident-review/{incident_id}`). `GET /api/v1/postmortems` is not implemented in this delivery (see integration boundary notes). Acceptance criteria met for the implemented scope.

> **BFF-gap note:** A `bff-gap` handoff was filed earlier (`.coordination/requests/PKT-003-post-incident-review-bff-gap.yaml`) because `GET /api/v1/incidents` omitted `resolved_at` from `_project_incident_home_item()`. That gap was resolved and a refreshed contract-ready packet was published (`.coordination/responses/PKT-003-post-incident-review-contract-ready.yaml`, `2026-04-16T06:59:27Z`). Implementation proceeded after the corrected projection was confirmed.

## Verified Against Pantheon

- `GET /api/v1/incidents?status=resolved` is consumed through the BFF client. The incident list panel renders one row per `items[]` entry with `incident_id`, `title`, `status`, `artifact_id`, and `resolved_at`. No raw `fetch` or `axios` in component files.
- `GET /api/v1/operator/post-incident-review/{incident_id}?snapshot=preferred` is the primary source for the detail panel. All composed sub-objects (`incident`, `postmortem`, `evolution_decisions`, `lineage_edges`, `telemetry_performance`) are fetched in one call.
- `GET /api/v1/postmortems` is **not implemented** in this delivery. No `/api/v1/postmortems` client method exists in `bffClient.ts`. The postmortem panel data comes exclusively from the composed detail response. Wiring a standalone `GET /api/v1/postmortems` call for navigation context (breadcrumb or related postmortem links) is a deferred follow-up item.
- The **Incident summary** section renders `incident_id`, `title`, `status`, `artifact_id`, `artifact_version`, `runtime_id`, and `trace_id`.
- The **Postmortem panel** gates on `meta.surfaces.postmortem`:
  - `ok`: renders `postmortem_id`, `status`, `root_cause`, and `action_items[]`.
  - `degraded`: renders "Postmortem pending" with `incident_id`. Panel is not hidden.
  - `unavailable`: renders explicit unavailable banner. Panel is not hidden.
- The **Evolution decisions panel** renders each `evolution_decisions[]` item with `action_type`, `risk_level`, `status`, and `artifact_id`. Renders "No evolution decisions" when the list is empty.
- The **Lineage edges panel** renders each `lineage_edges[]` item with `from_artifact_id`, `to_artifact_id`, and `relationship`. Renders "No lineage evidence" when empty or when `meta.surfaces.lineage` is `degraded`.
- The **Telemetry performance panel** renders `telemetry_performance.summary` (`total_pnl`, `max_drawdown`, `sharpe_ratio`) and `window`. Renders "No telemetry evidence" when `telemetry_performance` is null or `meta.surfaces.telemetry_performance` is `degraded`.
- The **Degradation banner** is non-dismissable and names the affected surface whenever any `meta.surfaces` entry is not `ok`. Content is never silently hidden.
- The **Staleness banner** is non-dismissable and is rendered when `meta.staleness` is present on the detail response.
- 404 on `{incident_id}` renders "Incident not found" with the ID and a back action.
- Any absent `meta.surfaces` key in the response emits a `bff-gap` alert state — it does not assume a surface health state.
- Loading, empty, degraded, and error states are explicit and visually distinct with no mock fallback.

## Notes

- `meta.surfaces` values in the example payload are delivered as `{ "status": "ok" }` objects rather than plain strings. The implementation reads `meta.surfaces.postmortem.status` (not `meta.surfaces.postmortem`) to remain consistent with the actual example payload shape. This is documented as a surface-envelope convention in the BFF contract.
- No write actions exist on this screen — all incident response write actions belong to `PKT-002`.
- The prior BFF-gap (`resolved_at` missing from incident list projection) is resolved. The implementation targets the corrected BFF contract published in the refreshed contract-ready packet.

## Integration Boundary Notes

- **URL state key:** The implementation uses `?incident=<id>` (not `?incident_id=<id>`). React reads/writes `searchParams.get('incident')`.
- **`GET /api/v1/postmortems` not implemented:** This endpoint is in `allowed_endpoints` but no BFF client method exists in this delivery. The postmortem panel is sourced exclusively from the composed detail response. Wiring standalone postmortem navigation context is a deferred follow-up.

## Pantheon Follow-up

- No new Pantheon API gap requested in this cycle. The original `resolved_at` bff-gap was resolved before implementation started.
- The next Pantheon-owned step is to wire this screen into the Operator Console routing surface once the workbench shell is finalized.
- Implement `GET /api/v1/postmortems` in `bffClient.ts` for navigation context when the Operator Console routing surface is finalized.
