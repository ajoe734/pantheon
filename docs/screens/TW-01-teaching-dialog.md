# TW-01 Teaching Dialog

## Classification

- Workbench: Trainer Workbench
- Screen ID: `screen-teaching-dialog`
- Feature ID: `TW-01-teaching-dialog`
- Packet status: **contract-published** — trainer-session lifecycle, transcript event shape, and create/list/detail/message semantics are defined; live BFF implementation is still the gate before UI work starts
- Task: `TW-01-FOUNDATION-001`

## Contract Note

The Trainer Workbench now has a published foundation slice for teaching dialog. UI implementation must not start until Pantheon confirms the create, list, detail, and message routes are live and returning the published field shape.

The UI must not infer trainer-session lifecycle, transcript ordering, actor context, or dialog write authority from client-side state, Persona teaching-history responses, or local message cache.

## User Goal

Let an operator open a trainer session for a known persona, browse existing trainer sessions by lifecycle state, inspect the ordered teaching transcript and actor context, and send coaching messages only when the backend says the session is still active.

## Routes

Primary routes:

- `/trainer/sessions`
- `/trainer/sessions/:session_id`

## Readiness Gate

Do not open the production page until Pantheon confirms:

1. `POST /api/v1/trainer/sessions` is live with the published create body and response shape.
2. `GET /api/v1/trainer/sessions` is live with `persona_id`, `status`, pagination, and `meta.surfaces.trainer_dialog`.
3. `GET /api/v1/trainer/sessions/{session_id}` is live with ordered `events[]`, `actor_context`, `session_summary`, and `allowedActions.canSendMessage`.
4. `POST /api/v1/trainer/sessions/{session_id}/message` is live and echoes the accepted `TeachingEvent` from the backend.

Until those gates are met, render a pending-BFF placeholder for both routes. No invented session rows, no local transcript cache, and no substitution with Persona teaching history.

## Page Sections

### 1. Session Composer

- Lives on `/trainer/sessions`.
- Fields come from the published create contract only:
  - `persona_id`
  - `session_type`
  - `objective`
  - optional `context_refs[]`
- Submission target: `POST /api/v1/trainer/sessions`
- `session_type` must be sent as `"trainer"`. Do not infer or omit it.

### 2. Session List

- Also lives on `/trainer/sessions`.
- Renders rows from `GET /api/v1/trainer/sessions`.
- Filters:
  - `persona_id`
  - `status`
- Each row shows:
  - `session_id`
  - `persona_id`
  - `actor_context.persona_display_name`
  - `objective`
  - `status`
  - `started_at`
  - `message_count`
  - `latest_outcome_signal` when non-null
- Row click navigates through `links.workbench_detail`.

### 3. Session Status Header

- Lives on `/trainer/sessions/:session_id`.
- Displays the read-side session truth from `GET /api/v1/trainer/sessions/{session_id}`:
  - `session_id`
  - `persona_id`
  - `session_type`
  - `objective`
  - `status`
  - `started_at`
  - `ended_at`
  - `opened_by`
  - `actor_context.persona_display_name`
  - `actor_context.persona_role_context`
- `status` is backend-owned. TW-01 does not publish local lifecycle mutation buttons.

### 4. Transcript Panel

- Lives on `/trainer/sessions/:session_id`.
- Renders `events[]` from the detail route only.
- Each row shows:
  - `sequence_number`
  - `actor`
  - `message_body`
  - `emitted_at`
  - `outcome_signal` when present
- Transcript order must follow ascending `sequence_number`.
- Do not insert optimistic rows into the transcript before the BFF echoes the accepted event.

### 5. Session Summary Strip

- Lives on `/trainer/sessions/:session_id`.
- Displays:
  - `session_summary.message_count`
  - `session_summary.last_event_at`
  - `session_summary.latest_outcome_signal`
- The summary strip is read-only and must stay aligned to the backend response after every message submission.

### 6. Message Composer

- Lives on `/trainer/sessions/:session_id`.
- Submission target: `POST /api/v1/trainer/sessions/{session_id}/message`
- Only field:
  - `message_body`
- Visible and enabled only when `allowedActions.canSendMessage === true`.
- After submit, re-read the detail route or merge the backend-echoed `event` response. Do not create a local transcript row without backend confirmation.

## Lifecycle Handling

| Status | Required behavior |
|---|---|
| `active` | transcript visible; message composer may be enabled when `allowedActions.canSendMessage === true` |
| `paused` | transcript remains visible; message composer hidden or disabled |
| `completed` | transcript remains visible as read-only history; no dialog writes |
| `abandoned` | transcript remains visible as read-only history; no dialog writes |

The frontend must not infer lifecycle transitions or expose pause, complete, or abandon CTAs until a later Trainer packet defines explicit write routes.

## Degradation Handling

| Surface state | Required behavior |
|---|---|
| `meta.surfaces.trainer_dialog = "fresh"` | normal list or detail rendering |
| `meta.surfaces.trainer_dialog = "stale"` | non-dismissable staleness banner; content remains visible |
| `meta.surfaces.trainer_dialog = "degraded"` | show degradation banner; do not present empty list or empty transcript as authoritative |
| `meta.surfaces.trainer_dialog = "unavailable"` | suppress list or transcript rendering and show unavailable notice |

## Constraints

- Use the Pantheon BFF only. No demo provider transcript or local chat store.
- Do not substitute Persona Management teaching-history data for the Trainer Workbench session list or dialog detail.
- Do not derive transcript ordering, actor context, or lifecycle state client-side.
- Do not infer message-send authority from `status`; use `allowedActions.canSendMessage`.
- Do not start production UI until Pantheon confirms the routes are live.
- If any required field is missing, emit a `bff-gap` handoff instead of mocking the missing state.

## Acceptance

- Session composer submits only the published create shape, including `session_type = "trainer"`.
- Session list renders from `GET /api/v1/trainer/sessions` with backend-owned filters and pagination.
- Session detail renders the canonical trainer-session object with actor context, ordered `events[]`, and backend-owned `session_summary`.
- Message composer is visible only when `allowedActions.canSendMessage` is `true`.
- Transcript rows come only from backend-returned `TeachingEvent` objects and respect `sequence_number`.
- Degradation behavior follows the published `meta.surfaces.trainer_dialog` rules.

## References

- BFF contract: `docs/bff/TW-01-teaching-dialog.md`
- Example payload: `docs/examples/TW-01-teaching-dialog.json`
- Frontend change spec: `docs/pantheon-handoffs/TW-01-teaching-dialog/FRONTEND_CHANGE_SPEC.md`
- Packet family: `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md`
- Frontend SA: `docs/lovable/PANTHEON_FRONTEND_SA.md`
