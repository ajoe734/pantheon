# Ephemeral Staging Runbook (`ENV-STG-EPHEMERAL-IMPL-001`)

- Status: Source implementation & local controller/host-bootstrap/gate-collector validation complete; hosted execution `external_wait`
- Canonical Design: [`docs/deployment/vm-dev-staging-prod-management-plan.md`](vm-dev-staging-prod-management-plan.md) § 9 & [`docs/04/pantheon_environment_closure_sa_sd_2026-09/SD.md`](../04/pantheon_environment_closure_sa_sd_2026-09/SD.md)
- Readiness Baseline: [`docs/deployment/first-production-release-readiness.md`](first-production-release-readiness.md)

> [!IMPORTANT]
> **Functional vs. Hosted Boundary**: Merging these scripts, schemas, templates, and GitHub Actions workflows does **not** mutate any cloud resource or GitHub Environment, and local/simulated controller tests do **not** count as hosted staging drill evidence. Historical PRs `#6758` (`5dc101e9d1b943d9411a2c5156f7163f0f36bf01`) and `#6764` (`e15d6390b7d72638641fc314bc48a105a4be1a0e`) are preserved as source history only. Hosted acceptance (`completion_tracks.hosted`) remains `external_wait` until an accepted `dev_verified` release manifest from `Root01a11`, refreshed GCP operator authentication, and two consecutive zero-to-zero live staging drills pass.

---

## 1. Architecture & Safety Invariants

1. **Zero Idle Staging VMs**: Staging exists only during release validation. Every GCE resource (`address`, `disk`, `instance`) carries valid GCP cloud labels (`^[a-z0-9_-]{1,63}$`: `environment=staging`, `release_id`, `owner`, `workflow_run_id`, compact UTC `created_at`/`expires_at`, `created_at_epoch`, `expires_at_epoch`, `cleanup_policy=automatic`) and a bounded 2-hour TTL (`7200s` default, `86400s` absolute cap). Standard Debian 12 `e2-standard-2` non-Spot VM sizing (`debian-12-bookworm-v20261023`, `application_installed=false`) matches the provisioned instance template `pantheon-stg-template-20261024`.
2. **IAP-Only SSH/SCP & Safe Remote Staging**: The staging VPC (`pantheon-staging` / `pantheon-staging-asia-east1`) permits SSH (`tcp:22`) exclusively from Google IAP (`35.235.240.0/20`) to target tag `pantheon-iap`. [`scripts/deploy/staging_ops.sh`](../../scripts/deploy/staging_ops.sh) passes `--tunnel-through-iap` on every `gcloud compute ssh` and `gcloud compute scp` invocation, and creates `/tmp/pantheon-staging-bundle` (`0700`) over IAP SSH before transferring the release bundle via IAP SCP.
3. **Separate WIF Deploy Identity vs. VM Runtime Identity (Zero Project-IAM Mutation)**:
   - GitHub Actions Workload Identity Federation authenticates as the deployer service account `pantheon-stg-github-deploy@pantheon-stg-20260902.iam.gserviceaccount.com` (`STAGING_GCP_DEPLOY_SERVICE_ACCOUNT` / `STAGING_DEPLOY_SERVICE_ACCOUNT`).
   - Ephemeral staging VMs run as the least-privilege runtime service account `pantheon-stg-runtime@pantheon-stg-20260902.iam.gserviceaccount.com` (`STAGING_RUNTIME_SERVICE_ACCOUNT` / `STAGING_SERVICE_ACCOUNT`).
   - [`scripts/deploy/staging_provision.sh`](../../scripts/deploy/staging_provision.sh) rejects attaching any deployer identity to the VM (`DEPLOYER_SA_FORBIDDEN_ON_VM`), verifies `serviceAccounts` on the created VM, and performs zero project-level IAM mutations (`projects add-iam-policy-binding` is never called).
