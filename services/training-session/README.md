# Training Session Contracts

`training-session-svc` owns the trainer teaching-session write surface used by
the BFF trainer workbench.

## TeachingSession

`teaching_session.schema.json` defines the persona-scoped teaching session
record:

- identity and scope: `session_id`, `persona_id`, `opened_by`, `trace_id`
- lifecycle: `mode`, `status`, `started_at`, `ended_at`
- trainer context: `objective`, `topic`, `context_refs`, `current_control_state_ref`
- replay/read-model fields: `events`, `outcomes`, `replay_resolution`, `artifacts`

The service emits `session_type=trainer` and current runtime statuses
`active`, `paused`, `completed`, `abandoned`, `committed`, `discarded`, or
`expired`. Terminal statuses require `ended_at`.

## TeachingEvent

`teaching_event.schema.json` defines the append-only event contract:

- identity/order: `event_id`, `session_id`, `sequence_number`, `correlation_id`
- actor: `actor_type` plus legacy `actor` / `actor_label` projection fields
- payload: canonical `payload` object plus replay-compatible top-level aliases
- timestamps: canonical `timestamp` plus BFF-compatible `emitted_at`

The event model rejects timestamp alias drift and duplicate event ids in a
session. It does not launch rapid eval, mutate live persona state, or publish
registry artifacts; those remain downstream TRN/IMT responsibilities.

## BFF Trainer Session Surface

The BFF exposes the operator-facing trainer session API at
`/api/v1/trainer/sessions`:

- `POST /api/v1/trainer/sessions` creates an active persona-scoped trainer
  session after persona resolution.
- `GET /api/v1/trainer/sessions?persona_id=...` lists trainer sessions with
  pagination metadata and read-surface health.
- `GET /api/v1/trainer/sessions/{session_id}` returns the projected session,
  event history, allowed actions, and workbench links.
- `POST /api/v1/trainer/sessions/{session_id}/message` appends an operator
  teaching message only while the session is active.
- `GET /api/v1/trainer/sessions/{session_id}/controls` and
  `POST /api/v1/trainer/sessions/{session_id}/patch` read and update trainer
  control state, with lifecycle and control validation.
- `GET /api/v1/trainer/sessions/{session_id}/preview` and
  `POST /api/v1/trainer/sessions/{session_id}/preview` expose before/after
  preview state. The POST route accepts the service-native `{ "mode":
  "refresh" }` body and the legacy BFF `{ "refresh_mode": "manual" }` body.

Replay commit/discard and rapid-eval routes are intentionally separate follow-on
contracts.

## Async Preview/Eval Worker

Trainer preview can run through a durable async job queue:

- `POST /api/training/sessions/{session_id}/preview-jobs` records a queued
  preview evaluation and appends a `preview_requested` `TeachingEvent`.
- `GET /api/training/preview-jobs?status=queued` exposes queued work to the
  supervised worker.
- `POST /api/training/preview-jobs/{job_id}/run` executes the existing vectorbt
  preview path, stores the completed preview bundle, and appends a
  `preview_result` event with an `evaluation_proof_ref`.

`services/training-session/preview_eval_worker.py` polls queued jobs over HTTP.
It is wired in Docker Compose behind the `training-session-preview-worker`
profile with `restart: unless-stopped`.

## Replay Decisions

The replay decision routes own the durable commit/discard record for a completed
trainer candidate:

- `POST /api/training/replays/{session_id}/commit`
- `POST /api/training/replays/{session_id}/discard`

Both routes accept `Idempotency-Key` and `X-Idempotency-Key`. When a decision
with the same key and payload is retried, the service replays the existing
decision without appending a second `TeachingEvent`; the same key with a
different decision payload returns a conflict.

Commit decisions stamp traceable lineage references into `artifacts` and the
decision event `artifact_refs`, including `lineage_ref`, `lineage_edge_id`,
`persona_policy_ref`, and `route_policy_ref`. Discard decisions record a
decision/lineage reference but leave `after_artifact_ref` empty and do not claim
persona or route-policy mutation.

Commit is fail-closed unless the replay candidate points to a completed preview
with an evaluation proof whose governance gate state is `passed`. Successful
commits copy the evaluation proof ref and governance gate state into the replay
resolution, decision event, and lineage audit artifact.
