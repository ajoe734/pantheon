# TW-04 Teaching Replay BFF Contract

## Status

**Routes live** — the trainer replay list/detail routes (`GET /api/v1/trainer/replay`, `GET /api/v1/trainer/replay/{session_id}`) and commit/discard write routes (`POST /api/v1/trainer/sessions/{session_id}/commit`, `POST /api/v1/trainer/sessions/{session_id}/discard`) are confirmed live as of `2026-04-20T12:45:00Z` and return the published replay list/detail, replay-grade `TeachingEvent`, evidence-link, and artifact-ref field shape. Frontend handoff bundle published at `docs/pantheon-handoffs/TW-04-teaching-replay/`. UI implementation may proceed.

Task: `TW-04-REPLAY-001`

## Purpose

Provide the fourth real production slice for the Trainer Workbench so operators can browse finished trainer sessions, replay the complete ordered teaching history, inspect backend-resolved evidence links, and explicitly commit or discard a completed candidate state without re-sorting events, constructing evidence navigation, or inferring replay authority in the browser.

## Dependencies

- `TW-01-FOUNDATION-001` for canonical `session_id`, trainer lifecycle semantics, and the dialog-safe `TeachingEvent` subset
- `TW-03-COMPARE-001` for stable preview evidence identity, `eval_id`, and candidate snapshot semantics

## Routes

### List replayable trainer sessions

- `GET /api/v1/trainer/replay`

Supported query params:

- `persona_id` — required for persona-scoped replay browsing
- `status` — optional terminal filter; allowed values are `"completed"` and `"abandoned"`
- `page_token`
- `page_size`

Each row in `data[]` must contain:

- `session_id`
- `persona_id`
- `objective`
- `status`
- `started_at`
- `ended_at`
- `event_count`
- `latest_event_type` — nullable `"message"` | `"control_patch"` | `"preview_trigger"` | `"outcome_signal"` | `"commit"` | `"discard"`
- `latest_outcome_signal` — nullable display label
- `replay_resolution.state` — `"pending_decision"` | `"committed"` | `"discarded"` | `"not_applicable"`
- `allowedActions.canReplay`
- `allowedActions.canCommit`
- `allowedActions.canDiscard`
- `links.replay_detail`

Response metadata must include:

- `page_info.next_page_token`
- `page_info.total`
- `meta.snapshot_at`
- `meta.surfaces.trainer_replay` — `"ok"` | `"stale"` | `"degraded"` | `"unavailable"`

Required invariants:

- The replay list is Trainer-owned and must not be substituted with Persona teaching-history routes.
- `allowedActions.canReplay` must be `true` only when the BFF can serve the ordered replay detail contract for that session.
- `allowedActions.canCommit` and `allowedActions.canDiscard` in the list response are advisory summaries only; the detail response remains the canonical CTA authority source.

### Get trainer replay detail

- `GET /api/v1/trainer/replay/{session_id}`

Required response fields:

- `session_id`
- `persona_id`
- `objective`
- `status`
- `started_at`
- `ended_at`
- `replay_resolution`
- `artifacts`
- `event_summary`
- `events[]` — ordered replay-grade `TeachingEvent` objects
- `allowedActions.canCommit`
- `allowedActions.canDiscard`
- `links.self`
- `links.session_detail`
- `meta.snapshot_at`
- `meta.surfaces.trainer_replay`

### Commit trainer replay result

- `POST /api/v1/trainer/sessions/{session_id}/commit`

Required request body:

- `expected_candidate_snapshot_at`
- `note` — nullable operator note

Required response fields:

- `session_id`
- `status`
- `replay_resolution`
- `artifacts`
- `committed_at`
- `committed_by`
- `event` — the backend-recorded replay-grade `TeachingEvent`
- `allowedActions.canCommit`
- `allowedActions.canDiscard`
- `meta.snapshot_at`
- `meta.surfaces.trainer_replay`

Required invariants:

