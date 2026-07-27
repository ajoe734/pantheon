# Nonprod CI/CD

Status date: 2026-06-11

This is the repo-local CI/CD operating record for Pantheon dev and
staging-live.

The current implementation keeps the VM/Compose non-prod topology in the
Benjamin GCP project and uses GitHub Actions for pinned VM deployment:

- CI remains `Pantheon Stage 0 CI`.
- Image publishing remains manual through `Publish images to Artifact Registry`.
- Dev deployment is automatic on `publish/v*` pushes.
- Staging-live deployment is automatic on `master` pushes and can also be run
  manually through the protected `staging-live` GitHub Environment.

## Workflows

| Workflow | File | Trigger | Role |
| --- | --- | --- | --- |
| Pantheon Stage 0 CI | `.github/workflows/stage-0-ci.yml` | PR, push, manual | changed target detection, baseline checks, focused verify, Docker build dry-run |
| Publish images to Artifact Registry | `.github/workflows/gcp-deploy.yml` | manual | GitHub OIDC to GCP, Cloud Build, Artifact Registry tags, build manifest |
| Pantheon Nonprod Deploy | `.github/workflows/nonprod-deploy.yml` | `publish/v*`, `master`, manual | VM checkout-to-commit, compose restart, health/CORS smoke |

## Deployment Script

The workflow uses:

```bash
scripts/deploy_nonprod_vm.sh
```

The script SSHes to the target VM through `gcloud compute ssh`, snapshots the
current human-facing remote checkout, prepares a managed clean deploy
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
Deploy` with `environment=dev`, `component=root`, and `ref=<commit-sha>` when
the target commit must be deployed before the next publish snapshot. The deploy
script still executes on the dev VM through CI-managed `gcloud compute ssh`;
that VM execution is an implementation detail of the CI deploy lane.

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

Agora frontend/BFF deploys must also pass the compatibility manifest gate before
the VM stack is treated as deployable:

```bash
python3 scripts/agora_compat_manifest.py deployment-gate \
  --manifest docs/contracts/agora/dev-compatibility-manifest.json \
  --frontend-root /home/lupin/code/execute-plans \
  --backend-dev-ref refs/remotes/origin/dev \
  --frontend-dev-ref refs/remotes/origin/dev \
  --backend-runtime-commit <exact-target-sha>
```

Use `verify --allow-pending` only as a repo sanity check for a deliberately
non-accepted candidate. Actual dev deployment requires
`compatibility_status=accepted`, exact backend/frontend runtime and handoff
commits reachable from both protected `dev` branches, matching v1.13
bundle/OpenAPI/capability/generated-type hashes, exact handoff bytes, and the
full advertised Agora capability set. The dev workflow performs this check
from a clean protected-`dev` gate-controller checkout and compares the accepted
backend runtime identity to the resolved deployment `TARGET_SHA`. It runs
before the environment lease and deploy command, so a pending, rejected,
tampered, or later arbitrary payload cannot reach the switch path.

Latest verified dev root deploy, 2026-06-11:

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

Dev authenticates through `DEV_GCP_WIF_PROVIDER` and
`DEV_GCP_DEPLOY_SERVICE_ACCOUNT`, with resource-name defaults for the
replacement project. Staging-live continues to use `GCP_WIF_PROVIDER` and
falls back from `GCP_DEPLOY_SERVICE_ACCOUNT` to `GCP_SERVICE_ACCOUNT`.

Recommended GitHub Environments:

- `dev`: no reviewer required; exact-pair admission and the shared environment
  lease remain mandatory before any switch.
- `staging-live`: required reviewers enabled.

## GCP IAM

The deploy identity must be allowed to SSH to the three non-prod VMs:

- `pantheon-lupin-dev`
- `pantheon-lupin-staging-control`
- `pantheon-lupin-staging-exec`

Use a separate deploy service account if possible, then set
`GCP_DEPLOY_SERVICE_ACCOUNT` to that account.

Required permissions depend on the VM SSH posture:

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

Dev promotion is a publish snapshot: push `publish/v*` after the target commit
has passed the required repository checks. Staging-live promotion consumes
`master` or an explicit manually selected ref.

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

To roll back a VM stack, manually dispatch the workflow with the last known good
commit SHA and the affected component.

Examples:

```text
environment=dev
component=root
ref=<last-good-sha>
```

```text
environment=staging-live
component=control
ref=<last-good-sha>
```

Rollback does not change Lovable publish state. If the frontend was promoted,
roll it back through the Lovable project as documented in
`lovable-dev-staging-operating-rules.md`.
