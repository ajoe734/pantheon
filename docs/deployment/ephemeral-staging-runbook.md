# Ephemeral Staging Runbook (`ENV-STG-EPHEMERAL-IMPL-001`)

- Status: Source implementation & local controller validation complete; hosted execution `external_wait`
- Canonical Design: [`docs/deployment/vm-dev-staging-prod-management-plan.md`](vm-dev-staging-prod-management-plan.md) § 9 & [`docs/04/pantheon_environment_closure_sa_sd_2026-09/SD.md`](../04/pantheon_environment_closure_sa_sd_2026-09/SD.md)
- Readiness Baseline: [`docs/deployment/first-production-release-readiness.md`](first-production-release-readiness.md)

> [!IMPORTANT]
> **Functional vs. Hosted Boundary**: Merging these scripts, schemas, templates, and GitHub Actions workflows does **not** mutate any cloud resource or GitHub Environment, and local/mocked controller tests do **not** count as hosted staging drill evidence. Historical PR `#6758` (`63155c0feecec77873b4623b029d137042a42c16` -> `5dc101e9d1b943d9411a2c5156f7163f0f36bf01`) is preserved as historical source evidence only. Hosted acceptance (`completion_tracks.hosted`) remains `external_wait` until infrastructure bootstrap, an accepted `dev_verified` release manifest from `Root01a11`, and two consecutive zero-to-zero live staging drills pass.

---

## 1. Architecture & Safety Invariants

1. **Zero Idle Staging VMs**: Staging exists only during release validation. Every GCE resource (`address`, `disk`, `instance`) carries valid GCP cloud labels (`^[a-z0-9_-]{1,63}$`: `environment=staging`, `release_id`, `owner`, `workflow_run_id`, compact UTC `created_at`/`expires_at`, `created_at_epoch`, `expires_at_epoch`, `cleanup_policy=automatic`) and a bounded 2-hour TTL (`7200s` default, `86400s` absolute cap). Standard Debian 12 `e2-standard-2` non-Spot VM sizing remains the target pending measured staging sizing confirmation during live drills.
2. **Build Once, Promote by Digest**: Ephemeral Staging never rebuilds `execute-plans` FE or `pantheon` BFF (`pull_policy: never` and `--no-build` in [`deploy/staging/docker-compose.staging.yml`](../../deploy/staging/docker-compose.staging.yml)). [`scripts/deploy/staging_ops.sh`](../../scripts/deploy/staging_ops.sh) retrieves the FE artifact bundle, computes its actual SHA-256, queries Artifact Registry for the `operator_bff@sha256:...` digest, and validates both against [`scripts/deploy/release_manifest.py`](../../scripts/deploy/release_manifest.py) and [`schemas/release-manifest.schema.json`](../../schemas/release-manifest.schema.json) before provisioning.
3. **No Relabeling Failed Dev Chains or Dry-Run Outputs**:
   - `release_manifest.py` explicitly rejects `candidate_built`/`dev_failed` manifests and refuses to relabel the unaccepted hosted all12 chain `h12-01a11-20261023-9aeb` or any non-`passed` all12 status as `dev_verified`.
   - `staging_provision.sh`, `staging_ops.sh`, `staging_verify.sh`, and `staging_destroy.sh` fail closed (`GCLOUD_BIN_REQUIRED`) when `STAGING_DRY_RUN=false` and `GCLOUD_BIN` is unset/unexecutable, and require real observed cloud resource identities (`id`, `selfLink`, `address`, attached boot/data disks) outside explicit `--dry-run`.
   - `staging_verify.sh` refuses to produce `status=staging_verified` from any dry-run lease or static/unobserved fixture (`DRY_RUN_LEASE_REJECTED` / `UNOBSERVED_FIXTURE_REJECTED`). Dry-run mode emits `status=dry_run_validated`, which `scripts/deploy/prod_preflight.py` rejects fail-closed.
4. **Fail-Closed Safe Defaults**:
   - `PANTHEON_PROVIDER_ENABLED=false`
   - `PANTHEON_LIVE_BROKER_ENABLED=false`
   - `PANTHEON_REAL_WRITES_ENABLED=false`
   - `PANTHEON_DEV_STUB_WRITES_ENABLED=false`
   - `PANTHEON_CONTROL_VM_BROKER_CREDENTIALS_PRESENT=false`
   - `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`
