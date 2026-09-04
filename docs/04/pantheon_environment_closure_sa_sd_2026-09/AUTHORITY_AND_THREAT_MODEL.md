# Authority & Threat Model — Pantheon Environment Closure

- Document: `AUTHORITY_AND_THREAT_MODEL.md`
- Status: Canonical Security & Governance Specification
- Date: 2026-09-04
- Baseline: `origin/dev`
- Task: `ENV-STAGING-PROD-PLAN-001`
- Companion Documents: [`INDEX.md`](INDEX.md), [`SA.md`](SA.md), [`SD.md`](SD.md)

---

## 1. Executive Summary

This document formalizes the **Authority Separation Model** and **Threat Model** governing the Dev, Staging, and Production environments of the Pantheon platform.

The foundational tenet is **fail-closed defense-in-depth**:
- Autonomous AI agents and development tooling are strictly untrusted regarding production resources and live capital.
- Production deployment and real-money trading are governed by cryptographic evidence, multi-party human approval with MFA, and physical network segmentation.

---

## 2. Authority Separation Model

Pantheon explicitly separates four operational authorities:

```text
+---------------------------------------------------------------------------------------+
| 1. OPERATOR AUTHORITY (Human/Ops & Trading Risk Officer)                              |
|    - Sole authority to authorize Production deployment and enable real capital.       |
|    - Enforced via GitHub Environment protection rules, MFA, and audit logging.       |
+---------------------------------------------------------------------------------------+
                                           |
                                           v
+---------------------------------------------------------------------------------------+
| 2. DELIVERY INFRASTRUCTURE AUTHORITY (GitHub Actions & WIF)                           |
|    - Validates release manifests, builds immutable digests, orchestrates deployments. |
|    - Authenticates via short-lived GCP Workload Identity Federation tokens.           |
|    - Zero permanent GCP service account keys in repository secrets.                   |
+---------------------------------------------------------------------------------------+
                                           |
                                           v
+---------------------------------------------------------------------------------------+
| 3. PRODUCT RUNTIME AUTHORITY (BFF, Adapters, Workers, Projectors)                     |
|    - Executes business logic, serves API requests, projects state.                    |
|    - Confined within containerized namespaces; no host filesystem or cloud admin access.|
+---------------------------------------------------------------------------------------+
                                           |
                 +-------------------------+-------------------------+
                 | (Strictly Air-Gapped)                             | (Task Leases Only)
                 v                                                   v
+---------------------------------------+   +-------------------------------------------+
| 4. EXECUTION BOUNDARY (Prod Exec VM)  |   | 5. DEVELOPMENT TOOLING (Supervisor/Agents)|
|    - Physical isolation (no public IP)|   |    - Git task worktrees, local testing    |
|    - Mounts broker credentials (tmpfs)|   |    - ZERO cloud deployment authority      |
|    - Governed by out-of-band kill sw. |   |    - ZERO access to production or secrets |
+---------------------------------------+   +-------------------------------------------+
```

### 2.1 Least-Privilege IAM Personas

| IAM Persona | Scope | Allowed Actions | Denied Actions |
|---|---|---|---|
| `pantheon-dev-deployer` | Dev VM only | Restart Dev compose, pull dev images, update Caddy | Any access to Staging or Production GCP projects |
| `pantheon-stg-deployer` | Staging project | Create/delete temporary VMs with `environment=staging` label, restore sanitized snapshots | Access to production data or production VPC |
| `pantheon-prod-deployer`| Prod Control VM | Pull release images, update Caddy upstream, restart blue/green candidate | Read broker secrets, mutate firewall rules, modify execution VM |
| `pantheon-exec-runtime` | Prod Execution VM | Read broker secrets from Secret Manager at boot, send telemetry to Control VM | Public internet ingress, cross-project access |

---

## 3. Threat Model & Security Controls

