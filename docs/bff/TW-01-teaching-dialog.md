# TW-01 Teaching Dialog BFF Contract

## Status

**Contract published** — the trainer-session identity, read-side lifecycle semantics, dialog event shape, and create/list/detail/message route shapes are now the definitive implementation target for the Pantheon BFF. UI work must not start until Pantheon confirms the routes are live and returning this field shape.

Task: `TW-01-FOUNDATION-001`

## Purpose

Provide the first real production slice for the Trainer Workbench so operators can open a trainer session, browse active and historical sessions, inspect the ordered teaching transcript, and send coaching messages without inventing session state, actor context, or transcript ordering in the browser.

## Routes

### Create trainer session

- `POST /api/v1/trainer/sessions`

Required request body:

- `persona_id` — target persona identity for the training session
- `session_type` — must be `"trainer"`
- `objective` — free-text coaching goal
- `context_refs[]` — optional typed context refs shaped as `{ type, id }`

Required response fields:

- `session_id`
- `persona_id`
- `session_type` — must return `"trainer"`
- `objective`
- `status` — must return `"active"` for a newly created session
- `started_at`
- `allowedActions.canSendMessage`
- `links.self`
- `links.workbench_detail`

### List trainer sessions

- `GET /api/v1/trainer/sessions`

Supported query params:

- `persona_id` — required for persona-scoped browsing
- `status` — filter by lifecycle state
- `page_token`
- `page_size`

Required response fields:

- `data[]`
  - `session_id`
  - `persona_id`
  - `session_type`
  - `objective`
  - `status`
  - `started_at`
  - `ended_at` — nullable
  - `message_count`
  - `last_event_at` — nullable
  - `latest_outcome_signal` — nullable display label
  - `actor_context.persona_display_name`
  - `actor_context.persona_role_context`
  - `allowedActions.canSendMessage`
  - `links.workbench_detail`
- `page_info.next_page_token`
- `page_info.total`
- `meta.snapshot_at`
- `meta.surfaces.trainer_dialog` — `"fresh"` | `"stale"` | `"degraded"` | `"unavailable"`

### Get trainer session detail

- `GET /api/v1/trainer/sessions/{session_id}`

Required response fields:

- `session_id`
- `persona_id`
- `session_type`
- `objective`
- `status`
- `started_at`
- `ended_at` — nullable
- `opened_by`
- `context_refs[]`
  - `type`
  - `id`
- `actor_context.persona_display_name`
- `actor_context.persona_role_context`
- `session_summary.message_count`
- `session_summary.last_event_at` — nullable
- `session_summary.latest_outcome_signal` — nullable
- `events[]` — ordered `TeachingEvent` objects
- `allowedActions.canSendMessage`
- `links.self`
- `links.workbench_detail`
- `meta.snapshot_at`
- `meta.surfaces.trainer_dialog` — `"fresh"` | `"stale"` | `"degraded"` | `"unavailable"`

### Send trainer coaching message

- `POST /api/v1/trainer/sessions/{session_id}/message`

Required request body:

- `message_body`

Required response fields:

- `session_id`
- `status`
- `accepted_at`
- `event` — the backend-echoed `TeachingEvent`
- `session_summary.message_count`
- `session_summary.last_event_at`
- `session_summary.latest_outcome_signal` — nullable
- `allowedActions.canSendMessage`

## TrainerSession Object

Canonical lifecycle:

- `active` — teaching dialog is open; `POST /message` is allowed when `allowedActions.canSendMessage` is `true`
- `paused` — session remains visible for review, but dialog writes are suspended
- `completed` — session is closed for dialog writes and becomes downstream input for compare or replay surfaces
- `abandoned` — session ended without a completed teaching outcome; dialog writes are permanently disabled

Required invariants:

- `session_id` is the canonical anchor for all downstream Trainer Workbench modules.
- `session_type` must always be `"trainer"` for this module and must never be inferred from route context alone.
- `persona_id` is immutable after session creation.
- `allowedActions.canSendMessage` must be `false` whenever `status` is `paused`, `completed`, or `abandoned`.
- The BFF must reject `POST /api/v1/trainer/sessions/{session_id}/message` when `status != "active"`.
- This slice publishes the lifecycle as read-side canonical truth only. It does not publish dedicated pause, complete, or abandon write routes; the frontend must not invent those mutation paths before a later Trainer packet defines them explicitly.
- The Trainer Workbench list route is distinct from Persona teaching history. The frontend must not substitute `/api/v1/personas/{persona_id}/teaching` for `/api/v1/trainer/sessions`.

## TeachingEvent Object

The TW-01 dialog subset defines only the transcript-safe fields needed by the teaching dialog. Replay-grade event expansion remains scoped to `TW-04`.

Required fields:

- `event_id`
- `session_id`
- `actor` — `"operator"` | `"persona"`
- `message_body`
- `emitted_at`
- `sequence_number`
- `outcome_signal` — nullable display label

Required invariants:

- `events[]` returned by the detail route must be strictly ordered by ascending `sequence_number`.
- `sequence_number` is append-only within a session and must not be re-used.
- The frontend must render transcript order from `sequence_number` and `events[]` as returned by the BFF; it must not insert, merge, or re-sort message history locally.
- `outcome_signal` is display-only in TW-01. The contract does not lock an enum in this slice; downstream replay semantics may refine it later without changing the dialog transcript fields.

## Degradation Rules

- When `meta.surfaces.trainer_dialog = "degraded"` or `"unavailable"`, the UI must not present "no sessions" or "no transcript yet" as authoritative.
- When the list surface is not `"fresh"`, show the shared degradation substrate from `PKT-005`.
- When the detail surface is `"unavailable"`, suppress transcript content and the message composer.
- When the detail surface is `"degraded"` and `allowedActions.canSendMessage` is absent or `false`, suppress the message composer.

## Write Authority

- Session creation: `POST /api/v1/trainer/sessions`
- Coaching message submission: `POST /api/v1/trainer/sessions/{session_id}/message`

This slice does not publish control-patch, preview, commit, discard, or replay write paths. Those remain the responsibility of `TW-02` through `TW-04`.

## Relationship to Downstream Trainer Modules

- `TW-02 Parameter Controls` depends on `session_id`, read-side lifecycle state, and `allowedActions.canSendMessage` to know whether the session is still active.
- `TW-03 Before/After Compare` depends on the same session identity and lifecycle semantics so compare output stays tied to one truthful trainer session.
- `TW-04 Teaching Replay` depends on the `TeachingEvent` dialog subset published here, but the full replay-grade event schema remains explicitly out of scope for `TW-01`.

## Example Payload

- `docs/examples/TW-01-teaching-dialog.json`
