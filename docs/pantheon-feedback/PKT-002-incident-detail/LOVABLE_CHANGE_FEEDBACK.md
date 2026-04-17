# PKT-002 Incident Detail Lovable Change Feedback

Reviewed the current `front-ai-trading-system` working tree against the mirrored PKT-002 handoff bundle and the checked-out base commit `faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7`.

## Outcome

Pantheon review result: ready for review handoff.

The Incident Detail screen is implemented against the published composed read contract. The page validates the required PKT-002 fields, renders explicit degraded and unavailable copy per documented surface, and keeps CTA authority backend-shaped through `allowedActions`.

One non-blocking follow-up remains for `HardRollback`: the embedded drawer cannot safely submit that command from Incident Detail because the detail contract does not publish a rollback target artifact ID.

## Verified Against Pantheon

- `GET /api/v1/operator/incident-response/{incident_id}` is consumed through `operatorApi.getIncidentResponse()` in the shared BFF client.
- No raw `fetch()` calls were added to `src/pages/operator/IncidentDetail.tsx`.
- The route is wired at `/incidents/:incidentId`, which matches the existing incident-home navigation already present in this checkout.
- The screen renders explicit loading, 404, contract-gap, degraded, unavailable, and empty-success states. It does not collapse degraded reads into generic empty copy.
- The degradation banner appears when any `meta.surfaces` entry is `degraded` or `unavailable`, and the kill-switch panel also shows the documented staleness note when `meta.staleness` is present.
- The action entry strip is driven only from `allowedActions`; the UI does not derive emergency authority locally.
- The **Open Action Drawer** CTA now opens the shared `IncidentActionDrawer` component, which performs its own fresh `GET /api/v1/kill-switch/status` fetch on open and submits commands only through the existing operator client.
- `PauseExecution`, `IssueRiskOff`, `LiquidateAll`, and `IssueSafeMode` can be issued from the embedded drawer when Pantheon authorizes them.

## Notes

- The affected-bindings panel distinguishes all three required states:
  - `ok` + empty list -> "No affected bindings recorded"
  - `degraded` -> available rows plus explicit named degradation copy
  - `unavailable` -> explicit unavailable alert without inventing rows
- The kill-switch panel distinguishes `ok`, `degraded`, and `unavailable` without inferring a state when Pantheon marks the surface unavailable.
- The embedded action drawer reuses the published PKT-002 command contract and standalone host route, so the detail CTA has a live target instead of a dead link.
- `HardRollback` remains visible in the embedded drawer when Pantheon authorizes it, but it stays disabled there until a rollback target artifact ID is available from screen context. The follow-up request is captured in `API_GAP_REQUESTS.json`.

## Pantheon Follow-up

- Review the Incident Detail screen against the latest PKT-002 contract, example payload, and embedded drawer behavior.
- Publish the canonical source of `target_artifact_id` for `HardRollback` when the action drawer is opened from Incident Detail, or add that context to the incident-detail packet.
- Run live browser QA against a real Pantheon BFF to confirm degraded-surface copy and action-drawer command receipts under live data.
