# EXEC-REBASE-TW04-001 BFF and Frontend Handoff Packet

**Sidecar kind:** `bff_handoff_packet`  
**Parent task:** `EXEC-REBASE-TW04-001` - Refresh TW-04 teaching replay frontend handoff and coordination bundle  
**Parent owner:** `Claude`  
**Parent reviewer:** `Codex`  
**Sidecar owner:** `Codex2`  
**Sidecar reviewer:** `Claude`  
**Date:** `2026-04-20`  
**Mutates canonical:** `no`

> Support artifact only. This packet does not change canonical truth, runtime
> behavior, or the live coordination bundle. It consolidates the repo's current
> TW-04 replay truth and the remaining handoff gaps so the parent owner can
> absorb the right materials into the main lane.

---

## 1. Executive Summary

`EXEC-REBASE-TW04-001` is blocked on handoff completeness, not on missing TW-04
replay contract intent.

What is already present in the repo:

- TW-04 BFF contract: `docs/bff/TW-04-teaching-replay.md`
- TW-04 screen spec: `docs/screens/TW-04-teaching-replay.md`
- TW-04 example payload: `docs/examples/TW-04-teaching-replay.json`
- TW-04 coordination prompt: `.coordination/responses/TW-04-teaching-replay-lovable-prompt.md`
- TW-04 coordination task: `.coordination/responses/TW-04-teaching-replay-lovable-ui-task.yaml`

What is still missing or inconsistent:

- no `docs/pantheon-handoffs/TW-04-teaching-replay/FRONTEND_CHANGE_SPEC.md`
- no `.coordination/requests/TW-04-teaching-replay-bff-gap.example.yaml`
- no `.coordination/requests/TW-04-teaching-replay-ui-done.example.yaml`
- `screen_id` drift between screen spec / prompt and lovable task
- downstream packet-family / backlog narrative still describes TW-04 as
  `pending-bff`

The parent task therefore needs a canonical handoff refresh, while this sidecar
provides the reviewer-ready support packet for that absorption.

## 2. Current Repo Truth Snapshot

| Area | Current truth | Notes for handoff |
|---|---|---|
| Feature identity | `TW-04-teaching-replay` | Stable across BFF/screen/example/prompt/ui-task |
| Screen slug | `teaching-replay` | Stable across prompt and ui-task |
| Screen ID | `screen-teaching-replay` in screen spec/prompt; `screen-trainer-teaching-replay` in ui-task | Must be normalized before frontend dispatch |
| Read routes | `GET /api/v1/trainer/replay`, `GET /api/v1/trainer/replay/{session_id}` | Published in BFF contract and prompt/ui-task |
| Write routes | `POST /api/v1/trainer/sessions/{session_id}/commit`, `POST /api/v1/trainer/sessions/{session_id}/discard` | Published in BFF contract and screen spec readiness gate, but omitted from lovable allowed-endpoints list because frontend is not expected to own raw command transport design here |
| Handoff folder | missing | Parent needs to publish canonical frontend spec |
| BFF-gap template | missing | Prompt and ui-task already point to the missing path |
| UI-done template | missing | Prompt and ui-task already point to the missing path |

## 3. BFF Query and Command Matrix

This is the minimum truthful matrix the frontend handoff must preserve.

| Surface | Method and path | Backend-owned purpose | Frontend rule |
|---|---|---|---|
| Replay list | `GET /api/v1/trainer/replay` | list completed / replayable sessions for one `persona_id` | require persona filter; do not substitute teaching-history routes |
| Replay detail | `GET /api/v1/trainer/replay/{session_id}` | provide ordered replay timeline, artifact rail, evidence links, and decision authority | render backend order and backend-owned `allowedActions` only |
| Commit decision | `POST /api/v1/trainer/sessions/{session_id}/commit` | record commit against replayable candidate snapshot | send `expected_candidate_snapshot_at`; do not infer success locally |
| Discard decision | `POST /api/v1/trainer/sessions/{session_id}/discard` | record discard against replayable candidate snapshot | send `expected_candidate_snapshot_at`; do not infer success locally |

Frontend critical fields that must stay BFF-owned in the eventual canonical
handoff:

- replay list row: `session_id`, `objective`, `status`, `started_at`,
  `ended_at`, `event_count`, `latest_event_type`, `latest_outcome_signal`,
  `replay_resolution.state`, `links.replay_detail`
- replay detail header: `session_id`, `persona_id`, `objective`, `status`,
  `meta.snapshot_at`, `replay_resolution.state`
- detail body: `events[]`, `artifacts`, `event_summary`, `evidence_links[]`
- authority: `allowedActions.canCommit`, `allowedActions.canDiscard`
- degradation: `meta.surfaces.trainer_replay`

## 4. Operator Journey for TW-04

The eventual frontend handoff should support this operator path without
inventing browser-owned truth:

1. Open `/trainer/replay` scoped to one persona and browse completed sessions.
2. Select a replay row and open `/trainer/replay/:session_id`.
3. Read the header, artifact rail, and ordered replay timeline exactly in
   backend order.
