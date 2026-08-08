# LIFECYCLE-PROJ-STORE-001 — Relational projection store

Status: ready after catalog merge and supervisor admission

| Field | Value |
| --- | --- |
| Owner capability | Antigravity — backend/data auto-worker; PostgreSQL migrations and transactions |
| Independent reviewer | Human/Ops — database correctness, security, and operability |
| Dependencies | none |
| Branch | `task/lifecycle-proj-store-001` |
| Worktree | `/tmp/pantheon-worker-worktrees/pantheon/lifecycle-proj-store-001` |
| Merge target | `dev` |

## Objective

Create the additive `trade_journey_projection` schema, required indexes, typed
persistence API, and one atomic event-to-checkpoint transaction. Canonical
`telemetry_events` remains unchanged and authoritative.

## Declared artifacts

- `services/trade_journey/projection_store.py`
- `services/trade_journey/migrations/`
- `services/trade_journey/test_projection_store.py`
- `docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-STORE-001/`

## Acceptance and validation

- Implement plan-required controller, receipt, identity, journey, stage,
  loop-run, and quarantine keys/constraints/indexes through expand-only,
  idempotent migrations.
- Do not copy unrestricted raw telemetry payloads into the projection.
- Atomically commit disposition, identity/stage/aggregate mutations, revision,
  and contiguous checkpoint.
- Prove exact duplicate, conflicting duplicate, identity conflict, quarantine,
  rollback/retry, and two-writer advisory-lock behavior against real Postgres.
- Apply the migration twice and prove prior code remains schema compatible.
- Record indexed `EXPLAIN` plans for checkpoint, identity, journey list,
  timeline, and loop list queries.
- Deliver a reviewed PR, required checks, merge SHA, and checksummed evidence.

## Boundaries, rollout, and rollback

Do not change reducer/BFF behavior, telemetry ingestion, compose, or deployment.
Roll out only additive target-dev schema with no consumer enabled. Rollback is
to stop consumers and leave the unused additive schema intact; no destructive
down migration.
