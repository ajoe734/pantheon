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

We deployed the candidate commit `710958642a8b387e387fe5f6a9e144a9d68b6507` to `/home/lupin/pantheon-ci-deploy/dev-root` and observed three consecutive scheduler cycles showing successful `observe_only` decisions and healthy readbacks.

### Cycle 1
- **Watchdog Execution Result**:
  `watchdog decision=observe_only reason=supervisor_healthy pid=3739917 new_pid=None`
- **Health Readback**:
```json
{
  "checks": [
    {
      "lock_held": true,
      "name": "supervisor_process_alive",
      "ok": true,
      "pid": 3739917,
      "pid_matches": true
    },
    {
      "last_heartbeat_at": "2026-07-17T16:57:24Z",
      "name": "supervisor_heartbeat_present",
      "ok": true
    },
    {
      "age_seconds": 4.769208,
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
      "age_seconds": 0.769208,
      "max_age_seconds": 180.0,
      "name": "watchdog_state_present",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-17T16:57:28Z"
    },
    {
      "age_seconds": 0.769208,
      "max_age_seconds": 180.0,
      "name": "watchdog_probe_fresh",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-17T16:57:28Z"
    }
  ],
  "healthy": true
}
```

### Cycle 2
- **Watchdog Execution Result**:
  `watchdog decision=observe_only reason=supervisor_healthy pid=3739917 new_pid=None`
- **Health Readback**:
```json
{
  "checks": [
    {
      "lock_held": true,
      "name": "supervisor_process_alive",
      "ok": true,
      "pid": 3739917,
      "pid_matches": true
    },
    {
      "last_heartbeat_at": "2026-07-17T16:57:24Z",
      "name": "supervisor_heartbeat_present",
      "ok": true
    },
    {
      "age_seconds": 15.766164,
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
      "age_seconds": 0.766164,
      "max_age_seconds": 180.0,
      "name": "watchdog_state_present",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-17T16:57:39Z"
    },
    {
      "age_seconds": 0.766164,
      "max_age_seconds": 180.0,
      "name": "watchdog_probe_fresh",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-17T16:57:39Z"
    }
  ],
  "healthy": true
}
```

### Cycle 3
- **Watchdog Execution Result**:
  `watchdog decision=observe_only reason=supervisor_healthy pid=3739917 new_pid=None`
- **Health Readback**:
```json
{
  "checks": [
    {
      "lock_held": true,
      "name": "supervisor_process_alive",
      "ok": true,
      "pid": 3739917,
      "pid_matches": true
    },
    {
      "last_heartbeat_at": "2026-07-17T16:57:24Z",
      "name": "supervisor_heartbeat_present",
      "ok": true
    },
    {
      "age_seconds": 26.34921,
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
      "age_seconds": 0.34921,
      "max_age_seconds": 180.0,
      "name": "watchdog_state_present",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-17T16:57:50Z"
    },
    {
      "age_seconds": 0.34921,
      "max_age_seconds": 180.0,
      "name": "watchdog_probe_fresh",
      "ok": true,
      "state_file": "/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/watchdog-state.json",
      "updated_at": "2026-07-17T16:57:50Z"
    }
  ],
  "healthy": true
}
```

---

## 3. Hash Evidence

State files and logs on the host verify complete data integrity:
- `watchdog-state.json` SHA-256: `7366986cb72bf9f79422dec6ed5c88b3be72941dd7158050605d090208248b2f`
- `state.json` SHA-256: `5a823e668366eeeabe12496e97674f7c5166230de8c2f0cb9fdf25061c7f5005`
- `supervisor-watchdog-contention.jsonl` SHA-256: `e13baae94c3e766538eab1b3a10f8a6b4cdca52edede7b1caa82862c8df6d42b`
- `supervisor-watchdog.jsonl` SHA-256: `d992a05644cc875ad084650b5a0d7b4a37326a444a9dceaab17291b3ad34ffa1`
- `supervisor-watchdog-cron.log` SHA-256: `87e6850e75a953c9765eddf3f11856478afeddf6661ded4c1656e7fac3067543`
