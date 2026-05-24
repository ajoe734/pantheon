# BFF API GAP - Delta Audit Spec

Status: active
Date: 2026-05-24
Sprint: Sprint BFF-DELTA

This document records BFF deltas found after the 2026-05-23 final integration
spec. It is an execution record, not a new L1 product authority.

---

## DELTA-1 CORS Preflight Regression - execute-plans Origin Blocked in Live Mode

Task: BFF-B1-001-DELTA
Owner: Claude
Reviewer: Codex

### Gap

After BFF-B1-001 added `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com`
to `_DEFAULT_LOVABLE_CORS_ORIGINS`, it was simultaneously added to
`_DEV_LOVABLE_CORS_ORIGINS`.

`_DEV_LOVABLE_CORS_ORIGINS` is the set of origins stripped from the allowlist
when the BFF runs in production strict mode (`PANTHEON_ENV=production/live/canary`
and `PANTHEON_BFF_AUTH_MODE=strict`). The production strict filter in
`_cors_origins_from_env()` removes any origin found in `_DEV_LOVABLE_CORS_ORIGINS`.

As a result, when the live BFF served an OPTIONS preflight from the execute-plans
frontend (`https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com`), no
matching `Access-Control-Allow-Origin` header was returned, and Starlette's
CORSMiddleware responded with HTTP 400. All subsequent CORS-gated requests from
the live execute-plans frontend therefore failed.

The preview-URL regex (`_LOVABLE_PREVIEW_ORIGIN_REGEX`) is disabled in strict
mode (`preview_regex = None`), so regex fallback could not compensate.

### Root Cause

`https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` is the
published URL for the execute-plans Lovable project, not a dev/preview URL. It
was incorrectly classified as dev-only when added by BFF-B1-001.

### Fix

**File: `services/control-plane/bff/main.py`**

Remove `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` from
`_DEV_LOVABLE_CORS_ORIGINS`. The URL remains in `_DEFAULT_LOVABLE_CORS_ORIGINS`
and now survives the production strict filter unchanged.

BFF-PM12-DELTA-002 also standardizes successful CORS preflight responses to
HTTP 204 through the BFF CORS middleware. Current regression tests therefore
assert successful preflight as HTTP 204 with the matching
`Access-Control-Allow-Origin` header.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | `_cors_origins_from_env()` includes `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` when `PANTHEON_ENV=production` and `PANTHEON_BFF_AUTH_MODE=strict` | Fixed |
| 2 | OPTIONS preflight from `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` succeeds with HTTP 204 and matching `Access-Control-Allow-Origin` in strict/production mode | Fixed |
| 3 | Dev-only origins (`pantheon-dev.lovable.app`, `b75d3452-...-lovableproject.com`) are still filtered in strict mode | Unchanged |
| 4 | Dynamic preview URLs (`id-preview-<hash>--<uuid>.lovable.app`) are still blocked in strict mode (regex disabled) | Unchanged |
| 5 | `pytest -q services/control-plane/bff/tests/test_auth_jwks_strict.py` exits 0 | Verified |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_auth_jwks_strict.py`
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md`

---

## DELTA-2 PM-12 Persona Performance Attribution Route

Task: BFF-PM12-DELTA-002
Owner: Codex2
Reviewer: Claude2

### Scope

Add a dedicated persona-grouped attribution route for execute-plans strict live
rendering:

```text
GET /bff/management/performance-attribution/by-persona?period=&page_token=&page_size=
```

The route reuses the PM-12 performance attribution composition logic and fixes
the attribution dimension to `persona`. It does not introduce a new performance
source of truth and does not change the generic
`GET /bff/management/performance-attribution` route.

### Contract

The response uses the canonical aggregate envelope:

```json
{
  "data": {
    "id": "pm12-performance-attribution-by-persona",
    "period": "latest",
    "dimensions": ["persona"],
    "items": [],
    "rows": [],
    "summary": {}
  },
  "items": [],
  "rows": [],
  "summary": {},
  "page_info": { "next_page_token": null, "total": 0, "page_size": 50 },
  "meta": {
    "snapshot_at": "...",
    "surfaces": {},
    "composition_sources": [],
    "policy": "read_only_performance_attribution"
  }
}
```

Rows keep the existing attribution row schema: `dimension`,
`dimensionKey` / `dimension_key`, `label`, `rank`, `period`, nested `metrics`,
top-level contribution fields, `sourceRefs` / `source_refs`, and persona
drilldown links when available.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | Implemented |
| 2 | Attribution grouped by persona dimension | Implemented |
| 3 | Accepts `period`, `page_token`, and `page_size` query parameters | Implemented |
| 4 | Anonymous request returns HTTP 401 | Implemented |
| 5 | Authenticated request returns HTTP 200 | Implemented |
| 6 | Response keeps canonical aggregate envelope | Implemented |
| 7 | CORS preflight returns HTTP 204 | Implemented |
| 8 | Focused pytest case covers `attribution_by_persona` | Implemented |
| 9 | execute-plans exposes typed path and fetch helpers | Implemented |

### Affected Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`
- `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py`
- `services/control-plane/bff/tests/test_auth_jwks_strict.py`
- `execute-plans/src/lib/bff-v1/paths.ts`
- `execute-plans/src/lib/bff-v1/management.ts`
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md`

### Validation

```bash
pytest -q services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result: 43 passed, 3 existing `datetime.utcnow()` deprecation warnings in
`services/control-plane/bff/read_store.py`.
