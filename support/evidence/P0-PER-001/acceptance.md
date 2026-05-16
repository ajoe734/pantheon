# P0-PER-001 Acceptance Evidence

Task: `/bff/personas` list/detail
Owner: Codex2
Reviewer: Claude

## Scope

Verified the BFF persona registry read surface used by execute-plans strict bootstrap:

- `GET /bff/personas`
- `GET /bff/personas/{persona_id}`

The implementation projects canonical persona registry records into the execute-plans Persona DTO shape with stable `data`, `items`, `page_info`, and read-surface `meta` envelopes for list responses, and a `data` plus `meta` envelope for detail responses.

## Contract Behavior

- Requires BFF read authorization through `_require_read_role`.
- Serves canonical persona registry records through `read_store.list_personas()` and `read_store.get_persona()`.
- Preserves BFF-local persona records created through the compatibility write path.
- List response supports `state`, `archetype`, `page_token`, and bounded `page_size`.
- Detail response returns `OBJECT_NOT_FOUND` for unknown persona ids.
- Persona DTO includes execute-plans compatibility fields: `id`, `name`, `owner`, `updatedAt`, `state`, `risk`, `archetype`, `routedStrategies`, `successRate`, `labelKey`, and `lifecycleStatus`.

## Verification

Commands run from repository root:

```bash
python3 -m pytest services/control-plane/bff/test_bff_strategy_persona_contract.py -q
python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -k 'final_openapi_json_is_route_discoverable' -q
python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -k 'seeded_detail_paths_use_read_model_dtos' -q
```

Results:

- `16 passed in 26.13s`
- `1 passed, 7 deselected in 13.77s`
- `1 passed, 7 deselected, 1 warning in 6.73s`

## Notes

No additional BFF code patch was required for this task in the current worktree; the persona list/detail implementation and contract coverage were already present. This evidence file records the focused P0-PER-001 verification packet for review.
