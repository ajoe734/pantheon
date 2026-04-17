# PKT-002 Incident Detail Review Packet

## Date

2026-04-17

## Reviewer

Codex

## Findings

### 1. High: the Pantheon-owned acceptance slice currently fails on the published composed read route

- I ran:
  `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q -k 'composed_incident_response or in05_kill_switch_status or in05_kill_switch_unavailable_disables_actions'`
- `test_in05_kill_switch_status` and
  `test_in05_kill_switch_unavailable_disables_actions` passed.
- `test_composed_incident_response` failed because
  `GET /api/v1/operator/incident-response/inc-20260410-001` returned `404 Not Found`.
- The route is present in [services/control-plane/bff/main.py](/home/edna/code/pantheon/services/control-plane/bff/main.py:1797), so this is not a contract typo. On the current working tree acceptance path, the composed incident-response route does not complete successfully.
- Impact: Pantheon cannot claim that the next integration or acceptance step passed. This requires a runtime-layer follow-up rather than a UI-only closeout.

### 2. High: the returned front handoff is still not replay-clean

- The mirrored Pantheon request advertises:
  `source_commit: c08acb3ea59f4c56ced578820aa6a5129a309de1`
- `git -C ../front-ai-trading-system show c08acb3ea59f4c56ced578820aa6a5129a309de1:.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
  fails because that payload path is absent from the advertised commit.
- Current front HEAD
  `60f366e0a745ce3bb10e913e53b332d6557e23f1` contains:
  - `.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
  - `docs/pantheon-feedback/PKT-002-incident-detail/`
- Current front HEAD still does **not** contain:
  - `.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml`
- Impact: the front-owned publication pair is incomplete, so the GitHub-visible transport tuple is still not replayable even though the UI files exist.

### 3. Medium: the current Incident Detail implementation still misses two required read-side UI details from the published screen spec

- The current sibling working tree never renders `data.incident.opened_at`, even though the published Incident summary panel requires `incident_id`, `title`, `severity`, `status`, `artifact_id`, `artifact_version`, `runtime_id`, `trace_id`, and `opened_at`.
- The current sibling working tree still renders the action-authority strip as badges only and does not provide the short rationale text required for each action by the PKT-002 screen spec.
- Impact: the UI is closer to the contract than the tracked front commit, but the current working tree still cannot be treated as a full acceptance pass. The next front-owned publication must include these missing screen-spec details in addition to the replay-clean transport fixes.

### 4. Medium: the non-blocking HardRollback target context follow-up remains open

- The reviewed UI still keeps `HardRollback` disabled when a rollback target artifact ID is unavailable, which is the correct behavior.
- Pantheon still has not published a canonical `target_artifact_id` source from Incident Detail context.
- Impact: this does not block read-side acceptance, but the drawer must continue to keep `HardRollback` explicitly disabled in this host context until Pantheon publishes that field or documents another canonical source.

## Reviewed Artifacts

