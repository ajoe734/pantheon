# Post-Merge Evidence: OPS-WATCHDOG-LOCK-QUEUE-POSTMERGE-001

Generated: 2026-07-18T02:28:05Z
Status: Successful Product Acceptance

## 1. Executive Summary

We resolved the remaining P1 and P2 implementation defects identified during the exact-head review:
- **P1 (Lock Contention Classification)**: Replaced process-global `fcntl.flock` monkeypatching inside `supervisor_watchdog.py` with a dedicated exception subclass `LockContentionError` in `common.py`. Lock contention on nonblocking calls is now classified internally in `stable_sidecar_lock` and raised as a clean local exception.
- **P1 (Acquired-Lock Cleanup)**: Reconciled the manual acquisition-only LockContentionError boundary. Because we must handle nonblocking lock contention during acquisition separately from the locked body execution, we manually call `__enter__()` and handle any `LockContentionError` exception. If successfully acquired, a `try/except/else` structure is used to run the locked watchdog body and ensure `__exit__()` is called cleanly, avoiding resource leaks.
- **P2 (Metric Lock EACCES)**: Initialized `lock_descriptor = None` inside `append_watchdog_contention_metric` to prevent masking of `os.open` `PermissionError` (EACCES) with `UnboundLocalError`.
- **P2 (Restart Count Metrics)**: Corrected the run_watchdog contention path to not report hard-coded `0` values for `restart_count_window` and `restart_count_hour` when they are unknown (reported as `None` instead).
- **P2 (Initially-Free Concurrent Probe Test)**: Added a new concurrent probe unit test (`test_initially_free_concurrent_probes_max_one_owner`) proving that when the lock is initially free and multiple probes execute, at most one active probe owns the critical section while others skip immediately and do not block.

All 44 unit and integration tests are passing. The dev-root deployment matches the mainline `dev` integration line. The live scheduler running the merged task commit `c5592c1068a8570c659cb484dbd53466c080769b` has been validated.

---

## 2. Exact-Head Watchdog Suite

Command:
```bash
PYTHONPATH=.orchestrator python3 -m pytest .orchestrator/test_supervisor_watchdog.py
```

Result:
```text
============================== 44 passed in 5.26s ==============================
```

Unit tests added and verified:
- `test_contention_metric_open_eacces_propagates` (verify that `PermissionError` on `os.open` propagates cleanly without being masked by `UnboundLocalError`).
- `test_watchdog_overlap_contention_coverage` (verify that concurrent overlapping lock attempts on the admission lock path fail with `LockContentionError` correctly while leaving other lock paths functional).
- `test_initially_free_concurrent_probes_max_one_owner` (verify that at most one active critical-section owner is admitted concurrently on an initially free lock path, while other threads skip immediately).

---

## 3. Isolated Contention Fixture

We verified lock contention behavior using the isolated test fixture script:
```bash
python3 docs/04/ops_watchdog_lock_queue_2026-07-17/archive/postmerge_lock_contention_fixture.py --repo .
```

All 12+12 concurrent probes were correctly handled:
- Primary held admission lock: 12/12 skipped with `lock_contention` reason and exit code 0.
- Secondary held metrics lock: 12/12 skipped and output dropped warning to stderr.
- Post-release: successfully verified normal healthy transition after lock release.

---

## 4. Three-Cycle Live Scheduler Evidence

We monitored the live supervisor scheduler running in the dev-root workspace `/home/lupin/pantheon-ci-deploy/dev-root` under PID `3670380` on installed HEAD commit `c5592c1068a8570c659cb484dbd53466c080769b` (the merge commit of the task).

We captured three genuinely consecutive successful watchdog cycles from the metrics log file `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/metrics/supervisor-watchdog.jsonl` with no skips or contentions in between them:

### Cycle 1 (2026-07-18T02:26:01Z)
- **Watchdog Execution Result**:
  `watchdog decision=observe_only reason=supervisor_healthy pid=3670380 new_pid=None`
