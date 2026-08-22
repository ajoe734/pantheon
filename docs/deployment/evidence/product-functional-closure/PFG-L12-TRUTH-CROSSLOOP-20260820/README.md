# PFG-L12-TRUTH-CROSSLOOP-20260820 evidence

This task replaces no product owner. It adds the closure gate that launches
the existing deployed Research (Loops 1–4), Human (Loops 5–7), and Runtime
(Loops 8–12) suites in one parent run, then accepts the current Management
loop-health projection captured by that same Runtime run.

## Evidence boundaries

- `test_current_cross_loop_deployed_e2e.py` is retained as a clearly labelled
  prebuilt-ID readback verifier. Its supplied manifest may be older than the
  invocation, so it cannot close this task.
- `test_stimulus_cross_loop_deployed_e2e.py` is the closure gate. It creates a
  new parent run ID, starts all three owner suites, accepts only temporary
  `status=passed` reports at the exact expected SHA, and records each loop's
  trigger, terminal output, authority readback, next receipt, and owner
  observation.
- The Runtime suite reads `/bff/v5/loop-health` through its existing strict
  BFF authentication and passes that same-run readback to the parent gate.
  The static catalog provides only loop/spec/owner contract; each Management
  row's `runtime_maturity` is derived from its current controller record.
- The Runtime suite stops `paper-fleet-reconciler` only in its isolated Compose
  run. While it is unhealthy, it reads BFF loop-health and proves the failure
  is attributed to `capital_pool_execution` (Loop 9), then restores the worker.

No live broker or capital authority is enabled. No product state is seeded,
and no third loop state store is introduced.

## Local verification

```bash
python3 scripts/dev/provision_python_distribution.py
.venv-pantheon/bin/python3 -m pytest -q \
  services/control-plane/bff/test_loop_inventory_read_model_contract.py \
  services/control-plane/bff/test_loop_health_read_model_contract.py
.venv-pantheon/bin/python3 -m pytest -q \
  tests/integration/l12/test_current_cross_loop_deployed_e2e.py \
  tests/integration/l12/test_stimulus_cross_loop_deployed_e2e.py \
  tests/integration/l12/test_current_runtime_loops_deployed_e2e.py
```

## Deployed closure command

Use the existing three domain-suite configuration for the controlled dev-paper
environment, including the Runtime suite's strict BFF credentials and isolated
Compose configuration. The parent passes its expected SHA to Runtime and
consumes Runtime's same-run Management readback; keep the result outside
checked-in evidence:

```bash
export PANTHEON_L12_STIMULUS_CROSS_LOOP_E2E=1
export PANTHEON_L12_STIMULUS_EXPECTED_SHA=<exact-40-character-deployed-sha>
export PANTHEON_L12_STIMULUS_EVIDENCE_OUTPUT=/tmp/pfg-l12-truth-crossloop-run.json
.venv-pantheon/bin/python3 -m pytest -q \
  tests/integration/l12/test_stimulus_cross_loop_deployed_e2e.py
```

After a passed run, copy the atomic temporary report into this directory,
update `evidence.json` with its SHA and exact deployed commit, and obtain an
independent review before requesting task closeout.

## Current external execution hold

The gate must not be pointed at a shared `pantheon` Compose project without a
governed current-dev window: the Runtime portion deliberately stops
`paper-fleet-reconciler`, and the Human portion restarts the policy scheduler
and `consultation-svc`.

The prerequisite `operator-bff` redeploy is now present. On 2026-08-22, a
short-lived strict-auth dev-login readback against current dev SHA
`97945de7c5193baa9832f6c02674714d889577b9` returned 13 inventory rows. Its
12 canonical rows still exposed static `current_maturity`, `target_maturity`,
`maturity`, `evidence`, and `execution_tasks` fields, and did not expose
`runtime_maturity`. This is the expected pre-task baseline, not a closure
pass: the stimulus gate and current-record projection live on task head
`fd0602813ce347f5665b6f43ac98373532bab446`.

Human/Ops must provide an authorized exact-head candidate deployment (or a
governed review/merge path that produces one) before this task can run its
fresh stimulus closure. The task's review policy is `review_before_merge` and
the evidence manifest correctly rejects approval until that exact deployed run
is committed. No shared-worker interruption, substitute authority, or
prebuilt-ID result is used to bridge this gap.
