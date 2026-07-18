# Post-Merge Evidence: OPS-WATCHDOG-LOCK-QUEUE-POSTMERGE-001

Generated: 2026-07-18T02:59:37Z
Status: Successful Product Acceptance on Deployed Final HEAD

## 1. Executive Summary

We resolved the remaining P1 and P2 implementation defects identified during the exact-head review, deploying the final fixes to HEAD `c9560db5cba9583bd2dff70894e583cdca5d2a20`. The previous c559 artifacts have been marked historical and fully replaced by the c956 evidence captured herein:
- **P1 (Lock Contention Classification)**: Replaced process-global `fcntl.flock` monkeypatching inside `supervisor_watchdog.py` with a dedicated exception subclass `LockContentionError` in `common.py`. Lock contention on nonblocking calls is now classified internally in `stable_sidecar_lock` and raised as a clean local exception.
- **P1 (Acquired-Lock Cleanup)**: Reconciled the manual acquisition-only LockContentionError boundary. If successfully acquired, a `try/except/else` structure is used to run the locked watchdog body and ensure `__exit__()` is called cleanly, avoiding resource leaks.
- **P2 (Metric Lock EACCES)**: Initialized `lock_descriptor = None` inside `append_watchdog_contention_metric` to prevent masking of `os.open` `PermissionError` (EACCES) with `UnboundLocalError`.
- **P2 (Restart Count Metrics)**: Corrected the run_watchdog contention path to not report hard-coded `0` values for `restart_count_window` and `restart_count_hour` when they are unknown (reported as `None` / `null` instead).
- **P2 (Initially-Free Concurrent Probe Test)**: Added a new concurrent probe unit test (`test_initially_free_concurrent_probes_max_one_owner`) proving that when the lock is initially free and multiple probes execute, at most one active probe owns the critical section while others skip immediately and do not block.

All 44 unit and integration tests are passing. The dev-root deployment matches the mainline `dev` integration line. The live scheduler running the merged task commit `c9560db5cba9583bd2dff70894e583cdca5d2a20` has been validated.

---

## 2. Exact-Head Watchdog Suite

Command:
```bash
PYTHONPATH=.orchestrator python3 -m pytest .orchestrator/test_supervisor_watchdog.py
```

Result:
```text
============================== 44 passed in 5.81s ==============================
```

Unit tests verified:
- `test_contention_metric_open_eacces_propagates` (verify that `PermissionError` on `os.open` propagates cleanly without being masked by `UnboundLocalError`).
- `test_watchdog_overlap_contention_coverage` (verify that concurrent overlapping lock attempts on the admission lock path fail with `LockContentionError` correctly while leaving other lock paths functional).
- `test_initially_free_concurrent_probes_max_one_owner` (verify that at most one active critical-section owner is admitted concurrently on an initially free lock path, while other threads skip immediately).

---

## 3. Isolated Contention Fixture

We verified lock contention behavior using the isolated test fixture script run on the exact deployed final HEAD:
```bash
python3 docs/04/ops_watchdog_lock_queue_2026-07-17/archive/postmerge_lock_contention_fixture.py --repo .
```

All 12+12 concurrent probes were correctly handled:
- Primary held admission lock: 12/12 skipped with `lock_contention` reason and exit code 0.
- Secondary held metrics lock: 12/12 skipped and output dropped warning to stderr.
- Post-release: successfully verified normal healthy transition after lock release.
- **Rerun verification**: The generated contention batches correctly record `null` for `restart_count_hour` and `restart_count_window`, matching the final c956 `None` contract under contention.

---

## 4. Three-Cycle Live Scheduler Evidence

We monitored the live supervisor scheduler running in the dev-root workspace `/home/lupin/pantheon-ci-deploy/dev-root` under PID `202546` on installed HEAD commit `c9560db5cba9583bd2dff70894e583cdca5d2a20` (the merge commit of the task).

