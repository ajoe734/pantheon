# MGMT-LOAD-005 - BFF Read Concurrency Isolation

Owner: Gemini
Reviewer: Claude2
Parent: `MGMT-GAP-010`
Depends on: `MGMT-LOAD-001`, `MGMT-LOAD-002`

## Problem

Concurrent shell startup reads can delay unrelated BFF routes. A fast isolated
Evidence request is not enough if `/health` and Evidence queue behind alert,
approval, job, or runtime aggregation work.

## Scope

- Add or harden per-route elapsed-time logging for management shell, Evidence,
  alerts, approvals, jobs, and health reads.
- Bound expensive synchronous read-store aggregation with cache, precomputed
  counters, threadpool isolation, or explicit timeout/degraded envelope.
- Keep `/health` independent from management read aggregation.
- Re-run the BFF fanout probe from `MGMT-LOAD-001` against the deployed dev BFF.

## Acceptance

- `/health` p95 <= 200 ms while shell summary and Evidence are concurrently
  requested in the dev probe.
- `/bff/management/evidence` p95 <= 750 ms during shell fanout, or blocker
  evidence identifies the exact backend read path that still queues.
- Expensive degraded paths return explicit degraded metadata instead of hanging
  unrelated routes.
- Tests cover timeout/degraded behavior and confirm `/health` does not depend on
  management read aggregation.
