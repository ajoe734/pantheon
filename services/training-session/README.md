# Training Session Contracts

`training-session-svc` owns the trainer teaching-session write surface used by
the BFF trainer workbench.

## Inbound Authority And Tenant Boundary

Every `/api/training/*` request is authenticated before route execution.
Health and metrics routes remain probe-readable. The caller must provide
`Authorization: Bearer <verified service JWT>`, `X-Pantheon-Service`, and
`X-Tenant-Id`.

The service header must match a verified token service identity, the token must
carry the `training-service` role, and the requested tenant must be present in
the verified tenant claims. Cross-tenant identifiers return `404` rather than
disclosing another tenant's records. Replay commit and discard additionally
require verified MFA.

Primary configuration:

```text
TRAINING_SESSION_AUTH_MODE=strict
TRAINING_SESSION_JWT_SECRET=<secret>
# or TRAINING_SESSION_JWKS_URI / TRAINING_SESSION_OIDC_DISCOVERY_URL
TRAINING_SESSION_ALLOWED_CALLER_SERVICES=control-plane-bff,training-session-preview-worker
TRAINING_SESSION_ALLOWED_ROLES=training-service
TRAINING_SESSION_MFA_REQUIRED=true
```

`TRAINING_SESSION_AUTH_DISABLED=true` is test-only and is rejected in an
enforced staging or production persistence posture.

## TeachingSession

`teaching_session.schema.json` defines the persona-scoped teaching session
record:

- identity and scope: `session_id`, `persona_id`, `tenant_id`, `opened_by`,
  `trace_id`
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
session. Every event carries the same required `tenant_id` as its session. It
does not launch rapid eval, mutate live persona state, or publish registry
artifacts; those remain downstream TRN/IMT responsibilities.

## Authoritative HA Store

`TRAINING_SESSION_EVENT_STORE_BACKEND=postgres` activates the complete training
authority store, not only the former event-log pilot. The service-owned
`training_session.authority_records` table durably stores sessions, controls,
preview bundles, preview jobs, replay decisions, and latest functional
results; `training_session.teaching_events` remains append-only.

Preview-job and replay mutations acquire a transaction-scoped Postgres advisory
lock around their read/decide/write sequence. Two API/worker instances cannot
both claim one job or both pass one replay decision. Persona-target commit
remains an idempotent owner-API operation: after a crash, a restarted worker
reuses the same idempotency key and accepts only exact terminal authoritative
readback. JSON/JSONL is a single-node development fallback; staging and
production persistence posture require Postgres.

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
- `GET /api/training/preview-jobs?status=claimable` exposes queued, retryable,
  and expired-lease work to the supervised worker.
- `POST /api/training/preview-jobs/{job_id}/run` executes the existing vectorbt
  preview path, stores the completed preview bundle, and appends a
  `preview_result` event with an `evaluation_proof_ref`. The response reports
  whether the attempt reclaimed an expired lease and whether a failed attempt
  remains retryable.

`services/training-session/preview_eval_worker.py` polls claimable jobs over HTTP.
Docker Compose starts it by default with `restart: unless-stopped`, mounts the
durable `training-session-data` volume, and health-checks a recent worker alive
marker. The service, rather than the worker request, owns the trusted evaluation
clock used by freshness admission.

The worker sends its own service token and tenant on both poll and run calls:

```text
TRAINING_SESSION_WORKER_TOKEN=<service JWT>
TRAINING_SESSION_TENANT_ID=<tenant>
TRAINING_SESSION_WORKER_SERVICE_ID=training-session-preview-worker
```

Its marker is written only after a tick with zero failed jobs and includes the
functional result; exception and failed-job ticks do not refresh it.

## Authoritative Evaluation Data

The default Compose path evaluates with the pinned upstream vectorbt backend
(`PANTHEON_VECTORBT_BACKEND=real`). Dataset authority comes from
`source-ingest` using `SOURCE_INGEST_API_URL`,
`TRAINING_SESSION_SOURCE_CONNECTOR_ID`, and
`TRAINING_SESSION_SOURCE_DATASET_ID`; the source-ingest data volume is also
mounted read-only for authoritative local readback. Missing, stale, invalid,
insufficient, or provenance-mismatched data fails closed and cannot produce a
passing commit gate.

Runtime evaluation and decision records are written under
`TRAINING_SESSION_RUNTIME_EVIDENCE_PATH` on the durable training-session data
volume. This runtime JSONL is separate from the task-scoped product evidence
manifest under `docs/deployment/evidence/` so tests and service execution do
not mutate a tracked closeout artifact.

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

Persona, approval, target-write, and terminal-readback records must match the
same `tenant_id`. Outbound authority calls include `X-Tenant-Id`, and tenant is
part of the digest-bound evaluation proof and committed target binding.

## Functional Health

`/healthz` and `/readyz` report persistence posture, storage connectivity,
inbound-authority configuration, and the latest durable evaluation/commit
result per tenant. A failed or governance-rejected evaluation, or a commit
without exact terminal readback, marks functional health `degraded`;
`/readyz` returns `503`. A later successful result for that operation and
tenant is the only path back to `ok`.
