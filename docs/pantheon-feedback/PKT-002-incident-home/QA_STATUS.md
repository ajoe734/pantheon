# PKT-002 Incident Home — QA Status

Feature ID: `PKT-002-incident-home`
Screen: `incident-home`
Workbench: `operator-console`
QA phase: **static-verification-complete**

## Status

Static verification complete.

## Checks completed

- `npx eslint src/pages/operator/IncidentHome.tsx src/pages/operator/types.ts src/lib/bffClient.ts src/App.tsx src/components/AppSidebar.tsx`
- `npm run build`
- Contract fields were cross-checked against `docs/bff/PKT-002-incident-home.md`, `docs/screens/PKT-002-incident-home.md`, and `docs/examples/PKT-002-incident-home.json`.
- Screen logic was checked for the required states: loading, contract gap, incident-list unavailable, kill-switch degraded, kill-switch unavailable, and pagination via `page_info.next_page_token`.

## Not completed in this cycle

- Live browser QA against a running `GET /api/v1/incidents` endpoint.
- Live browser QA against a running `GET /api/v1/kill-switch/status` endpoint.
- Runtime verification after any future snapshot-policy cleanup changes land.

## Risk note

The remaining risk is runtime verification only. No open contract-shape gap remains in the reviewed
Incident Home code path.
