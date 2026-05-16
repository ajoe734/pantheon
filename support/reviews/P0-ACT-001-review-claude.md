# Review: P0-ACT-001 — canonical action endpoint /bff/actions/{type}/{id}/{action}

Reviewer: Claude
Owner: Codex
Date: 2026-05-15
Outcome: **approved**

---

## Verification Commands Run

```bash
python3 -m py_compile services/control-plane/bff/main.py
# => OK

python3 -m pytest services/control-plane/bff/tests/test_actions_to_commands_adapter.py \
  services/control-plane/bff/tests/test_command_replay_conflict.py -v
# => 11 passed

python3 -m pytest \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_survives_warning_as_error -v
# => 3 passed
```

Total: 14 task-relevant tests pass. The one failure in the broader suite
(`test_execute_plans_final_stub_auth_smoke_avoids_server_errors` / `/bff/capital-pools/pool_001`
returning 503) is a pre-existing capital-pool service-availability issue with no connection
to the action adapter.

---

## Checklist

| Item | Result |
|---|---|
| `POST /bff/actions/{type}/{id}/{action}` registered in OpenAPI with `type`, `id`, `action` path params | ✅ |
| Legacy `POST /bff/actions/{entityType}/{entityId}/{actionId}` schema-hidden (`include_in_schema=False`) | ✅ |
| Both routes share `_submit_canonical_action_command` → `_submit_final_command_admission` | ✅ |
| `admission_route=POST /bff/v1/commands` in foundation context | ✅ |
| `source_route=POST /bff/actions/{entityType}/{entityId}/{actionId}` for backward-compatible audit | ✅ |
| Deprecation HTTP headers: `Deprecation`, `Sunset`, `Link`, `Warning`, `X-Pantheon-Deprecated-Route` | ✅ |
| Response markers: `data.deprecated`, `data.deprecation.replacement`, `data.receipt.deprecated`, `meta.deprecated` | ✅ |
| Missing idempotency key → 400 `INVALID_PARAMS` / `precondition_failed=idempotency_key` | ✅ |
| Policy denial → 403 with `policy_decision.decision=deny` + audit evidence | ✅ |
| `BFF_COMMAND_API_CONTRACT.md` §3 route table updated to reflect canonical vs alias distinction | ✅ |
| py_compile clean | ✅ |

---

## Notes

Implementation is correct and minimal. Both endpoint functions are thin wrappers
that forward to the shared `_submit_canonical_action_command` helper, which keeps
the admission logic in one place. The contract doc accurately describes the
dual-write semantics, deprecation timeline (until 2026-06-15), and header requirements.
No changes needed.
