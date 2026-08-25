# LIFECYCLE-SYNC-STORE-TIMEOUT-20260825: Bound synchronous lifecycle projection-store operations

## Summary

This task bounds all synchronous `ProjectionStore` database operations with configurable, finite connect, statement, and lock timeouts. Previously, synchronous PostgreSQL operations during lifecycle worker startup and batch commits were unbounded, risking indefinite worker hangs if database sockets, locks, or statements stalled.

## Implemented Changes

1. **ProjectionStore Bounded Timeouts (`services/trade_journey/projection_store.py`)**:
   - Added `timeout_seconds`, `connect_timeout_seconds`, `statement_timeout_seconds`, and `lock_timeout_seconds` parameters to `ProjectionStore.__init__` (defaulting to 10.0s).
   - Added validation via `_validate_timeout` enforcing finite, positive numeric values and rejecting non-finite, boolean, non-positive, or non-numeric inputs.
   - Implemented `_connect_db()` helper configuring PostgreSQL `connect_timeout` and session options `-c statement_timeout=... -c lock_timeout=...` at connection initialization, with automatic fallback to session `SET` queries for custom connectors lacking keyword argument support.
   - Bounded the entire connection attempt (including custom connectors, slow callables, and fallbacks) using a dedicated worker thread with `connect_timeout_seconds` deadline enforcement, while avoiding broad `TypeError` swallow/retry by strictly retrying only on keyword argument signature mismatches.
   - Coordinated timeout decision, result publication, and late-connection ownership atomically under a mutex lock, guaranteeing that late connections returned during or after timeout races are cleanly closed without socket leaks.
   - Ensured all pre-return opened connections are cleanly closed on session `SET` setup failure or timeout error without socket leaks.
   - Routed all internal database operations (`bootstrap_schema`, `get_controller_state`, `adopt_legacy_baseline`, `resolve_identity`, `get_receipts`, `load_journey_stage_events_bulk`, `execute_batch_transaction`) through `_connect_db()`.

2. **Environment Variable Configuration (`services/trade_journey/lifecycle_projector.py`)**:
   - `_configured_relational_projector()` binds `LIFECYCLE_PROJECTOR_PROJECTION_TIMEOUT_SECONDS`, `LIFECYCLE_PROJECTOR_PROJECTION_CONNECT_TIMEOUT_SECONDS`, `LIFECYCLE_PROJECTOR_PROJECTION_STATEMENT_TIMEOUT_SECONDS`, and `LIFECYCLE_PROJECTOR_PROJECTION_LOCK_TIMEOUT_SECONDS` (falling back to `LIFECYCLE_PROJECTOR_DB_TIMEOUT_SECONDS` or `DEFAULT_PROJECTION_TIMEOUT_SECONDS = 10.0s`).

3. **Resilience & Error Handling**:
   - Preserved all transaction atomicity, receipt deduplication, and quarantine semantics.
   - Synchronous statement/lock/connect timeouts raise database exceptions that are caught by `run_worker()`, which invokes `_record_worker_failure` to record failure/degraded status to the durable controller without hanging or crashing the worker loop or advancing checkpoints prematurely.

4. **Governed Dev Promotion & Hosted Readback**:
   - Governed nonprod deploy workflow run `32794526552` (dispatched for dev SHA `209fcc7f50bd6c206d1ced62124163a82ca85e18`) recorded `conclusion=failure` at step `bff_lifecycle_readiness`.
   - Verified automatic rollback and compensation to the accepted baseline backend SHA `40de8fcb1c69fad0bf5e54d4c0bd6e508c9162e0` paired with frontend SHA `cc4007f7f78a31c73548ce85457af17a45a4c4b9`.
   - Hosted endpoints verified via live readback (`/healthz`, `/bff/version`, `/deployment.json`) reflecting the verified rollback baseline in degraded read-only posture (`status: verified_rollback_baseline`).
   - Source Ingestion strictly verified as reconcile-only (`MAX_TICKS=0`) and live capital actions strictly disabled (`VITE_BFF_REAL_WRITES=false`).

