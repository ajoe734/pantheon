# BFF-B1-002 Closeout Evidence

Task: BFF-B1-002 - Fix /openapi.json 500; make Swagger/OpenAPI readable
Owner: Codex
Reviewer: Claude
Phase: Sprint BFF-1 / EPIC-BFF-GAP-P0

## Delivery

Implementation PR #411 merged to `dev` on 2026-05-23T05:30:29Z.
Implementation merge commit: `aedf9dac4611db40d98c8d0d7d7a17176ed736d0`
Implementation commit: `f25176a5dbc9e95b92080e020f170046f5774564`

Review artifact PR #414 merged to `dev` on 2026-05-23T05:51:58Z.
Review artifact merge commit: `0b12a3b9ccaade3601b1ac21cffed54d4c143e11`
Review artifact commit: `de07650ac9d4254369bc9765cddae8d1ab5ff437`

## Files Changed

- `services/control-plane/bff/main.py` - exposes both deprecated `/bff/actions/*`
  route templates in OpenAPI with distinct operation IDs and configures
  `expose_headers` for browser-readable BFF response headers.
- `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md` - documents the
  deprecated named and generic action adapter templates.
- `services/control-plane/bff/tests/test_actions_to_commands_adapter.py` -
  covers OpenAPI path visibility, operation IDs, deprecated flags, and adapter
  routing semantics.
- `services/control-plane/bff/tests/test_auth_jwks_strict.py` - covers CORS
  exposed response headers.
- `.orchestrator/reviews/BFF-B1-002-review-claude.md` - records reviewer approval.
- `.orchestrator/task-briefs/bff_b1_002.md` - records generated task context.

## Reviewer Approval

Claude approved: OpenAPI action surface correct, CORS `expose_headers` added,
BFF-B1-001 CORS regex intact after merge, 21 tests passed, and OpenAPI
discoverability verified.

## Verification

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_actions_to_commands_adapter.py services/control-plane/bff/tests/test_auth_jwks_strict.py -q`
  - Result: 21 passed.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONWARNINGS=error python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py::test_execute_plans_final_openapi_json_is_route_discoverable -q`
  - Result: 1 passed.
