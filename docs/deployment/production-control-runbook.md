# Production Control Runbook (`ENV-PROD-CONTROL-IMPL-001`)

- Status: source implementation and local controller validation only; hosted acceptance `external_wait`
- Consumes: immutable release manifest and `staging_verified` evidence from [`ephemeral-staging-runbook.md`](ephemeral-staging-runbook.md)
- Design: [`vm-dev-staging-prod-management-plan.md`](vm-dev-staging-prod-management-plan.md), [`first-production-release-readiness.md`](first-production-release-readiness.md)

> [!IMPORTANT]
> Merging this source mutates no cloud resource, GitHub setting or Environment. Local tests drive the real `prod_ops.sh`/`bootstrap.sh`/controller scripts against fake docker/curl/mountpoint binaries and a temp state tree; they prove script behavior only; they are **not** hosted drill evidence and cannot claim `release_complete`. No production project, domain, billing account, IdP, reviewer, RPO/RTO or on-call choice is assumed.

## 1. Components

| Artifact | Role |
| --- | --- |
| `scripts/deploy/prod_preflight.py` | Fail-closed config, manifest checksum, `staging_verified` evidence binding, approver-vs-Environment-policy, baseline/bootstrap checks |
| `deploy/production/bootstrap.sh` | Host bootstrap (`run_priv` OS Login support, bare-Debian runtime binary check, secret content validation, `compose.env`, `@sha256:...` digest-pinned image pre-acquisition, 503 upstream placeholder, never formats/mounts data disk) |
| `scripts/deploy/prod_ops.sh` | Control VM ops adapter (`prepull_images`, `snapshot_preflight`, `baseline_health`, `start_green`/`stop_green`, `gate`, `drain_singletons`/`activate_singletons`/`resume_baseline_singletons`, `reload_proxy`, `verify_runtime_config`, `post_identity`) |
| `scripts/deploy/prod_atomic_switch.sh` | Blue/green controller (baseline closure journal, immutable FE archive + manifest retention, green on alternate port, gates, singleton drain, upstream + FE atomic switch, public + local exact identity readback) |
| `scripts/deploy/prod_rollback.sh` | Restores FE symlink, BFF upstream, `runtime-config.js`, `active_manifest.json`, the exact prior `state/baseline.json` (journaled as `prev_baseline.json`), `compose.env` and baseline singleton owner (`baseline_env`) from the journal; on bootstrap abort removes `fe/current`, any `state/baseline.json` (honest no-baseline posture, never a fake healthy target), drains singletons, restores 503 upstream and exits 4 |
| `deploy/production/docker-compose.prod-control.yml` | Digest-pinned, `pull_policy: never`, safe-off defaults; stateful services exist once |
| `deploy/production/Caddyfile` | `bff_upstream.caddy` snippet + `fe/current` symlink root (`/deployment.json` and `/runtime-config.js` served `no-store`) |
| `.github/workflows/production-release.yml` | Inert `workflow_dispatch`; `deploy` job bound to protected `production` Environment |

Host-side effects go through `PROD_OPS_CMD <step> <release_id> [args]`, implemented by `scripts/deploy/prod_ops.sh` (runs on the control VM; every step is a real readback and fails non-zero, no synthetic pass JSON): `prepull_images` (validates and pulls `@sha256:...` digest-pinned images before `pull_policy: never` compose runs), `snapshot_preflight` (fresh READY snapshot record for this release and data disk, disk mounted), `baseline_health`, `start_green`/`stop_green` (`--no-build`, manifest-only digests), `gate health|auth|cors|tenant|read_only|schema|old_new_compat`, `drain_singletons`/`activate_singletons`/`resume_baseline_singletons` (exactly one `lifecycle-projector` verified against the release or baseline image digest), `reload_proxy`, `verify_runtime_config`, `post_identity` (verifies both local `.artifact_sha256` / container `Config.Image` and public hosted FE `/deployment.json` (`no-store`) + public BFF `/bff/version` and `/bff/readyz`). `deploy/production/bootstrap.sh` prepares the VM tree for the first release (503 upstream placeholder, no `fe/current`, validates secret files reject live broker credentials/unsafe flags, never formats/mounts the data disk, secrets must pre-exist at mode 0400/0600).

