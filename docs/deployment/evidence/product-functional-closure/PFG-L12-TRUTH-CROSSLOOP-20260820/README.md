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

The gate must not be pointed at this worker's shared `pantheon` Compose
project: the Runtime portion deliberately stops `paper-fleet-reconciler`, and
the Human portion restarts the policy scheduler and `consultation-svc`. The
task therefore needs a Human/Ops-owned current-dev window with the required
strict-auth credentials and restart authorization.

As of 2026-08-21, the prerequisite Human proof records that its
`pantheon-local` tenant fix still needs an `operator-bff` redeploy on the
current dev host before its fresh run is possible. The Runtime isolated
Compose harness is safe for Loops 8–12 but cannot substitute for that deployed
Human/OpenClaw proof. No shared-worker interruption or substitute authority is
attempted from this task worktree.