- Pantheon request mirror:
  - `.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
- Sibling front publication paths:
  - `../front-ai-trading-system/.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-detail/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-detail/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-detail/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-detail/QA_STATUS.md`
- Contract sources:
  - `docs/bff/PKT-002-incident-detail.md`
  - `docs/screens/PKT-002-incident-detail.md`
  - `docs/examples/PKT-002-incident-detail.json`
  - `docs/pantheon-handoffs/PKT-002-incident-detail/FRONTEND_CHANGE_SPEC.md`
- Sibling front implementation:
  - `../front-ai-trading-system/src/pages/operator/IncidentDetail.tsx`
  - `../front-ai-trading-system/src/components/operator/IncidentActionDrawer.tsx`
  - `../front-ai-trading-system/src/pages/operator/types.ts`

## Verified Positives

- The returned UI remains statically aligned on the published composed read contract:
  - `IncidentDetail.tsx` reads only through `operatorApi.getIncidentResponse()`
  - degraded and unavailable states remain explicit
  - `data.kill_switch.active_commands[]` is rendered
  - `meta.staleness` is rendered
  - the integration boundary remains `/incidents/:incidentId` ->
    `/incident-action-drawer`
- Targeted static validation against the sibling working tree passed:
  - `npx eslint src/pages/operator/IncidentDetail.tsx src/components/operator/IncidentActionDrawer.tsx src/pages/operator/types.ts src/lib/bffClient.ts src/App.tsx`
  - `npm run build`
- The targeted Pantheon acceptance slice confirmed the kill-switch read surface still behaves as expected under `ok` and `unavailable` states.

## Decision

`PKT-002-incident-detail` is **blocked**.

The current sibling UI implementation is statically aligned, but Pantheon cannot
complete the current acceptance step on this checkout because the published
composed read route fails under local smoke verification. The front-owned
handoff is also still not replay-clean because the advertised `source_commit`
does not contain the published payload path and the canonical
`frontend-feedback` request remains unpublished. Even the current front working
tree still needs to render `opened_at` and per-action rationale copy before the
screen fully matches the published PKT-002 spec.

## Required Follow-up

1. Restore the Pantheon runtime acceptance path for
   `GET /api/v1/operator/incident-response/{incident_id}` without changing the
   published PKT-002 contract or inventing alternate endpoints.
2. Re-run the targeted smoke slice after that runtime follow-up:
   `python3 -m pytest services/control-plane/bff/smoke_test_incident.py -q -k 'composed_incident_response or in05_kill_switch_status or in05_kill_switch_unavailable_disables_actions'`
3. Publish the front-owned canonical request pair from a Git-visible commit that
   actually contains both payload paths:
   - `.coordination/requests/PKT-002-incident-detail-ui-done.yaml`
   - `.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml`
4. Add the missing `opened_at` field to the Incident summary panel and the
   required short rationale copy for each action in the action-authority strip.
5. Keep the current `/incidents/:incidentId` ->
   `/incident-action-drawer` boundary truthful in the republished artifacts.
6. Publish or document the canonical `target_artifact_id` source for
   `HardRollback`, or keep that command explicitly disabled from Incident Detail.

## 2026-04-17 Addendum: truthful SSE republish verified, but closeout gate still not met

1. High: the republished feedback bundle is now truthful about the SSE boundary, but the canonical front-end `frontend-feedback` request is still absent, so the coordination loop remains at `ui_done_received`.

- Verified positive: front repo commit `dea4186` now updates all four PKT-002 feedback files to explicitly document the three SSE streams opened by `IncidentDetail.tsx` at `source_commit c08acb3`.
- Remaining blocker: `git -C ../front-ai-trading-system show dea4186:.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml` still fails because that canonical request path is absent from the published transport commit.
- The sibling front working tree also lacks the file entirely: `../front-ai-trading-system/.coordination/requests/PKT-002-incident-detail-frontend-feedback.yaml` is missing.
- Per `.coordination/README`, `frontend-feedback` requests come from the front-end lane and are the machine-readable signal for closed-loop review visibility. The Pantheon-side mirror at `.coordination/responses/PKT-002-incident-detail-frontend-feedback.yaml` is useful review output, but it does not satisfy the front-lane request requirement that drives coordination stage tracking.
- Impact: `ai-status.json` still shows `LUV-CLOSE-001` at `coordination_stage: ui_done_received` with `coordination_paths.frontend_feedback: null`, so the task still fails its own acceptance criterion "coordination 狀態與 task board 敘事一致，不再只停在 ui_done_received".

## 2026-04-17 Reviewer Decision After `dea4186`

Do not move `LUV-CLOSE-001` to `review_approved` yet.

The SSE omission identified in the previous addendum is fixed, and the follow-up bundle is now materially truthful. However, the closeout is still incomplete because the canonical front-end `frontend-feedback` request has not been published, so the machine-readable coordination state has not advanced beyond `ui_done_received`. The owner should republish the closeout tuple so the front repo contains the canonical request path, then re-run the status sync and return the task for review.
