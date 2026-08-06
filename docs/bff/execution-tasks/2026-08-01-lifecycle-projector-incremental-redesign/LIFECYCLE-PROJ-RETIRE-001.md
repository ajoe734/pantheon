# LIFECYCLE-PROJ-RETIRE-001 — Guarded legacy retirement

Status: depends on `LIFECYCLE-PROJ-CUTOVER-001=done`, seven accepted dev days,
and explicit post-dry-run operator approval

| Field | Value |
| --- | --- |
| Owner capability | Antigravity — compatibility cleanup and safe data retirement |
| Independent reviewer | Human/Ops — retention, recovery, and destructive-action approval |
| Branch | `task/lifecycle-proj-retire-001` |
| Worktree | `/tmp/pantheon-worker-worktrees/pantheon/lifecycle-proj-retire-001` |
| Merge target | `dev` |

## Objective

Remove the legacy JSON writer/reader paths after accepted soak, then separately
retire obsolete dev generation files through an exact-path dry-run and approval.

## Declared artifacts

- `services/trade_journey/lifecycle_projector.py`
- `services/control-plane/bff/trade_journeys.py`
- `services/control-plane/bff/read_store.py`
- `docker-compose.yml`
- `scripts/lifecycle_projector_legacy_retire.py`
- `docs/operations/lifecycle-projector-incremental-runbook.md`
- `docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-RETIRE-001/`

## Acceptance and validation

- Prove seven days of accepted dev freshness, capacity, parity, security, and
  error evidence.
- Remove legacy code/config through a reviewed PR while preserving BFF contracts.
- Generate an allow-listed dry-run inventory and checksum manifest; reject broad
  paths, globs, unresolved variables, source events, and projection tables.
- Obtain explicit Human/Ops approval after the dry-run before moving/deleting
  any legacy dev files and record recoverability and deletion receipt.
- Prove Postgres-only hosted restart/readback and update rollback/runbook/risk
  documentation.
- Deliver required checks, independent review, merge SHA, and checksummed
  evidence.

## Boundaries, rollout, and rollback

No `telemetry_events` or relational projection deletion, production cleanup, or
early cleanup. Remove code/config first; data cleanup is a separate approved
step. Application rollback uses the schema-compatible prior reader. If files
were moved, recover only from the recorded quarantine/archive path.
