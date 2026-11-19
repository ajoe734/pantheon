# SD: Dev Deploy / Acceptance Split (2026-11-17)

Status: design for work package RC1 of
`docs/operations/remediation-plan-20261117.md`. The governing decision is in
`AGENTS.md` ("Dev Deployment: Deployed Versus Accepted"), approved by the
operator on 2026-11-17. This document applies to the dev environment only. The
workflow has no staging or production entry, so no per-environment branch is
needed.

## 1. Problem

Between 2026-11-10 and 2026-11-17 `Pantheon Nonprod Deploy` ran 51 dev releases
and 10 succeeded. Failures were spread over 15 steps. A successful run takes 67
minutes. Hosted is 205 commits behind `dev`.

The pipeline treats functional checks as release gates. Any of them failing
rolls the release back, and the rollback can itself leave the stack incomplete
(2026-11-16 19:19Z: `source-ingest-scheduler` left stopped for more than six
hours while every acceptance check stayed green).

## 2. Findings that shape the design

All references are to `origin/dev` at ede3b8035.

1. The automatic deployer turns most functional gates on. It dispatches
   `run_loop_prod_tel_002_probe: "true"` (`scripts/auto_deploy_dev_pair.py:207`),
   which sets `PAPER_BOOTSTRAP_REQUESTED` (`nonprod-deploy.yml:204`) and thereby
   enables the Taiwan market refresh, the TAIFEX refresh, the governed paper
   baseline and the canonical paper lifecycle probe.
2. `continue-on-error` cannot make a lease-guarded step advisory. The pinned
   lease guard stops the heartbeat and writes a failure file whenever its
   command exits non-zero; every later guarded step then fails and the lease is
   quarantined (`nonprod-deploy.yml:2868-2872`). An advisory check must exit 0
   and write its real result to an evidence file. The TAIFEX `--non-blocking`
   mode is the existing pattern (`deploy_nonprod_vm.sh:633`, `:1221`).
3. The workflow refuses any backend or frontend commit that is not the branch
   tip (`nonprod-deploy.yml:277-281`, `:359-371`).