- **Watchdog process count**: 1 (Active supervisor PID: 3670380, no other active watchdog processes)
- **Admission lock waiter count**: 0
- **Metric Event**:
```json
{"version": 1, "event_id": "watchdog-1784341561641-142476", "at": "2026-07-18T02:26:01Z", "event_type": "watchdog_probe", "decision": "observe_only", "reason": "supervisor_healthy", "pid": 3670380, "new_pid": null, "heartbeat_age_seconds": 31.0, "resource": {"disk_free_gb": 311.072, "disk_used_percent": 35.64, "memory_available_mb": 29950.6, "load_1m": 5.73, "active_worker_count": 2, "active_worker_count_source": "live_worker_runner_pid_identity", "active_worker_live_count": 2, "active_worker_runtime_state_count": 2, "active_worker_scan_error": null, "state_parent_writable": true}, "restart_count_window": 0, "restart_count_hour": 0, "log_path": null, "lock_held": true}
```

### Cycle 2 (2026-07-18T02:27:01Z)
- **Watchdog Execution Result**:
  `watchdog decision=observe_only reason=supervisor_healthy pid=3670380 new_pid=None`
- **Watchdog process count**: 1 (Active supervisor PID: 3670380, no other active watchdog processes)
- **Admission lock waiter count**: 0
- **Metric Event**:
```json
{"version": 1, "event_id": "watchdog-1784341621754-154140", "at": "2026-07-18T02:27:01Z", "event_type": "watchdog_probe", "decision": "observe_only", "reason": "supervisor_healthy", "pid": 3670380, "new_pid": null, "heartbeat_age_seconds": 11.0, "resource": {"disk_free_gb": 310.854, "disk_used_percent": 35.68, "memory_available_mb": 29687.0, "load_1m": 5.04, "active_worker_count": 3, "active_worker_count_source": "live_worker_runner_pid_identity", "active_worker_live_count": 3, "active_worker_runtime_state_count": 3, "active_worker_scan_error": null, "state_parent_writable": true}, "restart_count_window": 0, "restart_count_hour": 0, "log_path": null, "lock_held": true}
```

### Cycle 3 (2026-07-18T02:28:02Z)
- **Watchdog Execution Result**:
  `watchdog decision=observe_only reason=supervisor_healthy pid=3670380 new_pid=None`
- **Watchdog process count**: 1 (Active supervisor PID: 3670380, no other active watchdog processes)
- **Admission lock waiter count**: 0
- **Metric Event**:
```json
{"version": 1, "event_id": "watchdog-1784341682075-166239", "at": "2026-07-18T02:28:02Z", "event_type": "watchdog_probe", "decision": "observe_only", "reason": "supervisor_healthy", "pid": 3670380, "new_pid": null, "heartbeat_age_seconds": 30.0, "resource": {"disk_free_gb": 310.851, "disk_used_percent": 35.68, "memory_available_mb": 29807.1, "load_1m": 6.34, "active_worker_count": 3, "active_worker_count_source": "live_worker_runner_pid_identity", "active_worker_live_count": 3, "active_worker_runtime_state_count": 3, "active_worker_scan_error": null, "state_parent_writable": true}, "restart_count_window": 0, "restart_count_hour": 0, "log_path": null, "lock_held": true}
```

---

## 5. Per-Cycle Live Health Proof

Running the health check script on the target environment verifies the healthy state for these cycles. We provide the full require-watchdog health check JSON for each of the three cycles:

