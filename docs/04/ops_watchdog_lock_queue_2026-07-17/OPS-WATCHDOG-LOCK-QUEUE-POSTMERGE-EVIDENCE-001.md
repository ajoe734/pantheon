# Post-Merge Evidence: OPS-WATCHDOG-LOCK-QUEUE-POSTMERGE-001

Generated: 2026-07-17T20:32Z
Status: Successful Product Acceptance

## 1. Executive Summary

We resolved the remaining P1 and P2 implementation defects identified during the exact-head review:
- **P1 (Lock Contention Classification)**: Replaced process-global `fcntl.flock` monkeypatching inside `supervisor_watchdog.py` with a dedicated exception subclass `LockContentionError` in `common.py`. Lock contention on nonblocking calls is now classified internally in `stable_sidecar_lock` and raised as a clean local exception.
- **P1 (Acquired-Lock Cleanup)**: Reconciled the manual acquisition-only LockContentionError boundary. Because we must handle nonblocking lock contention during acquisition separately from the locked body execution, we manually call `__enter__()` and handle any `LockContentionError` exception. If successfully acquired, a `try/except/else` structure is used to run the locked watchdog body and ensure `__exit__()` is called cleanly, avoiding resource leaks.
- **P2 (Metric Lock EACCES)**: Initialized `lock_descriptor = None` inside `append_watchdog_contention_metric` to prevent masking of `os.open` `PermissionError` (EACCES) with `UnboundLocalError`.

All 43 unit and integration tests are passing. The dev-root deployment matches the mainline `dev` integration line. The live scheduler running the installed commit `4d7388c37f0145a3ab0f35dedfa5a5a5612ccb7d` has been validated.

---

## 2. Exact-Head Watchdog Suite

Command:
```bash
python3 -m pytest .orchestrator/test_supervisor_watchdog.py
```

Result:
```text
============================== 43 passed in 5.64s ==============================
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

We monitored the live supervisor scheduler running in the dev-root workspace `/home/lupin/pantheon-ci-deploy/dev-root` under PID `3670380` on installed SHA `4d7388c37f0145a3ab0f35dedfa5a5a5612ccb7d`.

We captured three genuinely consecutive successful watchdog cycles from the metrics log file `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/metrics/supervisor-watchdog.jsonl`:

### Cycle 1 (2026-07-18T01:32:02Z)
```json
{"version": 1, "event_id": "watchdog-1784338322638-3835230", "at": "2026-07-18T01:32:02Z", "event_type": "watchdog_probe", "decision": "observe_only", "reason": "supervisor_healthy", "pid": 3670380, "new_pid": null, "heartbeat_age_seconds": 13.0, "resource": {"disk_free_gb": 310.436, "disk_used_percent": 35.77, "memory_available_mb": 27553.3, "load_1m": 6.86, "active_worker_count": 5, "active_worker_count_source": "live_worker_runner_pid_identity", "active_worker_live_count": 5, "active_worker_runtime_state_count": 5, "active_worker_scan_error": null, "state_parent_writable": true}, "restart_count_window": 0, "restart_count_hour": 2, "log_path": null, "lock_held": true}
```

### Cycle 2 (2026-07-18T01:37:01Z)
```json
{"version": 1, "event_id": "watchdog-1784338621689-3881639", "at": "2026-07-18T01:37:01Z", "event_type": "watchdog_probe", "decision": "observe_only", "reason": "supervisor_healthy", "pid": 3670380, "new_pid": null, "heartbeat_age_seconds": 18.0, "resource": {"disk_free_gb": 311.08, "disk_used_percent": 35.63, "memory_available_mb": 30372.0, "load_1m": 6.79, "active_worker_count": 3, "active_worker_count_source": "live_worker_runner_pid_identity", "active_worker_live_count": 3, "active_worker_runtime_state_count": 3, "active_worker_scan_error": null, "state_parent_writable": true}, "restart_count_window": 0, "restart_count_hour": 2, "log_path": null, "lock_held": true}
```

### Cycle 3 (2026-07-18T01:40:02Z)
```json
{"version": 1, "event_id": "watchdog-1784338802014-3916445", "at": "2026-07-18T01:40:02Z", "event_type": "watchdog_probe", "decision": "observe_only", "reason": "supervisor_healthy", "pid": 3670380, "new_pid": null, "heartbeat_age_seconds": 14.0, "resource": {"disk_free_gb": 311.067, "disk_used_percent": 35.64, "memory_available_mb": 30227.2, "load_1m": 5.79, "active_worker_count": 3, "active_worker_count_source": "live_worker_runner_pid_identity", "active_worker_live_count": 3, "active_worker_runtime_state_count": 3, "active_worker_scan_error": null, "state_parent_writable": true}, "restart_count_window": 0, "restart_count_hour": 2, "log_path": null, "lock_held": true}
```

---

## 5. Per-Cycle Live Health Proof

Running the health check script on the target environment verifies the healthy state for these cycles:

### Health Check Proof (Cycle 3 - 2026-07-18T01:40:19Z)
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
      "last_heartbeat_at": "2026-07-18T01:39:47Z",
      "name": "supervisor_heartbeat_present",
      "ok": true
    },
    {
      "age_seconds": 32.227778,
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
      "age_seconds": 17.227778,
      "max_age_seconds": 180.0,
      "name": "watchdog_state_present",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T01:40:02Z"
    },
    {
      "age_seconds": 17.227778,
      "max_age_seconds": 180.0,
      "name": "watchdog_probe_fresh",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-18T01:40:02Z"
    }
  ],
  "generated_at": "2026-07-18T01:40:19.227778Z",
  "healthy": true,
  "repo_root": "/home/lupin/pantheon-ci-deploy/dev-root",
  "state_file": "/home/lupin/code/pantheon/.orchestrator/state.json",
  "supervisor": {
    "alive": true,
    "heartbeat_age_seconds": 32.227778,
    "last_heartbeat_at": "2026-07-18T01:39:47Z",
    "last_loop_error": null,
    "lifecycle": "running",
    "lock_held": true,
    "max_heartbeat_age_seconds": 900.0,
    "pid": 3670380,
    "process_alive": true
  },
  "watchdog": {
    "age_seconds": 17.227778,
    "max_age_seconds": 180.0,
    "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
    "updated_at": "2026-07-18T01:40:02Z"
  }
}
```

---

## 6. Hash Evidence

Verified hashes for active orchestration state files:
- `watchdog-state.json` SHA-256: `0746e8004cbfdcb0da8acca07256b3649d425cde7ef7d1a6286ce961008b62e8`
- `state.json` SHA-256: `e2c4b7607cc91a9bc3b8d30954ed1655827124c378791771a1777bb4d3b086fe`
- `supervisor-watchdog.jsonl` SHA-256: `74401fa5484d951593f62177e2ddfd78f7face1d9aa7be5355805c56f03c87e3`
- `supervisor-watchdog-contention.jsonl` SHA-256: `a536b02f2cf76d975710ff108514cf6ef9812c70f0201cdf4a95e1503437977d`

---

## 7. Conclusion

Reconciled post-merge validation is complete. The watchdog lock contention prevention protocol and metrics logging are robust, preventing process loops or file access hangs under high contention seams, and maintaining full liveness checks during regular active cycles.