4. `ROOT-COMPENSATION-STEADY-SERVICES-20261117` (PR #7481) added
   `steady_service_drift` and `--steady-state-file` to
   `fetch_dev_artifact_evidence.py`, but the workflow call
   (`nonprod-deploy.yml:4972-4977`) does not pass the file, and the baseline is
   still derived from what was running at capture. A steady service that was
   down at capture is absent from the baseline, so restore stops the
   candidate's copy and the readback passes without it.
5. Five readers require `deploymentState` to be `accepted`
   (`nonprod-deploy.yml:532`, `:4018`; `auto_deploy_dev_pair.py:146`;
   `dev_release_artifacts.py:1165`; `fetch_dev_artifact_evidence.py:328`). A new
   state value would make the next deploy reject its own baseline.
6. The 14 failures at "Enforce accepted or proven compensated outcome" are
   verdicts of the frontend gate dispatched to `execute-plans`
   (`coordinate-dev-release` steps 4 and 5).

## 3. Design

### 3.1 What a dev deploy is

Blocking, because it protects the stack:

- admission and identity, the environment lease, the prior-artifact baseline
  and candidate seal;
- build, migration bootstrap, `compose up`, `/readyz` at the exact SHA, source
  SHA, the auth gate, ingress;
- every expected steady service running healthy at the expected image and
  revision (3.3);
- the public BFF smoke (health, exact version, CORS);
- frontend build, artifact authentication, atomic switch and final verify.

Only a failure in this list rolls back.

### 3.2 What moves to acceptance

Advisory for the deploy, run after the switch:

- workflow steps: Taiwan market refresh after candidate deploy, governed paper
  baseline, evolution dispatch probe, canonical paper lifecycle probe, Agora
  restart persistence smoke;
- in-script checks: `verify_dev_paper_fleet`, `verify_dev_evolution_daily_sweep`,
  `verify_trade_journey_residual_dev.sh`, `verify_bounded_source_refresh_readback`;
- the hosted functional checks inside the `execute-plans` integration gate.

A failure does not roll back and does not fail the deploy job.

### 3.3 Stack completeness

The expected steady set is derived from `docker-compose.yml` for the selected
profiles (the same resolution `validate_required_loop_workers` uses), not from
what happens to be running. For the `root` profile that is every service with
`restart: unless-stopped`. `verify_exact_component_deployment` covers 31 of
them today; it is widened to the whole set.

The same check runs after a deploy, after the in-script rollback, after
workflow compensation and after `restore-accepted-baseline`. A missing or
wrong-version steady service is drift, whatever `/bff/version` says.

### 3.4 Recording the result

`deploymentState=accepted` keeps its current meaning for the five readers: the
deployed pair of record. The acceptance verdict is stored separately as
`acceptance` (`pending`, `passed`, `failed`, with the failing checks and the
evidence run) in the hosted deployment manifest and in the run evidence. Human
reporting uses the delivery states in `AGENTS.md`: a release is `accepted` only
when `acceptance=passed`.

### 3.4a Frontend gate failure for an unchanged frontend (2026-11-19)

On dev, when the candidate's frontend commit equals the frontend commit the hosted stack already serves (the accepted baseline) and the frontend integration gate concludes failure, the release is not rejected: the candidate backend stays served, the served frontend stays the baseline build, no frontend switch runs and no compensation runs. The controller tolerates the failure only when the candidate's release compatibility manifest (the digest-bound `release-compatibility-manifest.json`, checked with the existing `validate_deployment_rules` for this exact backend/frontend pair) states the pair compatible; a manifest that is missing, unreadable, off-digest or not compatible rejects and compensates as before, as does a changed frontend, an unhealthy stack, or any non-dev target (staging and production keep gate-before-switch). The release record carries `outcome=deployed_not_accepted` with the gate run id, the failing gate names and the owning chain `frontend`, and the acceptance verdict is `failed` with a `gate:frontend-integration` entry owned by `frontend`, so the release is deployed, not accepted, and the frontend chain owns the follow-up task. Instance 2026-11-19: Root 44466874746 (backend fd4903dcc, frontend 0e886b9ce, the commit hosted already served and that had passed this gate 11 times) failed Gate 1 (`npm run test`) in execute-plans run 44470794822 on a browser-install race and was rolled back to 08d748790, leaving four persona-loop fixes undeployed; under this rule that release stays deployed and is not accepted.

### 3.5 Acceptance stage

A job after `coordinate-dev-release` runs the moved checks against the deployed
pair. Checks that mutate hosted state (the Agora smoke and the trade-journey
residual check restart `operator-bff`; the paper baseline creates a release
case) take the qualification lease the hosted-chain job already uses
(`nonprod-deploy.yml:4100`). The job writes the verdict of 3.4 and always
finishes without failing the run; the deploy success rate therefore measures
deploys, and the verdict measures acceptance.

Feature-chain acceptance probes (remediation plan RC2) plug into this stage.

### 3.6 Candidate selection

The automatic deployer selects, for each repository, the newest commit that has
a successful dev-push Branch CI run, is an ancestor of the tip and is ahead of
the served commit. The workflow accepts such a commit instead of requiring the
tip. Deploy scripts keep coming from the payload commit, as today.

## 4. Delivery order

Each step is useful alone. Steps 2 to 5 edit `nonprod-deploy.yml` or
`deploy_nonprod_vm.sh` and are therefore serialised.

1. The automatic deployer stops sending the probe input. The four gates it
   enabled no longer run inside automatic deploys.
2. Advisory mode for the remaining in-deploy functional checks and removal of
   those steps from the compensation and `complete_success` conditions.
3. Stack completeness (3.3) and wiring `--steady-state-file`.
4. Candidate selection (3.6).
5. Acceptance stage and verdict (3.4, 3.5).
6. In `execute-plans`: hosted functional checks of the integration gate become
   advisory for the dev release verdict.

Until step 5 lands, automatic releases have `acceptance=pending` and are
reported as `deployed`, never `accepted`.

## 5. Tests and documents that pin the current shape

Tests: `scripts/test_dev_environment_lease_deploy_contract.py`,
`scripts/test_nonprod_dev_artifact_workflow.py`, `scripts/test_deploy_nonprod_vm.py`,
`scripts/test_auto_deploy_dev_pair.py`, `scripts/test_cross_repo_release_controller.py`,
`scripts/test_nonprod_restore_accepted_baseline.py`,
`scripts/test_fetch_dev_artifact_evidence.py`,
`scripts/test_compensation_restores_steady_services.py`,
`scripts/test_deploy_nonprod_artifact_restore.py`,
`tests/test_release_gate_contract.py`.

Documents to amend with each step: `docs/deployment/nonprod-ci-cd.md`
(lines 151-152, 171-173, 187-189, 273-274, 284-288),
`docs/deployment/dev-exact-artifact-rollback.md` (193-196, 228-233),
`docs/deployment/vm-dev-staging-prod-management-plan.md` (126-127).

## 6. Targets

At least 90% of dev deploys succeed, a deploy takes at most 25 minutes, and
hosted is at most one day behind `dev`.
