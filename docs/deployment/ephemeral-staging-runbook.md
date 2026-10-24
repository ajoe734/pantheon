# Ephemeral Staging Runbook (`ENV-STG-EPHEMERAL-IMPL-001`)

- Status: Source implementation & local controller validation complete; hosted execution `external_wait`
- Canonical Design: [`docs/deployment/vm-dev-staging-prod-management-plan.md`](vm-dev-staging-prod-management-plan.md) § 9 & [`docs/04/pantheon_environment_closure_sa_sd_2026-09/SD.md`](../04/pantheon_environment_closure_sa_sd_2026-09/SD.md)
- Readiness Baseline: [`docs/deployment/first-production-release-readiness.md`](first-production-release-readiness.md)

> [!IMPORTANT]
> **Functional vs. Hosted Boundary**: Merging these scripts, schemas, templates, and GitHub Actions workflows does **not** mutate any cloud resource or GitHub Environment, and local/mocked controller tests do **not** count as hosted staging drill evidence. Hosted acceptance (`completion_tracks.hosted`) remains `external_wait` until the external prerequisites in § 6 are provisioned and two consecutive zero-to-zero releases pass.

---

## 1. Architecture & Safety Invariants

1. **Zero Idle Staging VMs**: Staging exists only during release validation. Every resource carries mandatory labels (`environment=staging`, `release_id`, `owner`, `workflow_run_id`, `created_at`, `expires_at`, `cleanup_policy=automatic`) and a bounded 2-hour TTL (`7200s` default, `86400s` absolute cap).
2. **Build Once, Promote by Digest**: Ephemeral Staging never rebuilds `execute-plans` FE or `pantheon` BFF (`pull_policy: never` in [`deploy/staging/docker-compose.staging.yml`](../../deploy/staging/docker-compose.staging.yml)). It accepts only a genuinely `dev_verified` release manifest validated by [`scripts/deploy/release_manifest.py`](../../scripts/deploy/release_manifest.py) against [`schemas/release-manifest.schema.json`](../../schemas/release-manifest.schema.json).
3. **No Relabeling Failed Dev Chains**: `release_manifest.py` explicitly rejects `candidate_built`/`dev_failed` manifests and refuses to relabel the unaccepted hosted all12 chain `h12-01a11-20261023-9aeb` or any non-`passed` all12 status as `dev_verified`.
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

Orchestrated by [`.github/workflows/staging-ephemeral-deploy.yml`](../../.github/workflows/staging-ephemeral-deploy.yml):

| Stage | Script / Entry Point | Responsibility |
| --- | --- | --- |
| **1. PROVISION** | `scripts/deploy/staging_provision.sh` | Validates explicit GCP inputs, checks `dev_verified` manifest & outer SHA-256, enforces lease conflict / idempotency rules, allocates labeled ephemeral IP, sanitized data disk, boot disk, VM (`e2-standard-2` standard non-Spot), and IAM binding. |
| **2. DEPLOY** | `scripts/deploy/staging_provision.sh` | Binds immutable FE `artifact_sha256` and BFF `operator_bff@sha256:...` digest plus `rollback_baseline` without rebuilding. Compensates partial-create failures immediately via `staging_destroy.sh --reason partial_create_failure`. |
| **3. VERIFY** | `scripts/deploy/staging_verify.sh` + `scripts/deploy/staging_sanitize.py` | Verifies deployed digests, sanitized dataset PII/secret exclusion, expand-phase forward migration, old-app/new-schema compatibility, backup restore readability, exact-pair rollback (`<= 120s`), tenant/RBAC & safe-off readbacks, and orphan cleanup dry-run. |
| **4. COLLECT** | `scripts/deploy/staging_destroy.sh` | Preserves bounded evidence (`<release_id>-teardown-evidence.json` and verification summary) before any resource deletion, with retry recovery on delayed upload. |
| **5. DESTROY** | `scripts/deploy/staging_destroy.sh --mode teardown` | Immediately deletes owned IAM/VM/disks/IP on success (or retains up to 2h TTL on test failure for inspection, capped at 24h), verifying zero lingering owned inventory. |

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

[`.github/workflows/staging-orphan-cleanup.yml`](../../.github/workflows/staging-orphan-cleanup.yml) runs every 30 minutes (`*/30 * * * *`) and invokes:

```bash
./scripts/deploy/staging_destroy.sh --mode orphan-sweep
```

- **Inert Until Configured**: While `STAGING_ORPHAN_CLEANUP_ENABLED != true` (or `STAGING_GCP_PROJECT_ID` / `STAGING_GCP_ZONE` are unset), the sweeper outputs `{"status":"inert_disabled","mutated_resources":0}` and exits `0` without cloud calls.
- **Ownership Protection**: Only resources carrying `environment=staging`, `cleanup_policy=automatic`, valid `release_id`/`owner`, and `now_epoch >= expires_at_epoch` are deleted. Unlabeled resources and `environment=dev` (`pantheon-dev-deploy`) or `environment=production` resources are never touched and are reported in `skipped_unowned_resources`.

---

## 5. Local Validation Commands

```bash
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
"$PANTHEON_PY" -m pytest -q tests/deployment/test_staging_release.py
```

---

## 6. External Prerequisites Required for Hosted Acceptance (`external_wait`)

Hosted acceptance (`completion_tracks.hosted`) remains held until Human/Ops provisions and verifies:

1. **GCP Authentication & Project Quota**:
   - Current host `gcloud` CLI requires reauthentication (`gcloud auth login` / WIF configuration).
   - Confirm active non-production GCP project, billing, Compute Engine quota (`e2-standard-2`, `pd-balanced`), instance template (`STAGING_INSTANCE_TEMPLATE`), staging deployer service account (`STAGING_SERVICE_ACCOUNT`), and initial sanitized snapshot (`STAGING_SANITIZED_SNAPSHOT`).
2. **GitHub `staging` Environment & Variables**:
   - Create the `staging` GitHub Environment in `ajoe734/pantheon` with concurrency `1`.
   - Configure explicit variables: `STAGING_GCP_PROJECT_ID`, `STAGING_GCP_ZONE`, `STAGING_INSTANCE_TEMPLATE`, `STAGING_SERVICE_ACCOUNT`, `STAGING_SANITIZED_SNAPSHOT`, `STAGING_EPHEMERAL_ENABLED=true`, and `STAGING_ORPHAN_CLEANUP_ENABLED=true`.
   - Remove legacy residual repo variables (`STAGING_BFF_CORS_ORIGIN`, `STAGING_CONTROL_ZONE`, `STAGING_EXEC_ZONE`) once operator confirms no historical job reads them.
3. **Accepted `dev_verified` Release Manifest & Two Consecutive Zero-to-Zero Drills**:
   - Wait for `DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002` to produce a genuinely `dev_verified` release manifest.
   - Execute two consecutive ephemeral staging releases starting from zero staging resources, passing all verification gates, and destroying all owned VM/disks/IP/IAM bindings with zero orphan inventory.
