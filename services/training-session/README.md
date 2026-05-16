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
