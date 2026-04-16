# PKT-002 Incident Home — QA Status

Feature ID: `PKT-002-incident-home`
Screen: `incident-home`
Workbench: `operator-console`
QA phase: **pending-lovable-implementation**

## Status

The Pantheon coordination loop (gap → delivery → dispatch) is complete. QA cannot begin until
Lovable implements the screen and publishes a `ui-done` handoff.

## Pre-QA Checklist (Pantheon side)

| Item | Status |
|---|---|
| BFF gap documented | ✅ `.coordination/requests/PKT-002-incident-home-bff-gap.yaml` |
| Backend delivery confirmed | ✅ commit `2782e502` |
| Contract-ready published | ✅ `.coordination/responses/PKT-002-incident-home-contract-ready.yaml` |
| Lovable UI task dispatched | ✅ `.coordination/responses/PKT-002-incident-home-lovable-ui-task.yaml` |
| Example payload available | ✅ `docs/examples/PKT-002-incident-home.json` |
| Screen spec available | ✅ `docs/screens/PKT-002-incident-home.md` |
| BFF spec available | ✅ `docs/bff/PKT-002-incident-home.md` |
| Frontend change spec available | ✅ `docs/pantheon-handoffs/PKT-002-incident-home/FRONTEND_CHANGE_SPEC.md` |

## QA Entry Criteria (when Lovable ui-done arrives)

- `.coordination/requests/PKT-002-incident-home-ui-done.yaml` published by Lovable
- Incident list panel renders from `GET /api/v1/incidents`
- Kill switch badge renders from `GET /api/v1/kill-switch/status`
- Degradation banner appears when any `meta.surfaces` entry is degraded or unavailable
- Non-dismissable warning banner appears when `meta.surfaces.kill_switch` is degraded or unavailable
- No raw fetch calls in component files
- No demo provider imports
- No invented fields

## Dependency Notes

- `BP5-SVC-015` (Remove BFF snapshot and default fallback) is `todo` and may affect QA once
  the screen is live. Monitor for snapshot field availability during QA.
