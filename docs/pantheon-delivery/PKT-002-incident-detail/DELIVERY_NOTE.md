# PKT-002 Incident Detail Backend Delivery Note

## Status

`blocked`

## Summary

Pantheon re-reviewed the returned `ui-done` handoff
`.coordination/requests/PKT-002-incident-detail-ui-done.yaml` against the
published PKT-002 read contract, the example payload, the mirrored feedback
bundle, the tracked front repo state at `60f366e0a745ce3bb10e913e53b332d6557e23f1`,
and the sibling `front-ai-trading-system` working tree.

Two blockers remain:

1. Pantheon runtime acceptance is still red. Running

   `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q -k 'composed_incident_response or in05_kill_switch_status or in05_kill_switch_unavailable_disables_actions'`

   still fails because `GET /api/v1/operator/incident-response/inc-20260410-001`
   returns `404 Not Found` from `TestClient(main.app)`.

2. The front handoff is still not replay-clean. The tracked `ui-done` packet at
   front HEAD advertises `source_commit:
   c08acb3ea59f4c56ced578820aa6a5129a309de1`, but that commit does not contain
   `.coordination/requests/PKT-002-incident-detail-ui-done.yaml` or the mirrored
   PKT-002 feedback bundle. Front HEAD still does **not** publish the canonical
   `.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml`
   request. The current sibling working tree also keeps the latest
   `IncidentDetail.tsx` fixes only as an uncommitted diff: CTA navigation into
   `/incident-action-drawer`, kill-switch `active_commands[]` rendering, and
   the `meta.staleness` alert are not in the tracked commit. Even that
   working-tree copy still omits `opened_at` from the Incident summary panel,
   renders the action-authority strip as badges only, and does not provide the
   short rationale text required for each action.

One non-blocking Pantheon follow-up remains unchanged: the embedded Incident
Action Drawer cannot safely issue `HardRollback` from Incident Detail until the
packet publishes a canonical `target_artifact_id` source for that command.

## Verified UI State

- `GET /api/v1/operator/incident-response/{incident_id}` is still the only read
  endpoint consumed by the page, through `operatorApi.getIncidentResponse()`
- `src/pages/operator/IncidentDetail.tsx` adds no raw `fetch()` calls
- the tracked front repo still routes the screen at `/incidents/:incidentId`
  and exposes `/incident-action-drawer` as the current host boundary
- the detail page still keeps explicit loading, 404, contract-gap, degraded,
  unavailable, and empty-success states
- `allowedActions` remains the only backend authority source; the SSE substrate
  only disables CTAs when the live kill-switch stream reports activation
- the drawer continues to keep `HardRollback` disabled without a published
  rollback target artifact ID
- the current sibling working tree adds the missing CTA wiring,
  `active_commands[]` rendering, and `meta.staleness` alert, but those fixes
  are not committed
- the current sibling working tree still omits `opened_at` from the Incident
  summary panel required by the PKT-002 screen spec
- the action-authority strip still omits per-action rationale copy required by
  the PKT-002 screen spec

## Coordination Outcome

- Pantheon contract: unchanged
- Published read endpoint set: unchanged
- Loop status: `blocked`
- Pantheon runtime follow-up: required
- Front publication follow-up: required
- Pantheon API gap: current acceptance path for the composed read route fails
  locally; `HardRollback` target context also remains unpublished

## Verification Performed

- Reviewed the mirrored Pantheon-side request artifact:
  - `.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
- Reviewed the mirrored feedback bundle:
  - `docs/pantheon-feedback/PKT-002-incident-detail/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/PKT-002-incident-detail/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/PKT-002-incident-detail/UI_DECISIONS.md`
  - `docs/pantheon-feedback/PKT-002-incident-detail/QA_STATUS.md`
- Reviewed the sibling front working tree files:
  - `src/App.tsx`
  - `src/pages/operator/IncidentDetail.tsx`
  - `src/components/operator/IncidentActionDrawer.tsx`
  - `src/pages/operator/types.ts`
  - `src/lib/bffClient.ts`
- Verified replay failure directly:
  - `git -C ../front-ai-trading-system show c08acb3ea59f4c56ced578820aa6a5129a309de1:.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
  - Result: payload path is absent from the advertised `source_commit`
- Verified the tracked front handoff still omits the canonical feedback request:
  - `git -C ../front-ai-trading-system show HEAD:.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml`
  - Result: path does not exist at front HEAD
- Verified current front HEAD `60f366e0a745ce3bb10e913e53b332d6557e23f1`
  contains the `ui-done` request and feedback bundle, but still does not
  publish `.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml`
- Ran the Pantheon-owned acceptance slice:
  - `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q -k 'composed_incident_response or in05_kill_switch_status or in05_kill_switch_unavailable_disables_actions'`
  - Result: 2 passed, 1 failed
  - Failure: `test_composed_incident_response` received `404 Not Found` from
    `GET /api/v1/operator/incident-response/inc-20260410-001`
- Re-ran targeted front-end validation in the sibling repo:
  - `npx eslint src/pages/operator/IncidentDetail.tsx src/components/operator/IncidentActionDrawer.tsx src/pages/operator/types.ts src/lib/bffClient.ts src/App.tsx`
  - `npm run build`
  - Result: both passed; Vite emitted a non-blocking chunk-size warning only

## Not Completed

- A runtime-clean Pantheon acceptance run for the composed incident-response
  route
- Live browser QA against a running Pantheon BFF
- Live command execution QA against `POST /api/v1/operator/commands`
- A replay-clean front publication commit that contains the canonical request
  pair

## Next Required Follow-up

- Restore the Pantheon runtime acceptance path for
  `GET /api/v1/operator/incident-response/{incident_id}` without changing the
  published contract or inventing alternate endpoints
- Re-run the targeted smoke slice after that runtime follow-up
- Front repo must publish the canonical `frontend-feedback` + `ui-done` request
  pair from a Git-visible commit that actually contains both payload paths
- Front repo must republish the final `IncidentDetail.tsx` implementation from
  that same Git-visible commit, including CTA navigation into
  `/incident-action-drawer`, `data.kill_switch.active_commands[]` rendering,
  and the `meta.staleness` alert
- That republished detail screen must also render `data.incident.opened_at` in
  the Incident summary panel
- That republished detail screen must also add the required short rationale
  copy for each action in the action-authority strip
- The republished artifacts must keep the current `/incidents/:incidentId` ->
  `/incident-action-drawer` integration boundary truthful
- Pantheon should publish or document the canonical `target_artifact_id` source
  for `HardRollback` from Incident Detail, or keep that command explicitly
  disabled in this host context
