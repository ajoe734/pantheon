# TW-04 Teaching Replay

## Classification

- Workbench: Trainer Workbench
- Screen ID: `screen-teaching-replay`
- Feature ID: `TW-04-teaching-replay`
- Packet status: **route-live** — replay list/detail routes and commit/discard write routes are live, and the frontend handoff bundle is published for production UI activation
- Task: `TW-04-REPLAY-001`

## Contract Note

The Trainer Workbench now has a route-live replay slice for browsing finished trainer sessions, replaying ordered teaching history, and recording commit/discard decisions against a completed candidate state. Pantheon has confirmed the replay list/detail routes and the commit/discard write routes are live and returning the published field shape, so production UI may proceed against this route family.

The UI must not substitute Persona teaching history, reconstruct evidence navigation from raw refs, or infer commit/discard authority locally. All replay truth comes from the Pantheon BFF replay surface. If the live payload diverges from the synced contract, emit the canonical TW-04 `bff-gap` handoff instead of inventing a fallback.

## User Goal

Let an operator browse replayable trainer sessions for one persona, inspect the complete ordered teaching arc for a finished session, open evidence links from backend-resolved event refs, and explicitly commit or discard the reviewed candidate state without inventing event order, artifact lineage, or CTA authority in the browser.

## Routes

Primary routes:

- `/trainer/replay`
- `/trainer/replay/:session_id`

## Readiness Gate

Pantheon has already confirmed the following production gate for TW-04:

1. `GET /api/v1/trainer/replay` is live with the published replay list row shape, `replay_resolution.state`, and `meta.surfaces.trainer_replay`.
2. `GET /api/v1/trainer/replay/{session_id}` is live with ordered replay-grade `events[]`, `artifacts`, `event_summary`, and `allowedActions.canCommit` / `allowedActions.canDiscard`.
3. `TeachingEvent.evidence_ref` is already BFF-resolved into the published typed link object; the client must not construct evidence navigation from raw identifiers.
4. `POST /api/v1/trainer/sessions/{session_id}/commit` is live with the published request guard (`expected_candidate_snapshot_at`) and append-only `commit` event behavior.
5. `POST /api/v1/trainer/sessions/{session_id}/discard` is live with the published request guard (`expected_candidate_snapshot_at`) and append-only `discard` event behavior.

The production pages may open against this route family now. If the live payload diverges from the synced contract, emit a `bff-gap` handoff instead of reintroducing a pending-BFF placeholder or local fallback state.

## Page Sections

### 1. Replay History List

- Lives on `/trainer/replay`.
- Displays one row per replayable trainer session.
- Each row shows:
  - `session_id`
  - `objective`
  - `status`
  - `started_at`
  - `ended_at`
  - `event_count`
  - `latest_event_type`
  - `latest_outcome_signal`
  - `replay_resolution.state`
- Row CTA uses `links.replay_detail`.

### 2. Replay Header

- Lives on `/trainer/replay/:session_id`.
- Displays:
  - `session_id`
  - `persona_id`
  - `objective`
  - `status`
  - `started_at`
  - `ended_at`
  - `meta.snapshot_at`
  - `replay_resolution.state`
- The header must distinguish trainer session lifecycle (`status`) from replay decision state (`replay_resolution.state`).

### 3. Artifact Rail

- Renders `artifacts.before_artifact_ref`, `artifacts.candidate_artifact_ref`, and `artifacts.after_artifact_ref`.
- Uses backend-owned refs only.
- The page must not reconstruct before/after lineage by replaying local patch history.

### 4. Ordered Event Timeline

- Renders `events[]` in backend order.
- Each row shows:
  - `sequence_number`
  - `actor_label` or `actor`
  - `event_type`
  - `message_body` or `summary`
  - `outcome_signal` when present
  - `emitted_at`
- Event-specific expansions:
  - `control_patch` rows show `patch_delta[]`
  - `preview_trigger` rows show `eval_ref`
  - `commit` and `discard` rows show `artifact_refs`