We captured three genuinely consecutive successful watchdog cycles from the metrics log file `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/metrics/supervisor-watchdog.jsonl` with no skips or contentions in between them:

### Cycle 1 (2026-07-18T02:57:02Z)
- **Watchdog Execution Result**:
  `watchdog decision=observe_only reason=supervisor_healthy pid=202546 new_pid=None`
- **Watchdog process count**: 0 active watchdog processes (at sample time, no concurrent watchdog run was active)
- **Supervisor process count**: 14 (Active supervisor PID: 202546 and worker runners)
- **Admission lock waiter count**: 0
- **Metric Event**:
```json
{"at": "2026-07-18T02:57:02Z", "decision": "observe_only", "event_id": "watchdog-1784343422183-470465", "event_type": "watchdog_probe", "heartbeat_age_seconds": 10.0, "lock_held": true, "log_path": null, "new_pid": null, "pid": 202546, "reason": "supervisor_healthy", "resource": {"active_worker_count": 5, "active_worker_count_source": "live_worker_runner_pid_identity", "active_worker_live_count": 5, "active_worker_runtime_state_count": 5, "active_worker_scan_error": null, "disk_free_gb": 310.299, "disk_used_percent": 35.8, "load_1m": 5.19, "memory_available_mb": 29269.3, "state_parent_writable": true}, "restart_count_hour": 1, "restart_count_window": 0, "version": 1}
```

### Cycle 2 (2026-07-18T02:58:02Z)
- **Watchdog Execution Result**:
  `watchdog decision=observe_only reason=supervisor_healthy pid=202546 new_pid=None`
- **Watchdog process count**: 0 active watchdog processes
- **Supervisor process count**: 12
- **Admission lock waiter count**: 0
- **Metric Event**:
```json
{"at": "2026-07-18T02:58:02Z", "decision": "observe_only", "event_id": "watchdog-1784343482546-478529", "event_type": "watchdog_probe", "heartbeat_age_seconds": 29.0, "lock_held": true, "log_path": null, "new_pid": null, "pid": 202546, "reason": "supervisor_healthy", "resource": {"active_worker_count": 4, "active_worker_count_source": "live_worker_runner_pid_identity", "active_worker_live_count": 4, "active_worker_runtime_state_count": 4, "active_worker_scan_error": null, "disk_free_gb": 310.516, "disk_used_percent": 35.75, "load_1m": 5.08, "memory_available_mb": 27280.5, "state_parent_writable": true}, "restart_count_hour": 1, "restart_count_window": 0, "version": 1}
```

### Cycle 3 (2026-07-18T02:59:35Z)
- **Watchdog Execution Result**:
  `watchdog decision=observe_only reason=supervisor_healthy pid=202546 new_pid=None`
- **Watchdog process count**: 0 active watchdog processes
- **Supervisor process count**: 12
- **Admission lock waiter count**: 0
- **Metric Event**:
```json
{"at": "2026-07-18T02:59:35Z", "decision": "observe_only", "event_id": "watchdog-1784343575072-489896", "event_type": "watchdog_probe", "heartbeat_age_seconds": 31.0, "lock_held": true, "log_path": null, "new_pid": null, "pid": 202546, "reason": "supervisor_healthy", "resource": {"active_worker_count": 4, "active_worker_count_source": "live_worker_runner_pid_identity", "active_worker_live_count": 4, "active_worker_runtime_state_count": 4, "active_worker_scan_error": null, "disk_free_gb": 310.508, "disk_used_percent": 35.75, "load_1m": 7.48, "memory_available_mb": 29542.5, "state_parent_writable": true}, "restart_count_hour": 1, "restart_count_window": 0, "version": 1}
```

---

## 5. Per-Cycle Live Health Proof

Running the health check script on the target environment verifies the healthy state for these cycles. We provide the full require-watchdog health check JSON for each of the three cycles:

