# LIFECYCLE-SYNC-STORE-TIMEOUT-20260825: Bound synchronous lifecycle projection-store operations

## Summary

This task bounds all synchronous `ProjectionStore` database operations with configurable, finite connect, statement, and lock timeouts. Previously, synchronous PostgreSQL operations during lifecycle worker startup and batch commits were unbounded, risking indefinite worker hangs if database sockets, locks, or statements stalled.

## Implemented Changes

1. **ProjectionStore Bounded Timeouts (`services/trade_journey/projection_store.py`)**:
   - Added `timeout_seconds`, `connect_timeout_seconds`, `statement_timeout_seconds`, and `lock_timeout_seconds` parameters to `ProjectionStore.__init__` (defaulting to 10.0s).
   - Added validation via `_validate_timeout` enforcing finite, positive numeric values and rejecting non-finite, boolean, non-positive, or non-numeric inputs.
   - Implemented `_connect_db()` helper configuring PostgreSQL `connect_timeout` and session options `-c statement_timeout=... -c lock_timeout=...` at connection initialization, with automatic fallback to session `SET` queries for custom connectors lacking keyword argument support.
   - Routed all internal database operations (`bootstrap_schema`, `get_controller_state`, `adopt_legacy_baseline`, `resolve_identity`, `get_receipts`, `load_journey_stage_events_bulk`, `execute_batch_transaction`) through `_connect_db()`.

2. **Environment Variable Configuration (`services/trade_journey/lifecycle_projector.py`)**:
   - `_configured_relational_projector()` binds `LIFECYCLE_PROJECTOR_PROJECTION_TIMEOUT_SECONDS`, `LIFECYCLE_PROJECTOR_PROJECTION_CONNECT_TIMEOUT_SECONDS`, `LIFECYCLE_PROJECTOR_PROJECTION_STATEMENT_TIMEOUT_SECONDS`, and `LIFECYCLE_PROJECTOR_PROJECTION_LOCK_TIMEOUT_SECONDS` (falling back to `LIFECYCLE_PROJECTOR_DB_TIMEOUT_SECONDS` or `DEFAULT_PROJECTION_TIMEOUT_SECONDS = 10.0s`).

3. **Resilience & Error Handling**:
   - Preserved all transaction atomicity, receipt deduplication, and quarantine semantics.
   - Synchronous statement/lock/connect timeouts raise database exceptions that are caught by `run_worker()`, which invokes `_record_worker_failure` to record failure/degraded status to the durable controller without hanging or crashing the worker loop.

4. **Test Coverage**:
   - `test_projection_store_timeout_configuration_and_validation`: proves default timeout assignment and validation error handling.
   - `test_projection_store_connect_timeout_fails_within_deadline`: proves deterministic connect failure within configured finite deadline when connecting to unreachable endpoints with psycopg.
   - `test_projection_store_connect_timeout_forwarded_and_enforced_on_connector`: proves connect_timeout and statement/lock options forwarding to custom connectors.
   - `test_projection_store_blocked_connect_error_propagation_on_fallback`: proves error propagation on fallback custom connectors.
   - `test_projection_store_statement_timeout_cancels_long_query`: proves PostgreSQL statement cancellation via `QueryCanceled` within configured deadline.
   - `test_projection_store_lock_timeout_cancels_blocked_lock`: proves blocked row/advisory locks cancel via `LockNotAvailable` / `QueryCanceled` within configured deadline.
   - `test_projection_store_connect_fallback_sets_timeouts_on_custom_connector`: proves fallback session timeout configuration on custom connectors.
   - `test_configured_relational_projector_binds_projection_timeouts`: proves environment variable threading to `ProjectionStore`.
   - `test_run_worker_recovers_after_projection_store_timeout_failure`: proves intermediate failure mutation records degraded/failed status and error message without premature checkpoint advancement, followed by recovery and steady-state live poll transitions.
   - `test_configured_relational_projector_rejects_invalid_timeout_env_vars`: proves validation of invalid environment variable values.
