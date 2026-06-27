# LOOP-AUTO-001 Evidence

Task: `LOOP-AUTO-001`

## Delivered Surface

- Added BFF read routes:
  - `GET /bff/v5/loop-inventory`
  - `GET /bff/v5/loop-inventory/{loop_id}`
- Source registry:
  - `docs/deployment/loop-catalog.registry.json`
- Adapter:
  - `services/control-plane/bff/loop_inventory.py`

## Read Model Boundary

The read model projects the static loop catalog into an operator-facing payload
with `current_maturity`, `target_maturity`, `owner`, `evidence`,
`evidence_statuses`, `truth_source`, and `live_status`.

It does not raise any loop to `reconciled` or `proven-live`. A loop is only
marked `live_status.is_live=true` when all of these are true in the catalog:

- `maturity.current == "proven-live"`
- `controller_contract.status == "proven_live"`
- `evidence_profile.proven_live_evidence.status == "present"`

The current registry has no loop that satisfies those conditions.

## Verification

```bash
pytest -q tests/test_loop_catalog_registry.py services/control-plane/bff/test_loop_inventory_read_model_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable
```

Result: `13 passed`.
