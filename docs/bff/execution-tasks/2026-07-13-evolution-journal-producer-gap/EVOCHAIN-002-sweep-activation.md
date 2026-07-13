# EVOCHAIN-002 — Evolution Daily Sweep Activation

Status: review approved; owner closeout revalidated; PR delivery pending

- Owner: Codex
- Reviewer: Claude
- Branch: `task/EVOCHAIN-002`
- PR target: `dev`
- Implementation anchors: `EVOCHAIN-002: anchor default sweep activation` and
  `EVOCHAIN-002: anchor dev sweep proof gate`

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
PATH=/tmp/evochain-002-acceptance-venv/bin:$PATH \
  PYTHON=/tmp/evochain-002-acceptance-venv/bin/python \
  scripts/run-acceptance.sh smoke
git diff --check
```

Results:

- Rendered default services include `evolution-daily-sweep-scheduler` with no
  `COMPOSE_PROFILES` input.
- Focused compose/worker tests: `2 passed in 7.55s`.
- Full evolution service file plus compose contract: `69 passed in 99.23s`.
- Deploy proof contract: `1 passed in 5.66s`.
- Repository smoke acceptance: passed in an isolated Python virtual
  environment. The first system-Python attempt stopped before project tests
  because PEP 668 forbids global package installation; no override was used.
- Shell syntax and diff checks: passed.

## Dev Tick And Proposal Evidence

The task-ref dev root deployment completed successfully:

- GitHub Actions run:
  [Pantheon Nonprod Deploy #29255933370](https://github.com/ajoe734/pantheon/actions/runs/29255933370)
- Requested, checked-out, and remotely prepared commit:
  `a71c35337578af6bdb599cc76a30de49c24a6d08`
- Job window: `2026-07-13T14:05:26Z` through
  `2026-07-13T14:25:20Z`; conclusion `success`.
- The VM deploy, OpenClaw live smoke, public BFF smoke, and Agora restart
  persistence smoke all passed.

At `2026-07-13T14:23:31Z`, the deployment proof gate printed the scheduler's
first successful tick verbatim:

```json
{"result":{"cooldown_blocked":0,"created_decisions":1,"existing_decisions":0,"items":[{"action_type":"flag_for_review","active_decision_id":null,"decision_id":"evo-sweep-inc-87c655c3e3c9","incident_id":"inc-87c655c3e3c9","reason":null,"status":"created","target_id":"artifact-rescue-0260531-1715d8d2","target_type":"candidate_artifact"}],"scanned_incidents":1,"scheduler_attach":{"compose_profile":"evolution-daily-sweep-scheduler","route":"POST /api/evolution/daily-sweep","worker_module":"services.evolution.scheduler_worker"},"skipped_incidents":0,"sweep_id":"scheduled-daily"},"tick":1}
```

The same proof gate read `/api/evolution/sweep-status`:

```json
{"last_success_at":"2026-07-13T14:21:43Z","last_success_proposal_count":1,"last_failure_at":null,"last_failure_reason":null,"total_sweeps_run":1,"total_proposals_created":1,"scheduler_attach":{"route":"POST /api/evolution/daily-sweep","worker_module":"services.evolution.scheduler_worker","compose_service":"evolution-daily-sweep-scheduler","compose_profile":"evolution-daily-sweep-scheduler"}}
```

An authenticated public BFF readback after deployment found the incident still
open, as expected for a proposal-only producer, and found both formal journal
projections. This is the minimal non-secret projection of the response at
`2026-07-13T14:26:15Z`:

```json
{
  "incident": {
    "incident_id": "inc-87c655c3e3c9",
    "status": "open",
    "artifact_id": "artifact-rescue-0260531-1715d8d2",
    "artifact_version": "1.0.0",
    "deployment_stage": "paper"
  },
  "journal": {
    "total_items": 4,
    "new_entries": [
      {
        "id": "mutation_review:evo-sweep-inc-87c655c3e3c9",
        "entry_type": "mutation_review",
        "status": "proposed",
        "action_type": "flag_for_review",
        "created_at": "2026-07-13T14:21:43Z"
      },
      {
        "id": "evolution_decision:evo-sweep-inc-87c655c3e3c9",
        "entry_type": "evolution_decision",
        "status": "proposed",
        "action_type": "flag_for_review",
        "created_at": "2026-07-13T14:21:43Z"
      }
    ],
    "evolution_decisions_source": "ok",
    "mutation_review_source": "ok"
  },
  "decision": {
    "decision_id": "evo-sweep-inc-87c655c3e3c9",
    "linked_incident_id": "inc-87c655c3e3c9",
    "target_type": "candidate_artifact",
    "target_id": "artifact-rescue-0260531-1715d8d2",
    "target_version": "1.0.0",
    "target_stage": "paper",
    "created_by_id": "evolution-daily-sweep",
    "metadata": {
      "source": "evolution_daily_sweep",
      "proposal_only": true,
      "live_mutation_allowed": false,
      "runtime_binding_mutation_allowed": false,
      "broker_order_allowed": false,
      "capital_binding_mutation_allowed": false
    }
  }
}
```

The journal grew from two baseline items to four: one mutation-review and one
evolution-decision projection were added for the deterministic decision. The
aggregate remains degraded only because the unrelated freeze-order and
rollback sources are unavailable; both producer surfaces used by this task
reported `ok`.

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
| Default dev `docker compose up -d` starts the scheduler | Rendered compose service list + contract test + task-ref root deployment | Passed |
| Scheduler tick recorded from dev | Run `29255933370` proof-gate tick and sweep-status output | Passed |
| Open seed incident becomes a proposal | Direct proposal and authenticated BFF journal readback | Passed |
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

- PR: [#3516](https://github.com/ajoe734/pantheon/pull/3516),
  `task/EVOCHAIN-002` into `dev`, with auto-merge enabled.
- Reviewed implementation head: `8086d6118786a775f81483182d604ef58200ea14`.
- Required checks at the reviewed head: Commit trailers, Runtime mirror guard,
  and Smoke acceptance passed. Branch protection will rerun and require the
  same gates on the final closeout head before merge.
- Reviewer decision: Claude approved the task after independently confirming
  default activation, unchanged cadence, proposal-only safety, the intentional
  disable procedure, the deploy proof gate, and the live tick/proposal record.
- Canonical lifecycle state: `review_approved`; merge completion and the final
  delivery SHA are recorded by the generated task archive when the owner runs
  the guarded `done` transition after GitHub merges the PR.
- Dev proof: run `29255933370`, commit
  `a71c35337578af6bdb599cc76a30de49c24a6d08`, passed

Owner closeout revalidation at `2026-07-13T14:47:24Z`, after integrating the
latest `origin/dev`, passed:

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml config --services |
  rg -x 'evolution-daily-sweep-scheduler'
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q \
  services/evolution/test_compose_activation.py \
  services/evolution/test_evolution_service.py::test_daily_sweep_threshold_fixture_creates_evolution_decision \
  services/evolution/test_evolution_service.py::test_daily_sweep_respects_cooldown_for_same_target_after_execute \
  services/evolution/test_evolution_service.py::test_scheduler_worker_posts_daily_sweep_tick
bash -n scripts/deploy_nonprod_vm.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q \
  scripts/test_evolution_daily_sweep_deploy_contract.py
git diff --check origin/dev...HEAD
```

Results: the scheduler is present in the default rendered service list, the
focused evolution slice passed `4` tests in `7.16s`, the deploy contract passed
`1` test, and shell syntax plus diff validation were clean.
