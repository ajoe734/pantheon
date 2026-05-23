# Review: BFF-B1-004 - PATCH /bff/me/locale

Reviewer: Codex
Date: 2026-05-23
Task: BFF-B1-004
Commit: 15706ecd3a069f785c61519df541d282bfd87322 (PR #418 merged)
Merge commit: 3d6f171e089520a355552c00a4835be425a12e7d

## Verdict: Approved

## Scope Reviewed

- `services/control-plane/bff/main.py` - existing `PATCH /bff/me/locale` handler, locale normalization helpers, and session response composition.
- `services/control-plane/bff/tests/test_bff_me_locale.py` - 6 focused regression tests added by this task.
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md` - section 16 acceptance record.

## Acceptance Criteria Check

| # | Criterion | Result |
|---|---|---|
| 1 | Authenticated `PATCH /bff/me/locale` with a valid BCP-47-ish tag returns HTTP 200 with `data.locale.resolved` equal to the submitted tag | Pass |
| 2 | Response `data.locale.source` is `session` | Pass |
| 3 | Response `data.operation.type` is `update_locale` | Pass |
| 4 | Locale tag is normalized, for example `ZH-tw` to `zh-TW` | Pass |
| 5 | A subsequent `GET /bff/me` from the same operator session reflects the persisted locale | Pass |
| 6 | Anonymous `PATCH /bff/me/locale` returns HTTP 401 | Pass |
| 7 | Missing `locale` returns HTTP 400 `INVALID_PARAMS` with `precondition_failed: locale` | Pass |
| 8 | Invalid locale tag, for example `not-a`, returns HTTP 400 `INVALID_PARAMS` | Pass |
| 9 | Focused and full BFF pytest suites pass locally | Pass |

## Verification

```text
python3 -m pytest services/control-plane/bff/tests/test_bff_me_locale.py
6 passed in 3.58s

python3 -m pytest services/control-plane/bff/tests/
39 passed in 14.71s
```

## Review Notes

- `_normalize_locale` rejects empty and malformed locale values before session persistence, then normalizes language, region, and script casing.
- `bff_update_locale` persists the normalized value into `session_lifecycle_store` with active session state and returns `_sem_session_current_response` with `operation.type = update_locale`.
- `GET /bff/me` prefers persisted session locale when no request locale headers are supplied, so the preference survives page reload style bootstrap calls.
- Authorization follows the existing BFF session surface gate via `_require_read_role`, matching the nearby session mutation implementation.
- The task did not need execute-plans changes; this endpoint is backend-side preference persistence for the existing client session surface.

No blocking issues found.
