# Future Implementation Packets — Pantheon Environment Closure

- Document: `FUTURE_PACKETS.md`
- Status: Canonical Materialized Task Packets (Privileged & Undispatched)
- Date: 2026-09-04
- Baseline: `origin/dev`
- Task: `ENV-STAGING-PROD-PLAN-001`
- Acceptance Gate: Acceptance Criterion 3 ("Materialize any proposed implementation as separately privileged future packets that remain undispatched pending explicit authorized-operator/MFA authorization.")

---

## 1. Governance & Execution Freeze Invariant

All implementation work arising from this architectural design is strictly segregated into three **separately privileged future execution packets**.

### Non-Negotiable Invariants:
1. **Zero Automatic Dispatch**: These packets MUST NOT be dispatched, claimed, or scheduled automatically by the Pantheon supervisor, auto-workers, or autonomous cron loops.
2. **Explicit Human/Ops MFA Authorization Required**: Execution of each packet requires an operator with multi-factor authentication (MFA), verified GCP Organization Admin privileges, and formal change-management sign-off.
3. **Fail-Closed Isolation**: The presence of these task specifications in the repository creates **zero cloud resources, zero credentials, zero network access, and zero live-capital capability**.

---

## 2. Packet Specifications

```text
  [ ENV-STAGING-PROD-PLAN-001 ] (Current Architecture & Design Closure - Complete)
                 |
                 v (Separately Privileged Future Packets)
  +---------------------------------------------------------------------------------+
  | 1. ENV-STG-EPHEMERAL-IMPL-001 (Phase 2: Ephemeral Staging Engine)               |
  |    - Status: UNDISPATCHED / HELD PENDING OPERATOR MFA                           |
  |    - Scope: GCE instance templates, automated TTL lifecycle, orphan sweeper    |
  +---------------------------------------------------------------------------------+
                 |
                 v (Requires Staging Proof)
  +---------------------------------------------------------------------------------+
  | 2. ENV-PROD-CONTROL-IMPL-001 (Phase 3: Production Control Plane Setup)           |
  |    - Status: UNDISPATCHED / HELD PENDING OPERATOR MFA                           |
  |    - Scope: Prod GCP project, GitHub Environment, Control VM, Blue/Green switch |
  +---------------------------------------------------------------------------------+
                 |
                 v (Requires Business Capital Authorization)
  +---------------------------------------------------------------------------------+
  | 3. ENV-PROD-EXEC-ISOLATE-001 (Phase 4: Real-Capital Execution Isolation)        |
  |    - Status: UNDISPATCHED / HELD PENDING OPERATOR MFA + TRADING RISK OFFICER   |
  |    - Scope: Private VPC (no public IP), broker secrets on tmpfs, kill switches  |
  +---------------------------------------------------------------------------------+
```

---

### Packet 1: `ENV-STG-EPHEMERAL-IMPL-001`
- **Title**: Ephemeral Staging Template & Automated Lifecycle Engine
- **Phase**: Phase 2 (Ephemeral Staging)
- **Lifecycle Status**: `undispatched` / `held_pending_operator_mfa`
- **Owner**: `Human/Ops` (assisted by `Gemini`)
- **Reviewer**: `Claude`
- **Required Authority & Privileges**:
  - GCP Compute Admin on non-production project (`pantheon-lupin-dev-20260719`).
  - GitHub Workflow Write permissions in `ajoe734/pantheon`.
- **Scope & Deliverables**:
  1. GCE Instance Template for `pantheon-stg-template` (`e2-standard-2`, Debian 12, Docker Compose, Caddy).
  2. Provisioning & teardown bash scripts (`scripts/deploy/staging_provision.sh`, `scripts/deploy/staging_destroy.sh`).
  3. Sanitized database snapshot automation script (masking PII, dropping broker secrets).
  4. GitHub Actions workflow `.github/workflows/staging-ephemeral-deploy.yml` implementing the 5-stage lifecycle.
  5. Standalone scheduled orphan sweeper workflow `.github/workflows/staging-orphan-cleanup.yml` running every 30 minutes.
- **Acceptance Criteria**:
  - Two consecutive releases can provision an ephemeral VM from zero, execute validation gates, and cleanly destroy the VM with zero lingering disks or IPs.
  - No staging resource exists longer than its 2-hour TTL.
  - Safe-write and live-broker defaults remain strictly disabled (`PANTHEON_LIVE_BROKER_ENABLED=false`).

---

### Packet 2: `ENV-PROD-CONTROL-IMPL-001`
- **Title**: Production Control Plane Provisioning & Blue/Green Delivery
- **Phase**: Phase 3 (Low-Resource Production Control)
- **Lifecycle Status**: `undispatched` / `held_pending_operator_mfa`
- **Owner**: `Human/Ops`
- **Reviewer**: `Claude`
- **Required Authority & Privileges**:
  - GCP Organization Admin / Billing Account Administrator.
  - GitHub Repository Administrator (to configure GitHub Environments and secret boundaries).
