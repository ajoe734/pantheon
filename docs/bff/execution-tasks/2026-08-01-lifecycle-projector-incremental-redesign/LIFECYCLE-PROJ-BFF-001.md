# LIFECYCLE-PROJ-BFF-001 — Postgres Trade Journey and loop reads

Status: depends on `LIFECYCLE-PROJ-STORE-001=done`

| Field | Value |
| --- | --- |
| Owner capability | Antigravity — BFF, PostgreSQL query, pagination, and RBAC |
| Independent reviewer | Human/Ops — API compatibility, isolation, privacy, and performance |
| Branch | `task/lifecycle-proj-bff-001` |
| Worktree | `/tmp/pantheon-worker-worktrees/pantheon/lifecycle-proj-bff-001` |
| Merge target | `dev` |

## Objective

Serve existing Trade Journey and loop-run contracts through tenant/environment
scoped indexed queries and bounded keyset pagination, behind one disabled backend
flag.

## Declared artifacts

- `services/control-plane/bff/trade_journeys.py`
- `services/control-plane/bff/trade_journey_projection_store.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/main.py`
- the focused Trade Journey, loop sentinel, and projector readiness tests named
  in `tasks.json`
- `docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-BFF-001/`

## Acceptance and validation

- Add scoped page/detail/timeline/graph/resolve/evidence/metrics, loop, and
  controller methods; expose no unscoped global list.
- Preserve routes and DTOs. Default page size is 50 and maximum is 200.
- Integrity-protect page tokens and reject scope/filter/sort reuse or tampering.
- Preserve protected 404, RBAC, tenant/environment isolation, and live-sensitive
  redaction.
- Preserve stale/error/backlog truth and never label JSON fallback as current
  Postgres truth.
- Pass existing contracts, pagination/tamper tests, isolation negatives, and
  indexed bounded-query evidence.
- Deliver a reviewed PR, checks, merge SHA, and checksummed evidence.

## Boundaries, rollout, and rollback

No frontend, worker, cutover, legacy deletion, or unrelated `read_store.py`
refactor. Ship disabled in target-dev. Rollback keeps the previously accepted
reader backend selected.
