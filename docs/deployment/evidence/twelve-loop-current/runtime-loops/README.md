# Current runtime-loop deployed E2E evidence

Task: `L12-CURRENT-E2E-RUNTIME-20260814`

This directory contains bounded evidence for current loops 8 through 12 over
the isolated Compose project `l12currentruntimee2e`. The deployed suite is
`tests/integration/l12/test_current_runtime_loops_deployed_e2e.py`.

The suite crosses only deployed HTTP, Compose, container-inspection, and Redis
boundaries. It proves this identity chain:

```text
approved registry artifact
  -> DeploymentPlan / deployment saga
  -> exact active RuntimeBinding
  -> artifact-hydrated paper signal
  -> binding-scoped paper runtime
  -> paper broker simulated fill / telemetry readback
  -> DriftReport / IncidentCase
  -> proposal-only EvolutionDecision
  -> BFF typed Runtime Manager and fleet-worker health
```

Two fail-closed cases are part of the same run:

- an active binding whose artifact projection lacks `checksum` is degraded
  without adding any signal to its binding-scoped Redis queue;
- stopping the fleet worker makes the typed worker target unhealthy while the
  Runtime Manager API target remains ready, after which the fixture restores
  the worker.

The checked report was produced from exact source and OCI revision
`64e516f748afdd436e39ab284d4426ad7901bcf2`. The isolated stack enabled only
the repository's paper broker simulation. Shioaji sandbox, live broker, real
orders, and real capital remained disabled.

The suite is opt-in. Supply the eleven `PANTHEON_L12_*_URL` owner endpoints,
the exact Compose project/files and source SHA, then run:

```bash
PANTHEON_L12_DEPLOYED_E2E=1 \
PANTHEON_L12_EXPECTED_SHA="$(git rev-parse HEAD)" \
PANTHEON_L12_COMPOSE_PROJECT=l12currentruntimee2e \
PANTHEON_L12_COMPOSE_FILES="$PWD/docker-compose.yml:/path/to/task.compose.override.yml" \
PANTHEON_L12_EVIDENCE_OUTPUT=/tmp/l12-current-runtime-e2e-proof.json \
pytest -q tests/integration/l12/test_current_runtime_loops_deployed_e2e.py -vv
```

Without the opt-in variable, all seven deployed cases skip. The report output
must stay outside `docs/deployment/evidence` during execution; the owner
reviews it for credentials and copies the immutable result here afterward.
