# BFF-B1-001-DELTA Owner Closeout Evidence

Task: BFF-B1-001-DELTA
Title: CORS preflight regression — live OPTIONS still 400 despite B1-001 done
Owner: Claude
Reviewer: Codex
Date: 2026-05-24

## Deliverable

Fixed CORS preflight regression where `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com`
was incorrectly placed in `_DEV_LOVABLE_CORS_ORIGINS` (stripped in strict mode) by BFF-B1-001.
The URL was removed from `_DEV_LOVABLE_CORS_ORIGINS` so it survives the production strict filter.

## Commits and PRs

- Implementation fix: `73a365fb` (`BFF-B1-001-DELTA: fix CORS preflight blocked in live mode`)
- Audit spec finalization: `47101b85` (`BFF-B1-001-DELTA: record closeout finalization in audit spec`)
- Merged via PR #511 into `dev`
- Reviewer approval: Codex (2026-05-24)

## Acceptance Status

| # | Criterion | Status |
|---|---|---|
| 1 | `_cors_origins_from_env()` includes execute-plans origin in strict/production mode | Fixed |
| 2 | OPTIONS preflight succeeds with HTTP 204 in strict/production mode | Fixed |
| 3 | Dev-only origins still filtered in strict mode | Unchanged |
| 4 | Dynamic preview URLs still blocked in strict mode | Unchanged |
| 5 | pytest -q services/control-plane/bff/tests/test_auth_jwks_strict.py exits 0 | Verified |

## Verification

```
pytest -q services/control-plane/bff/tests/test_auth_jwks_strict.py → 18 passed
pytest -q services/control-plane/bff/tests/test_auth_jwks_strict.py -k cors → 6 passed
```

## Artifacts

- `docs/04/pantheon_bff_api_gap_2026-05-24_delta/BFF_API_GAP_delta_audit_spec.md` (DELTA-1 section)
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-24-delta.md`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_auth_jwks_strict.py`

## Closeout Note

Task was preempted before final `done` transition. Fix was already merged via PR #511.
This closeout commit records the owner evidence packet and brings the task branch up
to date with origin/dev before the final done transition.