- The BFF must reject the commit route when `status != "completed"`.
- The BFF must reject the commit route when `allowedActions.canCommit` is absent or `false`.
- The BFF must reject the commit route when `expected_candidate_snapshot_at` does not match the currently replayable candidate snapshot.
- A successful commit must append a `TeachingEvent` with `event_type = "commit"` and advance `replay_resolution.state` to `"committed"`.

### Discard trainer replay result

- `POST /api/v1/trainer/sessions/{session_id}/discard`

Required request body:

- `expected_candidate_snapshot_at`
- `note` — nullable operator note

Required response fields:

- `session_id`
- `status`
- `replay_resolution`
- `artifacts`
- `discarded_at`
- `discarded_by`
- `event` — the backend-recorded replay-grade `TeachingEvent`
- `allowedActions.canCommit`
- `allowedActions.canDiscard`
- `meta.snapshot_at`
- `meta.surfaces.trainer_replay`

Required invariants:

- The BFF must reject the discard route when `status != "completed"`.
- The BFF must reject the discard route when `allowedActions.canDiscard` is absent or `false`.
- The BFF must reject the discard route when `expected_candidate_snapshot_at` does not match the currently replayable candidate snapshot.
- A successful discard must append a `TeachingEvent` with `event_type = "discard"` and advance `replay_resolution.state` to `"discarded"`.

## Replay Detail Objects

### Replay resolution

`replay_resolution` is the backend-owned decision state for the finished trainer session.

Required fields:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `state` | string | no | `"pending_decision"` \| `"committed"` \| `"discarded"` \| `"not_applicable"` |
| `decision_at` | string | yes | commit or discard timestamp |
| `decision_by` | string | yes | actor who recorded the commit/discard decision |
| `note` | string | yes | latest backend-recorded operator note |

Required invariants:

- `state = "pending_decision"` is only valid when `status = "completed"` and a candidate snapshot is still eligible for replay promotion.
- `state = "not_applicable"` is only valid when `status = "abandoned"` or when the session never produced a candidate snapshot.
- `state = "committed"` and `state = "discarded"` are terminal for the replay surface; both `allowedActions.canCommit` and `allowedActions.canDiscard` must then be `false`.

### Replay artifacts

`artifacts` is the backend-owned artifact-ref bundle for downstream compare and evidence navigation.

Required fields:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `before_artifact_ref` | string | yes | baseline artifact before the replayed session began |
| `candidate_artifact_ref` | string | yes | candidate artifact produced by the replayed session before commit/discard |
| `after_artifact_ref` | string | yes | committed artifact after a successful commit |

Required invariants:

- `before_artifact_ref` must be present whenever the session reached `status = "completed"`.
- `candidate_artifact_ref` must be present whenever `replay_resolution.state = "pending_decision"`, `"committed"`, or `"discarded"`.
- `after_artifact_ref` must be present only after a successful commit.
- The client must never construct before/after navigation by replaying event deltas locally.

### Replay event summary

`event_summary` is a backend-owned overview for header and cursor bounds.

Required fields:

- `event_count`
- `first_sequence_number`
- `last_sequence_number`
- `latest_outcome_signal` — nullable display label

## TeachingEvent Object

The TW-04 replay-grade event shape extends the TW-01 dialog subset without changing its ordering or append-only guarantees.

Required fields:

- `event_id`
- `session_id`
- `actor` — `"operator"` | `"persona"` | `"system"`
- `actor_label` — nullable BFF-resolved display label
- `event_type` — `"message"` | `"control_patch"` | `"preview_trigger"` | `"outcome_signal"` | `"commit"` | `"discard"`
- `message_body` — nullable
- `summary` — nullable backend-authored one-line event summary
- `emitted_at`
- `sequence_number`
- `outcome_signal` — nullable display label
- `evidence_ref` — nullable resolved evidence-link object
- `patch_delta[]` — nullable array
- `eval_ref` — nullable object
- `artifact_refs` — nullable object

### Resolved evidence link