5. **Test Coverage**:
   - `test_projection_store_timeout_configuration_and_validation`: proves default timeout assignment and validation error handling.
   - `test_projection_store_connect_timeout_fails_within_deadline`: proves deterministic connect failure within configured finite deadline when connecting to unreachable endpoints with psycopg.
   - `test_projection_store_connect_timeout_forwarded_and_enforced_on_connector`: proves connect_timeout and statement/lock options forwarding to custom connectors.
   - `test_projection_store_blocked_connect_timeout_on_fallback`: proves deterministic timeout failure (< 0.5s) on blocked custom connector taking only dsn.
   - `test_projection_store_blocked_connect_timeout_on_kwargs_connector`: proves deterministic timeout failure (< 0.5s) on blocked custom connector taking kwargs.
   - `test_projection_store_internal_type_error_not_swallowed_or_retried`: proves internal `TypeError` is not swallowed or retried.
   - `test_projection_store_internal_type_error_ambiguous_text_single_attempt`: proves ambiguous internal `TypeError` containing 'takes' or 'positional argument' executes exactly one attempt without fallback retry.
   - `test_projection_store_internal_type_error_nested_callee_single_attempt`: proves internal `TypeError` from nested callee is not treated as signature mismatch.
   - `test_projection_store_blocked_connect_does_not_hang_process_exit`: proves daemon worker thread does not block Python interpreter shutdown or require process timeout kill.
   - `test_projection_store_late_connection_is_closed_after_timeout`: proves late connection returned by slow connector after timeout deadline is closed without resource leak.
   - `test_projection_store_connect_timeout_result_publication_race_closes_connection`: proves late connection returned after caller timeout decision is closed atomically without leak.
   - `test_projection_store_connect_timeout_publication_race_controlled_interleaving`: proves deterministic controlled interleaving where connection success is published between wait timeout and caller lock acquisition while connection close blocks, asserting caller timeout deadline is strictly preserved and close executes on the dedicated `projection-store-conn-cleanup` thread. Demonstrates failure against 53f50201 (caller blocked on synchronous close) and passes at this fix.
   - `test_projection_store_fallback_cursor_timeout_race_closes_connection`: proves connection created during fallback session setup is closed atomically if timeout occurs during query execution.
   - `test_projection_store_blocked_connect_error_propagation_on_fallback`: proves error propagation on fallback custom connectors.
   - `test_projection_store_connect_fallback_closes_connection_on_setup_error`: proves connection is immediately closed on setup failure during session SET statement_timeout/lock_timeout without socket leak.
   - `test_projection_store_statement_timeout_cancels_long_query`: proves PostgreSQL statement cancellation via `QueryCanceled` within configured deadline.
   - `test_projection_store_lock_timeout_cancels_blocked_lock`: proves blocked row/advisory locks cancel via `LockNotAvailable` / `QueryCanceled` within configured deadline.
   - `test_projection_store_connect_fallback_sets_timeouts_on_custom_connector`: proves fallback session timeout configuration on custom connectors.
   - `test_configured_relational_projector_binds_projection_timeouts`: proves environment variable threading to `ProjectionStore`.
   - `test_run_worker_recovers_after_startup_projector_timeout_failure`: proves worker startup timeout error is caught, logged, and recovered on subsequent tick without crashing worker loop.
   - `test_run_worker_recovers_after_blocked_store_batch_timeout`: proves batch projection timeout causes durable failure mutation without premature checkpoint advancement, followed by recovery and steady-state live poll transitions.
   - `test_run_worker_recovers_after_projection_store_timeout_failure`: proves projector recovery after statement timeout failure.
   - `test_configured_relational_projector_rejects_invalid_timeout_env_vars`: proves validation of invalid environment variable values.
