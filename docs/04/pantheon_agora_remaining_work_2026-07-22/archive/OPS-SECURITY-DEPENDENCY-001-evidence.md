# OPS-SECURITY-DEPENDENCY-001 evidence

Date: 2026-07-22  
Repository: `ajoe734/pantheon`  
Merge target: `dev`  
Audited baseline: `52d9ed234af280fc239459bfeddf76886ae35f08`

## Result

The task began with 20 open Dependabot alerts: seven critical, three high,
nine medium, and one low. The candidate graph reconciles all 14 alerts that
still point at Pantheon-owned manifests to fixed pins. The six alerts that
pointed at the retired in-repository frontend mirror were dismissed only after
confirming that the manifest is absent from active `dev`.

The accepted candidate contains no critical/high dependency whose pinned
version is inside its alert range. Research services remain disabled outside
the explicit `dormant-smoke` profile and fail closed at activation boundaries.

## Alert reconciliation

| Alerts | Initial severity | Manifest/package | Candidate disposition |
|---|---|---|---|
| #1, #2, #3, #4, #5, #27, #31, #32 | 3 critical, 2 high, 3 medium | `services/research/mlflow/requirements.txt` / `mlflow` | Pin `3.11.1`; outside every reported range, including the range whose first complete fixed release is 3.11.1. |
| #16, #17, #18, #19, #20 | 3 critical, 2 medium | `services/research/rllib/requirements.txt` / `ray` | Pin `2.54.0`; outside every reported range. Alerts #17 and #19 did not publish a first-patched value, so range evaluation rather than missing metadata determines the result. |
| #37 | low | `services/research/finrl/requirements.txt` / `torch` | Pin CPU build `2.13.0+cpu`; fixed from the reported `<=2.12.1` range. RLlib uses the same fixed CPU build. |
| #22, #23, #24, #25, #26, #28 | 1 critical, 1 high, 4 medium | retired `execute-plans/package-lock.json` / npm | Dismissed as `not_used` with commit/path evidence; not copied back into Pantheon. |

Frontend path evidence:

- Commit `834318190c90962e024ee9b82243cf31a742f441` removed
  `execute-plans/package-lock.json` and is an ancestor of the audited `dev`.
- `origin/dev` has no object at that path. GitHub's promotion-lagged default
  `master` still has the historical mirror, so each dismissal records both the
  removal commit and the authoritative frontend repository
  `ajoe734/execute-plans`.
- The dismissal was limited to alerts #22, #23, #24, #25, #26, and #28. No
  active Pantheon pip alert was dismissed.

The remaining 14 alerts can stay visible in GitHub until its default-branch
dependency graph receives the normal `dev` promotion. The task CI evaluates
their advisory ranges against the checked-out candidate manifests; it does not
equate default-branch scanner lag with a vulnerable `dev` pin.

## Runtime boundaries

### MLflow

- Upgraded to `mlflow==3.11.1`.
- Runs as the unprivileged `pantheon` user with a read-only root filesystem,
  all capabilities dropped, no published port, and `network_mode: none` in the
  dormant profile.
- Defaults to `127.0.0.1`; allowed hosts and CORS are limited to localhost
  patterns. Invalid Host and disallowed CORS requests to functional UI/API
  routes return 403; the no-data `/health` liveness route remains probeable.
- Security middleware cannot be disabled by this entrypoint. Job execution is
  explicitly disabled and a valid job-shaped request is rejected.
- A non-loopback bind is refused unless MLflow basic auth is selected with a
  readable, non-default auth configuration.

### Ray/RLlib

- Upgraded to `ray[rllib]==2.54.0`, `ray[tune]==2.54.0`,
  `gymnasium==1.2.2`, and `torch==2.13.0+cpu`.
- Activation requires `RAY_AUTH_MODE=token` and a token of at least 32
  characters supplied through the environment or a mounted secret file.
- Remote Ray addresses and non-loopback dashboard hosts are refused. Secure
  initialization forces a local node address and disables the dashboard.
- The dormant profile has no network, no ports, a read-only root filesystem,
  all capabilities dropped, and no registry/paper/canary/live/capital write
  credentials.

### FinRL

- Upgraded to `torch==2.13.0+cpu`; the dormant profile has the same no-network,
  read-only, capability-free boundary.
- The default Compose smoke remains an explicit stub, so no upstream/broker
  integration can activate through the dormant profile.

## CI policy

`.github/workflows/dependency-alert-reachability.yml` queries every currently
open alert and binds it to the candidate checkout. The policy fails when a
critical/high alert points to a reachable pin inside the vulnerable range or
when such a reachable version cannot be resolved. Deleted manifests are
reported separately, and the full reconciliation is retained as a workflow
artifact.

## Validation

The following validations passed in this task worktree:

- dependency resolution for MLflow 3.11.1, Ray/RLlib 2.54.0 plus Gymnasium
  1.2.2 and Torch 2.13.0 CPU, and FinRL plus Torch 2.13.0 CPU;
- image builds for MLflow, RLlib, and FinRL and all four dormant Compose
  services;
- MLflow 3.11.1 registry adapter smoke and its 16 focused tests;
- MLflow Host-header, CORS, job-submission, non-loopback refusal, non-root,
  read-only, no-network, and health checks;
- an upstream RLlib PPO iteration with two evaluation episodes under secure
  local Ray initialization (`backend=ray_rllib_ppo`, mean reward 156.5 versus
  baseline 11.0);
- the FinRL image's Torch 2.13.0 and Stable-Baselines3 imports and 22 focused
  tests;
- dormant FinRL, RLlib, and Ray Tune stubs with their deployment/write gates
  closed;
- 4 reachability-policy tests, 7 MLflow boundary tests, 6 Ray boundary tests,
  19 RLlib adapter tests, 16 Ray Tune adapter tests, and 16 registry adapter
  tests;
- `docker compose config --quiet`, workflow/JSON parsing, Python compilation,
  and `git diff --check`.

## Residuals and ownership

- The upstream `finrl==0.3.7` top-level package imports an undeclared broker
  SDK (`alpaca_trade_api`). This task does not add broker credentials or broker
  activation; the dormant stub remains closed. A future FinRL activation must
  resolve and review that optional integration in its own task.
- RLlib 2.54 runs Pantheon's current synchronous action adapter through Ray's
  legacy API-stack compatibility mode. It is functional and tested here, but a
  later migration should adopt the new RLModule/EnvRunner action API before Ray
  removes the compatibility path.
- GitHub repository secret scanning is disabled (API returns 404). The branch
  therefore uses a local changed-content secret scan and records that platform
  configuration limitation rather than claiming server-side coverage.
- Reviewer `Claude` owns acceptance of the fail-closed runtime evidence and
  these residuals before merge.
