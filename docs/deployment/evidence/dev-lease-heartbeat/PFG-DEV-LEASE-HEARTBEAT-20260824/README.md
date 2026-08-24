# Review Evidence: PFG-DEV-LEASE-HEARTBEAT-20260824

## Task Summary
- **Task ID**: `PFG-DEV-LEASE-HEARTBEAT-20260824`
- **Title**: Repair dev deploy lease heartbeat persistence and startup retry
- **Owner**: `Antigravity`
- **Reviewer**: `Antigravity2`

## Key Fixes
1. **Heartbeat Lifecycle & SIGHUP Immunity**:
   - `scripts/dev_environment_lease.py`: Ignored `signal.SIGHUP` in `heartbeat_loop` and added retry logic on transient network / GitHub 5xx errors while the local lease remains unexpired.
   - `.github/workflows/nonprod-deploy.yml`: Added `disown` to background heartbeat startup so GHA runner subshell exit does not signal or terminate the heartbeat process.
2. **Startup Readiness Retry & Transient Connection Resets**:
   - `scripts/deploy_nonprod_vm.sh`: Removed premature `curl_with_retry http://127.0.0.1:18001/health` check before `wait_for_exact_bff_lifecycle_readiness` in `root` and `bff` deploy paths, ensuring transient connection resets during container startup enter the bounded lifecycle readiness gate (600s initial + 180s recovery extension) without triggering early rollback.
3. **Source Ingestion Invariant**:
   - Maintained `SOURCE_INGEST_CONTROLLER_MODE="reconcile_only"`, `SOURCE_INGEST_CONTROLLER_MAX_TICKS="0"`, `PANTHEON_EXTERNAL_EGRESS="deny"`.
