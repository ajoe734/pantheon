# OPS-SECURITY-DEPENDENCY-001 evidence

Date: 2026-07-22
Repository: `ajoe734/pantheon`
Merge target: `dev`
Audited baseline: `52d9ed234af280fc239459bfeddf76886ae35f08`

> Supersession notice (2026-07-24): this is point-in-time evidence for the
> advisories visible on 2026-07-22. Dependabot alert `#38`
> (`CVE-2026-41486`) later identified Ray 2.54 as vulnerable. Follow-up task
> `OPS-SECURITY-RAY-2026-41486-001` upgrades the dev graph to Ray 2.55.1.
> Existing Ray 2.54 promotion candidates must not be treated as secure.

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

## Post-merge revalidation

The owner repeated the full dependency/container/security pass after the
initial delivery and its alert-inventory permission follow-up merged:

- PR #3968 merged as `983c2a84b2f4947f848ffbbd0f7f230d6c8d5875`.
- PR #3969 merged as `1f51fc82f918412bd5654a2872bb48df716a4f82`.
- Pull-request run `29946691427` and delivered-`dev` run `29946794630` both
  passed `Reachable critical/high inventory`; the latter also uploaded the
  reconciliation artifact.
- A fresh GitHub API query returned 14 open alerts: six critical, two high,
  five medium, and one low. All eight critical/high alerts resolved as
  `candidate_fixed`; the other six resolved as `below_threshold_fixed`.

Successful focused commands and outcomes:

```text
python3 -m unittest scripts.security.test_dependabot_reachability
(cd services/research/mlflow && python3 -m unittest test_security_boundary)
(cd services/research/rllib && python3 -m unittest test_security_boundary)
(cd services/research/rllib && python3 -m unittest test_adapter test_ray_tune_adapter)
(cd services/registry/experiments && python3 -m unittest test_adapter)
# 68 tests passed in service-isolated processes

python3 -m unittest scripts.test_ci_stage0
# 7 tests passed

docker build -f services/research/mlflow/Dockerfile ...
docker build -f services/research/rllib/Dockerfile ...
docker build -f services/research/finrl/Dockerfile ...
# all full dependency images built; pip check reported no broken requirements
```

The rebuilt images reported MLflow `3.11.1`, Ray `2.54.0`, Torch
`2.13.0+cpu`, RLlib Gymnasium `1.2.2`, FinRL `0.3.7`, FinRL Gymnasium `1.2.3`,
and Stable-Baselines3 `2.8.0`.
All version/import checks ran with no network, a read-only root filesystem,
all capabilities dropped, and `no-new-privileges`.

`docker compose --profile dormant-smoke build` rebuilt MLflow, FinRL, RLlib,
and Ray Tune. The three bounded worker smokes completed with deployment stage
`none` and closed gates. The live MLflow dormant probe proved:

- healthy localhost request: 200;
- disallowed Host and Origin requests: 403;
- root-filesystem write: refused;
- runtime user: non-root; network mode: `none`; no port bindings;
- non-loopback bind without basic auth: refused with exit 78.

The full RLlib image completed one upstream PPO iteration under the secure
local initializer (`backend=ray_rllib_ppo`, mean reward 156.5, random baseline
11.0). Missing token material, a remote Ray address, and a non-loopback
dashboard host each failed before Ray activation. Compose validation, Python
compilation, workflow/JSON parsing, `git diff --check`, and a high-confidence
changed-content secret scan also passed.

## Codex2 reassignment revalidation

After ownership moved from Codex to Codex2, the replacement owner merged
current `origin/dev` at `fb2df8ec805754a3bf7a83ea544138ca9c32c521` into
the evidence branch and independently repeated the acceptance-critical checks.
The prior bare-module terminal report
`unittest.loader._FailedTest.test_adapter` did not reproduce when the two
same-named adapter suites were invoked from their owning service directories,
as required by the repository test layout.

Fresh results on 2026-07-22 UTC:

- 68/68 focused tests passed across Dependabot reachability, MLflow security,
  Ray security, RLlib/Ray Tune adapters, and the registry adapter; the registry
  and RLlib `test_adapter.py` modules ran in separate processes.
- 7/7 Stage-0 matrix tests passed, `docker compose config --quiet` passed,
  Python compilation and workflow/JSON parsing passed, `git diff --check`
  passed, and the changed-content high-confidence secret scan found no match.
- A live GitHub API query returned 14 open alerts: six critical, two high,
  five medium, and one low. Reconciliation produced eight `candidate_fixed`,
  six `below_threshold_fixed`, and zero violations.
- The four dormant Compose images rebuilt successfully. The FinRL, RLlib, and
  Ray Tune bounded smokes stayed on stub backends; RLlib and Ray Tune reported
  `deployment_stage=none` and `gate_state=closed`.
- Separate full RLlib and FinRL Dockerfile builds succeeded. In containers
  with no network, read-only roots, all capabilities dropped, and
  `no-new-privileges`, versions resolved to Ray `2.54.0`, Torch `2.13.0+cpu`,
  RLlib Gymnasium `1.2.2`, FinRL `0.3.7`, FinRL Gymnasium `1.2.3`, and
  Stable-Baselines3 `2.8.0`; MLflow resolved to `3.11.1`. All three images
  returned `No broken requirements found` from `pip check`.
- The full RLlib image repeated one required upstream PPO iteration inside the
  isolated container (`backend=ray_rllib_ppo`, mean reward 156.5, random
  baseline 11.0).
- The live dormant MLflow probe returned 200 for the allowed health request,
  403 for disallowed Host and Origin requests, refused a root-filesystem
  write, and reported non-root user, `network_mode=none`, no port bindings,
  read-only root, all capabilities dropped, and `no-new-privileges`. A
  non-loopback bind without basic auth again failed closed with exit 78.

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

## Owner closeout finalization

Reviewer `Claude` approved the delivered scope and all four residuals in
`OPS-SECURITY-DEPENDENCY-001-review-2026-07-22.md`. The approval is recorded by
commit `5075b15e34197132ace6c0ade1ab829fa1659cf1`; owner `Codex2` accepts that
decision without changing the dependency pins, runtime boundaries, exclusions,
or residual ownership described above.

Immediately before publication of the closeout record, the owner repeated the
acceptance-critical non-container checks on the task branch:

```text
python3 -m unittest scripts.security.test_dependabot_reachability
(cd services/research/mlflow && python3 -m unittest test_security_boundary)
(cd services/research/rllib && python3 -m unittest test_security_boundary)
(cd services/research/rllib && python3 -m unittest test_adapter test_ray_tune_adapter)
(cd services/registry/experiments && python3 -m unittest test_adapter)
docker compose config --quiet
python3 scripts/security/dependabot_reachability.py \
  --alerts-json /tmp/OPS-SECURITY-DEPENDENCY-001-open-alerts.json \
  --fail-on critical --fail-on high
git diff --check
```

Results: 68/68 focused tests passed, Compose configuration and diff checks
passed, and a fresh GitHub API result contained 14 open alerts (six critical,
two high, five medium, one low). Reconciliation returned eight
`candidate_fixed`, six `below_threshold_fixed`, zero violations, and exit 0.
The implementation, evidence, and reviewer-approved closeout records must merge
to `dev` through the task PR before the governed owner `done` transition.
