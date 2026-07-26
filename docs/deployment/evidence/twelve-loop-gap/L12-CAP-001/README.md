# L12-CAP-001 — Lossless and Isolated Governed-Paper Signal Execution Proof

Task ID: `L12-CAP-001`  
Phase: `Twelve Loop Remediation / Capital`  
Owner: `Antigravity`  
Reviewer: `Claude`  

## Implementation & Proof Summary

All required changes and proof runs for `L12-CAP-001` have been implemented and verified.

### Implementation Delivered

1. **(a) Lossless Claim/Ack Visibility Model in RedisPendingSignalStore**:
   - `RedisPendingSignalStore` and `InMemoryPendingSignalStore` in `services/execution/lean_runtime/pending_signal_store.py` now use atomic `LMOVE`/`RPOPLPUSH` into worker-scoped in-flight queues (`<queue_key>:inflight:<worker_id>`).
   - Signals are removed from in-flight only upon explicit `ack()` post-execution, or reclaimed back to pending after visibility timeout (`reclaim_expired_inflight()`).

2. **(b) Validation & Execution Error DLQ Routing**:
   - `SignalConsumer` in `services/execution/lean_runtime/signal_consumer.py` routes schema/payload validation failures to the binding-scoped DLQ.
   - Execution exceptions in `_execute_one` (including `ExecutionError`, `SymbolParseError`, and generic `Exception`) route the failed payload to the DLQ instead of dropping it off the queue.

3. **(c) Fail-Closed Governed Paper Signal Isolation**:
   - `SignalConsumer` enforces fail-closed checks: missing or empty `binding_id`, `runtime_id`, or `metadata.capital_pool_id` are rejected in governed mode.
   - Corresponding isolation unit tests in `test_signal_isolation.py` and `test_signal_consumer.py` now assert fail-closed rejection.

4. **(d) Leader Lease for PaperFleetReconciler**:
   - `PaperFleetReconciler` in `services/execution/runtime-manager/paper_fleet_reconciler.py` incorporates `try_acquire_lease()` so only one leader reconciler instance owns the active worker fleet.
   - `test_paper_fleet_reconciler.py::TestLeaderLease` proves that two concurrent reconcilers converge to a single leader.

5. **(e) Proof Required Artifacts Captured**:
   - `redis_crash_before_ack_proof.txt`: Redis `LMOVE` claim, crash before ack, and in-flight reclamation verification.
   - `execution_error_dlq_proof.txt`: Execution exception DLQ routing and payload preservation.
   - `leader_lease_convergence_proof.txt`: Multi-reconciler leader lease convergence run.
   - `six_binding_restart_isolation_drill.txt`: Multi-binding restart and queue key isolation run.

### Pytest Verification Results

```text
================ 163 passed, 1 warning in 72.45s (0:01:12) =================
```
Captured proof logs saved under `docs/deployment/evidence/twelve-loop-gap/L12-CAP-001/`.