When `evidence_ref` is present, it must contain:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `type` | string | no | canonical evidence-link type such as `"telemetry"`, `"compare_result"`, `"lineage_edge"`, or `"persona_capability"` |
| `id` | string | no | durable evidence identity |
| `display_label` | string | no | BFF-resolved display text |
| `url_pattern` | string | no | canonical navigation target or route template |

The client must not synthesize evidence routing from raw IDs, storage paths, or event metadata.

### Patch delta row

Each `patch_delta[]` row must contain:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `parameter_key` | string | no | durable control identity |
| `previous_value` | any | no | previously accepted value |
| `new_value` | any | no | replayed candidate value |

### Eval reference

When `eval_ref` is present, it must contain:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `eval_id` | string | no | canonical preview evaluation identity |
| `baseline_snapshot_at` | string | no | preview baseline timestamp |
| `candidate_snapshot_at` | string | no | preview candidate timestamp |

### Event-level artifact refs

When `artifact_refs` is present, it must contain:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `before_artifact_ref` | string | yes | baseline artifact reference |
| `candidate_artifact_ref` | string | yes | candidate artifact reference |
| `after_artifact_ref` | string | yes | committed artifact reference |

Required invariants:

- `events[]` must be strictly ordered by ascending `sequence_number`.
- `sequence_number` remains append-only within the session and must not be re-used.
- `event_type = "message"` requires `message_body` and does not require `patch_delta[]`, `eval_ref`, or `artifact_refs`.
- `event_type = "control_patch"` requires a non-empty `patch_delta[]`.
- `event_type = "preview_trigger"` requires `eval_ref`.
- `event_type = "outcome_signal"` requires `outcome_signal`.
- `event_type = "commit"` requires `artifact_refs.before_artifact_ref` and `artifact_refs.after_artifact_ref`.
- `event_type = "discard"` requires `artifact_refs.before_artifact_ref`; `artifact_refs.after_artifact_ref` must be `null`.
- The frontend must render replay order exactly as returned by the BFF; it must not sort, merge, or synthesize gaps client-side.

## Authority Rules

- `allowedActions.canCommit` and `allowedActions.canDiscard` are the sole CTA authority signals for replay promotion decisions.
- Both signals must be `false` when:
  - `status != "completed"`
  - `replay_resolution.state != "pending_decision"`
  - `meta.surfaces.trainer_replay` is `"degraded"` or `"unavailable"`
  - `artifacts.candidate_artifact_ref` is absent
  - the backend cannot guarantee the replay snapshot is current

The frontend must not infer commit/discard authority from `status` alone.

## Degradation Rules

- When `meta.surfaces.trainer_replay = "stale"`, the UI may show the last-known event history with a non-dismissable staleness banner, but commit/discard CTA visibility still depends on `allowedActions`.
- When `meta.surfaces.trainer_replay = "degraded"`, show the shared degradation substrate from `PKT-005`, preserve only backend-supplied replay content, and suppress commit/discard.
- When `meta.surfaces.trainer_replay = "unavailable"`, suppress the replay timeline, evidence drawer, and decision CTAs entirely.
- The frontend must not treat an empty `events[]` array as authoritative when the replay surface is `"stale"`, `"degraded"`, or `"unavailable"`.

## Non-Goals

- The client must not substitute Persona teaching history for the trainer replay surface.
- The client must not reconstruct evidence links from raw event refs or artifact IDs.
- The client must not infer commit/discard CTA visibility from session `status`, preview state, or event count alone.
- The client must not replay event ordering from timestamps when `sequence_number` is present.
- This slice does not publish a mutation route for editing historical replay events.

## Relationship to Adjacent Trainer Modules

- `TW-01 Teaching Dialog` remains the source of the dialog-safe `TeachingEvent` subset and trainer lifecycle identity.
- `TW-03 Before/After Compare` remains the source of preview evidence identity and candidate snapshot timestamps referenced by replay events.
- `TW-04` is the only Trainer module allowed to expose replay-grade evidence navigation and commit/discard promotion authority.

## Example Payload

- `docs/examples/TW-04-teaching-replay.json`
