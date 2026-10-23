# First Production Release — Current Readiness Gap Report

- Task: `ENV-RELEASE-READINESS-20261023` (owner Claude, reviewer Claude2)
- Collected: 2026-10-23T14:51Z, read-only, against `origin/dev` `854f33282912bc038533226e9a928b13ce8748a5`
- Evidence: [`evidence/ENV-RELEASE-READINESS-20261023/evidence.json`](evidence/ENV-RELEASE-READINESS-20261023/evidence.json)
- Nature: documentation only. No cloud, SSH, hosted probe, secret read, workflow dispatch or code change was made.

## 1. Bottom line

**The product cannot be released to production today, and Dev acceptance does not change that.**
Production and ephemeral Staging do not exist as configured, provisioned, served or accepted
resources. The source-level design is complete (`ENV-STAGING-PROD-PLAN-001`, done) and the old
staging deploy path is retired (`OPS-STAGING-LIVE-JOB-RETIRE-20261008`, done); neither created
any release infrastructure. The remaining work is two held implementation packets plus operator
choices (§6). Source delivery of this report is not hosted readiness.

Status vocabulary used below: **configured** (variable/setting exists) → **provisioned** (resource
exists) → **served** (hosted identity answers) → **accepted** (evidence reviewed). A row is only as
far along as its highest proven word; unavailable evidence is stated as such.

## 2. Environment status (GitHub metadata, 2026-10-23T14:51Z)

| Environment | Configured | Provisioned | Served | Accepted |
| --- | --- | --- | --- | --- |
| Dev | Yes: § 3.1 variables in `ajoe734/pantheon` match the management plan (project `pantheon-dev-20260902`, VM `pantheon-dev-deploy`, `asia-east1-b`, hosts `app.dev.mvl-cap.tw` / `api.dev.mvl-cap.tw`) | Not re-probed here (no SSH/cloud permitted); plan records the 2026-10-09/10 restore and static IP | Not re-probed. Root run `39966041271` (`workflow_dispatch`, sha `854f332…`) is `in_progress` at "Admit and switch exact FE/BFF release"; deploy-under-lease job succeeded; L1–L12 gate skipped | **No.** Owned by `DEV-SIX-AREA-HOSTED-CLOSEOUT-20261002` and `HOSTED-ALL12-OWNER-AUDIT-20261023`; not decided here |
| Staging | Only residue: repo variables `STAGING_BFF_CORS_ORIGIN` (a retired Lovable origin), `STAGING_CONTROL_ZONE`, `STAGING_EXEC_ZONE` (2026-05-08). No `staging` GitHub Environment in either repo | No (0 VM by design; no template, workflow or scripts) | No | No — `unavailable` |
| Production | Nothing: no `production` GitHub Environment in `ajoe734/pantheon` or `ajoe734/execute-plans`; no production-named variables or secrets | No project, VM, disk, DNS, TLS, monitoring | No | No — `unavailable` |

Environment lists returned only `dev` for both repositories; `dev` has no protection rules
(appropriate for Dev per plan § 13). Secret values were not read.

## 3. Requirement mapping

"Source" means repository code or documents; none of these rows is hosted-accepted.

