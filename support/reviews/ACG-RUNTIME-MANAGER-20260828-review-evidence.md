# ACG-RUNTIME-MANAGER-20260828 Review Evidence Manifest

Task ID: ACG-RUNTIME-MANAGER-20260828
Program ID: PANTHEON-ARCH-CLEANUP-20260828
Design Unit: ACG-05
Owner: Antigravity
Reviewer: Claude
Date: 2026-08-28

## 1. Summary of Changes

Consolidated Runtime Manager onto the deployed service and importable execution kernel, resolving disposition matrix items ACG-05-001 through ACG-05-006:

1. **Sole Deployed Mutation State Machine (`ACG-05-001`, `ACG-05-003`)**:
   - `RuntimeManagerService` is consolidated in canonical underscore-named package `services.runtime_manager`.
   - `services/runtime-manager/service.py` re-exports `RuntimeManagerService` as the thin deployment bridge for the HTTP container.
   - Deleted duplicate `services/execution/runtime-manager/runtime_manager.py` and redundant `test_runtime_manager_risk_policy.py`.

2. **Importable Kernel & Clean Module Resolution (`ACG-05-002`)**:
   - Moved `runtime_binding.py`, `runtime_binding.schema.json`, `kill_switch_controller.py`, and authority matrices to `services/runtime_manager/`.
   - Replaced all path-based `importlib.util.spec_from_file_location` and `sys.path` hacks with standard Python imports (`from services.runtime_manager import ...`).
   - Added clean re-exports to `services/execution/__init__.py` for backward compatibility.

3. **E2E Test Migration (`ACG-05-004`)**:
   - Migrated `tests/e2e/test_deployment_plan_to_paper_run.py` and `tests/e2e/test_allocation_policy_to_paper_run.py` from the deleted `RuntimeManager` class to `RuntimeManagerService`.
   - Removed all `_load_module_from_path` and `_ensure_runtime_manager_modules` routines.

4. **Paper Fleet Reconciler Worker Packaging (`ACG-05-005`, `ACG-05-006`)**:
   - Moved `paper_fleet_reconciler.py`, its Dockerfile, `requirements.txt`, and `test_paper_fleet_reconciler.py` to dedicated package `services/paper_fleet_reconciler/`.
   - Updated `docker-compose.yml` to build from `services/paper_fleet_reconciler/Dockerfile` and run `services/paper_fleet_reconciler/paper_fleet_reconciler.py` while keeping stable Compose service identity `paper-fleet-reconciler`.

## 2. Verification Results

All unit, integration, and E2E suites pass deterministically:

- `pytest services/runtime_manager/ services/runtime-manager/ services/paper_fleet_reconciler/ tests/e2e/test_deployment_plan_to_paper_run.py tests/e2e/test_allocation_policy_to_paper_run.py services/deployment/test_promote_pipeline.py scripts/test_paper_runtime_topology_contract.py -q`:
  `364 passed, 7 skipped, 5 warnings, 3 subtests passed`
- `pytest services/execution/lean_runtime/test_signal_isolation.py`: `43 passed`
- `python3 services/runtime_manager/smoke_test_runtime_binding.py`: `All checks passed`
- `python3 services/runtime_manager/smoke_test_kill_switch_controller.py`: `All checks passed`

## 3. Modified & Moved Files Inventory

- Created package `services/runtime_manager/`:
  - `__init__.py`, `service.py`, `runtime_binding.py`, `runtime_binding.schema.json`, `kill_switch_controller.py`, `authority_matrix.md`, `contract.md`, `rollback_action_matrix.md`
  - Tests: `test_runtime_binding.py`, `test_kill_switch_controller.py`, `smoke_test_runtime_binding.py`, `smoke_test_kill_switch_controller.py`
- Created package `services/paper_fleet_reconciler/`:
  - `__init__.py`, `paper_fleet_reconciler.py`, `Dockerfile`, `requirements.txt`, `test_paper_fleet_reconciler.py`
- Deleted duplicate execution manager:
  - `services/execution/runtime-manager/runtime_manager.py`
  - `services/execution/runtime-manager/test_runtime_manager_risk_policy.py`
- Updated callers and HTTP integration:
  - `services/runtime-manager/main.py`, `service.py`, `runtime_manager_client.py`, `internal_api_routes.py`, `test_runtime_manager.py`, `test_runtime_hardening.py`, `test_internal_api_routes.py`, `smoke_test.py`
  - `services/control-plane/internal/internal_api.py`
  - `services/deployment/test_promote_pipeline.py`
  - `services/execution/__init__.py`
  - `services/execution/lean_runtime/test_signal_isolation.py`
  - `tests/e2e/test_deployment_plan_to_paper_run.py`
  - `tests/e2e/test_allocation_policy_to_paper_run.py`
  - `docker-compose.yml`, `docker-compose.exec.yml`
