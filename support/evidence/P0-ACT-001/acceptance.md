# Acceptance Evidence: P0-ACT-001

Task: canonical action endpoint /bff/actions/{type}/{id}/{action}
Owner: Claude2 (finalized after Codex quota failure + reassignment)
Reviewer: Claude
Review outcome: approved (2026-05-15, support/reviews/P0-ACT-001-review-claude.md)

## Verification Commands

```bash
python3 -m py_compile services/control-plane/bff/main.py
# => OK

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/control-plane/bff/tests/test_actions_to_commands_adapter.py \
  services/control-plane/bff/tests/test_command_replay_conflict.py -q
# => 11 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_survives_warning_as_error -v
# => 3 passed
```

Total: 14 task-relevant tests pass.

## Delivery Summary

- `POST /bff/actions/{type}/{id}/{action}` registered in OpenAPI with `type`, `id`, `action` path params
- Legacy `POST /bff/actions/{entityType}/{entityId}/{actionId}` alias retained with `include_in_schema=False`
- Both routes share `_submit_canonical_action_command` → `_submit_final_command_admission`
- `admission_route=POST /bff/v1/commands`, backward-compatible `source_route` preserved
- Deprecation HTTP headers and response markers complete (`Deprecation`, `Sunset`, `Link`, `Warning`, `X-Pantheon-Deprecated-Route`, `data.deprecated`, `data.deprecation.replacement`, `meta.deprecated`)
- `BFF_COMMAND_API_CONTRACT.md` §3 route table updated to reflect canonical vs alias distinction

## Commit Note

main.py action adapter hunk is in the dirty worktree alongside sibling P0-BFF-002/003/CAP-001/AUD-001 hunks.
Non-interactive staging (background worker rule) prevents isolating only the P0-ACT-001 hunk.
The cleanly-owned files (BFF_COMMAND_API_CONTRACT.md, tests/test_actions_to_commands_adapter.py, review file,
evidence) are committed in the task-scoped commit. The main.py change is already reviewed and durable.
