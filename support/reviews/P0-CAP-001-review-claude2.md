# P0-CAP-001 Review: /bff/capital-pools list/detail

Reviewer: Claude2
Owner: Codex
Date: 2026-05-15 UTC
Status: **approved**

## Scope Reviewed

- `services/control-plane/bff/main.py` — BFF capital-pools list and detail route hunks
- `services/control-plane/bff/read_store.py` — `_project_canonical_capital_pool`, `list_capital_pools`, `get_capital_pool`, `get_bindings_for_pool`
- `services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py` — capital pools tests
- `support/evidence/P0-CAP-001/README.md` — evidence file

## Verification

```bash
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/read_store.py services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py
```
Result: OK

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py -q
```
Result: 27 passed, 10 warnings (pre-existing utcnow deprecation)

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_seeded_detail_paths_use_read_model_dtos \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_live_probe_catalog_no_longer_404s_anonymously -q
```
Result: 4 passed, 1 warning

## Findings

### Strict data/items/page_info envelope (PASS)

`GET /bff/capital-pools` returns `{"data": [...], "items": [...], "page_info": {"next_page_token": ..., "total": ...}, "meta": {...}}`.
`data == items` verified in `test_bff_capital_pools_list_returns_strict_items_envelope` (line 88).

### pool_id and canonical display fields preserved (PASS)

`_project_canonical_capital_pool` in read_store.py (line 8028) maps `pool_id`/`id` as aliases and preserves:
`owner_id`, `owner_type`, `single_runtime_enforced`, `risk_policy_ref`, `currency`, `budget` (with `capital_allocation` fallback), `description`, `metadata`, `created_at`, `updated_at`.
Contract test asserts both `id` and `pool_id` present with correct values (lines 90-92).

### Fail-closed DOWNSTREAM_UNAVAILABLE on unverifiable pool source (PASS)

`bff_get_capital_pool` calls `_raise_if_read_surface_unavailable(pool_surface, label="Capital pool")` before the OBJECT_NOT_FOUND 404.
When source is unavailable (monkeypatched env), endpoint returns 503 with `error.code == "DOWNSTREAM_UNAVAILABLE"`.
Tested in `test_bff_capital_pool_detail_503_when_pool_source_unavailable` (lines 252-273).

### persona_bindings surface degradation in meta (PASS)

Detail response sets `meta.surfaces.persona_bindings` and `meta.degradation.persona_bindings_reason` when binding source is unavailable.
Tested in `test_bff_capital_pool_detail_reports_binding_surface_unavailable` (lines 216-249):
- `surfaces["persona_bindings"]["status"] == "unavailable"`
- `meta["degradation"]["persona_bindings_reason"] == "persona bindings are currently unavailable."`

## Conclusion

Implementation is complete, correct, and well-tested. No changes required. Approved for finalization.
