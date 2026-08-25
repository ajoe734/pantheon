# OPS-DEV-POSTGRES-SHM-20260825 Evidence

## Defect & Root Cause

During dev deployment and telemetry pruning (`prune_dev_management_ai_telemetry_for_disk` in `scripts/deploy_nonprod_vm.sh`), PostgreSQL runs `VACUUM;` after table truncation. PostgreSQL parallel workers and dynamic shared memory (DSM/DSA) allocate memory in the container's `/dev/shm`.
Because Docker containers default to a 64MB `/dev/shm` unless explicitly specified, VACUUM operations attempting to allocate shared memory segments (e.g. 67,041,184 bytes ~66.3MB) failed with:
```
ERROR: could not resize shared memory segment "/PostgreSQL.XXXXXXXX" to 67041184 bytes: No space left on device
```
This occurred despite the host VM having ample disk space and `/dev/shm` capacity.

## Remediation

1. **Explicit PostgreSQL Shared Memory Floor (256MB)**:
   - Configured `shm_size: 256m` (268,435,456 bytes) on the `postgres` service in `docker-compose.yml`.
   - This provides >3.8x the incident failure threshold (66.3MB) and allows parallel VACUUM, VACUUM FULL, and VACUUM ANALYZE to complete without shared memory exhaustion.

2. **Strict Guardrail & Regression Suite (`scripts/test_deploy_nonprod_vm.py`)**:
   - `test_docker_compose_postgres_shm_size_floor_in_source`: verifies source `docker-compose.yml` sets postgres `shm_size >= 256m`.
   - `test_docker_compose_config_rendered_postgres_shm_size`: renders `docker compose config` and parses rendered postgres `shm_size`.
   - `test_regression_fails_if_postgres_shm_size_omitted_or_below_floor`: parameterized negative tests verifying rejection of missing, 0m, 64m, 128m, and 66.3MB incident failure points.
   - `test_deploy_nonprod_vm_script_syntax_and_vacuum_presence`: verifies syntax and presence of `VACUUM;` in telemetry prune.
   - `test_source_ingestion_remains_reconcile_only_manual`: verifies Source Ingestion defaults to `reconcile_only` with `max_ticks=0`.
   - `test_deploy_nonprod_vm_dry_run_execution`: verifies `deploy_nonprod_vm.sh --dry-run` executes cleanly.
   - `test_postgres_live_container_shm_size`: inspects live container `HostConfig.ShmSize >= 256MB`.
   - `test_postgres_db_behavior_vacuum_succeeds_without_enospc`: executes live `VACUUM;`, `VACUUM ANALYZE;`, and bounded table `VACUUM FULL;` under explicit opt-in (`PANTHEON_VERIFY_LIVE_POSTGRES_VACUUM=1`) and bounded timeouts (`lock_timeout = 5s`, `statement_timeout = 15s`), confirming 0 errors without blocking concurrent sessions.

3. **Topology Contract Integration (`tests/integration/test_product_functional_compose_contract.py`)**:
   - Added `test_postgres_container_shared_memory_floor_is_at_least_256m` to the canonical integration suite.

4. **CI Stage-0 Baseline Hook (`.github/pantheon-stage0-matrix.json`)**:
   - Added `scripts/test_deploy_nonprod_vm.py` to `baseline.commands` and `global_paths`, ensuring CI executes this test suite on every PR and branch run.

5. **Strict Operation Constraints Preserved**:
   - Source Ingestion remains in `reconcile_only` mode with `max_ticks=0` (no continuous background pull).
   - Writes remain fail-closed with `PANTHEON_LIVE_BROKER_ENABLED=false` and `PANTHEON_CANARY_EXECUTION_ENABLED=false`.

## Verification

- `bash -n scripts/deploy_nonprod_vm.sh` (passed)
- `docker compose config --quiet` (passed)
- `.venv-pantheon/bin/python3 -m pytest -v scripts/test_deploy_nonprod_vm.py` (14 passed, 1 skipped)
- `PANTHEON_VERIFY_LIVE_POSTGRES_VACUUM=1 .venv-pantheon/bin/python3 -m pytest -v scripts/test_deploy_nonprod_vm.py` (15 passed)
- `.venv-pantheon/bin/python3 -m pytest -v tests/integration/test_product_functional_compose_contract.py` (6 passed)
- `python3 scripts/ci_stage0.py validate` (passed)
- Live container inspection `docker inspect pantheon-postgres-1 --format '{{.HostConfig.ShmSize}}'` = `268435456` (256MB)
- Live database execution `VACUUM;`, `VACUUM ANALYZE;`, and bounded table `VACUUM FULL;` completed with code 0 and 0 errors (durable evidence captured in `docs/deployment/evidence/product-functional-closure/OPS-DEV-POSTGRES-SHM-20260825/vacuum_output.txt`).
