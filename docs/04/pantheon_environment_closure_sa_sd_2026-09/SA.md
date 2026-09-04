# System Architecture — Pantheon Environment Closure

- Document: `SA.md`
- Status: Canonical Architecture Specification
- Date: 2026-09-04
- Baseline: `origin/dev`
- Task: `ENV-STAGING-PROD-PLAN-001`
- Companion Documents: [`INDEX.md`](INDEX.md), [`REPORT.md`](REPORT.md), [`SD.md`](SD.md), [`docs/deployment/vm-dev-staging-prod-management-plan.md`](../../deployment/vm-dev-staging-prod-management-plan.md)

---

## 1. Objective

This System Architecture document defines the canonical multi-environment topology and boundaries for the Pantheon platform across **Dev**, **Staging**, and **Production**.

The core design principle is **maximum governance, zero unnecessary permanent overhead, and absolute capital safety**:
1. **Dev**: A single permanent VM (`pantheon-lupin-dev`) dedicated to fast-cycle integration between the `pantheon` backend and `execute-plans` frontend, operating strictly in paper/safe-write mode.
2. **Staging**: An **ephemeral, zero-idle** environment. A standalone VM is provisioned on-demand solely for validating a specific release candidate against sanitized data, after which it is verified and destroyed immediately.
3. **Production**: A strictly gated, low-resource environment isolated into two distinct planes:
   - **Prod Control Plane** (permanent VM): Operates Caddy reverse proxy, single-host blue/green request-facing candidate, and persistent database.
   - **Prod Execution Plane** (isolated VM, Phase 4): Operates inside a private internal-only VPC with zero public ingress, hosting `runtime-manager`, broker adapters, execution telemetry, and live credentials.

The platform deliberately avoids heavy, expensive orchestrators (such as GKE/Kubernetes) in favor of deterministic, observable, single-node Docker Compose stacks governed by GitHub Actions and Workload Identity Federation (WIF).

---

## 2. Non-Goals

1. **No Kubernetes or GKE**: The platform avoids GKE complexity, control-plane costs, and ingress overhead. Single-VM Docker Compose remains the deployment standard.
2. **No Premature Cloud Run Migration**: Cloud Run requires fully stateless services, externalized databases/queues, and separate background worker pools. It is deferred to Phase 5.
3. **No Permanent Staging Stack**: Maintaining a persistent staging environment causes configuration drift, silent resource leakage, and orphaned costs. Staging must be ephemeral.
4. **No Cross-Environment Resource Sharing**: Dev, Staging, and Production must never share databases, MinIO buckets, NATS clusters, Service Accounts, or encryption secrets.
5. **No Direct Execution on Control Plane**: In real-capital mode, live broker credentials and trading adapters must never reside on the request-facing Prod Control VM.
6. **No Self-Provisioning by Auto-Workers**: Background workers and supervisors have zero authority to provision cloud resources, create IAM roles, or trigger production deployments.

---

## 3. Architecture Invariants

1. **Three Independent Truth Planes**:
   - *Product Runtime Truth*: Derived solely from live HTTP responses, database states, and signed journal events.
   - *Development Tooling Truth*: Managed by the `V2 TaskStore`, supervisor leases, and canonical task locks (`ai-status.json`).
   - *Delivery Infrastructure Truth*: Determined by GitHub Actions environment protections, immutable artifact digests, and hosted deployment manifests (`deployment.json`).
   - *Invariant Rule*: Proof or health in one plane never implies health in another.
2. **Ephemeral Staging Lifecycle**:
   - Staging exists only during the lifetime of a specific release candidate validation job.
   - Normal state is **0 VMs**.
   - If validation succeeds, logs and evidence are persisted, and the VM is destroyed immediately.
   - If validation fails, the VM enters a bounded debug window (TTL $\le 2$ hours) and is reclaimed by an automated orphan sweeper.
3. **Control and Execution Plane Segregation**:
   - Prod Control VM has public HTTPS ingress (443) managed by Caddy.
   - Prod Execution VM has **zero public IP and zero public ingress**. It accepts traffic only from Prod Control over private RFC 1918 VPC peering.
   - Broker credentials (IBKR TWS API secrets, exchange keys) exist exclusively on the Prod Execution VM.
4. **Exact-Pair Immutable Delivery**:
   - A release is defined by an immutable pair: `execute-plans` FE commit + artifact SHA256, paired with `pantheon` BFF/backend commit + container image digest.
   - Environments never build from source; they deploy the exact immutable digests built once in CI.
5. **Fail-Closed Safe Writes**:
   - `PANTHEON_LIVE_BROKER_ENABLED=false` is hardcoded across Dev and default Staging.
   - Live trading is impossible without explicit, two-party operator authorization and provisioning of the isolated Execution VM.
