# LIFECYCLE-PROJ-CAPACITY-001 — Capacity and failure gates

Status: depends on `LIFECYCLE-PROJ-REDUCER-001=done` and
`LIFECYCLE-PROJ-BFF-001=done`

| Field | Value |
| --- | --- |
| Owner capability | Antigravity — Python/Postgres/Docker performance and fault injection |
| Independent reviewer | Human/Ops — capacity, SLO, and rollback evidence |
| Branch | `task/lifecycle-proj-capacity-001` |
| Worktree | `/tmp/pantheon-worker-worktrees/pantheon/lifecycle-proj-capacity-001` |
| Merge target | `dev` |

## Objective

Prove the target design is bounded and recoverable at more than the current
production-like volume before a reader cutover.

## Declared artifacts

- `services/trade_journey/lifecycle_projector_capacity.py`
- `services/trade_journey/test_lifecycle_projector_capacity.py`
- `services/trade_journey/test_lifecycle_projector_compose.py`
- `docker-compose.yml`
- `docs/deployment/evidence/lifecycle-projector/LIFECYCLE-PROJ-CAPACITY-001/`

## Acceptance and validation

- At 1,000,000 events/150,000 loop runs: steady RSS <=2.0 GiB, peak <=2.5
  GiB, and 500k-to-1M growth <=256 MiB.
- Batch-500 p95 <=5 seconds, steady backlog-age p95 <=30 seconds, and current
  baseline plus 100k backlog catch-up <=30 minutes on the documented dev host.
- Page <=200 journey/loop reads meet 300/300/500 ms list/detail/timeline p95 and
  use indexes without unbounded scans.
- SIGKILL, disconnect, deadlock, rollback, second-writer, duplicate, and
  out-of-order cases yield RPO=0 and no duplicate stages.
- Set a 4 GiB target worker limit only after measured gates pass; deterministic
  defects remain fail-stopped.
- Publish reproducible corpus/config, metrics, query plans, reviewed PR, merge
  SHA, and checksummed evidence.

## Boundaries, rollout, and rollback

No business redesign, cutover, production load, or data deletion. Apply resource
defaults only to the disabled/shadow worker. Rollback reverts config and stops
the new worker; never restart the unbounded legacy worker.
