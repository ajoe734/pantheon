# LOOP-AUTO-BFF-001 Evidence

Task: `LOOP-AUTO-BFF-001`
Owner: Codex
Reviewer: Claude
Date: 2026-06-27

## Delivered Surface

- Added BFF read routes:
  - `GET /bff/v5/loop-health`
  - `GET /bff/v5/loop-health/{loop_id}`
- Added optional controller snapshot input:
  - `PANTHEON_BFF_LOOP_HEALTH_STORE`
  - `loop_health` key in the local BFF snapshot fallback
- Adapter:
  - `services/control-plane/bff/loop_inventory.py`

## Read Model Boundary

The read model composes the static loop catalog with optional controller health
records. It lists every loop with current maturity, target maturity, controller
health, last success, last failure, downstream actual-state status, and an
evidence packet.

The evidence packet distinguishes:

- seed or fixture truth;
- local snapshot fallback truth;
- static registry metadata;
- scheduled tick evidence;
- reconciled or proven-live truth.

Registry-only payloads are intentionally degraded and do not claim live
liveness. This task does not raise any loop maturity to `reconciled` or
`proven-live`.

## Verification

```bash
python3 -m pytest services/control-plane/bff/test_loop_health_read_model_contract.py services/control-plane/bff/test_loop_inventory_read_model_contract.py
```

Result: `8 passed`.

Local route smoke:

```bash
PANTHEON_BFF_AUTH_STUB=true PANTHEON_BFF_AUTH_MODE=permissive python3 -c '<TestClient GET /bff/v5/loop-health and /bff/v5/loop-health/source_ingestion>'
```

Result:

```text
200 12 degraded
200 source_ingestion registry_metadata
```

Route registration guard:

```bash
python3 -m pytest services/control-plane/bff/test_route_resolution_no_shadowing.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable
```

Result: `4 passed`.
