# BFF API GAP — Delta Audit Spec

Status: active
Date: 2026-05-24
Sprint: Sprint BFF-DELTA / EPIC-BFF-DELTA-INFRA
Task: BFF-B1-001-DELTA
Owner: Claude
Reviewer: Codex

This document records the CORS preflight regression found after BFF-B1-001 closed
and specifies the exact fix applied.

---

## §DELTA-1 CORS Preflight Regression — execute-plans Origin Blocked in Live Mode

### Gap

After BFF-B1-001 added `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com`
to `_DEFAULT_LOVABLE_CORS_ORIGINS`, it was simultaneously added to `_DEV_LOVABLE_CORS_ORIGINS`.

`_DEV_LOVABLE_CORS_ORIGINS` is the set of origins stripped from the allowlist when the BFF
runs in production strict mode (`PANTHEON_ENV=production/live/canary/…` and
`PANTHEON_BFF_AUTH_MODE=strict`). The production strict filter in `_cors_origins_from_env()`
removes any origin found in `_DEV_LOVABLE_CORS_ORIGINS`.

As a result, when the live BFF served an OPTIONS preflight from the execute-plans frontend
(`https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com`), no matching
`Access-Control-Allow-Origin` header was returned, and Starlette's CORSMiddleware responded
with HTTP 400. All subsequent CORS-gated requests from the live execute-plans frontend
therefore failed.

The preview-URL regex (`_LOVABLE_PREVIEW_ORIGIN_REGEX`) is also disabled in strict mode
(`preview_regex = None`), so regex fallback could not compensate.

### Root Cause

`https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` is the **published**
URL for the execute-plans Lovable project, not a dev/preview URL. It was incorrectly
classified as dev-only when added by BFF-B1-001.

### Fix

**File: `services/control-plane/bff/main.py`**

Remove `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` from
`_DEV_LOVABLE_CORS_ORIGINS`. The URL remains in `_DEFAULT_LOVABLE_CORS_ORIGINS` and now
survives the production strict filter unchanged.

Replace the trailing comment to document the intentional omission:

```python
_DEV_LOVABLE_CORS_ORIGINS = {
    "https://preview--pantheon-dev.lovable.app",
    "https://preview--pantheon-ai-system-front-dev.lovable.app",
    "https://pantheon-dev.lovable.app",
    "https://pantheon-ai-system-front-dev.lovable.app",
    # Pantheon Frontend Lovable project preview URLs (dev tier).
    "https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com",
    "https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app",
    # BFF-B1-001-DELTA: 140c41d5 published URL intentionally NOT in dev-only set —
    # it must survive the production-strict filter so live OPTIONS succeeds.
}
```

**File: `services/control-plane/bff/tests/test_auth_jwks_strict.py`**

- Rename `test_execute_plans_lovableproject_filtered_in_strict_mode` →
  `test_execute_plans_lovableproject_survives_production_strict_filter` and flip the
  assertion to `in origins` (was `not in origins` — that test captured the bug).
- Add `test_execute_plans_options_preflight_succeeds_in_production_strict_mode` to assert
  OPTIONS from the live execute-plans origin returns 200 with correct
  `Access-Control-Allow-Origin` in strict mode.

### Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | `_cors_origins_from_env()` includes `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` when `PANTHEON_ENV=production` and `PANTHEON_BFF_AUTH_MODE=strict` | Fixed |
| 2 | OPTIONS preflight from `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` returns HTTP 200 with matching `Access-Control-Allow-Origin` in strict/production mode | Fixed |
| 3 | Dev-only origins (`pantheon-dev.lovable.app`, `b75d3452-...-lovableproject.com`) are still filtered in strict mode | Unchanged |
| 4 | Dynamic preview URLs (`id-preview-<hash>--<uuid>.lovable.app`) are still blocked in strict mode (regex disabled) | Unchanged |
| 5 | `pytest -q services/control-plane/bff/tests/test_auth_jwks_strict.py` exits 0 | Verified |

### Affected Files

- `services/control-plane/bff/main.py` — remove URL from `_DEV_LOVABLE_CORS_ORIGINS`
- `services/control-plane/bff/tests/test_auth_jwks_strict.py` — flip regression test, add OPTIONS preflight test
- `docs/04/pantheon_bff_api_gap_2026-05-24_delta/BFF_API_GAP_delta_audit_spec.md` — this file
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md` — execute-plans-side audit

### Task

BFF-B1-001-DELTA — Owner: Claude, Reviewer: Codex