Bootstrap resolves the effective postgres/nats refs (shell env, else existing `compose.env`) and prepulls exactly those before stateful `up`.

Contracts the adapter relies on (to be confirmed on the first hosted run): manifest `backend.images` must include `runtime_manager`, `governance`, `deployment`, `lifecycle_projector` besides `operator_bff`; the `schema`/`old_new_compat` gates do NOT require image labels (the accepted BFF image has none); they bind the exact manifest `migration_set_sha256`/`compatibility_manifest_sha256` and digest-pinned BFF image to the live `staging_verified` evidence (`migration_verified`, `old_app_new_schema_compatible`, matching `migration_set_sha256`), the running candidate container image, and the Stage-owned product migration contract. The product has no `schema_migrations` table: the qualified `migration-spec.json` plus `migrations/*.sql` bytes (installed read-only at `PROD_MIGRATION_DIR`, default `$PROD_ROOT/migration/<release_id>`) are re-hashed by `prod_preflight.py migration` with Stage's recipe (sha256 of sorted `<file-sha256>  <name>\n` lines) and must equal the manifest `migration_set_sha256`; the live `information_schema` catalog (same query Stage uses) must then contain every relation those verified SQL files create plus `public.telemetry_events` and `trade_journey_projection.controller`. The catalog check is column-level, not name-only: `public.telemetry_events` / `public.loop_controller_records` must carry the `scripts/db_migrate.sh` columns with matching `information_schema` types (and the `ingested_seq`/`ingested_at` defaults the projector relies on), and every column declared by the verified SQL (CREATE TABLE bodies, ALTER ... ADD COLUMN) must exist with its type. First-release/upgrade initialization is part of the workflow, not a manual prerequisite: the workflow runs `prod_preflight.py migration render` (Stage recipe over `services/trade_journey/migrations`, proven equal to the manifest `migration_set_sha256`, fails closed on any other checkout), ships that material plus `scripts/db_migrate.sh` to `$PROD_ROOT/migration/<release_id>`, and runs `prod_ops.sh migrate` after image pre-pull and before the switch: first the product's idempotent base-schema initializer (`db_migrate.sh --print-sql`, same psql transport as `scripts/bootstrap.sh`; its sha256 is journaled under `state/substrate-<release_id>.sha256`), then the Stage-qualified expand SQL, then the schema gate. Missing/tampered SQL, an unmigrated database or an unreachable database fails closed. The gates never apply migrations; `prod_ops.sh migrate <release_id>` applies the same verified, expand-only SQL (autocommit per file via `psql -f -`, like Stage) as an explicit out-of-band step before the release; the `tenant` gate (an `auth_tenant_gate` service check, separate from human login and from the protected release approval) mints, inside the candidate BFF container, a 120-second viewer SERVICE JWT (`sub=service:prod-release-gate`, `roles=[viewer]`, explicit `PROD_TENANT_ID`, the BFF's own `PANTHEON_BFF_JWT_SECRET`/issuer/audience, existing `encode_jwt_hs256`; no user/MFA/email/IAM claims) into a 0600 VM-local file removed on exit, then requires `/bff/me` 200 for the production tenant THEN 403 for a foreign tenant (401/404 alone fail). `PROD_GATE_TOKEN_FILE` may name a pre-issued token instead (missing/empty => gate fails, never skipped). The source worker never reads secrets or mints a real token. The BFF is stateless per color; the singleton owner is the single `lifecycle-projector`, which is handed off BEFORE the upstream switch: BFF `/bff/readyz` requires the projector controller `deployment_sha` to equal the BFF `BFF_COMMIT`, so the green BFF is only checked for liveness (`/livez`) until the baseline projector is drained and the candidate projector is activated, then `gate candidate_ready` requires `/bff/readyz` 200 before any traffic moves. The first release has no baseline projector (drain is a no-op). During the hand-off the retained baseline BFF reports not-ready (sha mismatch) but keeps serving; rollback removes the candidate projector, restores the baseline projector and waits for baseline readiness again. Colors alternate: the target is whichever of `PROD_BLUE_PORT`/`PROD_GREEN_PORT` the retained baseline (`active_port`) is not on.

Runtime config and deployment metadata: `/runtime-config.js` is rendered into `${PROD_ROOT}/config/`, served same-origin by Caddy with `no-store`, outside the sealed FE release dir (coordinated with FE-IMMUTABLE-RELEASE-CONFIG-20261024); `/deployment.json` is written into `${PROD_ROOT}/fe/releases/<id>/deployment.json` and served with `no-store` via `fe/current`. The rendered `/runtime-config.js` emits only the accepted `pantheon.fe.public-runtime-config.v1` fields (`schemaVersion`, `environment`, `configRef` = release id, `bffBaseUrl`, `gcpIdentity{apiKey,projectId,authDomain,issuer,audience}` and an optional truthful `configSha256`); deployment identity stays in `deployment.json`. Public Identity values come from Environment variables `PROD_PUBLIC_GCP_IDENTITY_API_KEY|PROJECT_ID|AUTH_DOMAIN` (required; preflight fails closed when missing) and optional `..._ISSUER|AUDIENCE`, forwarded by the workflow to the host; `verify_runtime_config` fails `RUNTIME_CONFIG_CONTRACT` on schema, `configRef`, identity or `configSha256` mismatch. Container wiring follows the accepted native ports: BFF `8001` (host blue/green ports map to it), runtime-manager `8080`, governance `8082`, deployment `8095`; the singleton projector runs `python -m services.trade_journey.lifecycle_projector run` with `GIT_SHA` and isolated `TELEMETRY_DB_DSN`/`LIFECYCLE_PROJECTOR_PROJECTION_DSN` from the secrets file (bootstrap rejects DSNs not targeting `PROD_POSTGRES_DB`). Immutable FE bundles (`fe/artifacts/<id>.tar.gz` + `.sha256`) and manifests (`manifests/<id>.json`) are retained on the control VM for rollback and audit.

Migration material: the workflow renders the versioned SQL and the base-schema initializer (`scripts/db_migrate.sh`) from the manifest `backend.commit` (`prod_preflight.py migration render-commit`: shallow-fetch if absent, `git archive`), never from its own checkout, and proves the set equals `migration_set_sha256`. Fail-closed reasons: `invalid_backend_commit`, `backend_commit_fetch_failed`, `migration_path_missing`, `substrate_path_missing`, `migration_archive_failed`, `migration_digest_or_spec_mismatch`.

Workflow: `PROD_*` variables are Environment-scoped, so they are read only in the `deploy` job (after Environment approval, before cloud auth). The `preflight` job has no Environment and validates only the manifest/evidence. After auth the runner takes the pre-release disk snapshot, creates `/tmp/pantheon-release-<id>` (`0700`) over IAP SSH, transports manifest, `staging_evidence`, approvals, env-policy, FE bundle, snapshot record and controller/bootstrap scripts to the control VM with `gcloud compute scp --tunnel-through-iap`, runs `bootstrap.sh` and `prod_ops.sh prepull_images`, and then runs `prod_atomic_switch.sh` on the control VM; the controller is never run on the GitHub runner.

## 2. Required explicit configuration (missing = fail closed)

`PROD_GCP_PROJECT_ID` (independent of dev/staging), `PROD_GCP_ZONE`, `PROD_WIF_PROVIDER`, `PROD_SERVICE_ACCOUNT`, `PROD_IAM_POLICY_REF`, `PROD_SECRETS_REF`, `PROD_DATA_DISK`, `PROD_NETWORK`, `PROD_BFF_HOST`, `PROD_FE_HOST` (HTTPS), `PROD_IDP_ISSUER` (https), `PROD_TENANT_ID` (explicit product-owner tenant, no default), `PROD_RPO_SECONDS`, `PROD_RTO_SECONDS`, `PROD_RETENTION_DAYS`, `PROD_ONCALL_OWNER`, `PROD_ROLLBACK_OWNER`. Placeholders, retired projects/hosts and enabled provider/broker/real-write flags are rejected. No broker credentials on the control VM.

### Minimum control VM size: `e2-standard-8`

The Prod control VM (`pantheon-prod-control`) must be at least `e2-standard-8` (8 vCPUs). Staging measurements (operator decision 2026-11-03): `e2-standard-2` saturated CPU on the full owner closure (paper-signal-producer healthcheck timeout); `e2-standard-4` took 151-155 s for the exact-pair rollback (limit 120 s); `e2-standard-8` passed at 107.7 s / 108.6 s. The workflow runs a read-only gate (`prod_preflight.py vm-size --vcpus <guestCpus>`, after auth, before the snapshot) and fails closed below 8 vCPUs, so no release is promoted onto an undersized VM.

#### Operator procedure: resize existing `pantheon-prod-control` (PREPARED, NOT EXECUTED)

Run only after the operator confirms. The resize stops the VM (brief outage; no release in flight, no workflow running). The data disk is never detached, deleted or recreated.

```bash
P="$PROD_GCP_PROJECT_ID"; Z="$PROD_GCP_ZONE"; VM=pantheon-prod-control

# 0. Readbacks before: size, status, attached disks (record in evidence)
gcloud compute instances describe "$VM" --project "$P" --zone "$Z" \
  --format='yaml(name,status,machineType.basename(),disks[].source.basename(),disks[].autoDelete)'
# Take a fresh data-disk snapshot first (name it; retain until the resize is verified)
gcloud compute disks snapshot "$PROD_DATA_DISK" --project "$P" --zone "$Z" --snapshot-names "pantheon-prod-pre-resize-$(date -u +%Y%m%d%H%M)"

# 1. Stop (graceful)
gcloud compute instances stop "$VM" --project "$P" --zone "$Z"
# 2. Resize
gcloud compute instances set-machine-type "$VM" --project "$P" --zone "$Z" --machine-type e2-standard-8
# 3. Start
gcloud compute instances start "$VM" --project "$P" --zone "$Z"

# 4. Readbacks after: expect RUNNING, e2-standard-8, same data disk, 8 vCPUs, Caddy/stateful services up
gcloud compute instances describe "$VM" --project "$P" --zone "$Z" --format='value(status,machineType.basename())'
gcloud compute machine-types describe e2-standard-8 --project "$P" --zone "$Z" --format='value(guestCpus)'
python3 -I scripts/deploy/prod_preflight.py vm-size --vcpus 8
gcloud compute ssh "$VM" --tunnel-through-iap --project "$P" --zone "$Z" --command 'nproc; mountpoint -q "$PROD_DATA_MOUNT" && echo data-mounted; sudo docker ps --format "{{.Names}} {{.Status}}"'
curl -fsS -o /dev/null -w '%{http_code}\n' "https://$PROD_BFF_HOST/bff/readyz"   # 503 is expected until a release is active
```

Rollback (only if the VM fails to start or the data mount is absent after resize): stop the VM, `set-machine-type ... --machine-type <previous type recorded in step 0>`, start, repeat the step 4 readbacks (the vCPU preflight is expected to fail on the old size). Do not touch the data disk; if it is damaged, recover from the step 0 snapshot onto a scratch disk per section 5, never over the live disk.

## 3. Switch sequence

1. Preflight: config, manifest SHA-256, `staging_verified` evidence for that exact manifest, genuine `production` Environment approval (run-approvals API) by a required reviewer of the actual Environment record (`--env-policy` / `ENV_POLICY_PATH`); the approver may equal the initiator only when that record has `prevent_self_review=false` (operator policy 2026-10-24, same GitHub account `ajoe734`; readback `production-same-account-approval-policy.json`). Without a policy record the strict distinct-approver default applies, baseline posture.
2. FE artifact bytes must equal manifest `artifact_sha256`; an existing release directory with different content is refused (never overwritten, never rebuilt). Immutable archives (`fe/artifacts/<id>.tar.gz` + `.sha256`) and manifests (`manifests/<id>.json`) are retained.
3. Snapshot preflight and healthy retained baseline (`baseline_health`, skipped only when `PROD_BOOTSTRAP_FIRST_RELEASE=true` and no `state/baseline.json` exists).
4. Journal restore targets (`prev_fe_target`, `prev_upstream.caddy`, `prev_runtime_config.js`, `prev_manifest.json`, `prev_compose.env`, `baseline_release.json` with full `backend_images` closure), stage FE + `deployment.json` into `fe/releases/<id>`, start green on the alternate port.
5. Gates: health, auth, CORS, tenant, read-only, schema, old/new compatibility (on two-release paths requires both old and new ports ready concurrently; on explicit `PROD_BOOTSTRAP_FIRST_RELEASE=true` without `state/baseline.json`, verifies the verified versioned SQL and live catalog against the manifest `migration_set_sha256` and candidate port liveness without requiring a nonexistent old port). Failure stops green; blue is untouched.
6. Singleton hand-off (`drain_singletons`, `activate_singletons`, `gate candidate_ready`), then atomic upstream replace + reload, external `runtime-config.js` + `active_manifest.json` update, atomic `fe/current` symlink switch.
7. Post-switch exact identities (`post_identity`: local `.artifact_sha256`, BFF + singleton `Config.Image`, public hosted FE `/deployment.json` (`no-store`) and public hosted BFF `/bff/version` + `/bff/readyz`). Any failure after step 6 starts runs `prod_rollback.sh` (using `baseline_env` so baseline singleton and owner images are restored, never candidate `release_env`, and verifying restored public FE/BFF identities via `baseline_health`), which must complete within 120 s (otherwise exit 6).

Exit codes: 0 active, 1 refused/aborted before traffic, 2 rolled back, 3 rollback failed, 4 no rollback target (bootstrap), 6 rollback over budget.

## 4. Bootstrap and data

First release needs explicit `PROD_BOOTSTRAP_FIRST_RELEASE=true` and no on-host `state/baseline.json`. The unmodified staging-qualified manifest may carry `rollback_baseline`, which is treated only as retained rollback ARTIFACT metadata (staging promotion requires it) and never as a healthy incumbent or rollback target; incumbent posture comes solely from genuine on-host baseline state (a declared bootstrap with an existing baseline, or a non-bootstrap release without one, is refused); failure after partial switch mutations drains any started singletons (`drain_singletons`), stops green (`stop_green`), removes `fe/current` and `active_manifest.json`, restores the explicit 503 no-active-release upstream placeholder (`respond "no production release active" 503`), reloads Caddy, and exits 4 (`prod_aborted` with `no_rollback_target`, never an invented healthy target). Application rollback is not database recovery. The production data disk is never overwritten or recreated by any script here; recovery uses snapshots and the drills below.

Durable application state: runtime-manager (`/data/runtime`: `runtime_bindings.json`, `internal_api_commands.json`), governance and deployment (`/data/governance`) and the BFF session lifecycle store plus projector (`/data/bff`) use the same selected store paths as Stage, bind-mounted from `$PROD_DATA_MOUNT/{runtime,governance,bff}` (created by `bootstrap.sh`). Both BFF colors mount identical directories (governance/runtime read-only), so a color switch, container replacement or rollback neither loses nor splits acknowledged state, and nothing lives in container `/tmp`. Per-owner product stores live under `$PROD_DATA_MOUNT/product/<owner>`. Backup is the Stage-proven pair: `pg_dump -Fc` of the product database plus a tar archive of exactly `bff`, `runtime`, `governance`, `product`; restore readability is proven by restoring both (catalog hash, row counts, owner-store file manifest) into scratch targets, never over the live data disk.

### Product owner closure (packaging extension)

The control compose packages the same existing product owners and Agora request worker Stage packages (`staging_sanitize.PRODUCT_OWNER_IMAGE_KEYS`, the single source; `bootstrap.sh` imports it and `prod_ops.sh` carries an identical list asserted equal by tests): registry, telemetry, incidents, postmortems, evolution, lineage-read, reconciliation-drift-svc, persona, consultation-svc, source-ingest, training-session-svc, policy-learning-svc, research-orchestrator-svc, memory, capital, openclaw-gateway-adapter (profile `core`, exactly one instance, no published ports, native Postgres stores, secrets only from `PROD_SECRETS_ENV_FILE`) and `agora-interaction-worker` (profile `singleton`), plus the existing binding-scoped `paper-fleet-reconciler` and artifact `paper-signal-producer` (profile `singleton`; manifest keys `paper_fleet_reconciler`/`paper_signal_producer` MUST equal the `training_session_svc` digest, enforced by `release_manifest.py`; no image is built) with the existing Redis `signal-store` (profile `stateful`, never recreated per release, data under `${PROD_DATA_MOUNT}/signal-store`; reconciler ledger under `${PROD_DATA_MOUNT}/runtime/paper-performance`). The Paper owners are drained/resumed with the lifecycle projector (exactly one each, never per color); service tokens, the controller-only reconciler DSN and the producer telemetry DSN come only from `PROD_SECRETS_ENV_FILE`; producer strategy is the Source-owned `artifact` snapshot strategy and domain failures remain visible (no smoke signals, no artificial freshness). The BFF (both colors; Stage `operator-bff`) reads the reconciler at `PANTHEON_PAPER_FLEET_RECONCILER_URL=http://paper-fleet-reconciler:8011` and the producer health file through a read-only `${PROD_DATA_MOUNT}/product/health:/data/health` mount; the reconciler gets `PANTHEON_DEPLOYMENT_SHA`/`GIT_SHA` bound to `PANTHEON_BACKEND_COMMIT`, and bootstrap requires the isolated-db `RECONCILER_LOOP_CONTROLLER_DSN` in the secrets file. A retained baseline that predates the Paper workers is a valid rollback target: its retained owners are restored at their exact digests and the absent Paper workers are removed (no image is invented); a partial closure of the older owners still fails closed. Stage packages the same services. No new framework, no Dev-only principal issuers or periodic producers/dispatchers; the OpenClaw adapter has no gateway/credentials and every adapter write flag is off.

- Acquisition: the manifest must carry a digest-pinned image for every owner key (`OWNER_IMAGES` fails closed otherwise); `bootstrap.sh` and `prepull_images` pull only those `@sha256` refs for the candidate and, when a retained baseline exists, the baseline. Owners start with `--no-recreate` at `start_green` and are updated only in `activate_singletons`.
- Singleton preservation: the Agora request worker is drained and activated together with the lifecycle projector, never per color, and the post-activation readback requires every owner to run exactly once at the exact candidate digest (`ACTIVATE` otherwise).
- Rollback: `baseline_env` restores each product owner to the exact retained baseline digest. A baseline that predates the product owners must carry none of them; rollback then removes the candidate's owners by compose labels and never starts a placeholder image. A partial baseline product closure is unrestorable and fails closed (`BASELINE_ENV_MISSING`). Application rollback is not database recovery: product-owner store schemas are covered by the snapshot/restore drills, not by rollback.
- Secrets validation (`PROD_REQUIRE_FULL_SECRET_KEYS=true`) requires every product DSN (isolated production database) and service/JWT secret and rejects Dev default credentials.

## 5. Hosted drills (not executed; `external_wait`)

- Backup/restore drill: restore latest snapshot to a scratch disk, record measured RPO against `PROD_RPO_SECONDS` and RTO against `PROD_RTO_SECONDS`.
- VM replacement drill: replace the control VM from the template, attach the existing data disk, verify identities.
- Two non-capital production-shaped deploy/rollback drills, each with rollback under 120 s.
- Protected exact-release publication, then a 30-minute observation window. Thresholds come from measured policy approved by the named owners; no green SLO is assumed here.

## 6. Missing hosted prerequisites (recorded, not forged)

Published staging evidence: public-safe allowlisted `staging_verified` summaries for release `pantheon-20261028-102` (manifest `sha256:617e2936…bcf3`, runs 41579314601 and 41581135775 on control commit `9df41340`, rollback 107.727 s / 108.599 s, 4-resource cleanup with readback each) are the top-level fields of `docs/deployment/evidence/ENV-PROD-CONTROL-IMPL-001/evidence.json` (latest run 41581135775; both runs under `staging_verification_runs`) and pass `prod_preflight.check_staging_evidence`; usable as `staging_evidence_path`. Both runs skipped hosted QA (QA 0, step 9), so this is NOT complete acceptance and release 102 is infrastructure evidence only.

Release baselines (source scope only; nothing below has been executed):

- Stage run 42145201460 (2026-11-06, QA300) proved operator login but failed viewer and foreign-tenant QA on release 102, so 102 must never be the first Prod baseline. Stage infrastructure 2/2 on 102 stays accepted as infrastructure evidence only.
- First healthy baseline A is `pantheon-20261107-103` (BFF `2638f809c`, FE `979294ceb`). Candidate B is `pantheon-20261107-104` (BFF `d77e03a05`, FE `b7cd3d847`), whose `rollback_baseline` is A. Manifests: `deploy/staging/releases/pantheon-20261107-103.json` and `-104.json`.
- Each needs its own `staging_verified` evidence with the three real accounts inside the QA300 window, because evidence binds to the exact `release_id` and manifest sha256.
- Prod sequence (all still pending): protected bootstrap of A; backup/restore with RPO 3600 s and RTO 1800 s; fresh-boot VM replacement with the existing data disk and IP; A to B then real rollback to A, twice, each under 120 s; final protected B plus 1800 s observation.

`PROD_*` values are GitHub Environment-scoped in `production` (read-only readback: 20 required names present), not repository-only.

Production project/billing/domain/IdP, WIF/SA/IAM, `production` GitHub Environment with required reviewers (ajoe734; prevent_self_review=false), `PRODUCTION_RELEASE_ENABLED`, ops adapter and artifact transport on the control VM, approved RPO/RTO/retention/on-call/rollback owner, and DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002 / all12 chain `h12-01a11-20261023-9aeb` (not rerun or relabeled here).

## Public runtime write posture (`VITE_BFF_REAL_WRITES`) — source wiring

Staging (`staging-ephemeral-deploy.yml`) and production (`production-release.yml`) each take an explicit
per-dispatch input named `VITE_BFF_REAL_WRITES` (string `true`/`false`, default `false`). It never inherits a shared
Dev setting.

- Default / `false` / empty: the key is **absent** from `/runtime-config.js`, so canonical bytes and `configSha256`
  are identical to the legacy payload.
- Explicit `true`: rendered only into the **candidate** runtime config and included in its new digest. Any other value
  is rejected (`PUBLIC_REAL_WRITES_INVALID`; the production preflight job rejects it before any Environment access).
- Baseline renders (staging baseline template, production synthesized journal baseline) never carry the flag. Rollback
  restores the journaled runtime-config bytes unchanged. `deploy/production/bootstrap.sh` placeholder stays legacy.
- Backend `PANTHEON_REAL_WRITES_ENABLED`, `PANTHEON_PROVIDER_ENABLED`, `PANTHEON_LIVE_BROKER_ENABLED` remain `false`.

This is source-only; no Stage/Prod was dispatched. Hosted acceptance stays `external_wait`.
