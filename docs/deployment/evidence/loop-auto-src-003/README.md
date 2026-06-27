# Evidence: LOOP-AUTO-SRC-003 - Harden Source Scheduler Supervision

Task: `LOOP-AUTO-SRC-003`
Owner: Claude (reassigned from Gemini)
Reviewer: Codex
Date: 2026-06-27

## Delivered Surface

- **`services/source_ingestion/scheduler_worker.py`** — hardened scheduler worker with:
  - Error handling: `run_tick()` failures are caught, logged as JSON, and do not crash the worker.
  - `SchedulerState` class: persists `last_success_at`, `last_failure_at`, `last_failure_error`, `missed_tick_count`, `total_successes`, `total_failures` to a JSON state file across restarts.
  - Startup missed-tick computation: on start, computes how many scheduled ticks were missed while the process was down (elapsed time since `last_success_at` / interval − 1) and reports this in the `startup` log line.
  - Alive file: writes current UTC timestamp to `SOURCE_INGEST_SCHEDULER_ALIVE_PATH` after each tick attempt — used by the Docker healthcheck.
  - Structured JSON log output on every tick with all metric fields.

- **`docker-compose.yml`** — `source-ingest-scheduler` service changes:
  - `restart: unless-stopped` — supervisor restarts the worker on exit.
  - `SOURCE_INGEST_SCHEDULER_STATE_PATH: /data/source-ingest/scheduler_state.json` — persistent state file on the shared `source-ingest-data` volume.
  - `SOURCE_INGEST_SCHEDULER_ALIVE_PATH: /data/source-ingest/scheduler_alive` — heartbeat file for healthcheck.
  - `volumes: - source-ingest-data:/data/source-ingest` — mounts the shared volume so state and alive files persist across restarts.
  - `healthcheck` — verifies `scheduler_alive` file was modified within the last 300 seconds; `start_period: 90s` gives the first tick time to complete.

- **`services/source_ingestion/tests/test_scheduler_worker.py`** — 22 new unit tests covering:
  - `SchedulerState` with and without a persistent path.
  - Success / failure / reset behavior.
  - `compute_startup_missed` logic for various elapsed times.
  - State persistence and reload across simulated restarts.
  - Corrupt state file tolerance.
  - `main()` integration: startup log line, missed-tick reporting, error emission, success reset.

## Acceptance Evidence

### 1. Source scheduler is supervised for required dev and staging truth

`docker-compose.yml` now includes:
```yaml
source-ingest-scheduler:
  restart: unless-stopped
  healthcheck:
    test: ["CMD-SHELL", "python -c \"import os, sys, time; s=os.stat('/data/source-ingest/scheduler_alive'); sys.exit(0 if time.time()-s.st_mtime < 300 else 1)\""]
    interval: 60s
    timeout: 5s
    retries: 3
    start_period: 90s
```
When Docker Compose starts the `source-ingest-scheduler` profile, the container is restarted automatically on any exit (API down, unhandled exception, OOM).

### 2. Restart recovers missed due schedules

On every startup, `main()` calls `state.compute_startup_missed(interval_seconds=...)` which computes `floor(elapsed_since_last_success / interval) - 1`. The result is:
- Reported in the `startup` log line as `startup_missed_ticks`.
- Added to `missed_tick_count` so the operator sees the cumulative missed count immediately.
- The first tick fires immediately (before sleep) on every start, catching up any due schedules.

Test evidence for startup missed-tick computation:
```
test_compute_startup_missed_counts_elapsed_intervals     PASSED
test_compute_startup_missed_subtracts_one_for_imminent_tick PASSED
test_startup_missed_ticks_reported_on_restart           PASSED
```

### 3. Worker exposes last success, last failure, and missed tick metrics

Every tick emits a JSON log line containing:
```json
{
  "tick": 1,
  "result": {...},
  "last_success_at": "2026-06-27T06:00:00Z",
  "last_failure_at": null,
  "missed_tick_count": 0,
  "total_successes": 1,
  "total_failures": 0
}
```
Failed ticks emit:
```json
{
  "tick": 2,
  "error": "URLError: <urlopen error [Errno 111] Connection refused>",
  "last_success_at": "2026-06-27T06:00:00Z",
  "last_failure_at": "2026-06-27T06:01:00Z",
  "missed_tick_count": 1,
  "total_successes": 1,
  "total_failures": 1
}
```

## Verification

```bash
pytest -q services/source_ingestion/tests/test_scheduler_worker.py
```

Result: `22 passed in 3.02s`.

## Maturity Boundary

This task raises `source_ingestion` scheduler loop from `scheduled` toward `reconciled`.

- Desired state: `source-ingest schedule store` (schedules loaded by the API endpoint).
- Actual state: `scheduler worker health`, `source run records`.
- Reconciliation truth: `last_success_at`, `missed_tick_count` in state file + alive file mtime.

SourceHealth projection into persona/BFF panels remains owned by `LOOP-AUTO-SRC-004`.
