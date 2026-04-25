# PKT-002 Incident Detail Backend Delivery Note

## Status

`approved`

## Summary

Pantheon re-reviewed the returned
`.coordination/requests/PKT-002-incident-detail-ui-done.yaml` and mirrored
`.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml`
artifacts against the canonical PKT-002 incident-detail contract, the
published feedback bundle, the sibling front implementation, and the local
Pantheon acceptance slice.

The current PKT-002 Incident Detail loop is now closeout-ready from the
Pantheon side:

1. the canonical composed read remains
   `GET /api/v1/operator/incident-response/{incident_id}`
2. the returned request pair is replay-clean on `origin/pkt-004-detail-fix`
   through transport commit
   `eb1a6cbb727a681db21ecd4b121348605fb8a4d3` and request-pair head
   `42dc4856b36a7c92f5c40cafd94bf8ef09665bbe`
3. the detail page now renders `opened_at`, truthful operator navigation, and
   backend-shaped action rationale copy
4. the live-update overlay stays on the approved PKT-005 SSE substrate; no new
   endpoint family or shadow state was introduced
5. the Pantheon-owned incident-detail smoke slice now passes locally
6. one non-blocking contract note remains: `HardRollback` still lacks a
   canonical `target_artifact_id` source when launched from Incident Detail,
   so that command must remain disabled from this host context until Pantheon
   publishes it

## Verified UI State

- Mirrored Pantheon request artifacts:
  - `.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
  - `.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml`
- GitHub-visible front transport:
  - `origin/pkt-004-detail-fix@eb1a6cbb727a681db21ecd4b121348605fb8a4d3`
- GitHub-visible request-pair head:
  - `origin/pkt-004-detail-fix@42dc4856b36a7c92f5c40cafd94bf8ef09665bbe`
- Screen route:
  - `/operator/incidents/:incidentId`
- Drawer route:
  - `/operator/incidents/:incidentId/action`
- Snapshot read:
  - `operatorApi.getIncidentResponse(...)`
  - `GET /api/v1/operator/incident-response/{incident_id}`
- Realtime boundary:
  - `/api/v1/runtime/{runtime_id}/events/stream`
  - `/api/v1/incidents/stream`
  - `/api/v1/kill-switch/updates`

## Verification Performed

- Reviewed the mirrored Pantheon request artifacts:
  - `.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
  - `.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml`
- Reviewed the canonical contract sources:
  - `docs/bff/PKT-002-incident-detail.md`
  - `docs/examples/PKT-002-incident-detail.json`
  - `docs/screens/PKT-002-incident-detail.md`
  - `docs/pantheon-handoffs/PKT-002-incident-detail/FRONTEND_CHANGE_SPEC.md`
  - `docs/screens/PKT-005-degradation-banner.md`
  - `docs/bff/PKT-005-sse-substrate.md`
- Reviewed the sibling front implementation:
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/lib/sseClient.ts`
  - `../front-ai-trading-system/src/lib/sseReconciler.ts`
  - `../front-ai-trading-system/src/pages/operator/IncidentDetail.tsx`
  - `../front-ai-trading-system/src/pages/operator/IncidentActionDrawerPage.tsx`
  - `../front-ai-trading-system/src/components/operator/IncidentActionDrawer.tsx`
- Ran front verification in the sibling repo:
  - `npm run build`
  - `npx eslint src/pages/operator/IncidentDetail.tsx src/pages/operator/IncidentActionDrawerPage.tsx src/pages/operator/PostIncidentReviewConsole.tsx src/pages/operator/DeploymentReviewConsole.tsx src/pages/operator/types.ts src/lib/sseClient.ts src/lib/sseReconciler.ts src/App.tsx`
- Ran the Pantheon-owned acceptance slice:
  - `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q -k 'in02_incident_detail or composed_incident_response or in05_kill_switch_status or in05_kill_switch_unavailable_disables_actions'`
  - Result: `5 passed, 15 deselected`

## Not Completed

- Live browser QA against a running Pantheon BFF
- Live command execution QA against `POST /api/v1/operator/commands`

## Next Step

Pantheon follow-up for the current PKT-002 Incident Detail UI cycle is
complete. No new endpoint, no shadow state, and no additional front-end
implementation pass is required for this packet. Future work should be limited
to non-blocking live QA and the separate HardRollback target-artifact contract
publication if Pantheon wants to enable that command from Incident Detail.
