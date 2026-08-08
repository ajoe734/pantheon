# LIFECYCLE-PROJ-REDUCER-001 — Bounded incremental reducer

Status: depends on `LIFECYCLE-PROJ-STORE-001=done`

| Field | Value |
| --- | --- |
| Owner capability | Antigravity — event sourcing, idempotency, and Python performance |
| Independent reviewer | Human/Ops — lifecycle semantics, recovery, and memory |
| Branch | `task/lifecycle-proj-reducer-001` |
| Worktree | `/tmp/pantheon-worker-worktrees/pantheon/lifecycle-proj-reducer-001` |
| Merge target | `dev` |

## Objective

Extract deterministic per-event reduction and make the active worker mutate
only the current batch's affected relational aggregates.

## Declared artifacts

- `services/trade_journey/lifecycle_projector.py`
- `services/trade_journey/incremental_materializer.py`
- `services/trade_journey/test_lifecycle_projector.py`
- `services/trade_journey/test_canonical_paper_lifecycle_integration.py`
- `docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-REDUCER-001/`

## Acceptance and validation

- Remove all-history `canonical_events`, `identity_chains`, full-state
  `deepcopy`, and full-global `_render()` from the active path.
- Bound memory to the fetch batch, per-batch maps, and affected aggregates.
- Preserve every accepted lifecycle type and fixture, identity, order,
  duplicate/conflict, quarantine, and mode/freshness rule.
- Advance only the highest contiguous durably disposed source sequence.
- Pass property/differential tests plus duplicate, out-of-order, replay,
  backfill, recovery, SIGTERM/SIGKILL, and DB reconnect/rollback faults.
- Deliver a reviewed PR, checks, merge SHA, and checksummed evidence.

## Boundaries, rollout, and rollback

Do not change BFF queries, telemetry ingestion, compose/cutover, or delete legacy
data. Deploy disabled; migration may later enable it only as a target-dev shadow
writer. Rollback stops the new worker and leaves source/projection data intact.
