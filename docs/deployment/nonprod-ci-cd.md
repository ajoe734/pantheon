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
current human-facing remote checkout, prepares a managed clean deploy worktree
under `~/pantheon-ci-deploy`, starts the expected Compose stack from the pinned
commit, and runs health checks. This keeps CI deploys from overwriting operator
or agent work in `/home/lupin/code/pantheon`.

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

Automatic dev deploy runs when a `publish/v*` snapshot branch is pushed. Manual
image-publish runs do not auto-deploy dev; use the manual deploy entry when
that is desired.

Normal dev delivery enters through GitHub Actions, not through an operator
locally SSHing to the VM and running Compose by hand. Use `Pantheon Nonprod
Deploy` with `environment=dev`, `component=root`, and `ref=<commit-sha>` when
the target commit must be deployed before the next publish snapshot. The deploy
script still executes on the dev VM through CI-managed `gcloud compute ssh`;
that VM execution is an implementation detail of the CI deploy lane.

Target:

- VM: `pantheon-lupin-dev`
- compose project: `pantheon`
- compose file: `docker-compose.yml`
- public BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`

Guardrails applied by the deploy script:

```env
PANTHEON_ENV=dev
PANTHEON_LIVE_BROKER_ENABLED=false
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
```

Latest verified dev root deploy, 2026-06-11:

- GitHub Actions run `27352642439`
- ref `2a9bf5891d6c29b26a533fca3b9dd295feeca386`
- CI log line: `deployment complete: dev/root 2a9bf5891d6c29b26a533fca3b9dd295feeca386`
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
```

`GCP_BUILD_STAGING_BUCKET` is the Cloud Build source staging path used by
`gcloud builds submit`; the current value is
`gs://pantheon-benjamin-20260528-pantheon-builds/source`. If it is absent, the
image publish workflow defaults to that path instead of the absent legacy
`gs://pantheon-benjamin-20260528_cloudbuild` bucket.

`GCP_DEPLOY_PROJECT_ID` is the VM project for `gcloud compute ssh`; the current
VMs live in `pantheon-benjamin-20260528`. If it is absent, the deploy workflow
defaults to `pantheon-benjamin-20260528`.

If `GCP_DEPLOY_SERVICE_ACCOUNT` is absent, the deploy workflow falls back to
`GCP_SERVICE_ACCOUNT`.

Recommended GitHub Environments:

- `dev`: no reviewer required, because it auto-deploys after publish success.
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
