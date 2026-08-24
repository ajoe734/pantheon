# PFG-LIFECYCLE-POSTGRES-DRIVER-20260824 Evidence

This directory contains the review evidence manifest for adding the missing `psycopg[binary]` runtime dependency and startup regression tests for the relational lifecycle projector.

## Summary of Changes

1. **Declared Runtime Dependency**: Added `psycopg[binary]>=3.1,<4.0` to `services/telemetry/requirements.txt`. The `loop-run-projector-scheduler` compose service builds `services/telemetry/Dockerfile`, which installs `services/telemetry/requirements.txt`.
2. **Explicit Fail-Closed Guard**: Updated `services/trade_journey/projection_store.py` to wrap `import psycopg` in `try...except ImportError` and raise `RuntimeError("psycopg is required for ProjectionStore")`.
3. **Regression Tests**:
   - `services/trade_journey/test_projection_store.py`:
     - `test_projection_store_driver_missing_fails_closed`: proves `ProjectionStore` fails closed when `psycopg` is missing.
     - `test_projection_store_default_connect_binds_psycopg`: proves default construction binds `psycopg.connect`.
   - `services/trade_journey/test_lifecycle_projector.py`:
     - `test_relational_projector_fails_closed_without_psycopg_driver`: proves `_configured_relational_projector()` fails closed when `psycopg` is missing.
     - `test_projector_runtime_requirements_declares_psycopg_driver`: asserts `services/telemetry/requirements.txt` contains `psycopg`.
     - `test_run_worker_startup_fails_immediately_without_psycopg_driver`: proves `run_worker()` fails immediately when `psycopg` is not importable.
