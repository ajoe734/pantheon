# Nonprod CI/CD

Status date: 2026-08-25

This is the repo-local CI/CD operating record for Pantheon dev and
staging-live.

The approved low-resource target plan for replacing unavailable staging,
adding production, and retaining VM/Compose as the primary runtime is
[`vm-dev-staging-prod-management-plan.md`](vm-dev-staging-prod-management-plan.md).
This file remains the current-state operating truth until each target-plan
phase has been implemented and accepted; the target plan must not be read as
proof that staging or production resources already exist.

The current implementation keeps the VM/Compose non-prod topology in the
Pantheon Lupin GCP projects and uses GitHub Actions for pinned VM deployment:

- CI remains `Pantheon Stage 0 CI`.
- Image publishing remains manual through `Publish images to Artifact Registry`.
- Dev deployment is an explicit exact-pair release from both repositories'
  protected `dev` tips. Publish snapshots never deploy.
- Staging-live deployment is automatic on `master` pushes and can also be run
  manually through the protected `staging-live` GitHub Environment.

## Workflows

| Workflow | File | Trigger | Role |
| --- | --- | --- | --- |
| Pantheon Stage 0 CI | `.github/workflows/stage-0-ci.yml` | PR, push, manual | changed target detection, baseline checks, focused verify, Docker build dry-run |
| Publish images to Artifact Registry | `.github/workflows/gcp-deploy.yml` | manual | GitHub OIDC to GCP, Cloud Build, Artifact Registry tags, build manifest |
| Pantheon Nonprod Deploy | `.github/workflows/nonprod-deploy.yml` | manual dev release; `master` or manual staging | exact FE/BFF admission, VM checkout-to-commit, compensated FE/BFF switch, health/CORS smoke |
| Pantheon FE-BFF Integration Gate | `execute-plans:.github/workflows/pantheon-integration-gate.yml` | controller dispatch only for deployable artifacts; PR/push CI remains non-deploying | rebuild and smoke the exact FE SHA against the exact hosted BFF SHA |
| Pantheon Dev FE Deploy | `execute-plans:.github/workflows/pantheon-dev-fe-deploy.yml` | controller dispatch only | authenticate the exact gate artifact, probe the candidate, then atomically switch the hosted FE |

## Deployment Script

The workflow uses:

```bash
scripts/deploy_nonprod_vm.sh
```

For dev, the script SSHes directly to the fixed VM address through
`scripts/dev_vm_ssh.sh`, using the CI-only key and pinned host record from the
protected `dev` GitHub Environment. Staging-live retains `gcloud compute ssh`.
The script snapshots the current human-facing remote checkout, prepares a managed clean deploy
worktree, starts the expected Compose stack from the pinned commit, and runs
health checks. Dev worktrees live under
`~/pantheon-ci-deploy/managed-deploy-worktrees`; the independently configured
staging paths retain their established `~/pantheon-ci-deploy` layout. This
keeps CI deploys from overwriting operator or agent work in the human-facing
checkout (`/home/lupin/pantheon` on the replacement dev VM).

The dev root deployment checkout is
`/home/lupin/pantheon-ci-deploy/managed-deploy-worktrees/dev-root`. It is
deliberately separate from the supervisor's installed command root at
`/home/lupin/pantheon-ci-deploy/dev-root`: deployments detach at the accepted
backend runtime SHA, while the command root remains pinned to the reviewed
`origin/dev` command SHA. The remote deploy controller rejects equality,
parent/child overlap, dot traversal, and symlink aliases before checkout.
Hosted probes derive their path from the same `DEV_DEPLOY_WORKTREE_ROOT`.

The workflow executes the deployment controller from its protected `dev`
controller checkout, not from the older accepted runtime payload. The
controller must advertise `dev-root-isolation-v1` before the workflow can
obtain the environment lease. Runtime source and images still come from the
exact compatibility-manifest-approved backend SHA.

For private repository fetches on the VM, GitHub Actions passes its short-lived
`GITHUB_TOKEN` only to the deploy SSH session. The token is used as a temporary
Git extra header for `git fetch`; it is not written into the VM git config.

Examples:

