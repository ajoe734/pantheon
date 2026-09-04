# System Design — Pantheon Environment Closure

- Document: `SD.md`
- Status: Canonical System Design Specification
- Date: 2026-09-04
- Baseline: `origin/dev`
- Task: `ENV-STAGING-PROD-PLAN-001`
- Companion Documents: [`INDEX.md`](INDEX.md), [`REPORT.md`](REPORT.md), [`SA.md`](SA.md), [`docs/deployment/vm-dev-staging-prod-management-plan.md`](../../deployment/vm-dev-staging-prod-management-plan.md)

---

## 1. Overview & Technical Objectives

This document specifies the concrete operational mechanics, automation sequences, and configuration layouts for implementing the multi-environment target architecture defined in [`SA.md`](SA.md).

It details:
1. The **Ephemeral Staging Automation & Lifecycle Engine**;
2. The **Sanitized Snapshot & Data Masking Pipeline**;
3. The **Production Control Plane Blue/Green Atomic Switch**;
4. The **Production Execution Boundary Physical Isolation**;
5. The **Orphan Reconciliation Sweeper**.

---

## 2. Ephemeral Staging Lifecycle Engine

### 2.1 State Transition Sequence

An Ephemeral Staging instance moves through an unalterable five-stage state machine:

```text
  [Triggered: dev_verified]
             |
             v
      +--------------+
      |  PROVISION   | -> Allocate GCE VM, attach temporary boot disk, apply labels & TTL
      +--------------+
             |
             v
      +--------------+
      |    DEPLOY    | -> Mount sanitized data snapshot, inject release manifest, start core profile
      +--------------+
             |
             v
      +--------------+
      |    VERIFY    | -> Execute automated contract, CORS, API, and browser smoke gates
      +--------------+
             |
             v
      +--------------+
      |   COLLECT    | -> Export bounded logs, evidence manifest, and audit proofs to storage bucket
      +--------------+
             |
             v
      +--------------+
      |   DESTROY    | -> Delete VM, ephemeral boot disk, and release temporary IP reservation
      +--------------+
             |
             v
        [COMPLETED]
```

### 2.2 Mandatory Resource Labeling

Every resource instantiated for staging must carry immutable labels:

```json
{
  "environment": "staging",
  "release_id": "pantheon-20260904-001",
  "workflow_run_id": "28591024851",
  "created_at": "2026-09-04T00:00:00Z",
  "expires_at": "2026-09-04T02:00:00Z",
  "cleanup_policy": "automatic"
}
```

### 2.3 Orphan Sweeper & Safety Invariant

To guarantee zero lingering infrastructure, a standalone GitHub Actions scheduled workflow runs every 30 minutes (`.github/workflows/staging-orphan-cleanup.yml`):
1. Queries `gcloud compute instances list --filter="labels.environment=staging"`.
2. Inspects `labels.expires_at`.
3. If `currentTime > expires_at`, issues forced deletion:
   ```bash
   gcloud compute instances delete "$INSTANCE_NAME" --zone="$ZONE" --quiet --delete-disks=all
   ```
4. Emits a security alert if any unmanaged disk or static IP remains.

---

## 3. Sanitized Snapshot & Data Masking Pipeline

Staging must never run on raw production data or contain live broker credentials.

### 3.1 Masking & Sanitization Specification

The pipeline executes a deterministic sanitization procedure prior to producing the staging base snapshot:

```sql
-- Sanitization Script executed on isolated staging build worker
BEGIN;

-- 1. Wipe all credential tables and API secrets
TRUNCATE TABLE broker_credentials CASCADE;
TRUNCATE TABLE exchange_api_keys CASCADE;
TRUNCATE TABLE user_sessions CASCADE;

-- 2. Anonymize user records and operator identifiers
UPDATE users 
SET email = 'staging-user-' || id || '@pantheon.internal',
    full_name = 'Staging Persona ' || id,
    password_hash = '$2b$12$dummyStagingHashForVerificationPurposesOnly';

-- 3. Reset live trading flags to fail-closed
UPDATE system_settings
SET live_broker_enabled = false,
    safe_write_strict = true,
    execution_mode = 'paper';

-- 4. Strip sensitive telemetry payload details
UPDATE telemetry_events
SET raw_payload = jsonb_strip_nulls(raw_payload - 'token' - 'auth' - 'secret');

COMMIT;
```

### 3.2 Snapshot Lifecycle
- Snapshots are retained for a maximum of 7 days.
- Snapshots are tagged `retention=7d` and stored in the non-production GCP project.

---

## 4. Production Control Plane Design: Single-Host Blue/Green

Prod Control avoids multi-machine overhead by executing an atomic, single-node Blue/Green candidate switch on the permanent Prod Control VM (`pantheon-prod-control`).

### 4.1 Topology on Prod Control VM

