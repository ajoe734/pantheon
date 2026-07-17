# Redacted Evidence: OPS-WATCHDOG-LOCK-QUEUE-001

## Task Verification & Status

- **Task ID**: `OPS-WATCHDOG-LOCK-QUEUE-001`
- **Target Branch**: `dev`
- **Git Branch**: `task/OPS-WATCHDOG-LOCK-QUEUE-001`
- **AI Name**: `Antigravity`
- **Base SHA**: `7097e8d2a7c15593763bdba302a8b3950a998b04`

---

## 1. Test Command & Results

We added unit and process-level integration tests covering the nonblocking contention contract, subprocess launches, and post-release health checks.

### Execution:
```bash
python3 .orchestrator/test_supervisor_watchdog.py
```

### Result:
```
................................
----------------------------------------------------------------------
Ran 32 tests in 2.007s

OK
```

All 32 tests passed, including the new lock contention, subprocess concurrency, and health check validation tests:
- `test_lock_contention_returns_skip_immediately` (unit: held lock + second probe bounded return with `skip` decision, verifying contention metrics are logged).
- `test_lock_contention_multi_tick_bounded` (unit: 10+ sequential watchdog cron ticks under lock contention exit immediately, bounded).
- `test_lock_contention_subprocess_launches` (process-level integration: 12 concurrent watchdog processes spawned in separate subprocesses while lock is held exit immediately with exit code 0, do not block/accumulate, and write 12 structured entries to the contention metrics file).
- `test_lock_release_and_probe_updates_state` (release & health check: release lock -> single normal probe updates state files successfully -> validates with `supervisor_runtime_health.py --require-watchdog --json` that the system is fully healthy and fresh).

---

## 2. Contention Path Evidence Contract

To prevent lock contention from blocking execution or corrupting files, the watchdog implements a safe, structured, and aggregate-able evidence contract:
1. **Zero Write to Locked State**: Under contention on `runtime-admission.lock`, the watchdog does not write to the main `watchdog-state.json` or `metrics.jsonl`.
2. **Independent Contention Metric Log**: It appends a structured JSON event to `.orchestrator/metrics/supervisor-watchdog-contention.jsonl` using its own independent lock file (`.lock`), bypassing the main admission lock.
3. **Structured JSON Output**: When run with `--json`, it prints the structured result to `stdout` under contention, allowing external monitoring tools to parse and aggregate the event.

**Contention Metric JSON Schema:**
```json
{
  "version": 1,
  "event_id": "watchdog-contention-1718612345000-12345",
  "at": "2026-07-17T03:00:00Z",
  "decision": "skip",
  "reason": "lock_contention",
  "pid": 123,
  "new_pid": null,
  "heartbeat_age_seconds": 45.0,
  "resource": {
    "disk_free_gb": 10.0,
    "disk_used_percent": 50.0,
    "memory_available_mb": 4096.0,
    "load_1m": 1.0,
    "active_worker_count": 0,
    "active_worker_count_source": "live_worker_runner_pid_identity",
    "active_worker_live_count": 0,
    "active_worker_runtime_state_count": 0,
    "active_worker_scan_error": null,
    "state_parent_writable": true
  },
  "restart_count_window": 0,
  "restart_count_hour": 0,
  "log_path": null,
  "lock_held": true
}
```

---

## 3. Process Concurrency & Waiter Behavior (Process Acceptance)

In the subprocess integration test `test_lock_contention_subprocess_launches`, we simulated 12 isolated cron launches (concurrently via Python subprocesses) under lock contention:
- **Active Waiters Count**: 0 (all 12 watchdog subprocesses exited immediately with exit code `0` and printed the structured contention JSON).
- **Elapsed Time**: Spawning 12 subprocesses and waiting for all to complete took < 1s, proving they do not wait or accumulate.
- This confirms that watchdog processes will not build up background waiter queues or exceed system process limits under lock contention.

---

## 4. Post-Release Health Verification (Health Acceptance)

When the lock is released:
- The next watchdog probe acquires the lock, executes successfully, and updates state freshness in both `state.json` and `watchdog-state.json`.
- Running `supervisor_runtime_health.py --require-watchdog --json` evaluates all freshness constraints:
  - `supervisor_process_alive`: `ok` (singleton lock is held by the supervisor).
  - `supervisor_heartbeat_present`: `ok` (heartbeat is found).
  - `supervisor_heartbeat_fresh`: `ok` (within allowed threshold).
  - `watchdog_state_present`: `ok` (watchdog state exists).
  - `watchdog_probe_fresh`: `ok` (watchdog updated within the 180s threshold).
  - Returns `"healthy": true` and exit code `0`.
