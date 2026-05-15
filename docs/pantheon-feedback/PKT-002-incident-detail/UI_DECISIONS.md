# PKT-002 Incident Detail UI Decisions

- The composed detail screen is routed at `/incidents/:incidentId` so it lines up with the existing incident-home navigation already present in this repo.
- The page reads only through `operatorApi.getIncidentResponse(incidentId, 'preferred')`; no component-level network call was added.
- Missing required fields are surfaced as an explicit contract-gap state instead of silently omitting panels or synthesizing fallback data.
- The affected-bindings panel treats `degraded` and `unavailable` as distinct states and never reuses the success empty-state copy for degraded reads.
- The kill-switch panel renders `meta.staleness` inside the panel in addition to the page-level degradation banner so operators can see why the last known state may be stale.
- The action entry strip renders only the backend-authorized emergency actions and disables the drawer entry whenever the `allowedActions` surface is not healthy.
- The **Open Action Drawer** CTA is backed by the shared `IncidentActionDrawer` component rather than a placeholder route, so the detail screen enters a real PKT-002 control surface.
- The shared drawer fetches `GET /api/v1/kill-switch/status` fresh on open and uses `POST /api/v1/operator/commands` through the existing operator client only.
- The embedded drawer passes `incidentId` and `runtimeId` from Incident Detail. `HardRollback` stays disabled there unless a rollback target artifact ID is supplied from host context, because the detail packet does not publish that field.