### 5. Evidence Drawer

- Opens only when `event.evidence_ref` is present.
- Drawer content uses:
  - `type`
  - `id`
  - `display_label`
  - `url_pattern`
- The client must not join external routes or storage IDs to create a deeper evidence panel.

### 6. Replay Cursor

- Cursor state is ephemeral UI state only.
- Controls may include:
  - previous event
  - next event
  - jump to first or last event
- Cursor bounds come from `event_summary.first_sequence_number` and `event_summary.last_sequence_number`.

### 7. Commit / Discard Actions

- Submission targets:
  - `POST /api/v1/trainer/sessions/{session_id}/commit`
  - `POST /api/v1/trainer/sessions/{session_id}/discard`
- Both actions must send:
  - `expected_candidate_snapshot_at`
  - `note` when the operator enters one
- CTA visibility is driven only by:
  - `allowedActions.canCommit`
  - `allowedActions.canDiscard`
- The page must not infer CTA visibility from `status = "completed"` alone.

## State Handling

| State | Required behavior |
|---|---|
| replay list surface `ok` | show full session history list |
| replay detail surface `ok` | show header, artifact rail, ordered timeline, evidence drawer, and CTA rail subject to `allowedActions` |
| `replay_resolution.state = "pending_decision"` | show decision CTA rail only if `allowedActions.canCommit` or `allowedActions.canDiscard` is true |
| `replay_resolution.state = "committed"` | show terminal committed state; suppress commit/discard CTA |
| `replay_resolution.state = "discarded"` | show terminal discarded state; suppress commit/discard CTA |
| `replay_resolution.state = "not_applicable"` | show replay history without promotion CTA |

## Degradation Handling

| Surface state | Required behavior |
|---|---|
| `meta.surfaces.trainer_replay = "ok"` | normal replay rendering |
| `meta.surfaces.trainer_replay = "stale"` | non-dismissable staleness banner; last-known replay content may remain visible; CTA visibility still depends on `allowedActions` |
| `meta.surfaces.trainer_replay = "degraded"` | show degradation banner; preserve only backend-supplied replay content; suppress commit/discard CTA |
| `meta.surfaces.trainer_replay = "unavailable"` | replace replay timeline, evidence drawer, and CTA rail with unavailable messaging |

The replay route owns degradation truth. Do not infer it from empty `events[]`, HTTP success, or a missing artifact ref.

## Constraints

- Use the dedicated replay route family only. Do not substitute `/api/v1/personas/{persona_id}/teaching`.
- Do not sort replay events by `emitted_at` when `sequence_number` is present.
- Do not derive evidence drawer links from raw IDs or artifact refs.
- Do not infer commit/discard authority from session `status`, preview state, or artifact presence alone.
- Do not mutate local timeline state after commit/discard without the backend-recorded `event`.
- Do not reintroduce a pending-BFF placeholder now that Pantheon has confirmed the replay routes and decision routes are live.
- If any required field is missing, emit a `bff-gap` handoff instead of inventing a fallback.

## Acceptance

- The replay list and replay detail pages render only backend-owned replay projections.
- `events[]` render in append-only `sequence_number` order without local re-sorting.
- Evidence drawer navigation is driven by BFF-resolved `evidence_ref` objects.
- Commit and discard CTA visibility follows `allowedActions.canCommit` and `allowedActions.canDiscard`, not local heuristics.
- Artifact rails use backend-owned before/candidate/after refs.
- Degradation behavior follows the published `meta.surfaces.trainer_replay` rules.

## References

- BFF contract: `docs/bff/TW-04-teaching-replay.md`
- Example payload: `docs/examples/TW-04-teaching-replay.json`
- Frontend handoff: `docs/pantheon-handoffs/TW-04-teaching-replay/FRONTEND_CHANGE_SPEC.md`
- Packet family: `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md`
- Frontend SA: `docs/lovable/PANTHEON_FRONTEND_SA.md`
