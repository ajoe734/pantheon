# P0-BFF-004 Acceptance Evidence

Task: Fix `/openapi.json` 500
Owner: Codex2
Reviewer: Codex
Date: 2026-05-15

## Diagnosis

`/openapi.json` returned 200 under default warning handling, but returned 500 when warnings were promoted to errors. FastAPI emitted a duplicate operation-id warning for the two OpenClaw broker adapter readiness aliases:

- `/api/v1/operator/openclaw/broker-adapter-readiness`
- `/api/v1/operator/openclaw/broker/adapter-readiness`

With `PYTHONWARNINGS=error`, that warning became an exception during schema generation and surfaced as an internal server error.

## Fix

- Added distinct explicit `operation_id` values for the two broker adapter readiness route aliases.
- Added a regression test that resets the cached OpenAPI schema and requests `/openapi.json` with `UserWarning` promoted to error.

## Verification

- `PYTHONWARNINGS=error python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_survives_warning_as_error -q` -> `2 passed`
- `python3 -W always -c "import sys; sys.path.insert(0, 'services/control-plane/bff'); import main; main.app.openapi()"` -> passed with no duplicate operation-id warning
- `python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_survives_warning_as_error services/control-plane/bff/test_bff_session_auth_me_contract.py::test_bff_session_lifecycle_routes_are_visible_in_openapi services/control-plane/bff/test_mgmt_syn_006_conflict_log_view.py::test_conflict_log_feature_flag_and_openapi_route_registration -q` -> `5 passed`
- `python3 -m pytest services/control-plane/bff/test_openclaw_ops_surface.py::test_broker_adapter_readiness_projects_fail_closed_live_and_canary services/control-plane/bff/test_openclaw_ops_surface.py::test_broker_adapter_readiness_degrades_when_adapter_unconfigured -q` -> `2 passed`
- `python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py` -> passed
