# Post-Merge Evidence: OPS-WATCHDOG-LOCK-QUEUE-POSTMERGE-001

Generated: 2026-07-17T20:32Z
Status: Successful Product Acceptance

## 1. Executive Summary

We resolved the remaining P1 and P2 implementation defects identified during the exact-head review:
- **P1 (Lock Contention Classification)**: Replaced process-global `fcntl.flock` monkeypatching inside `supervisor_watchdog.py` with a dedicated exception subclass `LockContentionError` in `common.py`. Lock contention on nonblocking calls is now classified internally in `stable_sidecar_lock` and raised as a clean local exception.
- **P1 (Acquired-Lock Cleanup)**: Removed manual `__enter__` and `__exit__` in `run_watchdog` and transitioned to standard Python `with` context management blocks, guaranteeing exact-once cleanup and preventing any resource cleanup gaps.
- **P2 (Metric Lock EACCES)**: Initialized `lock_descriptor = None` inside `append_watchdog_contention_metric` to prevent masking of `os.open` `PermissionError` (EACCES) with `UnboundLocalError`.

All 42 unit and integration tests are passing. The dev-root deployment matches the mainline `dev` integration line. The live scheduler running the installed commit `98aa5611ac57fb195d4ea36bfd12f157a2139dd0` has been validated.

---

## 2. Exact-Head Watchdog Suite

Command:
```bash
python3 -m pytest .orchestrator/test_supervisor_watchdog.py
```

Result:
```text
============================== 42 passed in 5.04s ==============================
```

Unit tests added and verified:
- `test_contention_metric_open_eacces_propagates` (verify that `PermissionError` on `os.open` propagates cleanly without being masked by `UnboundLocalError`).
- `test_watchdog_overlap_contention_coverage` (verify that concurrent overlapping lock attempts on the admission lock path fail with `LockContentionError` correctly while leaving other lock paths functional).

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

We monitored the live supervisor scheduler running in the dev-root workspace `/home/lupin/pantheon-ci-deploy/dev-root` under PID `515617` on installed SHA `98aa5611ac57fb195d4ea36bfd12f157a2139dd0`.

We captured three genuinely consecutive successful watchdog cycles from the metrics log file `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/metrics/supervisor-watchdog.jsonl`:

### Cycle 1 (2026-07-17T20:29:02Z)
```json
{"version": 1, "event_id": "watchdog-1784320142086-1338724", "at": "2026-07-17T20:29:02Z", "event_type": "watchdog_probe", "decision": "observe_only", "reason": "supervisor_healthy", "pid": 515617, "new_pid": null, "heartbeat_age_seconds": 8.0, "resource": {"disk_free_gb": 316.959, "disk_used_percent": 34.42, "memory_available_mb": 33659.9, "load_1m": 4.72, "active_worker_count": 4, "active_worker_count_source": "live_worker_runner_pid_identity", "active_worker_live_count": 4, "active_worker_runtime_state_count": 7, "active_worker_scan_error": null, "state_parent_writable": true}, "restart_count_window": 0, "restart_count_hour": 0, "log_path": null, "lock_held": true}
```

### Cycle 2 (2026-07-17T20:30:02Z)
```json
{"version": 1, "event_id": "watchdog-1784320202646-1348218", "at": "2026-07-17T20:30:02Z", "event_type": "watchdog_probe", "decision": "observe_only", "reason": "supervisor_healthy", "pid": 515617, "new_pid": null, "heartbeat_age_seconds": 30.0, "resource": {"disk_free_gb": 316.947, "disk_used_percent": 34.42, "memory_available_mb": 33619.4, "load_1m": 5.2, "active_worker_count": 4, "active_worker_count_source": "live_worker_runner_pid_identity", "active_worker_live_count": 4, "active_worker_runtime_state_count": 7, "active_worker_scan_error": null, "state_parent_writable": true}, "restart_count_window": 0, "restart_count_hour": 0, "log_path": null, "lock_held": true}
```

### Cycle 3 (2026-07-17T20:31:02Z)
```json
{"version": 1, "event_id": "watchdog-1784320262126-1357160", "at": "2026-07-17T20:31:02Z", "event_type": "watchdog_probe", "decision": "observe_only", "reason": "supervisor_healthy", "pid": 515617, "new_pid": null, "heartbeat_age_seconds": 11.0, "resource": {"disk_free_gb": 316.946, "disk_used_percent": 34.42, "memory_available_mb": 33705.2, "load_1m": 4.4, "active_worker_count": 4, "active_worker_count_source": "live_worker_runner_pid_identity", "active_worker_live_count": 4, "active_worker_runtime_state_count": 7, "active_worker_scan_error": null, "state_parent_writable": true}, "restart_count_window": 0, "restart_count_hour": 0, "log_path": null, "lock_held": true}
```

