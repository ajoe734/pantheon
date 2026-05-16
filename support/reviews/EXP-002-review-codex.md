# EXP-002 Review - Codex

Reviewed: 2026-05-16
Reviewer: Codex
Owner: Claude2
Task: EXP-002 - /bff/research-experiments list/detail

## Decision

Approved. No blocking findings for the EXP-002 scope.

## Scope Reviewed

- `services/control-plane/bff/test_exp002_bff_research_experiments_contract.py`
- Existing `/bff/research-experiments` list/detail route wiring in `services/control-plane/bff/main.py`
- Existing research experiment projection and persistence behavior in `services/control-plane/bff/read_store.py`

## Verification

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_exp002_bff_research_experiments_contract.py -q`
  - Result: 17 passed, 1 warning
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_consol_017_detail_smoke_b.py -q`
  - Result: 2 passed
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q`
  - Result: 7 passed, 1 failed
  - Non-blocking for EXP-002: failure is `test_execute_plans_final_stub_auth_smoke_avoids_server_errors` on `/bff/capital-pools/pool_001` returning 503, outside the research-experiments route family.

## Notes

- The EXP-002 contract test covers authenticated list/detail envelopes, seeded completed/running/failed experiment records, terminal `canCancel=false`, unknown-id 404 behavior, and launch-to-BFF list/detail readback with fallback disabled.
- The broader live-wiring regression should be handled by the capital-pools owner if it remains current; it does not indicate a research-experiments regression.
