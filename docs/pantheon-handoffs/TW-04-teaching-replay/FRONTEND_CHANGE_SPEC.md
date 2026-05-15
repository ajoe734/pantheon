# TW-04 Teaching Replay — Frontend Change Spec

## Overview

TW-04 is the production replay surface for completed trainer sessions. Operators
browse finished sessions, replay the ordered teaching history, inspect
backend-resolved evidence links, and explicitly commit or discard a completed
candidate state.

The frontend must use only the TW-04 replay route family and
backend-owned decision authority (`allowedActions`). No client-side event
sorting, evidence construction, or commit/discard state inference is permitted.

Feature ID: `TW-04-teaching-replay`
Screen slug: `teaching-replay`
Screen ID: `screen-teaching-replay`
BFF contract: `docs/bff/TW-04-teaching-replay.md`
Example payload: `docs/examples/TW-04-teaching-replay.json`
Screen spec: `docs/screens/TW-04-teaching-replay.md`

---

## Allowed APIs

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/trainer/replay` | Replay list — scoped to one `persona_id` |
| `GET` | `/api/v1/trainer/replay/{session_id}` | Replay detail — ordered timeline + authority |
| `POST` | `/api/v1/trainer/sessions/{session_id}/commit` | Record commit decision via BFF client action layer |
| `POST` | `/api/v1/trainer/sessions/{session_id}/discard` | Record discard decision via BFF client action layer |

Commit and discard calls must be routed through the existing BFF client action
layer. Do not issue raw fetch calls from UI components.

---

## Required UI Modules

| Module | Route | Source of truth |
|---|---|---|
| Replay list | `/trainer/replay` | `GET /api/v1/trainer/replay` with required `persona_id` |
| Replay detail header | `/trainer/replay/:session_id` | `GET /api/v1/trainer/replay/{session_id}` |
| Artifact rail | `/trainer/replay/:session_id` | `artifacts` from detail response |
| Ordered event timeline | `/trainer/replay/:session_id` | `events[]` from detail response, in BFF sequence order |
| Evidence drawer | `/trainer/replay/:session_id` | `evidence_ref` objects from individual events — open only when BFF provides the object |
| Commit/discard CTA rail | `/trainer/replay/:session_id` | `allowedActions.canCommit` / `allowedActions.canDiscard` from detail response |

---

## State Rules

- **Session lifecycle** (`status`) and **replay resolution** (`replay_resolution.state`) are separate backend-owned states; do not merge them.
- CTA visibility comes only from `allowedActions.canCommit` and `allowedActions.canDiscard` in the **detail response**. List-response `allowedActions` are advisory summaries only.
- Degradation state comes only from `meta.surfaces.trainer_replay`. See degradation rules below.
- `replay_resolution.state` must be rendered from the BFF value: `pending_decision` / `committed` / `discarded` / `not_applicable`.
- Do not substitute Persona teaching-history routes (`/api/v1/personas/{persona_id}/teaching`) for the Trainer replay routes.

---

## Required Fields

### Replay list row

- `session_id`, `persona_id`, `objective`, `status`
- `started_at`, `ended_at`, `event_count`
- `latest_event_type`, `latest_outcome_signal`
- `replay_resolution.state`
- `allowedActions.canReplay`
- `links.replay_detail`

### Replay detail header

- `session_id`, `persona_id`, `objective`, `status`
- `meta.snapshot_at`, `replay_resolution.state`

### Detail body

- `events[]` — ordered ascending by `sequence_number`; render exactly as returned
- `artifacts` — before/candidate/after artifact refs
- `event_summary`
- `events[].evidence_ref` — BFF-resolved when present; do not construct from raw event fields

### Authority fields

- `allowedActions.canCommit`, `allowedActions.canDiscard`

### Degradation field

- `meta.surfaces.trainer_replay`

---

## Failure Rules

If any required field listed above is absent from the BFF response:

1. Emit the TW-04 bff-gap handoff (`.coordination/requests/TW-04-teaching-replay-bff-gap.example.yaml`).
2. Stop rendering the affected surface.
3. Do not mock events, `event.evidence_ref` objects, replay resolution state, or decision authority.

---

## Degradation Rules

| `meta.surfaces.trainer_replay` | UI behaviour |
|---|---|
| `ok` | render normally |
| `stale` | show last-known event history with non-dismissable staleness banner; CTAs still depend on `allowedActions` |
| `degraded` | show canonical PKT-005 degradation banner; suppress commit/discard CTAs |
| `unavailable` | suppress replay timeline, evidence drawer, and decision CTAs entirely |

Do not treat an empty `events[]` array as authoritative when the surface is
`stale`, `degraded`, or `unavailable`.

---

## Completion Rules

On UI completion:

1. Emit the TW-04 ui-done handoff (`.coordination/requests/TW-04-teaching-replay-ui-done.example.yaml`).
2. Publish the required feedback bundle:
   - `docs/pantheon-feedback/TW-04-teaching-replay/LOVABLE_CHANGE_FEEDBACK.md`
   - `docs/pantheon-feedback/TW-04-teaching-replay/API_GAP_REQUESTS.json`
   - `docs/pantheon-feedback/TW-04-teaching-replay/UI_DECISIONS.md`
   - `docs/pantheon-feedback/TW-04-teaching-replay/QA_STATUS.md`

---

## Non-Goals

- Do not substitute Persona teaching history for the Trainer replay surface.
- Do not reconstruct evidence links from raw event refs or artifact IDs.
- Do not infer commit/discard CTA visibility from `status`, preview state, or event count alone.
- Do not re-order events from timestamps when `sequence_number` is present.
- Do not edit or synthesize historical replay events client-side.

---

## References

- BFF contract: `docs/bff/TW-04-teaching-replay.md`
- Screen spec: `docs/screens/TW-04-teaching-replay.md`
- Example payload: `docs/examples/TW-04-teaching-replay.json`
- Coordination task: `.coordination/responses/TW-04-teaching-replay-lovable-ui-task.yaml`
- Contract ready: `.coordination/responses/TW-04-teaching-replay-contract-ready.yaml`
- BFF-gap template: `.coordination/requests/TW-04-teaching-replay-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/TW-04-teaching-replay-ui-done.example.yaml`