```text
                               Public Internet (HTTPS 443)
                                            |
                                            v
                                     [ Caddy Server ]
                                            |
                 +--------------------------+--------------------------+
                 | (BFF Reverse Proxy)                                 | (Static FE Symlink)
                 v                                                     v
  +-------------------------------+                     +-------------------------------+
  |  Current (Blue) Candidate     |                     |  /var/www/pantheon-fe/current |
  |  - Container: bff-blue        |                     |      |                        |
  |  - Port: 127.0.0.1:18001      |                     |      +-> releases/release-A/  |
  +-------------------------------+                     +-------------------------------+
                 |                                                     |
  +-------------------------------+                     +-------------------------------+
  |  New (Green) Candidate        |                     |  /var/www/pantheon-fe/        |
  |  - Container: bff-green       |                     |      +-> releases/release-B/  |
  |  - Port: 127.0.0.1:18002      |                     |          (Tested pre-switch)  |
  +-------------------------------+                     +-------------------------------+
                 |                                                     |
                 +--------------------------+--------------------------+
                                            |
                                            v
                              [ Shared Persistent Disk ]
                              - /var/lib/pantheon/data/postgres
                              - /var/lib/pantheon/data/nats
```

### 4.2 Dual-Endpoint Switch & Compensation Protocol

Atomic switching across two endpoints (FE directory symlink and BFF HTTP upstream) requires a two-phase commit with automatic compensation:

1. **Pre-Switch Validation**:
   - Start `bff-green` on port `18002`.
   - Deploy `execute-plans` static build into `/var/www/pantheon-fe/releases/<release_id>`.
   - Run health probe: `curl -fsS http://127.0.0.1:18002/healthz`.
   - Run version verification: verify `source_commit_sha` matches release manifest.
   - Run CORS and contract probe on `18002`.
2. **Phase 1: Shift BFF Traffic**:
   - Reload Caddy configuration pointing `bff.pantheon.trade` upstream to `127.0.0.1:18002`:
     ```bash
     caddy reload --config /etc/caddy/Caddyfile.green
     ```
3. **Phase 2: Atomic Frontend Symlink Switch**:
   - Atomically swap the symlink using `ln -sfn`:
     ```bash
     ln -sfn "/var/www/pantheon-fe/releases/${RELEASE_ID}" /var/www/pantheon-fe/current
     ```
4. **Post-Switch Readback**:
   - Probe `https://pantheon.trade/deployment.json` and verify FE SHA and paired BFF SHA.
5. **Automatic Compensation (Rollback)**:
   - If Step 4 fails, immediately revert Caddy to `Caddyfile.blue` and restore the previous FE symlink.

---

## 5. Production Execution Plane Isolation (Phase 4 Specification)

When real capital is authorized by Human/Ops, execution services are strictly segregated to a dedicated VM.

### 5.1 Physical & Network Boundary

```text
[ Prod Control VM ]                             [ Prod Execution VM ]
(Subnet: 10.50.0.0/24)                          (Subnet: 10.60.0.0/24)
  |                                               |
  | Private VPC Peering                           | Zero Public IP / No Ingress
  +-------------------- Port 28081 (mTLS) ------->| - runtime-manager (port 28081)
                                                  | - broker-adapter (IBKR TWS)
                                                  | - exchange-adapter (Crypto)
                                                  | - Execution Outbox
                                                  |
                                                  v Cloud NAT (Egress Only)
                                                [ Broker Gateway / Exchanges ]
```

### 5.2 Broker Secret Management
- Secrets are never persisted on disk, in `.env` files, or in Git.
- At VM boot, secrets are fetched from GCP Secret Manager via IAM-authenticated metadata service and mounted into a volatile RAM disk (`tmpfs` at `/run/pantheon/secrets/`).
- If the VM reboots or is halted, secrets disappear instantaneously.

---

## 6. Exact-Pair Release Manifest Specification

The release artifact is governed by the schema below:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "PantheonReleaseManifest",
  "type": "object",
  "required": [
    "schema_version",
    "release_id",
    "frontend",
    "backend",
    "compatibility_manifest_sha256",
    "migration_set_sha256",
    "created_at",
    "created_by_workflow"
  ],
  "properties": {
    "schema_version": { "type": "integer", "const": 1 },
    "release_id": { "type": "string", "pattern": "^pantheon-[0-9]{8}-[0-9]{3}$" },
    "frontend": {
      "type": "object",
      "required": ["repository", "commit", "artifact_sha256"],
      "properties": {
        "repository": { "type": "string", "const": "ajoe734/execute-plans" },
        "commit": { "type": "string", "pattern": "^[0-9a-f]{40}$" },
        "artifact_sha256": { "type": "string", "pattern": "^sha256:[0-9a-f]{64}$" }
      }
    },
    "backend": {
      "type": "object",
      "required": ["repository", "commit", "images"],
      "properties": {
        "repository": { "type": "string", "const": "ajoe734/pantheon" },
        "commit": { "type": "string", "pattern": "^[0-9a-f]{40}$" },
        "images": {
          "type": "object",
          "required": ["operator_bff"],
          "additionalProperties": { "type": "string" }
        }
      }
    },
    "compatibility_manifest_sha256": { "type": "string", "pattern": "^sha256:[0-9a-f]{64}$" },
    "migration_set_sha256": { "type": "string", "pattern": "^sha256:[0-9a-f]{64}$" },
    "created_at": { "type": "string", "format": "date-time" },
    "created_by_workflow": { "type": "string" }
  }
}
```
