# Paper runtime recovery

Runtime-manager `POST /api/runtimes/deploy` creates bindings. The
paper-fleet-reconciler starts one worker for each admitted active binding and
restarts crashed workers with backoff, up to its configured restart cap.

If a paper runtime reaches that cap, fix the underlying failure, then use the
existing governed `PausePaperRuntime` action. Wait until the reconciler observes
the inactive binding and removes its worker entry before issuing
`ResumePaperRuntime`. Once the binding is active and admitted again, the
reconciler creates a new worker with its restart count reset to zero.

Verify recovery through fresh runtime heartbeats and telemetry projections;
zero trades alone does not indicate a liveness failure. There is no separate
BFF start or runtime-repair control path.

Implementation: `services/paper_fleet_reconciler/paper_fleet_reconciler.py`
(`reconcile_once`, `_start_worker`, `_terminate_worker`).
