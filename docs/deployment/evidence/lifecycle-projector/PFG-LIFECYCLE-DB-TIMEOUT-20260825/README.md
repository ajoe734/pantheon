# PFG-LIFECYCLE-DB-TIMEOUT-20260825: Bound Lifecycle Projector Postgres Operations and Add Timeout Regression Tests

## Summary

Bounds all lifecycle projector PostgreSQL source operations (`verify_read_contract`, `high_watermark`, `fetch_after`, `start_listener`, `close`) with finite configurable timeouts and proves error recording and recoverable loop health under transient timeouts.

### Problem
Previously, `high_watermark` and `fetch_after` in `PostgresLifecycleSource` performed unbounded `asyncpg.connect()`, `conn.fetchval()`, `conn.fetch()`, and `conn.close()` calls without deadline enforcement. A stalled database connection or slow query could block worker execution indefinitely without recording degraded status or allowing loop recovery.

### Solution
1. **Configurable Timeout Parameter**: `PostgresLifecycleSource` now accepts `timeout_seconds` (defaulting to `DEFAULT_SOURCE_TIMEOUT_SECONDS = 10.0`) and `startup_timeout_seconds` (defaulting to `DEFAULT_SOURCE_STARTUP_TIMEOUT_SECONDS = 10.0`).
2. **Environment Variable Wiring**: `run_worker()` threads `LIFECYCLE_PROJECTOR_SOURCE_TIMEOUT_SECONDS` (or `LIFECYCLE_PROJECTOR_DB_TIMEOUT_SECONDS`) and `LIFECYCLE_PROJECTOR_STARTUP_TIMEOUT_SECONDS` into `PostgresLifecycleSource`.
3. **Deadline Bounds Across Operations**:
   - `high_watermark()` bounds connect, query (`fetchval`), and close/terminate within the configured deadline.
   - `fetch_after()` bounds connect, query (`fetch`), and close/terminate within the configured deadline.
   - `start_listener()` bounds connect, `add_listener`, and close/terminate within the configured deadline.
   - `close()` terminates listener cleanly if `conn.close()` exceeds timeout.
4. **Degraded Health and Recoverability**: When a database timeout occurs, `_record_worker_failure` publishes the failure to the projection controller, recording `status="failed"`, `accepted_live=False`, and the exact `TimeoutError`. When the database recovers in subsequent ticks, `project_records` / `record_poll` clears the error and restores `status="ready"`, `accepted_live=True`.
5. **Comprehensive Unit and Regression Tests**: Added 10 timeout regression test cases covering connect, query, close/terminate timeouts across all source methods and demonstrating full loop recovery.