4. **Bare-Debian Bootstrap, Digest-Pinned Image Acquisition, & Full `/bff/readyz` Owner Closure**:
   - [`deploy/staging/bootstrap.sh`](../../deploy/staging/bootstrap.sh) bootstraps bare Debian 12 (`docker.io`, `docker-compose-plugin`, `caddy`, `postgresql-client`, `curl`, `util-linux`), installs [`deploy/staging/pantheon-staging-collect-gates`](../../deploy/staging/pantheon-staging-collect-gates) to `/usr/local/bin/pantheon-staging-collect-gates`, mounts the sanitized data disk, and validates `0400`/`0600` isolated secrets (`STAGING_SECRETS_ENV_FILE` and `STAGING_POSTGRES_PASSWORD_FILE`).
   - `bootstrap.sh` pre-pulls all digest-pinned (`@sha256:...`) images (`operator-bff`, `runtime-manager`, `governance`, `deployment`, `lifecycle-projector`, `postgres`, `nats`, and `rollback_baseline.backend_operator_bff_image`) via `docker pull` and verifies each via `docker image inspect` before starting [`deploy/staging/docker-compose.staging.yml`](../../deploy/staging/docker-compose.staging.yml) with `--no-build` and `pull_policy: never`.
   - External `/runtime-config.js` is rendered to `/var/lib/pantheon-staging-root/config/runtime-config.js` outside the sealed FE release directory (`/var/www/pantheon-staging-fe/current`) and served by [`deploy/staging/Caddyfile.staging.tmpl`](../../deploy/staging/Caddyfile.staging.tmpl) with `Cache-Control: no-store, no-cache, must-revalidate, max-age=0`.
   - After `docker compose ... up -d --no-build`, `bootstrap.sh` polls `/bff/readyz` (`200`) and executes real on-host queries (`docker inspect`, `curl /bff/readyz`, `curl /bff/version`, `deployment.json`, `.artifact_sha256`, `mountpoint`) to emit the observed deploy identity JSON on stdout.
5. **Fail-Closed Safe Defaults**:
   - `PANTHEON_PROVIDER_ENABLED=false`
   - `PANTHEON_LIVE_BROKER_ENABLED=false`
   - `PANTHEON_REAL_WRITES_ENABLED=false`
   - `PANTHEON_DEV_STUB_WRITES_ENABLED=false`
   - `PANTHEON_CONTROL_VM_BROKER_CREDENTIALS_PRESENT=false`
   - `PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED=false`
   - `PANTHEON_MGMT_NL_ASSISTANT_PROVIDER_ENABLED=false`
   - `PANTHEON_ASSISTANT_PROVIDER=disabled`
   - `PANTHEON_BFF_AUTH_MODE=strict`, `PANTHEON_BFF_AUTH_STUB=false`
   - `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`

---

## 2. Five-Stage Lifecycle Controller

Orchestrated by [`.github/workflows/staging-ephemeral-deploy.yml`](../../.github/workflows/staging-ephemeral-deploy.yml) under the `staging` GitHub Environment with Workload Identity Federation (`google-github-actions/auth` + `google-github-actions/setup-gcloud`):

