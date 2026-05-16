# P0-CAP-001 Capital Pools Read Evidence

Task: P0-CAP-001 - `/bff/capital-pools` list/detail
Owner: Codex
Reviewer: Claude2
Date: 2026-05-15 UTC

## Scope

- `GET /bff/capital-pools` returns the strict Management list envelope with both `data` and `items` arrays plus `page_info`.
- Canonical capital-pool projection now preserves `pool_id` alongside `id` and includes canonical display fields used by management screens: owner, status, risk policy, budget, currency, timestamps, description, metadata, and single-runtime policy.
- `GET /bff/capital-pools/{pool_id}` now fails closed with `DOWNSTREAM_UNAVAILABLE` when the capital-pool read source is not verifiable instead of returning a misleading 404.
- Capital-pool detail includes persona binding read-surface state in `meta.surfaces.persona_bindings`, so an unavailable binding source is distinguishable from a real empty binding list.

## Touched Contracts

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py`

## Verification

```bash
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/read_store.py services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py
```

Result: passed.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py -q
```

Result: 27 passed.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_seeded_detail_paths_use_read_model_dtos -q
```

Result: 1 passed.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_live_probe_catalog_no_longer_404s_anonymously -q
```

Result: 3 passed.

Known warning: focused pytest runs still emit the existing `datetime.utcnow()` deprecation warning from `read_store.py`; it is unrelated to this task.
