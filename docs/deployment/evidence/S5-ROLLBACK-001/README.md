# S5-ROLLBACK-001: Exact Prior FE/BFF Rollback Drill and Compensation Evidence

## 1. Executive Summary & Governance Metadata

- **Task ID**: `S5-ROLLBACK-001`
- **Task Title**: Exercise exact prior FE/BFF rollback drill
- **Task Class**: `execution` / `hosted`
- **Owner**: `Antigravity2`
- **Reviewer**: `Claude`
- **Phase**: `step-5-rollback`
- **Target Repository**: `pantheon` (cross-repository coordination with `execute-plans`)
- **Branch**: `task/S5-ROLLBACK-001` (from `dev` tip, commit `4519908b7`)
- **Canonical Dependencies**:
  - `S5-PAIR-001` (`status=done`, `satisfied=true`, PR #5817 merged into `dev` at `5eb6f8dda0909760cacdff6fac5c1042521df6d1`)
  - `S5-JOURNEYS-001` (`status=done`, `satisfied=true`, PR #5875 merged into `dev` at `eb3712813b81897fa7e57f6992a441f7af010aa5`)
- **Canonical Status Reference**: Operator 2026-09-13 explicit resumption of Step 5 dispatch; sole surviving paired-rollback owner is `S5-ROLLBACK-001`.
- **Status Root**: `/home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/coordination-root`
- **Command Runtime**: `PANTHEON_COMMAND_ROOT=/home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/command-runtimes/d5d0d3ea85fe83c854713b316bf7ecdabdd885e4`
- **Evidence Timestamp**: `2026-09-17T06:45:00Z`

This document records the canonical evidence reconciliation for `S5-ROLLBACK-001`. As mandated by the operator 2026-09-13 handoff instructions and governing fail-closed rules:
1. It reconciles Release F automatic exact failure compensation and Release G / Release H manual recovery after GitHub API 500 interruptions, without claiming the latter were fully automatic.
2. It documents the technical readiness and governance boundaries of the remaining bidirectional roundtrip drill (accepted Release I -> exact prior Pair E -> SAME accepted Release I bytes).
3. It truthfully records that the bidirectional roundtrip drill remains **unexecuted** and held pending an explicit operator execution grant (`execution_authorization.state = "pending_authorization"`, `grant: null`) and a non-overlapping dev maintenance window. The temporary rollback launcher script was not auto-run, no MFA or execution grants were fabricated, no duplicate rollback frameworks were built, and existing live dev data and paper/live boundaries were strictly preserved.

---

## 2. Canonical Acceptance Criteria Verification Matrix

| # | Acceptance Criterion | Verification Status | Detailed Evidence & Operational Truth |
|---|---|---|---|
| **1** | **Preserve immutable prior FE bytes, BFF image/config identity, manifest and lease epoch before the drill; no source rebuild is allowed during restore.** | **PASSED** | The byte-preserving restore architecture (`scripts/dev_release_artifacts.py`, `docs/deployment/dev-exact-artifact-rollback.md`) validates and restores exact image archives, nonsecret configuration, manifest bytes, and frontend CAS symlinks without rebuilding from source. In Releases F, G, and H, exact prior Pair E (`backend: cdc02e2c65136275e00828950c18e53672fa5a40`, `frontend: ba0b47f445517ded95d910ee9a62e779a6a8d9d1`) was restored from retained Docker image archives and CAS release bundles using `--no-build --pull never --no-deps`. The baseline snapshot for Release I likewise preserved exact prior Pair E (`dist_sha256: 1452f7b3...`, `manifest_sha256: 54d73cbf...`, image IDs `bef3371f...`, `45847dcc...`, `9d5ec848...`). Focused unit tests (78 passed, 1 skipped) confirm archive checksum verification, immutable Compose override generation, and `--no-build` enforcement. |
| **2** | **Force a bounded candidate failure or controlled drill point, restore the exact prior pair with CAS/lease protection, and verify both FE and BFF served identities.** | **RECONCILED (F/G/H RECOVERIES EXERCISED; BIDIRECTIONAL DRILL UNEXECUTED)** | Bounded candidate failure and exact prior pair restoration were verified in three historical releases: Release F (gate failure in run 34746588532 -> automatic restore of E), Release G (GitHub API 500 in run 34748213471 -> manual restore of E under lease `48e61b04-488d-445b-8656-1cdb859115aa`), and Release H (GitHub API 500 in run 34749911751 -> manual restore of E under lease). In all cases, restored FE and BFF served identities were verified (`public.source_sha = cdc02e2c65136275e00828950c18e53672fa5a40`, `frontend.frontend_sha = ba0b47f445517ded95d910ee9a62e779a6a8d9d1`). However, the planned bidirectional roundtrip drill (`accepted I -> exact prior E -> same accepted I`) has **not been executed** on the live environment. The launcher `run-I-roundtrip.py` was not auto-run per operator mandate, and canonical execution authorization remains `pending_authorization` (`grant: null`). |
| **3** | **Verify strict auth, CORS, readiness, paper-only/safe-write defaults and durable reload after restore. Missing prior artifact, digest or readback fails closed.** | **PASSED** | Each compensation readback (`artifact_readback` in `release-compensation.json` and `manual-release-compensation.json`) verified: (a) strict authentication (`strict_auth_denials_verified: true`, `auth_mode: strict`, `auth_stub: false`); (b) CORS allowing canonical dev origin `https://app.dev.mvl-cap.tw` with credentials; (c) core service readiness (`protected_owners_unchanged: true` across capital, deployment, outbox, principal issuer, governance, registry, and runtime manager); (d) paper-only safe defaults (`PANTHEON_LIVE_BROKER_ENABLED=false`, `BROKER_PAPER_ENABLED=true`); (e) missing archives or altered digest fails closed per `validate_images` and `restore_images`. |
| **4** | **Publish separate candidate failure, compensation result and restored-pair receipt; do not call compensation a successful candidate release.** | **PASSED** | In all three historical runs (F, G, H), candidate failure evidence (`release-controller-rejected.json`, controller failure log SHA256) and compensation receipts (`release-compensation.json`, `manual-release-compensation.json`) were published as distinct artifacts. The operation is explicitly marked `operation: "restore"`, `outcome: "compensated"`, and `outcome: "rejected"`. Compensation was never conflated with candidate acceptance. |

---

## 3. Historical Release Compensation Reconciliation (Releases F, G, H)

### 3.1 Release F: Automated Exact Compensation in Actions

- **GitHub Actions Run**: `34746588532` (Attempt 1, `nonprod-deploy.yml`)
- **Release Candidate ID**: `70e330ee62ccb720efb55ecefdb9961600609f1a949c70eafb9b1020c4fd12fc`
- **Rejected Candidate Pair**:
  - Backend SHA: `cdc02e2c65136275e00828950c18e53672fa5a40`
  - Frontend SHA: `43a1df560317cc85e1b7ecf3044e5e5cc931329a`
  - Controller Exit Code: `1` (rejected during Playwright / integration checks)
  - Failure Log Digest: `ce0f8f2c4a978ebd00bd63669616e6738bd8bfe9f052fe116864b768005c1d91`
- **Restored Prior Pair E**:
  - Backend SHA: `cdc02e2c65136275e00828950c18e53672fa5a40`
  - Frontend SHA: `ba0b47f445517ded95d910ee9a62e779a6a8d9d1`
  - Frontend Dist Digest: `1452f7b35e039ca92a10525e6929ef18ef3498270d7cf873d5cc51de275be3b8`
  - Frontend Target: `/var/www/pantheon-dev-fe-releases/20260913T074001Z-ba0b47f44551-gate-34744818811-34745406166-1-240646`
  - Restored Images:
    - `operator-bff`: `sha256:bef3371f6e0df6eec4d100905021b6c709cfbe454a6fa00057a3c00ba91228b7`
    - `agora-interaction-worker`: `sha256:45847dcc4ab19673b743bcc7ff596e0cd99f90a197c9ab8402bd9578518bcbab`
    - `loop-run-projector-scheduler`: `sha256:9d5ec8482a1c07d13a431b71def7e744c13ea5208d2a8dc8a3b785814b5084ad`
- **Execution Mode**: Fully automated exact compensation within the GitHub Actions workflow runner using the pre-captured baseline artifact.
- **Evidence Reference**: `docs/deployment/evidence/S5-ROLLBACK-001/release-f-compensation-reconciliation.json`

### 3.2 Release G: Manual Recovery Following GitHub API 500

- **GitHub Actions Run**: `34748213471` (Attempt 1, `nonprod-deploy.yml`)
- **Release Candidate ID**: `0ab6097e05231da126930e608608507afdba8cbcfa6a72029c9fe043168edf52`
- **Candidate Pair**:
  - Backend SHA: `cdc02e2c65136275e00828950c18e53672fa5a40`
  - Frontend SHA: `dbe737e0676640f1b9b2395b54fb3c0416099f8a`
- **Incident & Interruption**: During workflow execution, GitHub API returned HTTP 500 errors, terminating the Actions runner prematurely before automated compensation could complete.
- **Recovery Mechanism**: Manual operator recovery using the existing compensation entrypoint (`recover-G-prior.py`) executed under dev lease `48e61b04-488d-445b-8656-1cdb859115aa` in directory `pantheon-release-rollback-34748213471-1-AFORXc`.
- **Restored Prior Pair E**: Exact prior pair E restored and verified identical to Release F baseline.
- **Mandatory Disclosure**: Explicitly documented as **manual recovery**, not automated Actions compensation.
- **Evidence Reference**: `docs/deployment/evidence/S5-ROLLBACK-001/release-g-manual-recovery-reconciliation.json`

### 3.3 Release H: Manual Recovery Following GitHub API 500

- **GitHub Actions Run**: `34749911751` (Attempt 1, `nonprod-deploy.yml`)
- **Release Candidate ID**: `2677e22ccf35cb3c7322301523f70f28b64eb3c8d26f19dfb1c0cad253488252`
- **Candidate Pair**:
  - Backend SHA: `d7ce1036d87640a61f30aa3382200c55ef2165cd`
  - Frontend SHA: `dbe737e0676640f1b9b2395b54fb3c0416099f8a`
- **Incident & Interruption**: Similar GitHub API 500 outage interrupted Actions runner execution.
- **Recovery Mechanism**: Manual operator recovery using existing compensation entrypoint (`recover-H-prior.py`) in directory `pantheon-release-rollback-34749911751-1-C8b0Tr`.
- **Restored Prior Pair E**: Exact prior pair E restored and verified.
- **Mandatory Disclosure**: Explicitly documented as **manual recovery**, not automated Actions compensation.
- **Evidence Reference**: `docs/deployment/evidence/S5-ROLLBACK-001/release-h-manual-recovery-reconciliation.json`

---

## 4. Bidirectional Roundtrip Drill Architecture & Operational Boundary

### 4.1 Planned Architecture (Accepted I -> Exact Prior E -> SAME Accepted I)

The planned bidirectional drill exercises a complete non-destructive roundtrip without source rebuild:
1. **Starting Point**: Accepted Release I currently serving live (`backend: ae41705b...`, `frontend: dbe737e0...`).
2. **Step 1 (Rollback to E)**: Atomic CAS switch of frontend symlink to prior Pair E (`ba0b47f4...`) and Compose override restore of BFF containers (`bef3371f...`, `45847dcc...`, `9d5ec848...`) under active dev lease watchdog.
3. **Step 2 (Verify E Served Identities)**: Verify public HTTP readbacks, version endpoints, strict auth, and nonsecret config.
4. **Step 3 (Restore to I)**: Restore exact Release I frontend dist bytes (`01fbeb26...` / `eab97170...`) and exact candidate images (`a9ceabe6...`, `b01d9e27...`, `5fd644bc...`).
5. **Step 4 (Verify I Served Identities)**: Verify return to exact initial accepted state with byte-identical digests.

Tooling components verified in preflight (`scripts/dev_release_artifacts.py`, `scripts/dev_environment_lease.py`, `scripts/atomic-symlink-cas.py`, `rollback-config-preflight.py`, `rollback-readonly-layout.py`).

### 4.2 Operational Truth & Governance Hold Boundary

As recorded in `CURRENT-DELIVERY.zh-TW.md` (§ 尚未完成的驗收 & 舊部署任務唯讀對帳):
> "accepted I → exact prior E → 同一 accepted I 的雙向 drill 尚未執行。以前失敗發布的補償不是此雙向 drill。只使用現有 image archives、Compose、FE CAS 和部署入口，不 rollback DB 或重啟七個 business owners。"
>
> "S5-ROLLBACK-001：todo | F 有 Actions 內自動 exact compensation；G／H 的 API500 中斷後，是沿既有入口人工完成 exact prior restore。尚無 accepted I → exact prior E → 同一 accepted I 的完整 roundtrip。 | 這是原本未完成的操作驗收，不是缺第二套 rollback 程式。保留事實；本輪不執行 drill、不新增腳本。"

Furthermore, canonical task governance mandates:
- `execution_authorization.state`: `pending_authorization`
- `execution_authorization.grant`: `null`
- Operator mandate: "Hosted work requires a genuine execution grant; never fabricate MFA or operator proof. Do not auto-run the unexecuted temporary rollback script or expand safety gates."

**Definitive Operational Verdict**:
The bidirectional roundtrip drill remains **UNEXECUTED / HELD PENDING OPERATOR EXECUTION GRANT AND DEDICATED DEV WINDOW**. The temporary script `run-I-roundtrip.py` was strictly left unexecuted. No live containers were disrupted, no databases were rolled back, and no execution credentials were manufactured.

---

## 5. Technical Verification & Unit Test Suite

The underlying artifact capture, validation, override generation, and rollback restoration mechanisms were verified via the focused release and rollback test suite:

- **Test Command**: `pytest scripts/test_agora_compat_manifest.py scripts/test_check_shared_deploy_workflow_disabled.py scripts/test_cross_repo_release_controller.py scripts/test_deploy_nonprod_vm.py scripts/test_dev_release_artifacts.py`
- **Result**: **201 passed, 3 skipped in 91.76s**
- **Test Suite Breakdown**:
  - `test_agora_compat_manifest.py`: 22 passed
  - `test_check_shared_deploy_workflow_disabled.py`: 8 passed
  - `test_cross_repo_release_controller.py`: 70 passed
  - `test_deploy_nonprod_vm.py`: 23 passed, 2 skipped
  - `test_dev_release_artifacts.py`: 78 passed, 1 skipped

Key verified invariants:
- Archive validation fails closed on truncated, altered, or missing Docker image archives.
- Compose override generation enforces `--no-build --pull never --no-deps`.
- Frontend CAS verification strictly rejects non-directory targets and hash mismatches.
- Nonsecret config validation verifies `PANTHEON_BFF_AUTH_MODE=strict` and `PANTHEON_BFF_AUTH_STUB=false`.

---

## 6. Delivered Artifacts & Cryptographic Seal

The complete delivery evidence for `S5-ROLLBACK-001` is contained in `docs/deployment/evidence/S5-ROLLBACK-001/`:

1. `README.md`: This comprehensive report, acceptance matrix, and technical reconciliation.
2. `release-f-compensation-reconciliation.json`: Verified record of Release F automated exact compensation.
3. `release-g-manual-recovery-reconciliation.json`: Verified record of Release G manual recovery following GitHub API 500.
4. `release-h-manual-recovery-reconciliation.json`: Verified record of Release H manual recovery following GitHub API 500.
5. `roundtrip-drill-readiness-and-hold-boundary.json`: Technical specification and governance hold boundary for the bidirectional roundtrip drill.
6. `audit-seal.json`: Cryptographic SHA-256 seal covering all evidence files (excluding `evidence.json` to remain strictly acyclic).
7. `evidence.json`: The canonical task-scoped evidence manifest bound to the PR delivery head.
