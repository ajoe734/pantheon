# INC-001-RB Evidence

Task: `/bff/incidents (IncidentCase) (rebaseline)`
Owner: Codex
Reviewer: Claude2

## Scope

Implemented the `/bff/incidents` rebaseline contract as a live IncidentCase read surface:

- list responses now expose both `data` and `items`, `page_info.total`, surface metadata, and degradation metadata
- list/detail records preserve canonical IncidentCase evidence fields: `binding_id`, `deployment_stage`, `deployment_plan_id`, `capital_pool_id`, `persona_capital_binding_id`, `artifact_id`, `artifact_version`, `runtime_id`, and `trace_id`
- detail/action reads fail closed with `DOWNSTREAM_UNAVAILABLE` when the incident backend is missing instead of returning a misleading not-found response
- create overlay preserves canonical IncidentCase evidence fields when supplied and remains idempotent
- `affected_pool_id` filtering accepts both `capital_pool_id` and `affected_pool_id`

## Touched Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_inc001_rebaseline_incidents_contract.py`
- `services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py`

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q services/control-plane/bff/test_inc001_rebaseline_incidents_contract.py
PYTHONDONTWRITEBYTECODE=1 pytest -q services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py
PYTHONDONTWRITEBYTECODE=1 pytest -q services/control-plane/bff/test_pkt012_alerts_rail_contract.py services/control-plane/bff/test_bff_runtimes_contract.py
PYTHONDONTWRITEBYTECODE=1 pytest -q services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -k 'seeded_detail_paths or mounted_backend_routes'
PYTHONDONTWRITEBYTECODE=1 pytest -q services/control-plane/bff/test_bff_consol_010_fixture_pack_c.py -k 'live_smoke_routes or live_detail_routes'
```

Results:

- `3 passed`
- `4 passed`
- `6 passed`
- `1 passed, 7 deselected, 1 warning`
- `2 passed, 4 deselected`
