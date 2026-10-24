# Production Control Runbook (`ENV-PROD-CONTROL-IMPL-001`)

- Status: source implementation and local controller validation only; hosted acceptance `external_wait`
- Consumes: immutable release manifest and `staging_verified` evidence from [`ephemeral-staging-runbook.md`](ephemeral-staging-runbook.md)
- Design: [`vm-dev-staging-prod-management-plan.md`](vm-dev-staging-prod-management-plan.md), [`first-production-release-readiness.md`](first-production-release-readiness.md)

> [!IMPORTANT]
> Merging this source mutates no cloud resource, GitHub setting or Environment. Local tests use a fake ops adapter and prove controller behavior only; they are **not** hosted drill evidence and cannot claim `release_complete`. No production project, domain, billing account, IdP, reviewer, RPO/RTO or on-call choice is assumed.

## 1. Components

| Artifact | Role |
| --- | --- |
| `scripts/deploy/prod_preflight.py` | Fail-closed config, manifest checksum, `staging_verified` evidence binding, distinct-approver, baseline/bootstrap checks |
| `scripts/deploy/prod_atomic_switch.sh` | Blue/green controller (journal, green on alternate port, gates, singleton drain, upstream + FE atomic switch, exact identity readback) |
| `scripts/deploy/prod_rollback.sh` | Restores FE symlink, BFF upstream/config and singleton owner from the journal |
| `deploy/production/docker-compose.prod-control.yml` | Digest-pinned, `pull_policy: never`, safe-off defaults; stateful services exist once |
| `deploy/production/Caddyfile` | `bff_upstream.caddy` snippet + `fe/current` symlink root |
| `.github/workflows/production-release.yml` | Inert `workflow_dispatch`; `deploy` job bound to protected `production` Environment |

Host-side effects (compose start/stop, proxy reload, gates, snapshot, singleton handoff, identity readback) go through `PROD_OPS_CMD <step> <release_id> [args]`. The adapter is a hosted prerequisite and is not provided here.

## 2. Required explicit configuration (missing = fail closed)

`PROD_GCP_PROJECT_ID` (independent of dev/staging), `PROD_GCP_ZONE`, `PROD_WIF_PROVIDER`, `PROD_SERVICE_ACCOUNT`, `PROD_IAM_POLICY_REF`, `PROD_SECRETS_REF`, `PROD_DATA_DISK`, `PROD_NETWORK`, `PROD_BFF_HOST`, `PROD_FE_HOST` (HTTPS), `PROD_IDP_ISSUER` (https), `PROD_RPO_SECONDS`, `PROD_RTO_SECONDS`, `PROD_RETENTION_DAYS`, `PROD_ONCALL_OWNER`, `PROD_ROLLBACK_OWNER`. Placeholders, retired projects/hosts and enabled provider/broker/real-write flags are rejected. No broker credentials on the control VM.

## 3. Switch sequence

1. Preflight: config, manifest SHA-256, `staging_verified` evidence for that exact manifest, `production` Environment approval by someone other than the initiator (run-approvals API), baseline posture.
2. FE artifact bytes must equal manifest `artifact_sha256`; an existing release directory with different content is refused (never overwritten, never rebuilt).
3. Snapshot preflight and healthy retained baseline (`baseline_health`).
4. Journal restore targets, stage FE into `fe/releases/<id>`, start green on the alternate port.
5. Gates: health, auth, CORS, tenant, read-only, schema, old/new compatibility. Failure stops green; blue is untouched.
6. Singleton drain on blue, atomic upstream replace + reload, singleton activate, atomic `fe/current` symlink switch.
7. Post-switch exact identities (FE sha, BFF digest). Any failure after step 6 starts runs `prod_rollback.sh`, which must complete within 120 s (otherwise exit 6).

Exit codes: 0 active, 1 refused/aborted before traffic, 2 rolled back, 3 rollback failed, 4 no rollback target (bootstrap), 6 rollback over budget.

## 4. Bootstrap and data

First release needs explicit `PROD_BOOTSTRAP_FIRST_RELEASE=true` and a manifest without `rollback_baseline`; failure yields `prod_aborted` with `no_rollback_target`, never an invented healthy target. Application rollback is not database recovery. The production data disk is never overwritten or recreated by any script here; recovery uses snapshots and the drills below.

## 5. Hosted drills (not executed; `external_wait`)

- Backup/restore drill: restore latest snapshot to a scratch disk, record measured RPO against `PROD_RPO_SECONDS` and RTO against `PROD_RTO_SECONDS`.
- VM replacement drill: replace the control VM from the template, attach the existing data disk, verify identities.
- Two non-capital production-shaped deploy/rollback drills, each with rollback under 120 s.
- Protected exact-release publication, then a 30-minute observation window. Thresholds come from measured policy approved by the named owners; no green SLO is assumed here.

## 6. Missing hosted prerequisites (recorded, not forged)

Production project/billing/domain/IdP, WIF/SA/IAM, `production` GitHub Environment with required reviewers, `PRODUCTION_RELEASE_ENABLED`, ops adapter and artifact transport on the control VM, approved RPO/RTO/retention/on-call/rollback owner, `staging_verified` evidence from a genuine hosted staging run, and DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002 / all12 chain `h12-01a11-20261023-9aeb` (not rerun or relabeled here).