4. Open evidence drawer links only when the BFF provides an evidence object.
5. Inspect `replay_resolution.state`.
6. If and only if `allowedActions.canCommit` or `allowedActions.canDiscard` is
   true, submit the corresponding decision with
   `expected_candidate_snapshot_at`.
7. Wait for backend-recorded replay truth; do not optimistically mutate
   timeline/event history in the client.

## 5. Parent Absorption Checklist

The main lane can absorb this packet by publishing the missing canonical handoff
bundle with the following content.

### 5.1 Required Files

| File to create/update | Purpose |
|---|---|
| `docs/pantheon-handoffs/TW-04-teaching-replay/FRONTEND_CHANGE_SPEC.md` | production frontend implementation contract for TW-04 |
| `.coordination/requests/TW-04-teaching-replay-bff-gap.example.yaml` | stop-work template when required fields diverge |
| `.coordination/requests/TW-04-teaching-replay-ui-done.example.yaml` | completion handoff template for frontend return |
| `.coordination/responses/TW-04-teaching-replay-lovable-ui-task.yaml` | normalize `screen_id` and point at real bundle |

### 5.2 Required Truth Sync

| Truth source | Needed sync |
|---|---|
| `docs/screens/TW-04-teaching-replay.md` vs lovable ui-task | choose one `screen_id` and keep it identical everywhere |
| `WORKBENCH_DELIVERY_BACKLOG.md` | stop describing TW-04 as `BFF implementation pending` once the parent confirms live route truth |
| `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md` | update TW-04 row and replay contract notes away from `pending-bff` if the parent lane promotes it |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | update TW-04 status text so frontend dispatch truth matches the new bundle |

## 6. Proposed FRONTEND_CHANGE_SPEC Skeleton

The missing `FRONTEND_CHANGE_SPEC.md` should minimally include these sections:

1. Overview
   TW-04 is the production replay surface for completed trainer sessions and
   must use only the replay route family plus backend-owned decision authority.
2. Allowed APIs
   `GET /api/v1/trainer/replay`, `GET /api/v1/trainer/replay/{session_id}`,
   plus the commit/discard command surfaces routed through the existing BFF
   client action layer.
3. Required UI modules
   replay list, replay detail header, artifact rail, ordered event timeline,
   evidence drawer, commit/discard CTA rail.
4. State rules
   separate session lifecycle from `replay_resolution.state`; CTA visibility
   comes only from `allowedActions`; degradation comes only from
   `meta.surfaces.trainer_replay`.
5. Failure rules
   if any required field is absent, emit the TW-04 `bff-gap` handoff and stop;
   do not mock events, evidence links, or decision state.
6. Completion rules
   on UI completion, emit the TW-04 `ui-done` handoff and required feedback
   docs.

## 7. Proposed Example Template Fields

The support packet cannot create the canonical templates, but the parent owner
can lift these fields directly.

### 7.1 `TW-04-teaching-replay-bff-gap.example.yaml`

Recommended fields:

- `feature_id: TW-04-teaching-replay`
- `type: bff-gap`
- `screen: teaching-replay`
- `screen_id:` normalized canonical value
- `bff_contract_path: docs/bff/TW-04-teaching-replay.md`
- `frontend_change_spec: docs/pantheon-handoffs/TW-04-teaching-replay/FRONTEND_CHANGE_SPEC.md`
- `missing_or_divergent_fields: []`
- `observed_endpoint:`
- `notes:`

### 7.2 `TW-04-teaching-replay-ui-done.example.yaml`

Recommended fields:

- `feature_id: TW-04-teaching-replay`
- `type: ui-done`
- `screen: teaching-replay`
- `screen_id:` normalized canonical value
- `source_branch:`
- `source_commit:`
- `implemented_paths: []`
- `used_endpoints:`
- `feedback_paths:`
  - `docs/pantheon-feedback/TW-04-teaching-replay/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/TW-04-teaching-replay/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/TW-04-teaching-replay/UI_DECISIONS.md`
  - `docs/pantheon-feedback/TW-04-teaching-replay/QA_STATUS.md`

## 8. Reviewer Focus

For `Claude` reviewing this sidecar:

1. Confirm the packet stays support-only and does not mutate canonical truth.
2. Confirm the gap list matches the current repo: missing handoff dir and both
   request templates, plus `screen_id` drift.
3. Confirm the operator journey and BFF matrix align with the published TW-04
   BFF contract and screen spec.
4. Use this packet as an absorption guide for the parent task rather than as a
   substitute for the missing canonical handoff bundle.

## 9. References

- `docs/reviews/2026-04-20-exec-rebase-tw04-001-codex-review.md`
- `.coordination/responses/TW-04-teaching-replay-lovable-prompt.md`
- `.coordination/responses/TW-04-teaching-replay-lovable-ui-task.yaml`
- `docs/bff/TW-04-teaching-replay.md`
- `docs/screens/TW-04-teaching-replay.md`
- `docs/examples/TW-04-teaching-replay.json`
- `support/sidecars/AUTO-IMPL-TW04-001/AUTO-IMPL-TW04-001-SIDECAR-BFF-HANDOFF.md`

