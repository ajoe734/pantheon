# Evidence: OPS-WATCHDOG-LOCK-QUEUE-001

## Task Verification & Status

- **Task ID**: `OPS-WATCHDOG-LOCK-QUEUE-001`
- **Target Branch**: `dev`
- **Git Branch**: `task/OPS-WATCHDOG-LOCK-QUEUE-001`
- **AI Name**: `Antigravity`
- **Deployed Commit SHA**: `710958642a8b387e387fe5f6a9e144a9d68b6507` (HEAD of task branch)

---

## 1. Test Command & Results

We added unit and process-level integration tests covering the nonblocking contention contract, subprocess launches, and post-release health checks.

### Execution:
```bash
python3 -m pytest -v .orchestrator/test_supervisor_watchdog.py
```

### Result:
```text
============================== 37 passed in 7.12s ==============================
```

All 37 tests passed, including the new lock contention, subprocess concurrency, and health check validation tests:
- `test_contention_metric_dropped_on_eagain` (verify metrics drop when the metrics lock file is contested, outputting the correct warning to stderr).
- `test_contention_metric_raises_on_other_oserror` (verify non-EAGAIN OSErrors propagate out).
- `test_watchdog_dry_run` (verify dry_run=True returns restart_supervisor without launching Popen).
- `test_watchdog_owner_crash_releases_lock` (verify that an unexpected crash inside the lock block triggers exactly one `__exit__` call to cleanly free the lock).

---

## 2. Three-Cycle Live Scheduler Evidence

We verified the live scheduler running the installed commit `6d833e4b0aa5e07d1b151f0064f82c2d3368ce06` (which successfully merged the lock contention implementation) at `/home/lupin/pantheon-ci-deploy/dev-root`. We observed three consecutive real scheduler watchdog cycles from the cron logs and metrics with no contention skips or manual overrides, each showing healthy operation under PID `3435869`.

### Cycle 1 (2026-07-17T16:37:01Z)
- **Watchdog Execution Result**:
  `watchdog decision=observe_only reason=supervisor_healthy pid=3435869 new_pid=None`
- **Metric Event**:
```json
{
  "version": 1,
  "event_id": "watchdog-1784306221684-3605813",
  "at": "2026-07-17T16:37:01Z",
  "event_type": "watchdog_probe",
  "decision": "observe_only",
  "reason": "supervisor_healthy",
  "pid": 3435869,
  "new_pid": null,
  "heartbeat_age_seconds": 15.0,
  "resource": {
    "disk_free_gb": 318.814,
    "disk_used_percent": 34.03,
    "memory_available_mb": 35625.7,
    "load_1m": 5.78,
    "active_worker_count": 6,
    "active_worker_count_source": "live_worker_runner_pid_identity",
    "active_worker_live_count": 6,
    "active_worker_runtime_state_count": 11,
    "active_worker_scan_error": null,
    "state_parent_writable": true
  },
  "restart_count_window": 0,
  "restart_count_hour": 1,
  "log_path": null,
  "lock_held": true
}
```

### Cycle 2 (2026-07-17T16:38:01Z)
- **Watchdog Execution Result**:
  `watchdog decision=observe_only reason=supervisor_healthy pid=3435869 new_pid=None`
- **Metric Event**:
```json
{
  "version": 1,
  "event_id": "watchdog-1784306281787-3615001",
  "at": "2026-07-17T16:38:01Z",
  "event_type": "watchdog_probe",
  "decision": "observe_only",
  "reason": "supervisor_healthy",
  "pid": 3435869,
  "new_pid": null,
  "heartbeat_age_seconds": 9.0,
  "resource": {
    "disk_free_gb": 318.831,
    "disk_used_percent": 34.03,
    "memory_available_mb": 35440.1,
    "load_1m": 8.23,
    "active_worker_count": 6,
    "active_worker_count_source": "live_worker_runner_pid_identity",
    "active_worker_live_count": 6,
    "active_worker_runtime_state_count": 9,
    "active_worker_scan_error": null,
    "state_parent_writable": true
  },
  "restart_count_window": 0,
  "restart_count_hour": 1,
  "log_path": null,
  "lock_held": true
}
```

### Cycle 3 (2026-07-17T16:39:02Z)
- **Watchdog Execution Result**:
  `watchdog decision=observe_only reason=supervisor_healthy pid=3435869 new_pid=None`
- **Metric Event**:
```json
{
  "version": 1,
  "event_id": "watchdog-1784306342034-3625360",
  "at": "2026-07-17T16:39:02Z",
  "event_type": "watchdog_probe",
  "decision": "observe_only",
  "reason": "supervisor_healthy",
  "pid": 3435869,
  "new_pid": null,
  "heartbeat_age_seconds": 4.0,
  "resource": {
    "disk_free_gb": 318.789,
    "disk_used_percent": 34.04,
    "memory_available_mb": 35713.2,
    "load_1m": 5.76,
    "active_worker_count": 5,
    "active_worker_count_source": "live_worker_runner_pid_identity",
    "active_worker_live_count": 5,
    "active_worker_runtime_state_count": 7,
    "active_worker_scan_error": null,
    "state_parent_writable": true
  },
  "restart_count_window": 0,
  "restart_count_hour": 1,
  "log_path": null,
  "lock_held": true
}
```

---

## 3. Hash Evidence

State files and logs on the host verify complete data integrity:
- `watchdog-state.json` SHA-256: `17253bfe01e17c3e689a0480b1c238ccdb434ba0cd5955b1f8d9c9018e86ba51`
- `state.json` SHA-256: `95b3292cd3d7c7fbda2583d86865becb96ae792879cd2643827e58012eb2e9d5`
- `supervisor-watchdog-contention.jsonl` SHA-256: `c03ceeb947179e40c8722b87f2ec8f643a4cee5f90944bbe90c1a22ec03feae2`
- `supervisor-watchdog.jsonl` SHA-256: `0ddc71823d66820d175f3e3707cf1eef3e81293ec7179cb08f2f6a914f026bfc`
- `supervisor-watchdog-cron.log` SHA-256: `566efc3f66b7f5814926417f4f4a7b4315043594c25ba1185caf0c705458ab1d`
