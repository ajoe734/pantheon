# Promotion & Rollback Specification — Pantheon Environment Closure

- Document: `PROMOTION_AND_ROLLBACK_SPEC.md`
- Status: Canonical Operational Specification
- Date: 2026-09-04
- Baseline: `origin/dev`
- Task: `ENV-STAGING-PROD-PLAN-001`
- Companion Documents: [`INDEX.md`](INDEX.md), [`SA.md`](SA.md), [`SD.md`](SD.md)

---

## 1. Executive Summary

This specification governs the lifecycle of release candidate promotion from Dev through Ephemeral Staging to Production, as well as the deterministic, automated rollback protocols required to restore service in the event of failure.

The core guarantee is **atomic, verifiable promotion with zero downtime and sub-2-minute rollback**:
1. Promotion is strictly gated by immutable cryptographic release manifests;
2. Switching requires pre-switch green verification and a two-phase commit with automatic compensation;
3. Database changes adhere strictly to the expand/contract paradigm.

---

## 2. Release Lifecycle State Machine

```text
  [ candidate_built ]
          |
          v
  [ dev_verified ] ---------------------------> ( dev_failed )
          |
          v
  [ staging_provisioned ]
          |
          v
  [ staging_verified ] -----------------------> ( staging_failed )
          |
          v
  [ production_approved ] (Human/Ops + MFA)
          |
          v
  [ prod_green_verified ] --------------------> ( prod_aborted )
          |
          v
  [ prod_active ] ----------------------------> ( prod_rolled_back )
          |
          v (30-min observation passed)
  [ release_complete ]
```

### Transition State Table

| State | Entry Condition | Required Proof / Artifact | Next Action |
|---|---|---|---|
| `candidate_built` | FE build and BFF container build complete | FE artifact sha256 + BFF image digest | Deploy to Dev VM |
| `dev_verified` | Dev integration gate and browser smoke pass | Dev hosted readback proof in `deployment.json` | Trigger Ephemeral Staging |
| `staging_provisioned`| Ephemeral VM created and sanitized DB restored | Cloud VM ID and instance metadata label | Deploy exact candidate images |
| `staging_verified` | Staging test suite and migration rehearsal pass | Uploaded evidence bundle and logs | Tear down Staging; wait for human approval |
| `production_approved`| Human/Ops approves release in GitHub Environment | GitHub approval signature and run ID | Deploy green candidate on Prod Control |
| `prod_green_verified`| Green candidate passes health, auth, and CORS | Local HTTP 200 readback on alternate port | Atomic traffic switch |
| `prod_active` | Caddy upstream shifted & FE symlink swapped | Live `deployment.json` readback matches candidate | Enter 30-minute observation window |
| `release_complete` | Observation window ends with zero SLO breaches | Signed release archive recorded | Retain Blue for 24h rollback retention |

---

## 3. Baseline-Before-Switch Algorithm

To prevent partial, unrecoverable states, deployment scripts execute the 10-step Baseline-Before-Switch sequence:

1. **Verify Invariant Preconditions**:
   - Ensure the release manifest is in `staging_verified` state.
   - Verify that the previous healthy release (Blue) is actively serving traffic and recorded as the rollback baseline.
2. **Pre-Deploy Database Checkpoint**:
   - Create an automated, point-in-time snapshot of the persistent Postgres data disk:
     ```bash
     gcloud compute disks snapshot pantheon-prod-data-disk \
       --snapshot-names="pre-deploy-${RELEASE_ID}-$(date +%s)" \
       --zone=asia-east1-b
     ```
3. **Execute Backward-Compatible Migrations (Expand)**:
   - Run database schema migration scripts in dry-run mode first, then apply.
   - Invariant: Schema changes must be backward-compatible with the currently running Blue code.
4. **Deploy Green Candidate Containers**:
   - Launch `bff-green` on internal alternate port `18002`.
   - Ensure `bff-green` mounts the shared Postgres database in read/write mode.
5. **Stage Green Frontend Bundle**:
   - Unpack the verified `execute-plans` artifact into `/var/www/pantheon-fe/releases/<release_id>/`.
   - Do NOT modify `/var/www/pantheon-fe/current` symlink.
6. **Execute Pre-Switch Green Smoke Verification**:
   - Probe `http://127.0.0.1:18002/healthz` (expect 200 OK).
   - Verify `http://127.0.0.1:18002/bff/version` reports the exact backend commit SHA.
   - Run authenticated synthetic probe using non-destructive read operations.
