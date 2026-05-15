# Training And Research Postgres Event Store Pilot

Status: task-scoped pilot for `SVC-POSTGRES-TRAINING-RESEARCH-STORE-PILOT`
Last updated: 2026-04-29

## Scope

This pilot keeps JSON/JSONL as the default single-VM baseline and adds the first optional Postgres event-store path for `training-session`.

The pilot does not enable production research adapters. Research framework adapters remain governed by their own activation gates.

## Migration Slice

| Service | Current store | Proposed Postgres table | Write owner | Read contract | Pilot state |
|---|---|---|---|---|---|
| `training-session` | `teaching_events.jsonl` | `training_session.teaching_events` | `training-session-svc` | Read-only role or `training-session-svc` API | Implemented behind env flag |
| `research` | `research_events.jsonl` | `research_orchestrator.research_events` | `research-orchestrator-svc` | Read-only role or research API | Planned, not enabled |
| `policy-learning` | embedded job `events` in `policy_learning_jobs.json` | `policy_learning.policy_learning_events` | `policy-learning-svc` | Read-only role or policy-learning API | Planned, not enabled |
| `research-worker-gateway` | `worker_events.jsonl` | `research_worker_gateway.worker_events` | `research-worker-gateway-svc` | Read-only role or gateway API | Planned, not enabled |

The mapping follows `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`: shared Postgres cluster is allowed, but each table has exactly one write owner. Non-owner services may read through a read role or owner API, and must not write these event tables directly.

## Training-Session Activation

Default behavior remains JSONL:

```bash
TRAINING_SESSION_EVENT_STORE_BACKEND=jsonl
```

Postgres pilot activation requires an explicit backend and DSN:

```bash
TRAINING_SESSION_EVENT_STORE_BACKEND=postgres
TRAINING_SESSION_EVENT_STORE_DSN=postgresql://training-session-writer@postgres/pantheon
```

Optional table and bootstrap controls:

```bash
TRAINING_SESSION_EVENT_STORE_TABLE=training_session.teaching_events
TRAINING_SESSION_EVENT_STORE_BOOTSTRAP=1
```

When enabled, only the append-only event log moves to Postgres. Session, controls, preview, and replay JSON files remain on the existing local store in this pilot.

## Bootstrap DDL

The service can bootstrap this table when `TRAINING_SESSION_EVENT_STORE_BOOTSTRAP` is not false:

```sql
CREATE SCHEMA IF NOT EXISTS training_session;

CREATE TABLE IF NOT EXISTS training_session.teaching_events (
  append_id BIGSERIAL PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE,
  session_id TEXT NOT NULL,
  event_type TEXT,
  sequence_number INTEGER,
  emitted_at TEXT,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`append_id` preserves replay ordering. `payload` preserves the existing HTTP/event contract while the pilot validates the storage boundary.