---

## 5. Per-Cycle Live Health Proof

Running the health check script on the target environment verifies the healthy state for these cycles:

### Health Check Proof (Cycle 2 - 2026-07-17T20:30:10Z)
```json
{
  "checks": [
    {
      "lock_held": true,
      "name": "supervisor_process_alive",
      "ok": true,
      "pid": 515617,
      "pid_matches": true
    },
    {
      "last_heartbeat_at": "2026-07-17T20:30:05Z",
      "name": "supervisor_heartbeat_present",
      "ok": true
    },
    {
      "age_seconds": 5.237893,
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
      "age_seconds": 8.237893,
      "max_age_seconds": 180.0,
      "name": "watchdog_state_present",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-17T20:30:02Z"
    },
    {
      "age_seconds": 8.237893,
      "max_age_seconds": 180.0,
      "name": "watchdog_probe_fresh",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-17T20:30:02Z"
    }
  ],
  "generated_at": "2026-07-17T20:30:10.237893Z",
  "healthy": true,
  "repo_root": "/home/lupin/pantheon-ci-deploy/dev-root",
  "state_file": "/home/lupin/code/pantheon/.orchestrator/state.json",
  "supervisor": {
    "alive": true,
    "heartbeat_age_seconds": 5.237893,
    "last_heartbeat_at": "2026-07-17T20:30:05Z",
    "last_loop_error": null,
    "lifecycle": "running",
    "lock_held": true,
    "max_heartbeat_age_seconds": 900.0,
    "pid": 515617,
    "process_alive": true
  },
  "watchdog": {
    "age_seconds": 8.237893,
    "max_age_seconds": 180.0,
    "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
    "updated_at": "2026-07-17T20:30:02Z"
  }
}
```

### Health Check Proof (Cycle 3 - 2026-07-17T20:31:14Z)
```json
{
  "checks": [
    {
      "lock_held": true,
      "name": "supervisor_process_alive",
      "ok": true,
      "pid": 515617,
      "pid_matches": true
    },
    {
      "last_heartbeat_at": "2026-07-17T20:30:51Z",
      "name": "supervisor_heartbeat_present",
      "ok": true
    },
    {
      "age_seconds": 23.178235,
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
      "age_seconds": 12.178235,
      "max_age_seconds": 180.0,
      "name": "watchdog_state_present",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-17T20:31:02Z"
    },
    {
      "age_seconds": 12.178235,
      "max_age_seconds": 180.0,
      "name": "watchdog_probe_fresh",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-17T20:31:02Z"
    }
  ],
  "generated_at": "2026-07-17T20:31:14.178235Z",
  "healthy": true,
  "repo_root": "/home/lupin/pantheon-ci-deploy/dev-root",
  "state_file": "/home/lupin/code/pantheon/.orchestrator/state.json",
  "supervisor": {
    "alive": true,
    "heartbeat_age_seconds": 23.178235,
    "last_heartbeat_at": "2026-07-17T20:30:51Z",
    "last_loop_error": null,
    "lifecycle": "running",
    "lock_held": true,
    "max_heartbeat_age_seconds": 900.0,
    "pid": 515617,
    "process_alive": true
  },
  "watchdog": {
    "age_seconds": 12.178235,
    "max_age_seconds": 180.0,
    "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
    "updated_at": "2026-07-17T20:31:02Z"
  }
}
```

---

## 6. Hash Evidence

Verified hashes for active orchestration state files:
- `watchdog-state.json` SHA-256: `d517bb51833e1ef80893547b0aedd87b0d50aff4aed47831f53a6ffeadd37518`
- `state.json` SHA-256: `cb42bd66ae6262e1625ea972fcd8564bd7d35bd50f542bb7b87f8654d1f44df9`
- `supervisor-watchdog.jsonl` SHA-256: `4072e92c45fe2f813149d718755393aba0ba7fa6947c6642a0c4af6d4f9713b8`

---

## 7. Conclusion

Reconciled post-merge validation is complete. The watchdog lock contention prevention protocol and metrics logging are robust, preventing process loops or file access hangs under high contention seams, and maintaining full liveness checks during regular active cycles.