### Health Check Proof (Cycle 1 - 2026-07-18T02:57:04Z)
```json
{
  "checks": [
    {
      "lock_held": true,
      "name": "supervisor_process_alive",
      "ok": true,
      "pid": 202546,
      "pid_matches": true
    },
    {
      "last_heartbeat_at": "2026-07-18T02:56:52Z",
      "name": "supervisor_heartbeat_present",
      "ok": true
    },
    {
      "age_seconds": 12.105277,
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
      "age_seconds": 2.105277,
      "max_age_seconds": 180.0,
      "name": "watchdog_state_present",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T02:57:02Z"
    },
    {
      "age_seconds": 2.105277,
      "max_age_seconds": 180.0,
      "name": "watchdog_probe_fresh",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T02:57:02Z"
    }
  ],
  "generated_at": "2026-07-18T02:57:04.105277Z",
  "healthy": true,
  "repo_root": "/home/lupin/pantheon-ci-deploy/dev-root",
  "state_file": "/home/lupin/code/pantheon/.orchestrator/state.json",
  "supervisor": {
    "alive": true,
    "heartbeat_age_seconds": 12.105277,
    "last_heartbeat_at": "2026-07-18T02:56:52Z",
    "last_loop_error": null,
    "lifecycle": "running",
    "lock_held": true,
    "max_heartbeat_age_seconds": 900.0,
    "pid": 202546,
    "process_alive": true
  },
  "watchdog": {
    "age_seconds": 2.105277,
    "max_age_seconds": 180.0,
    "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
    "updated_at": "2026-07-18T02:57:02Z"
  }
}
```

### Health Check Proof (Cycle 2 - 2026-07-18T02:58:05Z)
```json
{
  "checks": [
    {
      "lock_held": true,
      "name": "supervisor_process_alive",
      "ok": true,
      "pid": 202546,
      "pid_matches": true
    },
    {
      "last_heartbeat_at": "2026-07-18T02:57:51Z",
      "name": "supervisor_heartbeat_present",
      "ok": true
    },
    {
      "age_seconds": 14.987716,
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
      "age_seconds": 3.987716,
      "max_age_seconds": 180.0,
      "name": "watchdog_state_present",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T02:58:02Z"
    },
    {
      "age_seconds": 3.987716,
      "max_age_seconds": 180.0,
      "name": "watchdog_probe_fresh",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T02:58:02Z"
    }
  ],
  "generated_at": "2026-07-18T02:58:05.987716Z",
  "healthy": true,
  "repo_root": "/home/lupin/pantheon-ci-deploy/dev-root",
  "state_file": "/home/lupin/code/pantheon/.orchestrator/state.json",
  "supervisor": {
    "alive": true,
    "heartbeat_age_seconds": 14.987716,
    "last_heartbeat_at": "2026-07-18T02:57:51Z",
    "last_loop_error": null,
    "lifecycle": "running",
    "lock_held": true,
    "max_heartbeat_age_seconds": 900.0,
    "pid": 202546,
    "process_alive": true
  },
  "watchdog": {
    "age_seconds": 3.987716,
    "max_age_seconds": 180.0,
    "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
    "updated_at": "2026-07-18T02:58:02Z"
  }
}
```

