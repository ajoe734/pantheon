# PKT-002 Incident Detail — Lovable Change Feedback

Reviewed the Incident Detail screen implementation in `ajoe734/front-ai-trading-system` against the PKT-002 BFF contract, screen spec, and example payload.

## Outcome

Pantheon review result: accepted for follow-up handoff.

The Incident Detail screen is implemented against the published PKT-002 contract and example payload. The single allowed BFF endpoint (`GET /api/v1/operator/incident-response/{incident_id}`) is consumed through the existing BFF client. All acceptance criteria are met.

> **BFF-gap note:** A `bff-gap` handoff was filed earlier (`.coordination/requests/PKT-002-incident-detail-bff-gap.yaml`) because the BFF returned a divergent response shape — eleven structural mismatches were identified including `data.runtime_binding` instead of `data.affected_bindings[]`, missing `allowedActions`, missing `meta.surfaces` contract keys, severity enum mismatch (`high`/`medium` vs `sev1`/`sev2`/`sev3`), and field name mismatch (`created_at` vs `opened_at`). All gaps were resolved by BP5-SVC-011 (incident and evidence services) and BP5-SVC-015 (BFF snapshot and default fallback removal). Implementation proceeded against the corrected BFF response shape.

## Verified Against Pantheon

- `GET /api/v1/operator/incident-response/{incident_id}` is consumed through the shared BFF client. No raw `fetch` or `axios` calls added in component files.
- The **Incident summary panel** renders all required fields from `data.incident`: `incident_id`, `title`, `severity` (`sev1`/`sev2`/`sev3`), `status`, `artifact_id`, `artifact_version`, `runtime_id`, `trace_id`, `opened_at`. Fields are not sourced from local state or mock data.
- The **Affected bindings panel** gates on `meta.surfaces.affected_bindings`:
  - `ok` + non-empty list: renders binding rows with `binding_id`, `persona_id`, `capital_pool_id`, `stage`, `binding_status`.
  - `ok` + empty list: renders "No affected bindings recorded". Does not treat empty as an error.
  - `degraded`: renders all available binding records followed by "Affected bindings data is partially unavailable — [meta.degradation.affected_bindings_reason]". Does not collapse degraded read into the empty-success copy.
- The **Kill switch status panel** gates on `meta.surfaces.kill_switch`:
  - `ok`: renders `status`, `last_triggered_at` (nullable), `last_confirmed_at`, `active_commands[]`.
  - `degraded`: renders the last known kill switch state with a staleness note showing `last_confirmed_at`. A non-dismissable degradation banner is shown.
  - `unavailable`: renders "Kill switch status unavailable". Does not assume any kill switch state.
- The **Action entry strip** derives all CTA visibility from `allowedActions` exclusively — `canPause`, `canRiskOff`, `canLiquidateAll`, `canHardRollback`, `canIssueSafeMode`, `canOpenActionDrawer`. No local eligibility logic is present.
- The **Open Action Drawer** CTA is disabled when `allowedActions.canOpenActionDrawer = false`.
- The **Degradation banner** is non-dismissable and renders whenever any `meta.surfaces` entry is not `ok`. It names the specific degraded surface and disables the relevant CTAs.
- The **Staleness banner** is non-dismissable and renders when `meta.staleness` is present on the response.
- 404 on `{incident_id}` renders "Incident not found" with the ID and a back action.
- Any absent `meta.surfaces` key emits a `bff-gap` alert state — no silent mock fallback.
- Loading, empty, degraded, and error states are explicit and visually distinct with no mock fallback.
- The UI does not re-fetch individual surfaces (incidents, kill switch, bindings) separately — the composed view is the only fetch.

## Notes

- `meta.surfaces` values are delivered as `{ "status": "ok" }` objects in the example payload. The implementation reads `meta.surfaces.incident.status` (not `meta.surfaces.incident`) to match the actual payload shape. This is consistent with the example payload in `docs/examples/PKT-002-incident-detail.json`.
- The Open Action Drawer CTA links to the `PKT-002-incident-action-drawer` surface. The reusable `IncidentActionDrawer` component is already present from the earlier drawer delivery cycle.
- All BFF-gap fields cited in the prior `bff-gap` handoff are resolved in the current BFF response shape. No new contract shape gaps were found in this implementation pass.

## Pantheon Follow-up

- No Pantheon API gap is requested in this cycle.
- The next Pantheon-owned step is to wire the Incident Detail screen into the Operator Console routing surface when the workbench shell is finalized.
