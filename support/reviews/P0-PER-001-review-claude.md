# Review: P0-PER-001 — /bff/personas list/detail

Reviewer: Claude
Owner: Codex2
Date: 2026-05-16

## Verdict: Approved

The `/bff/personas` list/detail implementation is correct and fully verified.

## Verification

Commands re-run by reviewer from repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_strategy_persona_contract.py -q
# 16 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -k 'final_openapi_json_is_route_discoverable' -q
# 1 passed, 7 deselected

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -k 'seeded_detail_paths_use_read_model_dtos' -q
# 1 passed, 7 deselected, 1 warning
```

The 1 warning is the pre-existing `datetime.utcnow()` DeprecationWarning in `read_store.py:73` — present across all tasks, not introduced here.

## Implementation Checks

- `GET /bff/personas`: returns correct `data/items/page_info/meta` envelope; pagination via `state`, `archetype`, `page_token`, `page_size` params ✓
- `GET /bff/personas/{persona_id}`: returns `data/meta` envelope; `OBJECT_NOT_FOUND` 404 for unknown IDs ✓
- `_project_persona_dto`: projects all required execute-plans compatibility fields: `id`, `name`, `owner`, `updatedAt`, `state`, `risk`, `archetype`, `routedStrategies`, `successRate`, `labelKey`, `lifecycleStatus` ✓
- Read auth via `_require_read_role` ✓
- BFF-local overlay merge via `_PERSONA_BFF_OVERLAY` and `_list_persona_records()` ✓
- `_routed_strategies_for_persona` correctly counts strategy_specs per persona ✓
- Route priority: specific handlers at lines 18131/18232 registered before generic stubs at 24587/24634; FastAPI routes first match wins ✓
- Seeded detail matrix test: list then detail round-trip returns real DTO (not generic stub or degraded) ✓

## Notes

No additional code patch was needed — the implementation was already present in the worktree. Evidence file `support/evidence/P0-PER-001/acceptance.md` accurately describes the scope and verified commands.
