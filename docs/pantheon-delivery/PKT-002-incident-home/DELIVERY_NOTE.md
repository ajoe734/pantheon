# PKT-002 Incident Home Backend Delivery Note

## Status

`loop-complete`

## Summary

Pantheon re-reviewed the returned
`.coordination/requests/PKT-002-incident-home-ui-done.yaml` and mirrored
`.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml`
artifacts against the canonical PKT-002 incident-home contract, the
published example payload, the refreshed sibling feedback bundle, the sibling
front implementation, and the Pantheon-owned incident-home acceptance slice.

The previous PKT-002 blockers are now closed:

- the screen still reads only `GET /api/v1/incidents` and
  `GET /api/v1/kill-switch/status`
- reviewed source commit `82b1ceb76a7de4f8e49b5f08c0b9fcb865f52bd9` refreshes
  the feedback bundle so its route narration and API-gap state are truthful
  again
- `IncidentHome.tsx` now routes row selection to
  `/operator/incidents/${incident_id}`, matching the mounted operator route
  family in `src/App.tsx`
- current publish commit `1a1a42eebda033a1fbda4696df5b81271f5eed9b`
  republishes the canonical PKT-002 request pair on
  `origin/pkt-004-detail-fix` without changing the reviewed UI slice
- targeted front verification and Pantheon's incident-home acceptance slice
  both pass again

Pantheon therefore closes the current Lovable loop for
`PKT-002-incident-home`.

## Verified Contract Alignment

- `GET /api/v1/incidents`
- `GET /api/v1/kill-switch/status`
- `IncidentHome.tsx` reads only through the shared `operatorApi` helpers and
  does not add raw component-level network calls.
- `src/lib/degradationBanner.ts` remains the shared path for split-read banner
  aggregation; Incident Home still merges only `incident_list` and
  `kill_switch` without inventing local shadow state.
- `API_GAP_REQUESTS.json` now reports `no_open_gaps`, and the front-owned
  feedback bundle truthfully narrates the mounted `/operator/incidents` route
  family.
- Incident row navigation now stays on the mounted
  `/operator/incidents/:incidentId` detail route.

## Replayable Transport

The sibling front repo now publishes the reviewed request pair through two
consecutive commits:

- `82b1ceb76a7de4f8e49b5f08c0b9fcb865f52bd9`
  - reviewed source commit that contains the refreshed PKT-002 request pair,
    feedback bundle, and integrated Incident Home UI files
- `1a1a42eebda033a1fbda4696df5b81271f5eed9b`
  - current republish commit on `origin/pkt-004-detail-fix` that republishes
    the canonical request pair and keeps both request bodies pinned to
    reviewed source commit `82b1ceb76a7de4f8e49b5f08c0b9fcb865f52bd9`

Pantheon verified that diffing the reviewed PKT-002 scope from source to
publish commit changes only:

- `.coordination/requests/PKT-002-incident-home-ui-done.yaml`
- `.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml`

## Verification Performed

- Reviewed the mirrored Pantheon request artifacts:
  - `.coordination/requests/PKT-002-incident-home-ui-done.yaml`
  - `.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml`
- Reviewed the canonical contract sources:
  - `docs/bff/PKT-002-incident-home.md`
  - `docs/examples/PKT-002-incident-home.json`
  - `docs/screens/PKT-002-incident-home.md`
  - `docs/pantheon-handoffs/PKT-002-incident-home/FRONTEND_CHANGE_SPEC.md`
  - `docs/bff/PKT-005-degradation-banner.md`
- Reviewed the sibling front implementation at source commit
  `82b1ceb76a7de4f8e49b5f08c0b9fcb865f52bd9`:
  - `src/lib/bffClient.ts`
  - `src/lib/degradationBanner.ts`
  - `src/pages/operator/IncidentHome.tsx`
  - `src/pages/operator/types.ts`
  - `src/App.tsx`
  - `docs/pantheon-feedback/PKT-002-incident-home/`
- Verified the replay-clean publish commit:
  - `origin/pkt-004-detail-fix@1a1a42eebda033a1fbda4696df5b81271f5eed9b`
- Ran sibling front verification:
  - `./node_modules/.bin/tsc --noEmit --pretty false`
  - `npx eslint src/pages/operator/IncidentHome.tsx src/pages/operator/types.ts src/lib/bffClient.ts src/lib/degradationBanner.ts src/App.tsx src/components/AppSidebar.tsx`
  - `npx --yes tsx src/components/GlobalDegradationBanner.test.tsx`
  - `npm run build`
  - Result: all passed; the build emitted only the existing non-blocking Vite
    chunk-size warning
- Ran the Pantheon-owned acceptance slice:
  - `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q -k 'test_in01_incident_list or test_in01_incident_list_filtered or test_in01_resolved_incident_list_includes_resolved_at or test_in05_kill_switch_status or test_in05_kill_switch_unavailable_disables_actions'`
  - Result: `5 passed, 15 deselected`
- Confirmed the active local runtime still advertises the incident-home route
  family:
  - `curl -sSf http://127.0.0.1:18001/openapi.json | rg '\"/api/v1/incidents\"|\"/api/v1/kill-switch/status\"'`
  - Result: both published PKT-002 read routes are present

## Residual Risk

- No live browser QA against a deployed Pantheon environment was performed in
  this closure step.
- The front production build still reports the existing Vite chunk-size
  warning. It does not block PKT-002 contract acceptance.