6. **Expand/Contract Schema Evolution**:
   - Database migrations must strictly follow the expand/contract paradigm.
   - Schema additions (expand) must be backward-compatible with the currently running blue version before the green candidate is promoted.

---

## 4. Target Architecture & Environment Topology

```text
                                +-------------------------------------------------------+
                                |               GitHub Actions CI/CD Pipeline           |
                                |  (WIF short-lived tokens, Exact-Pair Release Manifest)|
                                +---------------------------+---------------------------+
                                                            |
                     +--------------------------------------+-------------------------------------+
                     |                                      |                                     |
                     v                                      v                                     v
+---------------------------------------+  +---------------------------------+  +-----------------------------------------+
|        Dev Environment (Permanent)    |  |  Ephemeral Staging (On-Demand)  |  |    Production Environment (Isolated)    |
| Project: pantheon-lupin-dev-20260719  |  | Project: pantheon-lupin-dev-... |  | Project: pantheon-prod-XXXX             |
|                                       |  |                                 |  |                                         |
|  [pantheon-lupin-dev VM]              |  |  [pantheon-stg-<release-id> VM] |  |  +-----------------------------------+  |
|  - Caddy (HTTPS 443)                  |  |  - Caddy (Ephemeral IP/DNS)     |  |  | Prod Control VM (Permanent)       |  |
|  - Compose: pantheon (core+research)  |  |  - Sanitized DB Snapshot        |  |  | - Caddy (HTTPS 443)               |  |
|  - Internal: 127.0.0.1:18001 (BFF)    |  |  - Exact Release Manifest       |  |  | - Blue/Green candidate containers |  |
|  - Paper Broker Only                  |  |  - Auto-destroy on completion   |  |  | - Persistent Data Disk (Postgres) |  |
|  - Shared with local dev workers      |  |  - TTL <= 2 hours on failure    |  |  +-----------------+-----------------+  |
+---------------------------------------+  +---------------------------------+  |                    | Private VPC Only   |
                                                                                |                    v (No Public IP)     |
                                                                                |  +-----------------------------------+  |
                                                                                |  | Prod Execution VM (Phase 4 Only)  |  |
                                                                                |  | - runtime-manager, broker-adapter |  |
                                                                                |  | - TWS / IBKR credentials          |  |
                                                                                |  | - Execution Telemetry Outbox      |  |
                                                                                |  +-----------------------------------+  |
                                                                                +-----------------------------------------+
```

---

## 5. Component Profiles & Resource Bounds

To prevent uncontrolled memory bloat across 70 Compose services, each environment activates only defined profiles:

| Profile | Included Services | Dev | Ephemeral Staging | Prod Control | Prod Execution |
|---|---|---|---|---|---|
| `core` | Postgres, NATS, Caddy, Operator BFF, Projectors, Lifecycle | YES | YES | YES | NO |
| `workers` | Scheduler, Outbox consumer, Policy/Audit reconcilers | YES | Conditional | YES (Singleton) | NO |
| `research` | Model engines, MLflow, FinRL, Qlib, Data Scrapers | YES | NO | NO | NO |
| `management-ai`| OpenClaw, diagnostic kernel (read-only) | YES | Conditional | YES (Read-Only) | NO |
| `execution` | Runtime Manager, Broker Adapter, Exchange Adapter, Live LEAN | NO (Paper) | Conditional (Sandbox) | NO | YES (Phase 4) |

### Memory & Sizing Constraints (Phase 0 Baseline)
- Measured `core` working set: 4.10 – 4.25 GiB (including host OS buffer and candidate BFF during transition).
- Machine sizing: `e2-standard-2` (2 vCPU, 8.0 GiB RAM) provides 48.9% memory headroom (exceeding the $\ge 30\%$ requirement).
- For unconstrained CPU during heavy peak reconciliation, `e2-standard-4` (4 vCPU, 16.0 GiB RAM) is the authorized scale-up target.

---

## 6. Authority & Governance Boundaries

1. **Development Tooling Boundary**:
   - Auto-workers and supervisors operate in `pantheon-lupin-dev-20260719` within `/tmp/pantheon-worker-worktrees/`.
   - Tooling authority stops at the git repository boundary: it creates PRs and commits. It cannot deploy to production.
2. **Delivery Infrastructure Boundary**:
   - Managed strictly via GitHub Actions Environments:
     - `dev`: Automatic admission on `task/*` PR merge into `dev`.
     - `staging`: Triggered only after `dev_verified` proof is published.
     - `production`: Requires explicit human review with **prevent-self-review** and MFA confirmation.
3. **Execution Safety Boundary**:
   - Production trading can only be initiated by the designated Trading Risk Officer.
   - Automated kill switches operate out-of-band to terminate broker sessions upon anomaly detection.
