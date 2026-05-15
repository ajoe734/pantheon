# PKT-003 Post-Incident Review Console — UI Decisions

- The screen is routed at `/operator/post-incident-review` with `incident` in the query string (not `incident_id`) so list-panel selection and detail panel state are URL-addressable without inventing alternate navigation state. The actual React implementation reads and writes `searchParams.get('incident')` / `nextParams.set('incident', ...)` / `nextParams.delete('incident')`. A query-string parameter named `incident_id` is not implemented in this delivery.
- The incident list panel fetches `GET /api/v1/incidents?status=resolved` on mount. It does not fetch all incidents — the `status=resolved` filter is passed to the BFF as documented.
- Row click in the incident list triggers `GET /api/v1/operator/post-incident-review/{incident_id}?snapshot=preferred` for the composed detail view. It does not fetch individual sub-surfaces separately.
- `meta.surfaces` gating authority comes entirely from the BFF response. No panel visibility is derived locally from the presence or absence of data fields.
- The `meta.surfaces` values in the example payload are delivered as `{ "status": "ok" }` envelope objects. The implementation reads `surfaces[key].status` to be consistent with the example payload. This is treated as the BFF convention; a plain string value would also be accepted by reading `surfaces[key]` directly.
- The degradation banner is non-dismissable and names each affected surface explicitly. It is rendered when any `meta.surfaces[key].status` is not `ok`. Content panels are not hidden behind the banner.
- The staleness banner is non-dismissable and is rendered when `meta.staleness` is present in the detail response.
- The postmortem panel is always rendered — it is never hidden when `meta.surfaces.postmortem` is `degraded` or `unavailable`. In `degraded` state it shows "Postmortem pending" with `incident_id`. In `unavailable` state it shows an explicit unavailable banner.
- Evolution decisions: when `evolution_decisions[]` is empty, renders "No evolution decisions" regardless of surface status.
- Lineage edges: when `lineage_edges[]` is empty or `meta.surfaces.lineage` is `degraded`, renders "No lineage evidence" with a staleness note. When `unavailable`, renders "Lineage unavailable" banner.
- Telemetry performance: when `telemetry_performance` is null or `meta.surfaces.telemetry_performance` is `degraded`, renders "No telemetry evidence". When `unavailable`, renders "Telemetry performance unavailable" banner.
- 404 on `{incident_id}` renders "Incident not found" with the incident ID and a back action. It does not fall back to an empty detail state.
- Any absent `meta.surfaces` key in the detail response triggers a BFF-gap alert state rather than assuming surface health. No silent mock fallback exists.
- `GET /api/v1/postmortems` is listed in the allowed endpoints but is **not implemented** in this delivery. The BFF client (`bffClient.ts`) implements `listIncidents()` and `getPostIncidentReview()` only. The postmortem panel data is sourced entirely from the composed `GET /api/v1/operator/post-incident-review/{incident_id}?snapshot=preferred` response. Wiring a standalone `GET /api/v1/postmortems` call for navigation context (breadcrumb or related postmortem links) is a deferred follow-up item.
- No write actions exist on this screen. All incident response write actions (acknowledge, escalate, resolve) belong to `PKT-002` (Incident Response Console).
- The prior BFF-gap for `resolved_at` is resolved. The implementation targets the corrected BFF contract.