```bash
# Dev root stack
scripts/deploy_nonprod_vm.sh \
  --environment dev \
  --component root \
  --sha <commit-sha>

# Staging-live full dual-VM stack: VM2 exec first, then VM1 control
scripts/deploy_nonprod_vm.sh \
  --environment staging-live \
  --component all \
  --sha <commit-sha>
```

Emergency flags:

- `--allow-dirty`: permits a dirty remote checkout, but does not reset or
  discard files. Use only for an explicit hotfix.
- `--allow-example-env`: lets staging use `env/*.env.example` when real
  machine-local env files are missing. This is for rehearsal only.

## Dev Lane

The hourly publish cut creates immutable `publish/v*` snapshots but does not
dispatch a dev deployment. A push event produced with `GITHUB_TOKEN` does not
recursively start this workflow, and the publish workflow no longer works
around that suppression. Dev delivery is a separate governed operation:
`nonprod-deploy.yml` must admit the exact Pantheon/execute-plans pair before any
switch. An inadmissible snapshot may remain available for investigation or
promotion history, but must not create a doomed deploy dispatch.

Normal dev delivery enters through GitHub Actions, not through an operator
locally SSHing to the VM and running Compose by hand. Use `Pantheon Nonprod
Deploy` from the workflow's `dev` ref with:

```text
environment=dev
component=root
ref=<exact-current-pantheon-dev-sha>
frontend_sha=<exact-current-execute-plans-dev-sha>
dev_auth_profile=strict
```

The workflow rejects branch names, `main`, older ancestors, task-branch
commits, and any pair that is no longer the two repositories' exact `dev`
tips. The deploy script executes on the dev VM through the same direct-SSH
transport used by its VM-backed acceptance probes. It never falls back to
`gcloud compute ssh`, so a missing key or mismatched host record fails before
the remote Compose transaction starts.

Target:

- VM: `pantheon-lupin-dev`
- GCP project: `pantheon-lupin-dev-20260719`
- compose project: `pantheon`
- compose file: `docker-compose.yml`
- public BFF: `https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io`

Guardrails applied by the deploy script:

```env
PANTHEON_ENV=dev
PANTHEON_LIVE_BROKER_ENABLED=false
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io
```

Agora frontend/BFF deploys generate and pass a candidate-specific compatibility
manifest before the VM stack is treated as deployable:

```bash
python3 scripts/agora_compat_manifest.py write \
  --output <candidate-dir>/release-compatibility-manifest.json \
  --frontend-root /home/lupin/code/execute-plans \
  --backend-dev-ref refs/remotes/origin/dev \
  --frontend-dev-ref refs/remotes/origin/dev \
  --backend-runtime-commit <exact-pantheon-dev-sha> \
  --frontend-runtime-commit <exact-execute-plans-dev-sha> \
  --compatibility-status accepted

python3 scripts/agora_compat_manifest.py deployment-gate \
  --manifest <candidate-dir>/release-compatibility-manifest.json \
  --frontend-root /home/lupin/code/execute-plans \
  --backend-dev-ref refs/remotes/origin/dev \
  --frontend-dev-ref refs/remotes/origin/dev \
  --backend-runtime-commit <exact-pantheon-dev-sha> \
  --frontend-runtime-commit <exact-execute-plans-dev-sha> \
  --evidence-out <candidate-dir>/release-candidate-ledger.json
```

Use `verify --allow-pending` only as a repo sanity check for a deliberately
non-accepted candidate. Actual dev deployment requires
`compatibility_status=accepted`, exact backend/frontend runtime and handoff
commits reachable from both protected `dev` branches, matching v1.13
bundle/OpenAPI/capability/generated-type hashes, exact handoff bytes, and the
full advertised Agora capability set. The dev workflow performs this check
from a clean protected-`dev` gate-controller checkout and compares the accepted
backend and frontend runtime identities to the resolved pair. The deterministic
`release_candidate_id` is the SHA-256 identity of the compatible pair and its
manifest. The workflow also records the currently hosted FE/BFF SHAs and
uploads the ledger, generated manifest, and rollback baseline as one immutable
GitHub artifact before the environment lease or any switch. A pending,
rejected, tampered, stale, `main`-only, or arbitrary payload cannot reach the
switch path.

The accepted release transaction is ordered:

1. Resolve both exact protected `dev` tips.
2. Generate and seal the immutable compatibility ledger and hosted rollback
   baseline.
