# Auto Worker Readiness

Supervisor Authority V2 separates configured capacity from observed account
health. No historical provider matrix in this document is runtime truth.

## Authorities

- `agents.<id>.max_parallel` is the sole logical-agent capacity. `0` is the
  configured stop for that lane.
- `providers.<id>.account` is the sole account identity.
- `ready_dispatcher.max_concurrent_per_account` is the sole account cap.
- `ready_dispatcher.max_concurrent_workers` is the fleet-wide cap.
- `worker_slots` describes physical delivery topology; it does not create
  capacity.
- A fresh provider probe may clear a runtime auth/quota pause. A missing or
  stale probe never proves health and never triggers task reassignment.

The retired `disabled_agents`, `max_tasks_per_agent`,
`max_tasks_per_agent_by_agent`, provider account aliases, and
`max_concurrent_per_quota_group` fields are invalid in a running V2 config.

## Dispatch semantics

The planner consumes one canonical task snapshot, one runtime lease/queue
snapshot, and cached provider health. It reserves an intent only when global,
account, agent, lifecycle, assignment, dependency, and duplicate-intent gates
all pass. The delivery queue revalidates those facts immediately before the
only worker-launch call.

Terminal auth, terminal quota, unknown-agent, or configured-zero-capacity
assignments may be changed by the bounded recovery reconciler. Temporary
capacity pressure, probe timeout, stale cache, and ordinary worker failure do
not change task assignment.

Human/Ops may always correct a current owner/reviewer through canonical
`ai-status assign`; repository branch/PR/check governance does not grant or
revoke that runtime authority.

## Verification

```bash
python3 .orchestrator/doctor.py --json --no-write
python3 scripts/supervisor_runtime_health.py --require-watchdog --json
python3 scripts/check_config_drift.py --live-config /path/to/live/config.json --json
python3 scripts/explain_dispatch.py TASK-ID --json
```

Readiness requires all of the following evidence independently:

- watchdog and exact supervisor process identity are live;
- the promoted runtime reports the expected source identity;
- canonical TaskStore head and journal are valid;
- queue and worker leases reconcile without duplicate task generations;
- the target provider/account is not durably paused;
- configured global, account, and agent capacities are nonzero and available.

Dashboard or terminal sessions are observational conveniences, not liveness or
delivery authority.
