# L12 reconciliation worker heartbeat evidence

Status: owner evidence ready for independent `Codex` review.

This workstream closes the manifest health/heartbeat gap for:

- `reconciliation-drift-consumer`
- `reconciliation-drift-scheduler`
- `reconciliation-drift-incident-listener`

Each worker now atomically publishes a process-local heartbeat. Its Compose
healthcheck fails closed until the expected worker has completed a tick, rejects
another worker's file, and rejects a stale file. Startup rewrites any retained
heartbeat to `starting`, so a restarted worker cannot inherit a prior green
state.

The heartbeat deliberately separates worker-loop liveness from reconciliation
business truth. A completed tick makes the liveness heartbeat current even when
the worker records `controller_status=degraded|failure|unhealthy`; the
downstream failure and its reason remain visible in the same document. A blocked
or dead loop stops rewriting the file and becomes unhealthy when the configured
freshness window expires.

## Integration boundary

This branch owns the worker source, the shared atomic heartbeat helper, the
three root Compose healthchecks, and focused tests. It does not edit
`.orchestrator/config.json`, change reconciliation authority, alter the
nonprod worker inventory, or claim a hosted deployment.

`L12-MANIFEST-001` should integrate this task PR, then replace its current
six-worker no-healthcheck waiver for these three reconciliation workers with
the accepted commands and environment fields recorded in
[`evidence.json`](evidence.json). Hosted container health and restart proof
remain with `L12-MANIFEST-001` / `L12-HOSTED-001`.

## Owner validation

- Reconciliation-drift plus foundation health suite: `107 passed`, one
  dependency deprecation warning.
- Adjacent worker and Compose regression suite: `10 passed`.
- Initial focused heartbeat/Compose/resilience suite: `11 passed`.
- `docker compose config --quiet`: exit 0.
- Resolved Compose JSON contains all three expected healthcheck commands.
- A real CLI probe ran every worker for one tick against deliberately
  unavailable local endpoints, then invoked each healthcheck as a separate
  process. All three healthchecks exited 0, while the heartbeat documents
  retained their exact downstream failure and worker identity.

This is owner evidence only. Independent reviewer approval is not asserted
until `Codex` appends an approved review record and the governed task state
binds this exact manifest.