7. **Drain & Quiesce Singleton Schedulers**:
   - Temporarily signal the Blue scheduler/worker to pause queue polling and complete in-flight tasks.
8. **Execute Dual-Endpoint Atomic Switch**:
   - **Step 8a**: Reload Caddy configuration to point BFF upstream to `127.0.0.1:18002`:
     ```bash
     caddy reload --config /etc/caddy/Caddyfile.green
     ```
   - **Step 8b**: Atomically update frontend directory symlink:
     ```bash
     ln -sfn "/var/www/pantheon-fe/releases/${RELEASE_ID}" /var/www/pantheon-fe/current
     ```
9. **Served Readback Validation**:
   - Immediately issue HTTPS requests against public endpoints:
     - Fetch `https://pantheon.trade/deployment.json`.
     - Confirm FE SHA and BFF SHA match the release manifest.
10. **Activate Green Schedulers & Begin Observation**:
    - Resume background workers under the Green release.
    - Keep Blue containers alive in standby mode during the 30-minute observation window.

---

## 4. Multi-Level Deterministic Rollback Protocols

If any gate, health check, or user journey fails during deployment or the observation window, the automated compensation protocol is invoked immediately.

### Level 1: Application Rollback (Fast-Path, RTO < 2 Minutes)
When the failure is isolated to application logic, memory leaks, or unhandled exceptions:

```bash
# 1. Immediately switch Caddy upstream back to Blue (port 18001)
caddy reload --config /etc/caddy/Caddyfile.blue

# 2. Atomically point frontend symlink back to previous release
ln -sfn "/var/www/pantheon-fe/releases/${PREVIOUS_RELEASE_ID}" /var/www/pantheon-fe/current

# 3. Terminate Green containers
docker stop bff-green && docker rm bff-green

# 4. Verify live service recovery
curl -fsS https://pantheon.trade/deployment.json | jq .
```
*Result*: Traffic is fully restored to the known-good Blue release in under 120 seconds.

### Level 2: Configuration Rollback
When an error is traced to invalid environment variables, feature flags, or external service endpoints:
- Configuration is version-controlled via immutable Git configuration tags (`config/vYYYYMMDD.N`).
- The previous configuration tag is checked out and re-applied without manual editing of host `.env` files.

### Level 3: Database Disaster Recovery (Data Plane Restoration)
Invoked ONLY if a forward migration caused data corruption or schema deadlock:
1. Immediately halt all application containers to prevent further writes:
   ```bash
   docker compose down
   ```
2. Unmount the corrupted data disk from the Prod Control VM.
3. Instantiate a replacement persistent disk from the pre-deploy snapshot taken in Step 2:
   ```bash
   gcloud compute disks create pantheon-prod-data-restored \
     --source-snapshot="pre-deploy-${RELEASE_ID}-..." \
     --zone=asia-east1-b
   ```
4. Attach the restored disk to the Prod Control VM and remount at `/var/lib/pantheon/data`.
5. Restart the Blue container stack and verify data integrity.

---

## 5. Database Expand / Contract Migration Protocol

To guarantee zero downtime and safe rollback, schema modifications MUST span two distinct release cycles:

```text
[ Release N ]  --> EXPAND PHASE
                   - Add new nullable columns or tables.
                   - Old code ignores new columns; new code writes to both.
                   - Rollback to Release N-1 remains 100% possible.

[ Release N+1 ] -> STABILIZATION & BACKFILL
                   - Background worker backfills data from old columns to new columns.
                   - Application reads exclusively from new columns.

[ Release N+2 ] -> CONTRACT PHASE (Minimum 24h later)
                   - Remove deprecated columns and old constraints.
                   - Applied only after Release N+1 is fully established.
```

---

## 6. Observation Window & Automated Abort Criteria

Following a traffic switch, the system remains in `prod_active` under active observation for **30 minutes**.

The release is automatically aborted and rolled back if any of the following triggers fire:
1. **HTTP 5xx Error Spike**: Sustained error rate $> 0.5\%$ over a 3-minute window.
2. **Latency Degradation**: P95 API response time exceeds $500\text{ms}$ (baseline $< 120\text{ms}$).
3. **Container Restarts**: Any core container restarts more than twice.
4. **Database Saturation**: Active connection count exceeds 85% of pool capacity.
5. **Execution Telemetry Disconnect**: In live trading mode, if heartbeat between Control and Execution VM is lost for $> 5$ seconds.
