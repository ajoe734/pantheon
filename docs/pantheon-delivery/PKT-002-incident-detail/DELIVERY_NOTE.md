# PKT-002 Incident Detail Backend Delivery Note

## Status

`followup-required`

## Summary

Pantheon re-reviewed the returned `ui-done` handoff
`.coordination/requests/PKT-002-incident-detail-ui-done.yaml` against the
published PKT-002 read contract, the example payload, the mirrored feedback
bundle, and the sibling `front-ai-trading-system` working tree.

This pass confirms that the current front working tree closes the earlier
functional UI gaps:

- the detail screen is routed at `/incidents/:incidentId`
- the **Open Action Drawer** CTA now navigates to the real
  `/incident-action-drawer` host route
- the kill-switch surface renders `data.kill_switch.active_commands[]`
- the screen renders the required `meta.staleness` copy

Pantheon mirrored the refreshed `ui-done` packet and feedback bundle so the
review record now matches the current sibling working tree.

The loop still cannot close as `loop-complete` because the front-owned handoff
is not replay-clean:

- the returned payload still advertises `source_commit:
  faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7`
- that commit does not contain
  `.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
- the refreshed request and feedback bundle still exist only in the sibling
  working tree at front HEAD `87340e96ce4247ccc177e8dff7579e804991b895`

One non-blocking Pantheon follow-up also remains open: the embedded Incident
Action Drawer cannot safely issue `HardRollback` from Incident Detail until the
packet publishes a canonical `target_artifact_id` source for that command.

## Verified UI Alignment

- `GET /api/v1/operator/incident-response/{incident_id}` is still the only read
  endpoint consumed by the page, through `operatorApi.getIncidentResponse()`
- `src/pages/operator/IncidentDetail.tsx` adds no raw `fetch()` calls
- the current route boundary is `/incidents/:incidentId` ->
  `/incident-action-drawer`, not the earlier `/operator/incident/:incident_id`
  wording
- the screen keeps explicit loading, 404, contract-gap, degraded, unavailable,
  and empty-success states
- degradation and staleness behavior is explicitly rendered from `meta`
- `allowedActions` remains the only source of CTA authority
- the kill-switch panel renders `active_commands[]` when present
- the embedded drawer uses the existing PKT-002 drawer contract and keeps
  `HardRollback` disabled without a published rollback target

## Coordination Outcome

- Pantheon contract: unchanged
- Published read endpoint set: unchanged
- Loop status: `followup-required`
- Front publication follow-up: required
- Pantheon API gap: one non-blocking write-contract follow-up for
  `HardRollback` target context

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
  - `git -C ../front-ai-trading-system show faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7:.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
  - Result: payload path is absent from the advertised `source_commit`
- Re-ran targeted front-end validation in the sibling repo:
  - `npx eslint src/pages/operator/IncidentDetail.tsx src/components/operator/IncidentActionDrawer.tsx src/pages/operator/types.ts src/lib/bffClient.ts src/App.tsx`
  - `npm run build`
  - Result: both passed; Vite emitted a non-blocking chunk-size warning only

## Not Completed

- Live browser QA against a running Pantheon BFF
- Live command execution QA against `POST /api/v1/operator/commands`
- A replay-clean front publication commit that contains the refreshed request
  and feedback bundle

## Next Required Follow-up

- Front repo must republish the refreshed `ui-done` request and feedback bundle
  from a Git-visible commit that actually contains those payload paths
- The republished artifacts must keep the current `/incidents/:incidentId` ->
  `/incident-action-drawer` integration boundary truthful
- Pantheon should publish or document the canonical `target_artifact_id` source
  for `HardRollback` from Incident Detail, or keep that command explicitly
  disabled in this host context