### Health Check Proof (Cycle 1 - 2026-07-18T02:26:04Z)
```json
{
  "checks": [
    {
      "lock_held": true,
      "name": "supervisor_process_alive",
      "ok": true,
      "pid": 3670380,
      "pid_matches": true
    },
    {
      "last_heartbeat_at": "2026-07-18T02:26:02Z",
      "name": "supervisor_heartbeat_present",
      "ok": true
    },
    {
      "age_seconds": 2.67025,
      "max_age_seconds": 900.0,
      "name": "supervisor_heartbeat_fresh",
      "ok": true
    },
    {
      "last_loop_error": null,
      "lifecycle": "running",
      "name": "supervisor_not_degraded",
      "ok": true
    },
    {
      "age_seconds": 3.67025,
      "max_age_seconds": 180.0,
      "name": "watchdog_state_present",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T02:26:01Z"
    },
    {
      "age_seconds": 3.67025,
      "max_age_seconds": 180.0,
      "name": "watchdog_probe_fresh",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T02:26:01Z"
    }
  ],
  "generated_at": "2026-07-18T02:26:04.670250Z",
  "healthy": true,
  "repo_root": "/home/lupin/pantheon-ci-deploy/dev-root",
  "state_file": "/home/lupin/code/pantheon/.orchestrator/state.json",
  "supervisor": {
    "alive": true,
    "heartbeat_age_seconds": 2.67025,
    "last_heartbeat_at": "2026-07-18T02:26:02Z",
    "last_loop_error": null,
    "lifecycle": "running",
    "lock_held": true,
    "max_heartbeat_age_seconds": 900.0,
    "pid": 3670380,
    "process_alive": true
  },
  "watchdog": {
    "age_seconds": 3.67025,
    "max_age_seconds": 180.0,
    "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
    "updated_at": "2026-07-18T02:26:01Z"
  }
}
```

### Health Check Proof (Cycle 2 - 2026-07-18T02:27:04Z)
```json
{
  "checks": [
    {
      "lock_held": true,
      "name": "supervisor_process_alive",
      "ok": true,
      "pid": 3670380,
      "pid_matches": true
    },
    {
      "last_heartbeat_at": "2026-07-18T02:26:50Z",
      "name": "supervisor_heartbeat_present",
      "ok": true
    },
    {
      "age_seconds": 14.039266,
      "max_age_seconds": 900.0,
      "name": "supervisor_heartbeat_fresh",
      "ok": true
    },
    {
      "last_loop_error": null,
      "lifecycle": "running",
      "name": "supervisor_not_degraded",
      "ok": true
    },
    {
      "age_seconds": 3.039266,
      "max_age_seconds": 180.0,
      "name": "watchdog_state_present",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T02:27:01Z"
    },
    {
      "age_seconds": 3.039266,
      "max_age_seconds": 180.0,
      "name": "watchdog_probe_fresh",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T02:27:01Z"
    }
  ],
  "generated_at": "2026-07-18T02:27:04.039266Z",
  "healthy": true,
  "repo_root": "/home/lupin/pantheon-ci-deploy/dev-root",
  "state_file": "/home/lupin/code/pantheon/.orchestrator/state.json",
  "supervisor": {
    "alive": true,
    "heartbeat_age_seconds": 14.039266,
    "last_heartbeat_at": "2026-07-18T02:26:50Z",
    "last_loop_error": null,
    "lifecycle": "running",
    "lock_held": true,
    "max_heartbeat_age_seconds": 900.0,
    "pid": 3670380,
    "process_alive": true
  },
  "watchdog": {
    "age_seconds": 3.039266,
    "max_age_seconds": 180.0,
    "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
    "updated_at": "2026-07-18T02:27:01Z"
  }
}
```

### Health Check Proof (Cycle 3 - 2026-07-18T02:28:04Z)
```json
{
  "checks": [
    {
      "lock_held": true,
      "name": "supervisor_process_alive",
      "ok": true,
      "pid": 3670380,
      "pid_matches": true
    },
    {
      "last_heartbeat_at": "2026-07-18T02:27:31Z",
      "name": "supervisor_heartbeat_present",
      "ok": true
    },
    {
      "age_seconds": 33.343176,
      "max_age_seconds": 900.0,
      "name": "supervisor_heartbeat_fresh",
      "ok": true
    },
    {
      "last_loop_error": null,
      "lifecycle": "running",
      "name": "supervisor_not_degraded",
      "ok": true
    },
    {
      "age_seconds": 2.343176,
      "max_age_seconds": 180.0,
      "name": "watchdog_state_present",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T02:28:02Z"
    },
    {
      "age_seconds": 2.343176,
      "max_age_seconds": 180.0,
      "name": "watchdog_probe_fresh",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T02:28:02Z"
    }
  ],
  "generated_at": "2026-07-18T02:28:04.343176Z",
  "healthy": true,
  "repo_root": "/home/lupin/pantheon-ci-deploy/dev-root",
  "state_file": "/home/lupin/code/pantheon/.orchestrator/state.json",
  "supervisor": {
    "alive": true,
    "heartbeat_age_seconds": 33.343176,
    "last_heartbeat_at": "2026-07-18T02:27:31Z",
    "last_loop_error": null,
    "lifecycle": "running",
    "lock_held": true,
    "max_heartbeat_age_seconds": 900.0,
    "pid": 3670380,
    "process_alive": true
  },
  "watchdog": {
    "age_seconds": 2.343176,
    "max_age_seconds": 180.0,
    "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
    "updated_at": "2026-07-18T02:28:02Z"
  }
}
```