| Stage | Script / Entry Point | Responsibility |
| --- | --- | --- |
| **1. PROVISION** | `scripts/deploy/staging_provision.sh` + `scripts/deploy/staging_ops.sh verify-artifacts` | Validates explicit GCP inputs, enforces deployer/runtime SA separation (`DEPLOYER_SA_FORBIDDEN_ON_VM`), verifies `dev_verified` manifest & immutable FE/BFF digests, checks global cloud + local lease conflict/idempotency, allocates labeled regional external IP (`--address`), restored sanitized data disk (`device-name=staging-data,auto-delete=no`), labeled Debian 12 boot disk (`boot=yes,auto-delete=no`), and VM (`e2-standard-2` standard non-Spot with runtime SA `pantheon-stg-runtime@...`). |
| **2. DEPLOY** | `scripts/deploy/staging_ops.sh deploy-host` + `deploy/staging/bootstrap.sh` | Creates `/tmp/pantheon-staging-bundle` over IAP SSH (`--tunnel-through-iap`), transfers the verified FE tarball, manifest, Compose overlay, Caddy template, `bootstrap.sh`, and `pantheon-staging-collect-gates` over IAP SCP (`--tunnel-through-iap`), executes `bootstrap.sh` over IAP SSH to bootstrap bare Debian, validate isolated secrets (`0400`/`0600`), `docker pull` all digest-pinned owner-closure images, unpack the FE release, render external `/runtime-config.js` (`no-store`), start Compose `--no-build`, and emit live on-host deploy identity readbacks. Compensates partial-create/deploy failures via `staging_destroy.sh --reason partial_create_failure`. |
| **3. VERIFY** | `scripts/deploy/staging_verify.sh` + `scripts/deploy/staging_ops.sh collect-verify-gates` + `deploy/staging/pantheon-staging-collect-gates` + `scripts/deploy/staging_sanitize.py` | Invokes `/usr/local/bin/pantheon-staging-collect-gates` on the live VM over IAP SSH (`--tunnel-through-iap`) to execute live Postgres sanitized dataset queries (`psql`), expand-phase SQL migrations & compatibility smoke (`psql`), backup creation & restore verification (`pg_dump`, `pg_restore`, `psql`), live exact-pair rollback rehearsal (`<= 120s`) and restoration, and HTTP/container security & safe-off probes (`curl`, `docker inspect`). Emits `staging_verified` evidence aligned with `scripts/deploy/prod_preflight.py`. |
| **4. COLLECT** | `scripts/deploy/staging_destroy.sh` | Preserves bounded evidence (`<release_id>-teardown-evidence.json` and verification summary) before any resource deletion, with retry recovery on delayed upload. |
| **5. DESTROY** | `scripts/deploy/staging_destroy.sh --mode teardown` | Queries live cloud + local inventory, deletes owned VM -> boot/data disks -> regional IP on success (or retains up to 2h TTL on test failure, capped at 24h), and performs post-delete cloud readback to verify `lingering_owned_resources == 0`. |

---

## 3. Local Validation Commands

```bash
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
"$PANTHEON_PY" -m pytest -q tests/deployment/test_staging_release.py tests/deployment/test_production_release.py
/tmp/pantheon-env-current-20261024/tools/actionlint .github/workflows/staging-ephemeral-deploy.yml .github/workflows/staging-orphan-cleanup.yml
bash -n scripts/deploy/staging_provision.sh scripts/deploy/staging_ops.sh scripts/deploy/staging_verify.sh scripts/deploy/staging_destroy.sh deploy/staging/bootstrap.sh
```

---

## 4. Current Cloud/Project Baseline & Remaining Hosted Prerequisites (`external_wait`)

1. **Provisioned GCP Staging Foundation & GitHub `staging` Environment (2026-10-25)**:
   - Project `pantheon-stg-20260902` (`58603439583`, billing `017011-E5B441-28904B` active) has VPC `pantheon-staging`, subnet `pantheon-staging-asia-east1` (`10.71.0.0/24`), firewall rules `pantheon-staging-iap-ssh` (`35.235.240.0/20` -> `pantheon-iap`) and `pantheon-staging-https` (`tcp:80,443` -> `pantheon-https`), WIF pool/provider `pantheon-github-pool`/`pantheon-github-provider`, deployer SA `pantheon-stg-github-deploy@pantheon-stg-20260902.iam.gserviceaccount.com`, runtime SA `pantheon-stg-runtime@pantheon-stg-20260902.iam.gserviceaccount.com`, Artifact Registry `pantheon-releases`, instance template `pantheon-stg-template-20261024`, sanitized snapshot `pantheon-stg-sanitized-seed-20261024`, and GitHub `staging` Environment variables configured (evidence `/tmp/pantheon-env-current-20261024/foundation/pantheon-stg-20260902.json` and `github-staging-environment.json`).
2. **Remaining Hosted Prerequisites (`external_wait`)**:
   - Wait for `DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002` (`Root01a11`) to produce a genuinely `dev_verified` release manifest.
   - Refresh expired GCP operator authentication (`bryan@cctech-support.com` `invalid_grant` / reauth required) or execute via GitHub Actions WIF once the corrective PR is merged to `dev`.
   - Execute two consecutive live ephemeral staging releases starting from zero staging resources, passing all live verification gates, and destroying all owned VM/disks/IP resources with verified zero lingering inventory.
