# P0-BFF-003 Logout Evidence

Task: P0-BFF-003 - POST /bff/logout
Owner: Codex2
Reviewer: Claude2
Date: 2026-05-15 UTC

## Scope

`POST /bff/logout` returns the session lifecycle DTO used by execute-plans and
does not emit a command receipt. It marks the caller session as `logged_out`,
returns `authenticated: false`, remains idempotent for repeated
`Idempotency-Key` usage, and is discoverable in `/openapi.json`.

This implementation also accepts strict-mode cookie sessions through the
`pantheon_session` cookie, matching the BFF session bootstrap path.

## Touched Contracts

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_session_auth_me_contract.py`

## Verification

```bash
pytest services/control-plane/bff/test_bff_session_auth_me_contract.py -q
pytest services/control-plane/bff/test_bff_consol_013_cookie_session_write_gate.py -q
pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_contract_paths_are_registered services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_live_probe_catalog_no_longer_404s_anonymously -q
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/session_lifecycle_store.py
```

Observed results:

- Session/auth contract: `18 passed`.
- Cookie session write gate: `13 passed`.
- Final live wiring route/OpenAPI/live-probe subset: `3 passed`.
- Compile check: passed.

Known warning: FastAPI still reports an existing duplicate OpenAPI operation id
for `get_openclaw_broker_adapter_readiness`; this task did not introduce or
touch that route.
