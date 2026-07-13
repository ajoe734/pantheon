# EVOCHAIN-002 — Evolution Daily Sweep Activation

Status: implementation anchored; live dev proof pending

- Owner: Codex
- Reviewer: Claude
- Branch: `task/EVOCHAIN-002`
- PR target: `dev`
- Activation anchor: `7a04ca7fe`
- Deploy-proof anchor: `78defbfda`

## Scope

This task makes `evolution-daily-sweep-scheduler` a default root-compose
service on dev and records one scheduler tick that converts the existing open
seed incident into a governed `EvolutionDecision` proposal.

It does not change the scheduler cadence, threshold policy, cooldown rules,
proposal-only safety boundary, or approval/execution semantics. The existing
environment default remains `EVOLUTION_SCHEDULER_INTERVAL_SECONDS=86400`.

## Implementation

- Removed the scheduler's `profiles` gate from `docker-compose.yml`.
- Added a compose contract test that requires the scheduler to have no profile,
  retain `restart: unless-stopped`, wait for a healthy evolution service, and
  preserve the 86400-second default.
- Added a dev root deployment proof gate that waits for the scheduler's JSON
  tick log and validates `/api/evolution/sweep-status` reports a successful
  sweep. Failure diagnostics now include scheduler logs.
- `services/evolution/scheduler_worker.py` was inspected but intentionally not
  changed: it already runs the first tick immediately and only then sleeps for
  the configured interval.

## Pre-deploy Hosted Baseline

Read-only hosted checks at `2026-07-13T13:41:58Z` established the before state.
Authentication values are intentionally omitted from this record.

```bash
curl -fsS -H 'Authorization: Bearer <read-token>' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/incidents
curl -fsS -H 'Authorization: Bearer <read-token>' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/management/evolution-journal
```

- Incidents: exactly one item, `inc-87c655c3e3c9`, status `open`, target
  `artifact-rescue-0260531-1715d8d2@1.0.0`, paper stage.
- Evolution Journal: exactly two items, both projections of the older seed
  decision `evo-vslice-1`; neither is linked to the target incident.
- The old seed decision targets `tw-momentum-vslice@v1`, so it does not collide
  with the incident target under the single-active-decision rule.
- The deterministic expected decision ID is
  `evo-sweep-inc-87c655c3e3c9`.

## Local Verification

The following checks passed on `task/EVOCHAIN-002`:

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml config --services
python3 -m pytest \
  services/evolution/test_compose_activation.py \
  services/evolution/test_evolution_service.py::test_scheduler_worker_posts_daily_sweep_tick -q
python3 -m pytest \
  services/evolution/test_compose_activation.py \
  services/evolution/test_evolution_service.py -q
bash -n scripts/deploy_nonprod_vm.sh
python3 -m pytest scripts/test_evolution_daily_sweep_deploy_contract.py -q
git diff --check
```

Results:

- Rendered default services include `evolution-daily-sweep-scheduler` with no
  `COMPOSE_PROFILES` input.
- Focused compose/worker tests: `2 passed in 7.55s`.
- Full evolution service file plus compose contract: `69 passed in 99.23s`.
- Deploy proof contract: `1 passed in 5.66s`.
- Shell syntax and diff checks: passed.

## Dev Tick And Proposal Evidence

Pending the task-ref dev root deployment. Before review handoff, replace this
paragraph with:

- GitHub Actions deployment run URL and deployed commit SHA;
- raw `evolution-daily-sweep-scheduler` tick JSON from the deploy log;
- `/api/evolution/sweep-status` success payload;
- proposal readback for `evo-sweep-inc-87c655c3e3c9`;
- authenticated BFF journal readback for the linked formal entry.

The expected successful first-tick item is:

```text
incident_id=inc-87c655c3e3c9
decision_id=evo-sweep-inc-87c655c3e3c9
status=created
decision_state=proposed
metadata.source=evolution_daily_sweep
metadata.proposal_only=true
```

## Intentional Disable And Re-enable

Temporarily stop the scheduler without changing cadence configuration:

```bash
docker compose -p pantheon -f docker-compose.yml stop evolution-daily-sweep-scheduler
```

For a broad compose reconciliation that must keep it disabled, explicitly
scale it to zero on that invocation:

```bash
docker compose -p pantheon -f docker-compose.yml up -d \
  --scale evolution-daily-sweep-scheduler=0
```

An ordinary later `docker compose up -d` intentionally restores the default.
To re-enable it immediately and run a new first tick:

```bash
docker compose -p pantheon -f docker-compose.yml up -d evolution-daily-sweep-scheduler
```

Do not use an interval of zero as a disable switch; the worker rejects values
below one second.

## Acceptance Matrix

| Criterion | Evidence | State |
|---|---|---|
| Default dev `docker compose up -d` starts the scheduler | Rendered compose service list + contract test | Passed locally |
| Scheduler tick recorded from dev | Deployment proof gate output | Pending deploy |
| Open seed incident becomes a proposal | Direct proposal and BFF journal readback | Pending deploy |
| Intentional disable is documented | Commands above | Passed |

## Residual Risks

- The worker exits on transport or invalid-response errors and relies on
  `restart: unless-stopped` for retry. Owner: Evolution service. Review by
  `EVOCHAIN-010` producer-chain verification.
- `/api/evolution/sweep-status` is process-memory state and resets with the
  evolution service. The container tick log and durable proposal are the
  primary evidence. Owner: Evolution service. Review by `EVOCHAIN-010`.
- Existing `scheduler_attach.compose_profile` response metadata retains a
  legacy field name even though the gate is gone; it is not used as activation
  evidence. Owner: Evolution service contract. Resolve or explicitly retain
  before `EVOCHAIN-010` closeout.
- The journal aggregate remains degraded until the unrelated freeze/rollback
  store tasks land; the `evolution_decisions` source itself is live and healthy.
  Owners: `EVOCHAIN-004` and `EVOCHAIN-005`.

## Review And Delivery

- PR: pending
- Required checks: pending
- Merge SHA: pending
- Reviewer decision: pending
