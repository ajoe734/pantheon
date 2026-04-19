# TW-01 Teaching Dialog — Frontend Change Spec

## Feature

- Feature ID: `TW-01-teaching-dialog`
- Screen ID: `screen-teaching-dialog`
- Workbench: Trainer Workbench
- Packet status: contract-published — UI implementation must not start until the BFF routes are live
- Task: `TW-01-FOUNDATION-001`

## Summary

Build the first truthful Trainer Workbench slice inside `front-ai-trading-system`. This slice includes the trainer-session composer, session list, session detail page, ordered transcript, status header, summary strip, and coaching message composer. All trainer-session identity, lifecycle state, transcript ordering, actor context, and dialog write authority must come from the Pantheon BFF.

## Files to Create or Modify

```text
src/pages/trainer/TeachingDialogList.tsx      — new trainer session composer + list page
src/pages/trainer/TeachingDialogDetail.tsx    — new trainer session detail page
src/pages/trainer/types.ts                    — add trainer-session and TeachingEvent types
src/lib/bffClient.ts                          — add TW-01 trainer session calls
```

## Readiness Gate

Do not open the production page until Pantheon confirms these routes are live and returning the published field shape:

- `POST /api/v1/trainer/sessions`
- `GET /api/v1/trainer/sessions`
- `GET /api/v1/trainer/sessions/{session_id}`
- `POST /api/v1/trainer/sessions/{session_id}/message`

Until then, render a pending-BFF placeholder. No invented trainer-session rows, no local transcript cache, and no Persona teaching-history fallback.

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` or `axios` in component files.

### Create trainer session

```http
POST /api/v1/trainer/sessions
```

Body fields:

- `persona_id`
- `session_type`
- `objective`
- `context_refs[]`

### List trainer sessions

```http
GET /api/v1/trainer/sessions
```

Supported query params:

- `persona_id`
- `status`
- `page_token`
- `page_size`

### Get trainer session detail

```http
GET /api/v1/trainer/sessions/{session_id}
```

Required detail-only fields:

- `actor_context`
- `session_summary`
- `events[]`
- `allowedActions.canSendMessage`

### Send coaching message

```http
POST /api/v1/trainer/sessions/{session_id}/message
```

Accepted field:

- `message_body`

## Component Rules

### `TeachingDialogList.tsx`

- Hosts both the trainer-session composer and the session list.
- Composer fields must exactly match the published create contract.
- Always send `session_type = "trainer"` as a request field.
- List rows must come from the BFF list response only.
- Filter state may be local UI state, but filter vocabulary must match backend query params exactly.
- If `meta.surfaces.trainer_dialog` is `degraded` or `unavailable`, render the shared degradation banner and do not present an empty list as authoritative.

### `TeachingDialogDetail.tsx`

- Reads `session_id` from `/trainer/sessions/:session_id`.
- Renders the full trainer-session object, status header, summary strip, transcript timeline, and message composer.
- Transcript rows must render from backend `events[]` only.
- Transcript order must follow `sequence_number`.
- Message composer is visible only when `allowedActions.canSendMessage === true`.
- If the route response is `degraded`, suppress the composer unless `allowedActions.canSendMessage` is still explicitly present and `true`.
- After message submission, merge only the backend-echoed `event` or re-fetch the detail route. No optimistic transcript mutation.

## Constraints

- Use the existing BFF client only.
- Do not add raw network calls in components.
- Do not import demo providers or mock trainer state.
- Do not use `/api/v1/personas/{persona_id}/teaching` as a replacement for the trainer-session list or detail route.
- Do not derive transcript ordering, actor context, or lifecycle state from local state.
- Do not infer message authority from `status`; use `allowedActions.canSendMessage`.
- Do not start production UI until Pantheon confirms the routes are live.
- If any required field is missing, emit a `bff-gap` handoff instead of mocking.

## Degradation Handling

| State | Handling |
|---|---|
| `meta.surfaces.trainer_dialog = "stale"` | render non-dismissable staleness banner; content remains visible |
| `meta.surfaces.trainer_dialog = "degraded"` | render degradation banner; suppress authoritative empty-state claims |
| `meta.surfaces.trainer_dialog = "unavailable"` | suppress list or transcript rendering and show unavailable notice |

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/TW-01-teaching-dialog-ui-done.yaml` using `.coordination/requests/TW-01-teaching-dialog-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/TW-01-teaching-dialog.md`
- BFF contract: `docs/bff/TW-01-teaching-dialog.md`
- Example payload: `docs/examples/TW-01-teaching-dialog.json`
- Contract-ready: `.coordination/responses/TW-01-teaching-dialog-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/TW-01-teaching-dialog-lovable-ui-task.yaml`
- Packet family: `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md`
