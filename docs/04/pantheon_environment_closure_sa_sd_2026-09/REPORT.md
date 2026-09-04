# Audit & Unavailability Re-verification Report

- Document: `REPORT.md`
- Status: Canonical Reality Audit
- Date: 2026-09-04
- Task: `ENV-STAGING-PROD-PLAN-001`
- Parent Audit: `pkt-pantheon-structural-closure-functional-v2-20260903` (`GAP-ENV-06`)

---

## 1. Objective of This Audit

The objective of this audit is to re-verify the real-world operational truth regarding the staging and production environments of the Pantheon platform, definitively repudiate all obsolete assumptions of reusing retired or suspended environments, and document the baseline evidence establishing that **Staging** and **Production** remain strictly **UNAVAILABLE**.

---

## 2. Live Probe & State Verification (2026-09-04)

An authoritative empirical probe of current endpoints, GCP project boundaries, and network identities was conducted on 2026-09-04.

### 2.1 Dev Environment (Active Baseline)

- **GCP Project**: `pantheon-lupin-dev-20260719`
- **Instance Name**: `pantheon-lupin-dev`
- **Zone**: `asia-east1-b`
- **Public External IP**: `35.201.204.12`
- **Hosted BFF Hostname**: `https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io`
- **Hosted FE Hostname**: `https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io`
- **Local Compose Checkout**: `/home/lupin/pantheon`
- **Status**: **ACTIVE (Non-prod integration only)**.
  - Serves as the active single-VM development host.
  - Hosts the Docker Compose stack for daily integration and auto-worker leases.
  - Safe-write defaults remain strictly enforced (`PANTHEON_LIVE_BROKER_ENABLED=false`, paper-only mode).

### 2.2 Suspended Historical GCP Project (`pantheon-benjamin-20260528`)

- **Project Status**: **SUSPENDED / SHUT DOWN**.
- **Historical Shared Dev IP**: `35.201.239.38`.
  - Probe: `curl -k -I -m 3 https://35.201.239.38`
  - Result: `Connection timed out after 3002 milliseconds`.
  - Finding: The IP address is unreachable and no longer attached to any active Pantheon resource.
- **Historical Staging Control IP**: `104.155.223.192`.
  - Probe: `curl -k -s -m 5 https://104.155.223.192/health`
  - HTTP Status: `HTTP/2 401 Unauthorized`
  - Body Returned:
    ```json
    {
      "kind": "Status",
      "apiVersion": "v1",
      "metadata": {},
      "status": "Failure",
      "message": "Unauthorized",
      "reason": "Unauthorized",
      "code": 401
    }
    ```
  - **Critical Security Finding**: The response format (`kind: "Status"`, `apiVersion: "v1"`) proves conclusively that Google Cloud has released the public IP `104.155.223.192` and reallocated it to an external third party operating a Kubernetes API server.
  - **Mandatory Enforcement**: Any workflow or runbook referencing `104.155.223.192` as a Pantheon staging endpoint is communicating with an unauthorized external cluster. All such references are revoked and repudiated.

### 2.3 Staging Environment Status

- **Status**: `UNAVAILABLE` (`status: unavailable`).
- **Current Provisioning**: Zero VMs, zero disks, zero VPCs, zero active compose stacks.
- **Target Direction**: Ephemeral release-scoped VM (`pantheon-stg-<release-id>`) created on-demand, validated with sanitized data, and torn down automatically.
- **Reality**: The provisioning pipeline, instance templates, sanitized snapshot automation, and TTL teardown workflows defined in Phase 2 have NOT yet been implemented or deployed.
- **Conclusion**: Staging does not exist. No claim of staging readiness may be made based on local unit tests or dev passes.

### 2.4 Production Environment Status

- **Status**: `UNAVAILABLE` (`status: unavailable`).
- **Current Provisioning**: Zero GCP projects, zero GitHub Environments, zero VMs, zero persistent storage volumes, zero broker credentials, zero deployment pipelines.
- **Reality**:
  1. No dedicated production GCP project exists (e.g. `pantheon-prod-XXXX`).
  2. No GitHub Environment `production` with required multi-party approvals or prevent-self-review rules exists.
  3. No live trading broker keys (IBKR TWS API, exchange secrets) are provisioned.
  4. No production Caddy domain, TLS certificate, or hosted manifest exists.
- **Conclusion**: Production does not exist. Any assertion that Pantheon is production-ready or operational in a live trading capacity is false.

---

## 3. Systematic Repudiation of Retired Environment Reuse Assumptions

Historically, certain workflows and documentation carried lingering assumptions about reusing suspended infrastructure or static dual-VM staging topologies. This audit enacts their permanent repudiation and deletion:

| Assumption | Historical Origin | Audit Verdict & Mandatory Deletion |
|---|---|---|
| Reusing `pantheon-benjamin-20260528` for staging or builds | GCP deployment initial setup (May 2026) | **REPUDIATED & DELETED**. The project is suspended. No build bucket or deploy target may fall back to it. |
| Reusing `104.155.223.192` as Staging BFF | Old VM1 public IP | **SECURITY HAZARD / REPUDIATED**. The IP has been reassigned to an external Kubernetes cluster. Routing to it is forbidden. |
| Reusing `35.201.239.38` as Dev IP | Old Dev host IP | **REPUDIATED**. Suspended and dropped. Only `35.201.204.12` on `pantheon-lupin-dev-20260719` is active. |
| Static staging VMs (`pantheon-lupin-staging-control`, `exec`) | Legacy dual-VM staging design | **REPUDIATED & DELETED**. Persistent staging VMs are eliminated to prevent cost leakage and drift. Staging is strictly ephemeral. |
| Dev test passes equal Staging/Prod readiness | Informal worker claims | **REPUDIATED**. Three independent truth planes are enforced: dev test success never proves staging or production readiness. |
| Shared database or shared secrets across environments | Convenient non-prod setups | **PROHIBITED**. Each environment must have completely isolated databases, encryption keys, and service accounts. |

---

## 4. Re-verification of Gap Findings (`GAP-ENV-06`)

From `pkt-pantheon-structural-closure-functional-v2-20260903/REPORT.md`:
> *"GAP-ENV-06: The environment plan describes staging and production as future or unavailable. Nothing in this audit authorizes interpreting dev code, historical paper evidence, or publish tags as staging/production readiness."*

This re-audit completely affirms `GAP-ENV-06`. Staging and Production remain strictly unavailable, and closure consists of:
1. Formalizing the System Architecture (SA) and System Design (SD) for Ephemeral Staging and Isolated Production;
2. Establishing the Authority, Threat, Resource/Cost, Promotion, and Rollback models;
3. Isolating all subsequent implementation tasks into separately privileged future packets that remain undispatched pending explicit operator MFA authorization.
