# Review: BFF-B1-001-DELTA-2
Reviewer: Claude
Date: 2026-05-25
Status: approved

## Summary

CORS preflight fix for Lovable `id-preview` origin in strict mode and hex-only regex requirement.

## Acceptance Criteria Verification

| # | Criterion | Status |
|---|---|---|
| 1 | Static `id-preview--b75d...lovable.app` survives production-strict filtering | ✅ Verified in code: origin in `_DEFAULT_LOVABLE_CORS_ORIGINS`, not in `_DEV_LOVABLE_CORS_ORIGINS` |
| 2 | OPTIONS preflight from static `id-preview--b75d...` returns 204 with echoed ACAO | ✅ Verified by `test_static_id_preview_survives_production_strict_filter` + live curl evidence |
| 3 | Dynamic regex accepts `id-preview-<hex>--140c41d5...lovable.app` in non-prod-strict mode | ✅ Verified by `test_preview_regex_allows_known_uuid_with_optional_commit_hash` |
| 4 | Dynamic regex accepts `id-preview--140c41d5...lovable.app` in non-prod-strict mode | ✅ Same test covers no-hash form |
| 5 | Dynamic regex rejects non-hex deploy prefixes (e.g. `id-preview-main--<uuid>`) | ✅ Verified by `test_preview_regex_rejects_non_hex_commit_prefix` |

## Code Review

- `_DEFAULT_LOVABLE_CORS_ORIGINS` correctly includes `https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app`.
- `_DEV_LOVABLE_CORS_ORIGINS` correctly excludes the static id-preview URL, so it survives the production-strict filter.
- `_LOVABLE_PREVIEW_ORIGIN_REGEX` uses `(?:-[a-f0-9]+)?` — the commit segment is correctly made optional with hex-only enforcement.
- `_cors_origins_from_env()` correctly filters dev-only origins in production-strict mode.
- Deploy script fix (PR #569) correctly appends mandatory Lovable dev origins to the env override.

## Test Coverage

20 tests passed. Relevant new tests:
- `test_static_id_preview_survives_production_strict_filter`
- `test_execute_plans_lovableproject_survives_production_strict_filter`
- `test_preview_regex_allows_known_uuid_with_optional_commit_hash`
- `test_preview_regex_allows_old_project_uuid_with_commit_hash`
- `test_preview_regex_rejects_unknown_uuid`
- `test_preview_regex_rejects_non_hex_commit_prefix`
- `test_preview_regex_blocked_in_production_strict_mode`
- `test_cors_origin_allowed_includes_preview_regex`

## Live Evidence

nonprod-deploy run 26383877729 deployed PR #569 merge commit. Final OPTIONS `/bff/me` results:

| Origin | Status | ACAO |
|---|---:|---|
| `https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app` | 204 | exact origin |
| `https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com` | 204 | exact origin |
| `https://pantheon-dev.lovable.app` | 204 | exact origin |
| `https://140c41d5-9cd8-4d6b-ba02-66d5941d0dbe.lovableproject.com` | 204 | exact origin |

## Caveats

- ACEH is emitted on actual CORS responses, not OPTIONS preflight (Starlette behavior, not a regression).
- The `POST /bff/approvals/batch-decide` with reviewer auth returns 403/207 per existing RBAC, not 200 — but this is pre-existing RBAC behavior, not in scope for this task.

## Decision

**APPROVED.** Implementation is correct, complete, well-tested, and live-verified. Task returned to Codex (owner) for closeout.