---

## 6. Hash Evidence

State files and logs on the host verify complete data integrity. Below are the file sizes and SHA-256 hashes captured at each cycle of our natural three-tick window:

### Cycle 1 (2026-07-18T02:26:01Z)
- `watchdog-state.json` (6438 bytes) SHA-256: `3a2f9a85242158780a5b5acafe22a11e0d2bd9c259ec186507bdab0ad8e92f1d`
- `supervisor-watchdog.jsonl` (23521593 bytes) SHA-256: `c9c3b0d4a2df98e65e9fb0528f8a2a75e7ffcc6eda74b726f38fd86ede638860`
- `state.json` (3105664 bytes) SHA-256: `840785c30912e0e5fab77ab9427d7b20c3e3c3dc501b7c8834b3d171503f6cfc`

### Cycle 2 (2026-07-18T02:27:01Z)
- `watchdog-state.json` (6438 bytes) SHA-256: `9eb5480b92dd0f36c8ef7b7d6624c3ad854fe10eeea0f4d47e20bf12f9a13920`
- `supervisor-watchdog.jsonl` (23522271 bytes) SHA-256: `f2fe3d0bd51b65f2c2b5041d65f9e7bb27e7622a6fa086a0c064595c12706c33`
- `state.json` (3123359 bytes) SHA-256: `786abcabadf334a5c80522f00ff82ebab3c369d1e4a0247c7e7ebdd7c14ce89e`

### Cycle 3 (2026-07-18T02:28:02Z)
- `watchdog-state.json` (6438 bytes) SHA-256: `5a46068151b921c32cf1d46e5dc87cf413490b30cadefb9388b1bcb97282b1db`
- `supervisor-watchdog.jsonl` (23522949 bytes) SHA-256: `bba67282ea87ce6990d262561dd5a6f8f8f4eaddfad33f3cf6cf9b629750dc91`
- `state.json` (3123656 bytes) SHA-256: `828116e61e75fb6710f66b02fb03307e8cad7ca60e236a474ff0ecf3cb86e9ff`

Static/Shared configurations remain stable:
- `live-supervisor-mainroot-config.json` SHA-256: `007ed3c9af032463b03d45ee494ee284b5b6a6bf6651eb79d0ac27e6b7df9e6a`
- `supervisor-watchdog-contention.jsonl` SHA-256: `c48e45392cc2c660bd0c8500bd060a29664f31e1243a05ca5b72ddbd87d6d85b`

---

## 7. Raw Archive Manifest

The complete raw logs, stats, config hashes, and deployed SHA reflog proofs are stored in the redacted raw structured artifact at:
[ops_watchdog_three_tick_manifest.json](file:///tmp/pantheon-worker-worktrees/pantheon/ops-watchdog-lock-queue-001/docs/04/ops_watchdog_lock_queue_2026-07-17/archive/ops_watchdog_three_tick_manifest.json)

---

## 8. Conclusion

Reconciled post-merge validation is complete. The watchdog lock contention prevention protocol and metrics logging are robust, preventing process loops or file access hangs under high contention seams, and maintaining full liveness checks during regular active cycles.