3. Deploy the exact BFF candidate under the shared environment lease and pass
   BFF health/version/CORS and existing governed smokes.
4. Release that lease, then use
   `scripts/cross_repo_release_controller.py` to dispatch the exact
   execute-plans integration gate.
5. The gate builds `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, with real
   and stub writes disabled, and smokes the candidate against the exact hosted
   BFF before it uploads a deployable artifact.
6. The controller dispatches `pantheon-dev-fe-deploy.yml`; that workflow
   authenticates the ledger-bound artifact, probes it before changing the live
   symlink, and performs its own atomic FE rollback on failure.
7. If either frontend workflow rejects the candidate, Pantheon reacquires the
   shared lease, restores the recorded BFF SHA, verifies `/bff/version`, and
   verifies the hosted FE `deployment.json` is back at the recorded frontend
   SHA. The run remains failed, but uploads
   `pantheon.cross-repo-release-compensation.v1` proof.

There is no deployment trigger on an ordinary fix push, PR merge, dev push, or
publish cut. Multiple repairs compose on `dev`; one explicit controller run
admits and switches one pair.

Historical pre-controller verified dev root deploy, 2026-06-11:

- GitHub Actions run `27357842338`
- ref `0d9fe5864a9b39b1775dcc94da91a54357cdeb9d`
- CI job `Nonprod deploy` completed in `10m17s` with `Deploy requested VM
  stack` and `Public BFF smoke` successful.
- public BFF health/CORS smoke passed

Post-deploy smoke:

- local VM BFF `/health`
- local VM BFF `/readyz`
- public HTTPS BFF `/health`
- CORS preflight for the Pantheon-owned dev FE origin

## Staging-Live Lane

Staging-live deploy is manual through `Pantheon Nonprod Deploy`.

Use:

- `environment=staging-live`
- `component=all` for normal promotion
- `component=exec` only for VM2 execution changes
- `component=control` only for VM1 control/BFF changes
- `ref=<verified commit sha>` for pinned promotion

Target:

- VM1: `pantheon-lupin-staging-control`, compose project `pantheon-control`,
  `docker-compose.control.yml`
- VM2: `pantheon-lupin-staging-exec`, compose project `pantheon-exec`,
  `docker-compose.exec.yml`
- public BFF: `https://pantheon-lupin-staging-bff.104.155.223.192.sslip.io`

Normal full deploy order:

1. VM2 execution stack
2. VM1 control stack
3. VM1 to VM2 runtime-manager reachability
4. public staging BFF health
5. staging Lovable CORS preflight

Staging should have real machine-local env files on the VMs:

```text
env/prod-exec.env
env/prod-control.env
```

Do not place broker/TWS/exchange secrets in GitHub variables, Lovable env vars,
or VM1 control env. VM2 remains the broker-secret boundary.

## Required GitHub Configuration

Repository variables already used by image publishing:

```text
GCP_PROJECT_ID
GCP_WIF_PROVIDER
GCP_SERVICE_ACCOUNT
```

Optional deploy-specific variable:

```text
GCP_BUILD_STAGING_BUCKET
GCP_DEPLOY_PROJECT_ID
GCP_DEPLOY_SERVICE_ACCOUNT
DEV_GCP_DEPLOY_PROJECT_ID
DEV_GCP_WIF_PROVIDER
DEV_GCP_DEPLOY_SERVICE_ACCOUNT
```

After the 2026-07-19 dev-project replacement, dev uses its own project and WIF
defaults (`pantheon-lupin-dev-20260719`) so staging-live remains independent.
The `DEV_GCP_*` variables may override those dev-only defaults without changing
staging-live promotion.

`GCP_BUILD_STAGING_BUCKET` is the Cloud Build source staging path used by
`gcloud builds submit`; the current value is
`gs://pantheon-benjamin-20260528-pantheon-builds/source`. If it is absent, the
image publish workflow defaults to that path instead of the absent legacy
`gs://pantheon-benjamin-20260528_cloudbuild` bucket.

`GCP_DEPLOY_PROJECT_ID` remains the staging-live/shared VM project override.
Dev uses `DEV_GCP_DEPLOY_PROJECT_ID` and defaults to
`pantheon-lupin-dev-20260719`; this prevents a suspended or stale staging
project variable from silently redirecting dev deployment.

