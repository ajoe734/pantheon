# Nonprod CI/CD

Status date: 2026-05-02

This is the repo-local CI/CD operating record for Pantheon dev and
staging-live.

The current implementation keeps the existing VM/Compose topology and adds the
missing GitHub Actions deployment lane:

- CI remains `Pantheon Stage 0 CI`.
- Image publishing remains `Publish images to Artifact Registry`.
- Dev deployment is automatic after push-triggered image publishing succeeds on
  the deployment branch. The current GitHub default branch is
  `backend-dev-publish-20260429`; the workflows also accept `master` and `main`
  while the repo branch naming is being cleaned up.
- Staging-live deployment is manual and should be protected by GitHub
  Environment reviewers.

## Workflows

| Workflow | File | Trigger | Role |
| --- | --- | --- | --- |
| Pantheon Stage 0 CI | `.github/workflows/stage-0-ci.yml` | PR, push to deployment branch, manual | changed target detection, baseline checks, focused verify, Docker build dry-run |
| Publish images to Artifact Registry | `.github/workflows/gcp-deploy.yml` | push to deployment branch, manual | GitHub OIDC to GCP, Cloud Build, Artifact Registry tags, build manifest |
| Pantheon Nonprod Deploy | `.github/workflows/nonprod-deploy.yml` | after image publish, manual | VM checkout-to-commit, compose restart, health/CORS smoke |

## Deployment Script

The workflow uses:

```bash
scripts/deploy_nonprod_vm.sh
```

The script SSHes to the target VM through `gcloud compute ssh`, snapshots the
current remote state, refuses dirty remote checkouts by default, checks out the
requested commit, starts the expected Compose stack, and runs health checks.

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

Automatic dev deploy runs after a push-triggered
`Publish images to Artifact Registry` run completes successfully on the
deployment branch. The current GitHub default branch is
`backend-dev-publish-20260429`; `master` and `main` are also included in the
workflow triggers during the branch cleanup period.
Manual image-publish runs do not auto-deploy dev; use the manual deploy entry
when that is desired.

Target:

- VM: `pantheon-dev-vm1`
- compose project: `pantheon`
- compose file: `docker-compose.yml`
- public BFF: `https://pantheon-dev-bff.35.236.178.81.sslip.io`

Guardrails applied by the deploy script:

```env
PANTHEON_ENV=dev
PANTHEON_LIVE_BROKER_ENABLED=false
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-dev.lovable.app
```

Post-deploy smoke:

- local VM BFF `/health`
- local VM BFF `/readyz`
- public HTTPS BFF `/health`
- CORS preflight for the dev Lovable origin

## Staging-Live Lane

Staging-live deploy is manual through `Pantheon Nonprod Deploy`.

Use:

- `environment=staging-live`
- `component=all` for normal promotion
- `component=exec` only for VM2 execution changes
- `component=control` only for VM1 control/BFF changes
- `ref=<verified commit sha>` for pinned promotion

Target:

- VM1: `pantheon-taiwan`, compose project `pantheon-control`,
  `docker-compose.control.yml`
- VM2: `pantheon-exec-vm2-20260424`, compose project `pantheon-exec`,
  `docker-compose.exec.yml`
- public BFF: `https://pantheon-staging-bff.34.81.225.122.sslip.io`

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
```

`GCP_BUILD_STAGING_BUCKET` is the Cloud Build source staging path used by
`gcloud builds submit`; the current value is
`gs://pantheon-493602-pantheon-builds/source`. If it is absent, the image publish
workflow defaults to that path instead of the absent legacy
`gs://pantheon-493602_cloudbuild` bucket.

`GCP_DEPLOY_PROJECT_ID` is the VM project for `gcloud compute ssh`; the current
VMs live in `pantheon-493602`. If it is absent, the deploy workflow defaults to
`pantheon-493602` so it does not accidentally reuse the shared image/build
project.

If `GCP_DEPLOY_SERVICE_ACCOUNT` is absent, the deploy workflow falls back to
`GCP_SERVICE_ACCOUNT`.

Recommended GitHub Environments:

- `dev`: no reviewer required, because it auto-deploys after publish success.
- `staging-live`: required reviewers enabled.

## GCP IAM

The deploy identity must be allowed to SSH to the three non-prod VMs:

- `pantheon-dev-vm1`
- `pantheon-taiwan`
- `pantheon-exec-vm2-20260424`

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

Dev promotion is automatic only for commits that have reached the default
branch and passed the image-publish workflow.

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