- **Scope & Deliverables**:
  1. Creation of dedicated production GCP project (e.g. `pantheon-prod-202609XX`).
  2. Setup of GitHub Environment `production` with required multi-party human reviewers and `prevent_self_review: true`.
  3. Provisioning of `pantheon-prod-control` VM (`e2-standard-2`) and independent 100 GB `pd-ssd` persistent data disk.
  4. Configuration of Caddy reverse proxy with Let's Encrypt TLS for production domain.
  5. Deployment scripts implementing single-node Blue/Green candidate execution, Caddy reload, atomic FE symlink swapping, and sub-2-minute rollback.
  6. Automated pre-deploy database checkpoint snapshotting.
- **Acceptance Criteria**:
  - Successful execution of two non-real-capital production deployment drills.
  - Verified atomic switch between Blue and Green without request interruption.
  - Verified automated rollback restoring service in $< 120$ seconds upon injected fault.
  - Prod Control VM contains zero live broker credentials or trading keys.

---

### Packet 3: `ENV-PROD-EXEC-ISOLATE-001`
- **Title**: Production Execution Boundary Physical Isolation & Broker Gateways
- **Phase**: Phase 4 (Real-Capital Execution Boundary)
- **Lifecycle Status**: `undispatched` / `held_pending_operator_mfa`
- **Owner**: `Human/Ops` (co-signed by `Trading Risk Officer`)
- **Reviewer**: `Claude`
- **Required Authority & Privileges**:
  - Formal executive authorization for real-capital trading.
  - GCP Network & Security Admin.
  - Access to live Interactive Brokers (IBKR) / exchange account management.
- **Scope & Deliverables**:
  1. Private subnet configuration (`10.60.0.0/24`) with zero external IP allocation and zero public ingress.
  2. Provisioning of `pantheon-prod-exec` VM (`e2-standard-2`).
  3. Cloud NAT egress-only routing for connecting to authorized broker gateways.
  4. Private mTLS channel between Prod Control VM (`10.50.0.0/24`) and Prod Execution VM (`10.60.0.0/24`) on port 28081.
  5. Volatile RAM disk (`tmpfs`) secret injection from GCP Secret Manager at boot time.
  6. Fail-closed watchdog and kill switch terminating open orders within 500ms of telemetry disconnect.
- **Acceptance Criteria**:
  - Complete air-gap verification: Prod Execution VM is unreachable from public internet.
  - Broker secrets exist solely in volatile memory and are never written to disk.
  - Automated kill switch drill successfully cancels paper/sandbox orders when Control VM connection is severed.
  - Production trading remains locked until both Human/Ops and Trading Risk Officer provide cryptographic signatures.

---

## 3. Materialized Task Registry JSON

The machine-readable definitions for these future packets are recorded below for downstream registration upon authorized activation:

```json
[
  {
    "id": "ENV-STG-EPHEMERAL-IMPL-001",
    "title": "Ephemeral Staging Template & Automated Lifecycle Engine",
    "phase": "Phase 2",
    "status": "todo",
    "owner": "Human/Ops",
    "reviewer": "Claude",
    "dispatch_paused": true,
    "requires_mfa": true,
    "depends_on": ["ENV-STAGING-PROD-PLAN-001"],
    "artifacts": [
      "scripts/deploy/staging_provision.sh",
      "scripts/deploy/staging_destroy.sh",
      ".github/workflows/staging-ephemeral-deploy.yml",
      ".github/workflows/staging-orphan-cleanup.yml"
    ]
  },
  {
    "id": "ENV-PROD-CONTROL-IMPL-001",
    "title": "Production Control Plane Provisioning & Blue/Green Delivery",
    "phase": "Phase 3",
    "status": "todo",
    "owner": "Human/Ops",
    "reviewer": "Claude",
    "dispatch_paused": true,
    "requires_mfa": true,
    "depends_on": ["ENV-STG-EPHEMERAL-IMPL-001"],
    "artifacts": [
      "deploy/production/docker-compose.prod-control.yml",
      "deploy/production/Caddyfile",
      "scripts/deploy/prod_atomic_switch.sh",
      "scripts/deploy/prod_rollback.sh"
    ]
  },
  {
    "id": "ENV-PROD-EXEC-ISOLATE-001",
    "title": "Production Execution Boundary Physical Isolation & Broker Gateways",
    "phase": "Phase 4",
    "status": "todo",
    "owner": "Human/Ops",
    "reviewer": "Claude",
    "dispatch_paused": true,
    "requires_mfa": true,
    "depends_on": ["ENV-PROD-CONTROL-IMPL-001"],
    "artifacts": [
      "deploy/production/docker-compose.prod-exec.yml",
      "scripts/deploy/setup_exec_network_isolation.sh",
      "services/execution/fail_closed_watchdog.py"
    ]
  }
]
```