### Health Check Proof (Cycle 3 - 2026-07-18T02:59:37Z)
```json
{
  "checks": [
    {
      "lock_held": true,
      "name": "supervisor_process_alive",
      "ok": true,
      "pid": 202546,
      "pid_matches": true
    },
    {
      "last_heartbeat_at": "2026-07-18T02:59:22Z",
      "name": "supervisor_heartbeat_present",
      "ok": true
    },
    {
      "age_seconds": 15.471898,
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
      "age_seconds": 2.471898,
      "max_age_seconds": 180.0,
      "name": "watchdog_state_present",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T02:59:35Z"
    },
    {
      "age_seconds": 2.471898,
      "max_age_seconds": 180.0,
      "name": "watchdog_probe_fresh",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T02:59:35Z"
    }
  ],
  "generated_at": "2026-07-18T02:59:37.471898Z",
  "healthy": true,
  "repo_root": "/home/lupin/pantheon-ci-deploy/dev-root",
  "state_file": "/home/lupin/code/pantheon/.orchestrator/state.json",
  "supervisor": {
    "alive": true,
    "heartbeat_age_seconds": 15.471898,
    "last_heartbeat_at": "2026-07-18T02:59:22Z",
    "last_loop_error": null,
    "lifecycle": "running",
    "lock_held": true,
    "max_heartbeat_age_seconds": 900.0,
    "pid": 202546,
    "process_alive": true
  },
  "watchdog": {
    "age_seconds": 2.471898,
    "max_age_seconds": 180.0,
    "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
    "updated_at": "2026-07-18T02:59:35Z"
  }
}
```

---

## 6. Hash Evidence

State files and logs on the host verify complete data integrity. Below are the file sizes and SHA-256 hashes captured at each cycle of our natural three-tick window:

### Cycle 1 (2026-07-18T02:57:02Z)
- `watchdog-state.json` (6683 bytes) SHA-256: `f959f97039416dcc6e0a33347bf123ea55c251228d550c025bf68df42f4a91b1`
- `supervisor-watchdog.jsonl` (23538612 bytes) SHA-256: `299244e0ef7434f6e3a8f0ac68bb36c3c6a498c2526ff6cc6c5623e35390d0ff`
- `state.json` (3146507 bytes) SHA-256: `ecae5ccbedd0e0cd7fbbda1c90f3ec8616ba3a67d648c178ee9464f31637b5f5`

### Cycle 2 (2026-07-18T02:58:02Z)
- `watchdog-state.json` (6684 bytes) SHA-256: `f9ee46d15473647df63bd085fa7988f059a38f20e741d2934330acf73ecfbb77`
- `supervisor-watchdog.jsonl` (23539281 bytes) SHA-256: `75e42e3c31a57f705eacae6ca09434f21a86f29794eec893600a02c8ed6d1f35`
- `state.json` (3131614 bytes) SHA-256: `ab6278ed870da7c4b654d1bc8c997dd6d0261b4df508a713fb2c0d930a759413`

### Cycle 3 (2026-07-18T02:59:35Z)
- `watchdog-state.json` (6684 bytes) SHA-256: `e13d38ae8cfc1cc7adc055a79a542f27c7ab99a231b8cddf24b8e795668316d9`
- `supervisor-watchdog.jsonl` (23539951 bytes) SHA-256: `c5da355a5a713fa34586739c444031083a08aec6c460712eaa40909c25bdec7a`
- `state.json` (3131647 bytes) SHA-256: `d8ce881da2f75537dbe4934730546950333d4735571972902f19d1a3954b4e71`

Static/Shared configurations remain stable:
- `live-supervisor-mainroot-config.json` SHA-256: `007ed3c9af032463b03d45ee494ee284b5b6a6bf6651eb79d0ac27e6b7df9e6a`
- `supervisor-watchdog-contention.jsonl` SHA-256: `f3bb7acf41bac2a97aa66a04be8ba3de2ec33c67f0a73bbee6088066bf9c4ccb`

---

## 7. Raw Archive Manifest

The complete raw logs, stats, config hashes, and deployed SHA reflog proofs are stored in the redacted raw structured artifact at:
[ops_watchdog_three_tick_manifest.json](file:///tmp/pantheon-worker-worktrees/pantheon/ops-watchdog-lock-queue-001/docs/04/ops_watchdog_lock_queue_2026-07-17/archive/ops_watchdog_three_tick_manifest.json)

---

## 8. Conclusion

Reconciled post-merge validation is complete on the final c956 deployed head. The watchdog lock contention prevention protocol and metrics logging are robust, preventing process loops or file access hangs under high contention seams, and maintaining full liveness checks during regular active cycles.
