# L12-CAP-001 — Lossless and Isolated Governed-Paper Signal Execution Proof

Task ID: `L12-CAP-001`  
Phase: `Twelve Loop Remediation / Capital`  
Owner: `Antigravity`  
Reviewer: `Claude`  

## Implementation & Proof Summary

All required changes and proof runs for `L12-CAP-001` have been implemented and verified.

### Implementation & Review Blocker Resolution

1. **[B1] Lossless Claim/Ack Visibility Model & Per-Entry Timestamp Reclaim**:
   - `RedisPendingSignalStore` in `services/execution/lean_runtime/pending_signal_store.py` records worker claim timestamps (`<inflight_key>:timestamps`) upon claim.
   - `reclaim_expired_inflight()` uses `SCAN` to locate in-flight lists, checks timestamps, and reclaims entries older than `_visibility_timeout_seconds`. Live worker in-flight lists holding unexpired claims are preserved.
   - Rebalance buffering in `signal_consumer.py` now acknowledges deduplicated or processed signals.

2. **[B2] Cross-Process Leader Lease for PaperFleetReconciler**:
   - `PaperFleetReconciler` in `services/execution/runtime-manager/paper_fleet_reconciler.py` supports cross-process lease backends (Redis client, file-backed JSON store, or dict) using wall-clock timestamps (`time.time()`).
   - `_run_loop` passes `self._leader_store` to `reconcile_once(self._leader_store)` on every cycle, ensuring single-leader fleet management across processes.

3. **[B3] Real Unit & Isolation Tests and Executable Proof Logs**:
   - Unit tests implemented in `test_signal_isolation.py` cover crash-before-ack reclamation (`TestRedisPendingSignalStoreClaimVisibility`), cross-process leader lease (`TestLeaderLeaseCrossProcess`), execution exception DLQ routing (`TestExecutionErrorDLQ`), and 6-binding restart isolation drill (`TestSixBindingRestartIsolationDrill`).
   - Command lines and outputs captured in evidence files: `redis_crash_before_ack_proof.txt`, `execution_error_dlq_proof.txt`, `leader_lease_convergence_proof.txt`, `six_binding_restart_isolation_drill.txt`.

4. **[B4] Authenticated Actor and Tenant Scoping on Capital Service Mutations**:
   - `CapitalBoundaryService` in `services/capital/main.py` enforces actor role write authority through `_authorize(resource_type, operation, actor_role)`.
   - `PermissionError` is raised with HTTP status 403 when authorization fails.

### Unit Test Verification Results

```text
Ran 69 tests in services.execution.lean_runtime (test_signal_isolation, test_signal_consumer) -> OK
Ran 40 tests in services.execution.runtime-manager (test_paper_fleet_reconciler) -> OK
```
Captured proof logs saved under `docs/deployment/evidence/twelve-loop-gap/L12-CAP-001/`.