Dev VM transport is configured by the protected `dev` GitHub Environment:

- secret `DEV_DEPLOY_SSH_PRIVATE_KEY`: dedicated unencrypted CI private key;
- variable `DEV_DEPLOY_SSH_KNOWN_HOSTS`: pinned OpenSSH host entry for the
  fixed dev address; and
- variable `DEV_DEPLOY_SSH_HOST`: `35.201.204.12`.

The matching public key is installed once in the `lupin` account's
`authorized_keys`. The workflow materializes both files under `RUNNER_TEMP`
with mode `0600`; neither file is written into a checkout. GCP WIF remains for
workflows that actually call Cloud Build, Artifact Registry, or another GCP
API. Staging-live continues to use `GCP_WIF_PROVIDER` and falls back from
`GCP_DEPLOY_SERVICE_ACCOUNT` to `GCP_SERVICE_ACCOUNT`.

`COORDINATION_REPO_TOKEN` is required by the dev environment lease and by the
Pantheon controller's exact workflow dispatches into
`ajoe734/execute-plans`. Its repository access must be limited to the
coordination branch and the two named execute-plans workflows needed by this
transaction.

Recommended GitHub Environments:

- `dev`: no reviewer required; exact-pair admission and the shared environment
  lease remain mandatory before any switch.
- `staging-live`: required reviewers enabled.

## Remote deployment identity

Dev uses its dedicated public key on `pantheon-lupin-dev`; GitHub's deploy
identity does not need Compute Instance Admin or permission to add temporary
SSH metadata. Staging-live still requires its deploy identity to reach:

- `pantheon-lupin-staging-control`
- `pantheon-lupin-staging-exec`

Use a separate deploy service account if possible, then set
`GCP_DEPLOY_SERVICE_ACCOUNT` to that account.

Required staging permissions depend on its VM SSH posture:

- OS Login posture: grant Compute OS Login permissions for the deploy identity.
- Metadata SSH key posture: grant the deploy identity the Compute permissions
  needed by `gcloud compute ssh` to inspect instances and add temporary SSH
  keys.
- Default Compute service account posture: grant the deploy identity
  `roles/iam.serviceAccountUser` on
  `41950751674-compute@developer.gserviceaccount.com`, because `gcloud compute
  ssh` checks access to the service account attached to the VM before adding
  temporary SSH metadata.
- Cloud Build submitter posture: grant `roles/serviceusage.serviceUsageConsumer`
  and write access to the configured build source staging bucket.

Keep this identity narrower than the runtime service accounts. It should deploy
VM compose stacks, not read broker secrets from Secret Manager.

## Promotion Contract

An immutable `publish/v*` snapshot is a promotion input and historical source
identity; it is not a dev deployment. Dev delivery is the explicit exact-pair
controller run described above. Staging-live promotion consumes `master` or an
explicit manually selected ref.

Staging-live promotion is manual:

1. Pick the verified commit SHA.
2. Run `Pantheon Nonprod Deploy` with `environment=staging-live`.
3. Use `component=all` unless deliberately limiting to VM1 or VM2.
4. Let the GitHub Environment reviewer gate approve the run.
5. Confirm the workflow health/CORS smoke passes.
6. Publish or update the staging Lovable project only after backend staging
   health is green.

## Rollback

The deploy script writes a pre-deploy snapshot on each VM under:

```text
~/pantheon-deploy-snapshots/
```

For a rejected paired dev release, rollback is automatic and fail-closed:

- execute-plans must leave or restore the recorded frontend symlink;
- Pantheon restores the recorded BFF SHA under a fresh environment lease;
- both hosted identities must equal the pre-switch baseline;
- the rejected run fails even when compensation succeeds, so it cannot be
  mistaken for an accepted release.

Do not manually dispatch an older dev SHA: the controller deliberately rejects
anything except the current protected `dev` tips. If an already accepted dev
pair must be reverted later, land the revert through normal PRs in both
repositories as needed, then release the new exact `dev` pair. Emergency VM
snapshot recovery remains an operator procedure and must be followed by a
controller release that restores repository/deployment identity agreement.

Staging-live can still be rolled back independently by manually dispatching
`environment=staging-live`, the affected component, and the verified last-good
SHA. Pantheon dev frontend delivery does not use Lovable publish state.
