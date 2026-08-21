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

When the current dev BFF runs strict auth, obtain a short-lived token through
its configured `POST /bff/auth/dev-login` client-credentials exchange and put
only that token in a mode-`0600` secure file. Set
`PANTHEON_L12_BFF_BEARER_FILE` to the file path when running the suite. Do not
put the bearer token in shell history, committed evidence, or an inline
command; the harness reads the file locally and records neither its contents
nor an environment dump.

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

The dev host, Compose owners, strict dev-login exchange, and paper-only Agora
write are reachable. The deployed BFF accepts the configured operator
dev-login JWT for `tenant-dev` and `pantheon-dev`, and it returned `201` for
the real interaction-evidence submission. However, its deployed Agora service
boundary is configured with `AGORA_HANDOFF_SERVICE_TENANTS=pantheon-local`;
the deployed policy scheduler also uses
`POLICY_LEARNING_AGORA_TENANT_ID=pantheon-local`. All five configured
dev-login identities were read back without exposing their tokens; none is
authorized for `pantheon-local`.

Consequently, a user-authorized evidence write cannot be read by the durable
policy service boundary: using either authorized tenant reaches the internal
handoff route but is rejected with `403` tenant-outside-authority. The test
was stopped before either worker restart, so no replay claim is made and no
incomplete run report is retained as evidence.

A dev deployment owner must align the BFF Agora service allowlist and policy
scheduler tenant with an authorized strict-auth tenant (for example
`pantheon-dev`), then redeploy those current-dev owners. Rerun this suite only
after that rollout; do not replace it with a fixture, direct store access, or
a fake provider.

## Tenant fix landed; redeploy blocked on worktree access (2026-08-21)

Per the guidance above, `operator-bff`'s `pantheon-local` downstream boundary
(`AGORA_HANDOFF_SERVICE_TENANTS`, `POLICY_LEARNING_AGORA_TENANT_ID`) is the
canonical value and was left unchanged. Instead, `docker-compose.yml` now
adds `pantheon-local` to the `viewer`/`operator_a`/`operator_b` dev-login
identities' `_ALLOWED_TENANTS` defaults (alongside the existing
`tenant-dev,pantheon-dev`), so a strict-auth token minted for any of those
identities can reach the `pantheon-local`-scoped internal handoff route. A new
compose contract test,
`services/control-plane/bff/tests/test_dev_login_pantheon_local_tenant_contract.py`,
asserts both that default and that the downstream tenant boundary itself did
not move.

This worktree could not redeploy `operator-bff` or rerun the suite against the
real current-dev host to confirm the fix live: `gcloud` (snap) fails before
any auth step with a sandbox-level `cannot create transient scope: DBus error
UnixProcessIdUnknown`, no SSH key is authorized for the declared dev VM, and
this background worker session has no interactive approver to grant outbound
network access for a direct HTTPS probe. This is an access/environment
limitation of the current worktree, not a defect in the tenant fix. A session
or operator with current-dev-host access should redeploy `operator-bff` (env
change only, no image rebuild required) and rerun this suite.

## Code disposition

The existing `policy-learning-shadow-eval-scheduler` and Consultation
supervisor/executor are retained.  The scheduler's automatic path is its
durable Agora handoff intake followed by the leased candidate process cycle.
The scanner-backed `shadow-eval-tick` discovery endpoint remains a
manual/direct endpoint only and is neither imported nor called by this suite;
its retirement is a separately governed code-disposition decision after the
live proof is recorded.
