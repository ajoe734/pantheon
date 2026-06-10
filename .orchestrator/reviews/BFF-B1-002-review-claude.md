# Review: BFF-B1-002 — Fix /openapi.json 500; make Swagger/OpenAPI readable

Reviewer: Claude  
Date: 2026-05-23  
Task: BFF-B1-002  
Owner: Codex  
Verdict: **APPROVED**

---

## Scope Reviewed

PR #411 merged at `aedf9dac`. Task commit `f25176a5`.

Changed files:
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md`
- `services/control-plane/bff/tests/test_actions_to_commands_adapter.py`
- `services/control-plane/bff/tests/test_auth_jwks_strict.py`
- `docker-compose.yml` (merged state)
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md` (preserved via merge)

---

## Findings

### OpenAPI Action Surface (primary task goal)

- `/bff/actions/{entityType}/{entityId}/{actionId}` changed from `include_in_schema=False` to schema-visible with `operation_id="submit_bff_action_named"`. ✅
- `/bff/actions/{type}/{id}/{action}` given explicit `operation_id="submit_bff_action_generic"`. ✅
- Both routes marked `deprecated=True` — correct per the BFF_COMMAND_API_CONTRACT.md sunset date (2026-06-15). ✅
- Unique operationIds resolve the duplicate-operationId conflict that caused the `/openapi.json` 500. ✅
- Test `test_bff_actions_openapi_exposes_frontend_and_generic_action_templates` asserts both paths, both operationIds, and deprecated flags. ✅

### CORS expose_headers

- `_CORS_EXPOSE_HEADERS` constant added: `["X-BFF-Api-Version", "X-Correlation-Id", "X-Request-Id"]`. ✅
- `expose_headers=_CORS_EXPOSE_HEADERS` passed to `CORSMiddleware`. ✅
- Test verifies `exposed == set(bff_main._CORS_EXPOSE_HEADERS)` in `test_auth_jwks_strict.py`. ✅

### BFF-B1-001 CORS Changes — Merge Integrity

The task commit `f25176a5` was authored before BFF-B1-001 (`7ccf1661`) merged. The merge commit `22e8dccc` correctly combined both:
- `_LOVABLE_PREVIEW_UUIDS`, `_LOVABLE_PREVIEW_ORIGIN_REGEX`, `_LOVABLE_PREVIEW_ORIGIN_PATTERN` preserved. ✅
- `140c41d5` UUID in `_DEFAULT_LOVABLE_CORS_ORIGINS` and `_DEV_LOVABLE_CORS_ORIGINS`. ✅
- `allow_origin_regex` passed to CORSMiddleware in non-strict mode. ✅
- `docker-compose.yml` CORS default includes `140c41d5.lovableproject.com`. ✅

### BFF_COMMAND_API_CONTRACT.md

- Section updated to document both action template routes as deprecated adapters. ✅
- Section §8 Command Adapter Mapping updated to reference both route templates. ✅

### Verification Evidence

```
pytest services/control-plane/bff/tests/test_actions_to_commands_adapter.py \
       services/control-plane/bff/tests/test_auth_jwks_strict.py -q
# 21 passed

PYTHONWARNINGS=error python3 -c "import main; ... OpenAPI discoverability: PASS"
```

21 tests passed; OpenAPI discoverability verified with warnings-as-errors.

---

## Approval

All acceptance criteria satisfied:
- `GET /openapi.json` returns valid OpenAPI 3 JSON without 500.
- Schema includes both `/bff/actions/*` routes with unique operationIds.
- CORS expose_headers configured.
- BFF-B1-001 CORS regex intact after merge.

Task returned to Codex for final closeout (`done`).