5. **No Retired Project or Host Fallback**: Retired projects (`pantheon-lupin-dev-20260719`, `pantheon-benjamin-20260528`) and retired IPs (`35.201.204.12`, `35.201.239.38`, `34.81.75.241`, `104.155.223.192`) are rejected fail-closed across all scripts and manifests.

---

## 2. Five-Stage Lifecycle Controller

Orchestrated by [`.github/workflows/staging-ephemeral-deploy.yml`](../../.github/workflows/staging-ephemeral-deploy.yml) under the `staging` GitHub Environment with Workload Identity Federation (`google-github-actions/auth` + `google-github-actions/setup-gcloud`):

| Stage | Script / Entry Point | Responsibility |
| --- | --- | --- |
| **1. PROVISION** | `scripts/deploy/staging_provision.sh` + `scripts/deploy/staging_ops.sh verify-artifacts` | Validates explicit GCP inputs, verifies `dev_verified` manifest & immutable FE/BFF digests, checks global cloud + local lease conflict/idempotency, allocates labeled regional external IP (`--address`), restored sanitized data disk (`device-name=staging-data,auto-delete=no`), labeled Debian 12 boot disk (`boot=yes,auto-delete=no`), VM (`e2-standard-2` standard non-Spot) with verified disk/IP attachment readback, and IAM binding (recording `preexisting` status). |
| **2. DEPLOY** | `scripts/deploy/staging_ops.sh deploy-host` | Transfers verified FE tarball, manifest, Compose overlay, and Caddy template via `gcloud compute scp`; mounts sanitized data disk at `/var/lib/pantheon-staging`; extracts FE release and switches `/var/www/pantheon-staging-fe/current` symlink; starts `core` profile with `docker compose --profile core up -d --no-build`; records live observed VM deployment readback. Compensates partial-create/deploy failures via `staging_destroy.sh --reason partial_create_failure`. |
| **3. VERIFY** | `scripts/deploy/staging_verify.sh` + `scripts/deploy/staging_ops.sh collect-verify-gates` + `scripts/deploy/staging_sanitize.py` | Collects live gate outputs from the staging VM over SSH (`pantheon-staging-collect-gates`), verifies live observed FE/BFF digests against the manifest, validates sanitized dataset PII/secret exclusion, expand-phase forward migration & old-app/new-schema compatibility, backup restore readability, exact-pair rollback (`<= 120s`), tenant/RBAC & safe-off readbacks, and orphan cleanup dry-run. Emits `staging_verified` evidence aligned with `scripts/deploy/prod_preflight.py`. |
| **4. COLLECT** | `scripts/deploy/staging_destroy.sh` | Preserves bounded evidence (`<release_id>-teardown-evidence.json` and verification summary) before any resource deletion, with retry recovery on delayed upload. |
| **5. DESTROY** | `scripts/deploy/staging_destroy.sh --mode teardown` | Queries live cloud + local inventory, deletes owned IAM (only if not `preexisting`) -> VM -> boot/data disks -> regional IP on success (or retains up to 2h TTL on test failure, capped at 24h), and performs post-delete cloud readback to verify `lingering_owned_resources == 0`. |

---

## 3. Sanitized Data & Migration Rehearsal (`staging_sanitize.py`)

[`scripts/deploy/staging_sanitize.py`](../../scripts/deploy/staging_sanitize.py) enforces:
- **Non-Production Provenance**: Only `dev_sanitized_seed`, `staging_synthetic`, or `nonprod_seed` sources are accepted; `production`/`prod` or unknown sources fail closed immediately.
- **Explicit Schema Allowlist**: Unknown tables or columns outside `ALLOWED_TABLE_COLUMNS` fail closed.
- **Deterministic Masking & Secret Exclusion**:
  - Truncates `broker_credentials`, `exchange_api_keys`, and `user_sessions`.
  - Rewrites user emails to `staging-user-<id>@pantheon.internal`, names to `Staging Persona <id>`, and password hashes to the dummy verification hash; clears `phone` and `national_id`.
  - Strips sensitive keys (`token`, `auth`, `secret`, `api_key`, `private_key`, `password`, `cookie`, `authorization`, `broker_secret`, `tws_password`) recursively from `telemetry_events.raw_payload`.
  - Forces `system_settings` to `live_broker_enabled=false`, `provider_enabled=false`, `real_writes_enabled=false`, `safe_write_strict=true`, `execution_mode=paper`.