| Requirement | Existing source/evidence | Precise remaining obligation |
| --- | --- | --- |
| Staging lifecycle and TTL cleanup | Design: plan §§ 4.2, 9; `SD.md`. Nothing executable: `scripts/deploy/` and `staging-*.yml` do not exist; `nonprod-deploy.yml` staging job retired | `ENV-STG-EPHEMERAL-IMPL-001`: provision/destroy scripts, 5-stage workflow, independent orphan sweeper, 2 h TTL labels. Acceptance = two consecutive zero-to-zero releases with empty inventory |
| Sanitized restore | Design only; capability matrix (`ops-vm-env-phase0-20260825`) records no DB-volume snapshot and no restore automation | Same packet: sanitized snapshot pipeline (mask PII, drop broker secrets) and a restore-readable verification gate per release |
| Immutable FE/BFF promotion | Dev exact-pair transaction, managed worktrees, gate-before-switch, served-identity check and exact prior-artifact rollback exist in `deploy_nonprod_vm.sh` / `nonprod-deploy.yml`. Plan Phase 1 (release manifest keyed by `release_id`, digest/checksum deploy) is not shown delivered by any artifact in this audit | Confirm or implement the manifest builder so Staging/Prod deploy digests, not checkouts. Needed before Staging can accept a manifest; fits the Staging packet's "deploy exact artifacts" stage, not a new platform |
| DB migration, backup, restore, rollback | Migration runs via one-shot controller; app rollback exists for Dev only; no pre-deploy DB snapshot (gap matrix) | Staging: forward migration + old-app/new-schema check + restore rehearsal. Prod: pre-deploy checkpoint snapshot, expand/contract rule, restore drill, approved RPO/RTO (§ 6) |
| TLS/DNS | Dev hosts on `mvl-cap.tw` with Caddy; sslip.io hosts are historical | Production domain and Let's Encrypt/Caddy config are `ENV-PROD-CONTROL-IMPL-001` and need an operator-chosen domain (§ 6) |
| Auth, tenant, secrets isolation | Dev uses Firebase/Identity Platform securetoken JWKS, strict role map, verified-email requirement; plan § 12 requires per-environment secrets, service accounts, WIF | Production IdP/issuer, role mapping and secret boundary must be set per environment by the Prod packet. Dev `DEV_BFF_MFA_*` values are a static product-config claim list, not a current gate; `development-tooling-mfa-retirement.md` removed tooling MFA and states it does not endorse them. No replacement gate is invented here |
| Monitoring and observation | Plan § 10.4 defines a 30-minute window; no Prod thresholds or alert backend exist | Prod packet: alert backend, thresholds, on-call owner; thresholds come from measured baseline |
| Rollback | Dev only (exact prior artifacts) | Prod: blue/green switch and sub-2-minute rollback drill, twice on non-capital data |

## 4. Source-only next slices

These are ordered obligations inside the held packets. This report does not dispatch, claim or
provision them; their infra holds are not lifted here.

1. **Manifest check (source, no infra).** Verify the Dev pipeline emits the § 7.2 manifest fields
   (FE/BFF commits, artifact sha256, image digest, compatibility and migration hashes). Any missing
   field is the first slice of the Staging packet.
2. **Staging scripts and workflows (source, then held).** Write `staging_provision.sh`,
   `staging_destroy.sh`, sanitize script, `staging-ephemeral-deploy.yml`, `staging-orphan-cleanup.yml`
   with dry-run/local tests. Source can merge only after review; no run until the hold is lifted and a
   non-production project and billing are confirmed.
3. **Residual cleanup (source/settings).** Remove the three leftover `STAGING_*` variables only when a
   dispatched owner confirms nothing reads them; that is a shared-settings change, not made here.
4. **Prod control scripts (source, then held).** Blue/green switch, atomic FE symlink, pre-deploy
   snapshot and rollback scripts with local contract tests; creation of the project, Environment and VM
   waits for § 6 choices and operator authorization.
5. **Execution isolation (`ENV-PROD-EXEC-ISOLATE-001`)** is out of scope for a paper-only release.

## 5. Paper-only release versus real capital

A paper-only product release needs Phases 2 and 3 plus Dev-accepted hosted closure. It requires
`PANTHEON_LIVE_BROKER_ENABLED=false`, no broker credential on the Prod control VM, and real writes
disabled unless time-boxed authorization exists. Real capital additionally needs Phase 4 and the
documented operator plus Trading Risk Officer authorization. Nothing here assumes consent for
business approvals, provider enablement or trades.

## 6. Operator choices still missing

1. Production GCP project name, billing account and region.
2. Production domain, DNS owner and TLS approach.
3. Launch mode: paper-only control VM first, or include real capital later (separate approval).
4. Required reviewers for the `production` GitHub Environment (distinct from the initiator).
5. Approved RPO, RTO (Phase 0 proposed <1 h / <30 m), backup retention, rollback owner and on-call.
6. Whether to lift the holds on `ENV-STG-EPHEMERAL-IMPL-001` and `ENV-PROD-CONTROL-IMPL-001`, and the
   staging project/quota to use. The packet text still names a retired non-prod project and "MFA"
   wording; it should be reconciled to § 3.1 identity and the MFA retirement before dispatch.
7. Production identity provider, issuer/audience and admin role mapping.

## 7. Unavailable evidence and limits

- Hosted Dev state, VM/disk/IP, DNS and TLS were not probed; only GitHub metadata was read.
- Root run `39966041271` was non-terminal at collection; its result is not asserted.
- Whether Phase 1 manifest tooling is fully delivered was not exhaustively audited (§ 3, row 3).
- Secret and variable values beyond non-secret identity names were not recorded.
