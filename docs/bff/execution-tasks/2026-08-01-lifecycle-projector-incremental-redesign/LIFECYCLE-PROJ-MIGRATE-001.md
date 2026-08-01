# LIFECYCLE-PROJ-MIGRATE-001 — Backfill, shadow, and parity

Status: depends on `LIFECYCLE-PROJ-REDUCER-001=done` and
`LIFECYCLE-PROJ-BFF-001=done`

| Field | Value |
| --- | --- |
| Owner capability | Antigravity — replay, data migration, reconciliation, and evidence |
| Independent reviewer | Human/Ops — migration safety, parity, and recovery |
| Branch | `task/lifecycle-proj-migrate-001` |
| Worktree | `/tmp/pantheon-worker-worktrees/pantheon/lifecycle-proj-migrate-001` |
| Merge target | `dev` |

## Objective

Backfill the relational model from canonical source rows, close the live delta,
and prove deterministic old/new parity without serving the new store.

## Declared artifacts

- `services/trade_journey/projection_migration.py`
- `services/trade_journey/test_projection_migration.py`
- `scripts/lifecycle_projector_migrate.py`
- `scripts/lifecycle_projector_parity.py`
- `docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-MIGRATE-001/`

## Acceptance and validation

- Use bounded resumable transactions and a separate durable migration
  watermark; never mutate `telemetry_events`.
- Capture a source watermark, backfill, close the delta, and reach shadow-live
  backlog zero.
- Compare controller, identity, stage, journey, loop, quarantine, duplicate,
  replay, and recovery semantics with stable scoped hashes and drill-down.
- Classify every difference; unexplained mismatch count must be zero.
- Prove interruption/restart and source growth during backfill without skipped
  rows or duplicate stages.
- Keep reports redacted and deliver a reviewed PR, checks, merge SHA, parity
  manifest, and checksummed evidence.

## Boundaries, rollout, and rollback

No BFF cutover, production migration, source mutation, or legacy deletion. Run
only in target-dev shadow mode. Rollback stops jobs and leaves the accepted
reader unchanged.