---

## 4. Independent Orphan Cleanup (`staging-orphan-cleanup.yml`)

[`.github/workflows/staging-orphan-cleanup.yml`](../../.github/workflows/staging-orphan-cleanup.yml) runs every 30 minutes (`*/30 * * * *`):

```bash
./scripts/deploy/staging_destroy.sh --mode orphan-sweep
```

- **Inert Until Configured**: While `STAGING_ORPHAN_CLEANUP_ENABLED != true`, the `preflight` job (which binds no GitHub Environment so scheduled runs never auto-create or mutate `staging`) outputs `{"status":"inert_disabled","mutated_resources":0}` and exits `0` without cloud calls, and the `orphan-sweep` job (`environment: staging`) is skipped.
- **Stateless Cross-Run Cloud Discovery & Ownership Protection**: When enabled, `staging_destroy.sh --mode orphan-sweep` queries GCP (`compute instances list`, `compute disks list`, `compute addresses list`) directly via `GCLOUD_BIN` so expired resources are discovered and deleted even on a fresh runner with empty local state. Only resources carrying `environment=staging`, `cleanup_policy=automatic`, valid `release_id`/`owner`, and `now_epoch >= expires_at_epoch` are deleted, followed by post-delete cloud readback. Unlabeled resources, preexisting IAM bindings, and `environment=dev` (`pantheon-dev-deploy`) or `environment=production` resources are never touched and are reported in `skipped_unowned_resources`.

---

## 5. Local Validation Commands

```bash
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
"$PANTHEON_PY" -m pytest -q tests/deployment/test_staging_release.py
/tmp/pantheon-env-current-20261024/tools/actionlint .github/workflows/staging-ephemeral-deploy.yml .github/workflows/staging-orphan-cleanup.yml
```

---

## 6. Current Cloud/Project Baseline & Remaining Hosted Prerequisites (`external_wait`)

1. **Resolved GCP Project, Billing & API Baseline (2026-10-24)**:
   - Projects `pantheon-stg-20260902` and `pantheon-prod-20260902` have verified project owner permissions and active billing relinked at `2026-10-24T12:14Z` to `017011-E5B441-28904B` (`billingEnabled=true`, Dev unchanged; evidence `/tmp/pantheon-billing-relink-20261024/20261024T121443Z/summary.json`).
   - Compute, IAM, IAMCredentials, STS, ArtifactRegistry, SecretManager, DNS, Logging, and Monitoring APIs are enabled on both projects; `asia-east1` quota is verified (`CPUS=32`, `DISKS_TOTAL_GB=2048`, `STATIC_ADDRESSES=8`, `INSTANCES=8`, current usage `0`, 0 VMs present).
2. **Remaining Infrastructure Bootstrap & GitHub `staging` Environment Configuration**:
   - Provision the staging Workload Identity Federation provider (`STAGING_WIF_PROVIDER`), staging deployer service account (`STAGING_SERVICE_ACCOUNT`), GCE instance template (`STAGING_INSTANCE_TEMPLATE`), and initial non-production sanitized snapshot (`STAGING_SANITIZED_SNAPSHOT`) in `pantheon-stg-20260902`.
   - Create the `staging` GitHub Environment in `ajoe734/pantheon` (concurrency `1`) and configure `STAGING_GCP_PROJECT_ID=pantheon-stg-20260902`, `STAGING_GCP_ZONE=asia-east1-b`, `STAGING_WIF_PROVIDER`, `STAGING_INSTANCE_TEMPLATE`, `STAGING_SERVICE_ACCOUNT`, `STAGING_SANITIZED_SNAPSHOT`, `STAGING_FE_ARTIFACT_URI`, `STAGING_EPHEMERAL_ENABLED=true`, and `STAGING_ORPHAN_CLEANUP_ENABLED=true`.
3. **Accepted `dev_verified` Release Manifest & Two Consecutive Zero-to-Zero Drills**:
   - Wait for `DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002` (`Root01a11`) to produce a genuinely `dev_verified` release manifest.
   - Execute two consecutive live ephemeral staging releases starting from zero staging resources, passing all live verification gates, and destroying all owned VM/disks/IP/IAM bindings with verified zero lingering inventory.
