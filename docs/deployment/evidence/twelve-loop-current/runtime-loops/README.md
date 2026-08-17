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

## Default-compose rerun evaluated and found infeasible (2026-08-17)

Task `L12-CURRENT-RUNTIME-DEFAULT-COMPOSE-20260817` asked whether this same
suite could be rerun with `PANTHEON_L12_COMPOSE_PROJECT=pantheon` against the
already-running default `docker-compose.yml` stack instead of the isolated
`l12currentruntimee2e` project, to replace the opt-in evidence above with a
truly closed-loop run. That was evaluated and is **not safe to do as a
routine task action**, so the isolated-compose evidence above remains the
current evidence and this section is the documented policy decision.

Findings:

- `docker compose ls` shows exactly one project named `pantheon`: the
  persistent dev-root stack (51 services, each up for many hours), backed by
  `docker-compose.yml` plus the deploy worktree's copy of the same file. It
  is fleet infrastructure shared by every other task and loop concurrently
  exercising the dev environment, not a disposable project scoped to this
  suite.
- Every case in this suite mutates whichever compose project it targets: it
  creates real `CapitalPool` / `PersonaCapitalBinding` records, mutates and
  approves a real registry artifact, dispatches a real `DeploymentPlan`
  through `deployment`/`deployment-outbox-consumer`, and drives an active
  `RuntimeBinding` through `runtime-manager`. Pointing
  `PANTHEON_L12_COMPOSE_PROJECT` at `pantheon` does not sandbox any of this;
  it writes the same objects directly into the persistent stack's live
  state.
- The `negative_typed_worker_failure` case additionally runs
  `docker compose -p <project> stop paper-fleet-reconciler` and then
  `start` to prove the BFF distinguishes a dead worker from a healthy API.
  Against the `pantheon` project this stops a service that other
  concurrently-running loops and tasks depend on, for the duration of the
  case, with no way to scope the outage to this suite's own traffic.
- `docs/conventions/GIT_WORKFLOW.md` §9.1 ("Shared Deploy Workflow
  Ownership") states the rule this falls under: no task may unilaterally
  disrupt shared fleet infrastructure; exclusivity over it must go through
  the governed dev-environment lease (`scripts/dev_environment_lease.py`),
  never a home-rolled local action. That lease is designed for
  `nonprod-deploy.yml`'s CI-driven `deploy-dev` job, requires
  `PANTHEON_ENVIRONMENT_LEASE_TOKEN`, and this task's worker environment has
  no such token provisioned -- there is no governed mechanism available here
  to hold the `pantheon` project exclusively for a local pytest invocation.

Decision: the isolated Compose project (`l12currentruntimee2e`) remains the
supported way to run this suite, and the existing evidence above stays
canonical. Rerunning it against the default `pantheon` project is not
attempted without a governed exclusivity mechanism that scopes to ad hoc
proof/test runs (not only CI deploy jobs) and without a way to bound the
worker-outage window to this suite's own traffic. Extending
`scripts/dev_environment_lease.py` (or an equivalent) to cover that case is
future work, not part of this task's scope.
