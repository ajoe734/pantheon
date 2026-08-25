# Capability Gap Matrix (Phase 0 Baseline)

- **Measurement Date:** 2026-08-25
- **Baseline Context:** `pantheon-lupin-dev` (GCP project: `pantheon-lupin-dev-20260719`, Zone: `asia-east1-b`)
- **Target Design:** [`vm-dev-staging-prod-management-plan.md`](../../vm-dev-staging-prod-management-plan.md)

---

## 1. Executive Summary

This matrix evaluates the current Dev VM operational capabilities against the approved target management plan across four key operational domains:
1. **Frontend / BFF Paired Release & Traffic Switching**
2. **Database Migration & Schema Compatibility**
3. **Worker Singleton Ownership & Concurrency Lifecycle**
4. **Backup, Restore, and Disaster Recovery**

---

## 2. Capability Gap Matrix

| Domain / Capability | Current Dev VM Baseline (2026-08-25) | Required Ephemeral Staging Target (Phase 2) | Required Low-Resource Prod Target (Phase 3) | Gap Classification & Resolution Phase |
|---|---|---|---|---|
| **FE/BFF Release Admission** | Exact-pair admission via `scripts/agora_compat_manifest.py` verifying git SHAs and contract hashes. | Immutable release manifest with artifact SHA-256 and container image digests. | Digest-pinned immutable manifest; no dynamic source checkout during deployment. | **Medium** (Phase 1 Build-once + Manifest Schema) |
| **FE Live Ingress & Cutover** | Host-level Caddy reverse proxy; atomic symlink swap under `/var/www/pantheon-dev-fe`. | Release-specific temporary hostname and HTTPS cert on ephemeral Staging VM. | Pre-validated green candidate on alternate local port, upstream reload, then atomic symlink swap. | **Low** (Current Caddy symlink design directly portable; needs automated alternate-port green probe) |
| **FE/BFF Failure Compensation** | Fail-closed automatic rollback: restores recorded baseline BFF commit if FE verification fails. | Entire ephemeral Staging VM is marked failed, preserved for 2h debug, then auto-destroyed. | Immediate Caddy upstream switch back to blue BFF and symlink switch back to blue FE directory. | **Low** (Dev already implements rollback compensation; Prod needs blue container retention window) |
| **Multi-Environment Promotion** | Dev-only workflow; Staging is unavailable (suspended project `pantheon-benjamin-20260528`). | Ephemeral Staging VM provisioned on-demand per release candidate; destroyed after test. | Promotion consumes only `staging_verified` manifest; no rebuilding in Prod. | **High** (Phase 2 Ephemeral Staging Workflow & Template) |
| **DB Migration Execution** | Central `scripts/db_migrate.sh` executed via single `source-ingest-controller-migrate` one-shot in base `docker-compose.yml` (gating `source-ingest-scheduler`); no Alembic migration path exists, and `docker-compose.control.yml` / `docker-compose.staging-full.yml` omit migration services entirely (requiring manual or bootstrap execution). | Forward migration rehearsal on sanitized snapshot data before service startup. | Expand/contract migration policy; zero-downtime backward-compatible schema changes. | **High** (Phase 1 Compose migration parity + Phase 2 Expand/contract guard & snapshot restore pipeline) |
| **DB Schema Backward Compatibility** | Manual review of PR schema changes; no automated dual-version compatibility test. | Staging gate verifies old application against new schema, and new application against new schema. | Mandatory two-release cycle for schema changes: expand in release $N$, contract in release $N+1$. | **High** (Phase 2 Dual-version compatibility gate) |
| **Worker Singleton Ownership** | Single container instances in Compose; `REQUIRED_LOOP_WORKERS` / `FORBIDDEN_DUPLICATE_WORKERS` CI checks. | Automated verification of single owner and zero duplicate message consumers across all 12 loops. | Drain / pause singleton workers before BFF traffic switch; restore with single active owner. | **Medium** (Phase 1/2 Worker pause/drain runbook and lock verification) |
| **Worker Duplicate Prevention** | Statically forbidden containers (`pantheon-paper-runtime`) checked during deploy. | Runtime assertion that consumer groups have exactly 1 active consumer connection in NATS/Postgres. | Strict singleton fencing to prevent split-brain event processing during blue/green release. | **Medium** (Phase 2 Ephemeral Staging validation gate) |
| **Execution Boundary Isolation** | Dev VM co-locates `runtime-manager` & `broker` but disables live trading (`PANTHEON_LIVE_BROKER_ENABLED=false`); historical staging-live set `PANTHEON_LIVE_BROKER_ENABLED=true` in control stack but isolated credentials to VM2 (suspended project `pantheon-benjamin-20260528`); future ephemeral staging targets live-broker-off by default (§5, §9.3). | Standard releases use single VM (live-broker-off); Execution releases provision temporary dual-VM staging. | Dedicated Prod Execution VM with strict firewall, internal-only VPC, and isolated broker secrets. | **High** (Phase 4 Real-capital execution boundary) |
| **Pre-Deploy Snapshotting** | File/git snapshot in `~/pantheon-deploy-snapshots/` (head, status, diff, ps). No DB volume snapshot. | Ephemeral VM boot/data disks created from sanitized base snapshot; torn down after verification. | Automated GCP disk snapshot of Postgres persistent data disk prior to green container startup. | **High** (Phase 3 Persistent disk snapshot automation) |
| **Restore Rehearsal & Verification** | No automated restore testing; manual recovery from git only. | Mandatory restore rehearsal on every Staging release before marking `staging_verified`. | Documented and drilled recovery runbook from last-known-good snapshot. | **High** (Phase 2 Staging restore rehearsal gate) |
| **Disaster Recovery (RPO/RTO)** | Not measured; single Dev VM recovery depends on manual reprovisioning. | RPO/RTO validated during automated staging teardown / rebuild drills. | RPO target < 1h, RTO target < 30m via instance template replacement and persistent disk attach. | **High** (Phase 3 Operator gate approval + DR runbook) |

---

## 3. Recommended Remediation Roadmap

1. **Phase 1 (Immutable Release & Profiles):**
   - Introduce Compose profiles (`core`, `workers`, `research`, `management-ai`, `execution`).
   - Implement build-once release manifest schema binding FE artifact SHA-256 and BFF image digest.
   - Add container resource limits (CPU/Memory) based on Phase 0 measured peak.

2. **Phase 2 (Ephemeral Staging):**
   - Author GCE Staging instance template and automated `provision -> deploy -> verify -> teardown` workflow.
   - Implement sanitized database snapshot restore pipeline and restore verification gate.
   - Implement TTL labels and orphan cleanup scheduled workflow.

3. **Phase 3 (Low-Resource Prod Control):**
   - Create independent GCP production project and GitHub Environment with required reviewers.
   - Implement same-host blue/green BFF cutover with alternate local port validation and Caddy reload.
   - Automate pre-deploy database disk snapshot and 30-minute observation window.

4. **Phase 4 (Real-Capital Execution Boundary):**
   - Deploy dedicated Prod Execution VM when operator explicitly approves live trading mode.
   - Implement VPC-internal control-to-execution contract and broker secret isolation.
