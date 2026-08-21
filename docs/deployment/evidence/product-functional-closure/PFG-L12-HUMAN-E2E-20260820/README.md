# PFG-L12-HUMAN-E2E-20260820: current-dev human-learning proof

This task turns the Loops 5--7 human-learning check into an opt-in
current-dev Compose journey.  It is deliberately an execution proof, not a
component-test substitute: it only calls deployed HTTP owners and restarts
the deployed workers that own replay handling.

## What one run proves

1. A paper-only `training_example` submitted to the deployed Agora BFF becomes
   a durable `DatasetVersion` and handoff.
2. Restarting the deployed `policy-learning-shadow-eval-scheduler` drains that
   handoff, trains the exact candidate from the durable Agora data, and records
   the automatic Research `ExperimentTask` / `ExperimentRun` receipt.
3. Restarting that scheduler again preserves one candidate for the handoff;
   the test never calls the drainer or the legacy scanner endpoint itself.
4. A Consultation request tied to that exact Research run is completed by the
   executor supervised inside `consultation-svc`, via the deployed OpenClaw
   adapter and Governance sink.  Its memo and acknowledged Governance handoff
   are read over the Consultation API.
5. Restarting `consultation-svc` preserves exactly one memo and one acknowledged
   Governance handoff.

All test-created records are paper-only and carry a fresh `l12-hl` run token.
The test does not enable a broker, capital authority, fake provider, local
store, in-process ASGI app, or direct drainer/executor call.

## Current-dev execution

Run this only on the dev host that owns the already deployed Compose project.
It restarts the policy scheduler twice and `consultation-svc` once, so first
confirm that the dev worker restart window is clear.  The deployed OpenClaw
profile and its credentials must already be healthy; the harness does not
substitute a fake provider when they are unavailable.

```bash
PANTHEON_L12_HUMAN_LEARNING_E2E=1 \
PANTHEON_L12_COMPOSE_PROJECT=<current-dev-project> \
PANTHEON_L12_REPORT_PATH=docs/deployment/evidence/product-functional-closure/PFG-L12-HUMAN-E2E-20260820/deployed-run.json \
.venv-pantheon/bin/python3 -m pytest -v \
  tests/integration/l12/test_current_human_learning_deployed_e2e.py
```

The run is accepted only when `deployed-run.json` says `status: passed`, each
case contains the expected owner identity/readback, and the restart readbacks
show exactly one candidate, memo, and Governance handoff for its run token.
Commit the bounded run report and update `evidence.json` with the exact
deployed revision before requesting independent review.

## Current execution blocker (2026-08-21)

This task worktree has no running local Compose project.  A read-only attempt
to reach the declared dev VM could not authenticate: the configured `gcloud`
identity requires interactive reauthentication, and the direct non-interactive
SSH attempt was refused for lack of an authorized public key.  No deployed run
report has therefore been created.  A supervisor or operator with the current
dev-host credentials must run the command above and return the bounded report
to this task branch; do not replace that proof with a fixture or a local
in-process run.

## Code disposition

The existing `policy-learning-shadow-eval-scheduler` and Consultation
supervisor/executor are retained.  The scheduler's automatic path is its
durable Agora handoff intake followed by the leased candidate process cycle.
The scanner-backed `shadow-eval-tick` discovery endpoint remains a
manual/direct endpoint only and is neither imported nor called by this suite;
its retirement is a separately governed code-disposition decision after the
live proof is recorded.