### Threat 1: Cross-Environment Data Contamination (Dev/Staging touching Prod)
- **Attack Vector**: Misconfigured database connection strings or shared cloud storage causing Dev to read/write production tables or overwrite production state.
- **Impact**: Catastrophic data corruption, loss of audit lineage, or breach of user confidentiality.
- **Controls**:
  1. *Project Isolation*: Dev, Staging, and Production reside in completely distinct GCP projects.
  2. *Network Segregation*: VPCs are not peered across environments. Dev cannot resolve or reach Prod database IP addresses.
  3. *Credential Segregation*: Dev and Staging use environment-specific credentials. Production DB credentials exist exclusively in the production GCP project Secret Manager.
  4. *Sanitized Snapshots*: Staging databases are initialized exclusively from sanitized, anonymized snapshots containing zero production PII or credentials.

### Threat 2: Unauthorized or Accidental Live Capital Trading
- **Attack Vector**: A bug in algorithm logic, rogue worker commit, or misconfigured environment variable enabling live orders in Dev or unverified Staging.
- **Impact**: Real financial loss, unauthorized leverage, regulatory violation.
- **Controls**:
  1. *Hardcoded Default*: `PANTHEON_LIVE_BROKER_ENABLED=false` is enforced at the container entrypoint.
  2. *Physical VM Isolation*: Real trading software (`runtime-manager`, `broker-adapter`) runs exclusively on the dedicated Prod Execution VM (Phase 4). It is never installed on Dev or Staging VMs.
  3. *No Public Ingress*: The Prod Execution VM has no public IP address and accepts connections solely from the Prod Control VM over mTLS on internal port 28081.
  4. *Two-Man Rule*: Transitioning from Paper to Live trading requires cryptographic sign-off from both Human/Ops and the designated Trading Risk Officer.
  5. *Fail-Closed Kill Switch*: If network connectivity between Control and Execution is severed, the Execution VM automatically cancels all open orders within 500ms and halts execution.

### Threat 3: Supply Chain Tampering & Digest Substitution
- **Attack Vector**: An attacker replaces a container image or frontend static bundle in Artifact Registry between staging verification and production deployment.
- **Impact**: Compromised application logic running in production.
- **Controls**:
  1. *Build Once*: Artifacts are built once in CI and never rebuilt for downstream environments.
  2. *Immutable Digest Pinning*: The release manifest binds exact container image digests (`sha256:...`) and frontend artifact checksums. Promotion scripts deploy strictly by digest, ignoring mutable tags like `latest` or `master`.
  3. *Admission Cryptographic Verification*: Pre-deploy hooks verify the sha256 checksum of the candidate bundle against the immutable release manifest before starting the container.

### Threat 4: Autonomous Worker Privilege Escalation
- **Attack Vector**: An AI auto-worker attempts to bypass review gates, alter deployment scripts, or push directly to protected branches.
- **Impact**: Unreviewed code deployed to production.
- **Controls**:
  1. *Branch Protection*: `dev` and `master` enforce GitHub branch protection with 3 required status checks and mandatory PR review. Workers cannot push directly.
  2. *Scope Check Hook*: `.githooks/pre-commit` and `check_commit_scope.py` reject any commit touching files outside the declared task brief scope.
  3. *No CI/CD Secret Exposure*: Auto-workers run in local worktrees with zero access to GitHub Actions repository secrets or cloud tokens.

### Threat 5: Resource & Billing Exhaustion (Denial of Wallet)
- **Attack Vector**: Failed staging runs leaving dozens of VMs and persistent disks running undetected.
- **Impact**: Excessive cloud billing charges.
- **Controls**:
  1. *Enforced TTL*: All ephemeral resources require `expires_at` metadata ($\le 2$ hours).
  2. *Automated Sweeper*: A separate GitHub Actions cron sweeps and deletes expired staging resources every 30 minutes.
  3. *Budget Alerts*: GCP Cloud Billing alerts configured at 50%, 80%, and 100% thresholds with automated webhook alerting.
