# TRN-002 Evidence: Trainer Session Endpoints

Task: TRN-002
Owner: Codex
Reviewer: Claude
Status: review approved; owner closeout verification complete

## Scope

TRN-002 covers the BFF trainer session endpoint contract:

- `POST /api/v1/trainer/sessions`
- `GET /api/v1/trainer/sessions`
- `GET /api/v1/trainer/sessions/{session_id}`
- `POST /api/v1/trainer/sessions/{session_id}/message`
- `GET /api/v1/trainer/sessions/{session_id}/controls`
- `POST /api/v1/trainer/sessions/{session_id}/patch`
- `GET /api/v1/trainer/sessions/{session_id}/preview`
- `POST /api/v1/trainer/sessions/{session_id}/preview`

Commit/discard/replay behavior is covered by TRN-004. Rapid-eval behavior is
covered by TRN-003.

## Implementation Notes

- Added TRN-002 BFF contract tests for create, list, detail, message, controls,
  patch, preview read, and preview refresh paths.
- Aligned BFF preview refresh ingress with the training-session service contract
  by accepting `{ "mode": "refresh" }` while preserving the existing
  `{ "refresh_mode": "manual" }` TW-03 body.
- Kept lifecycle validation explicit: completed trainer sessions fail preview
  refresh with `precondition_failed=status`.
- Documented the trainer session BFF surface in
  `services/training-session/README.md`.
- Worktree note: `services/control-plane/bff/main.py` also contains unrelated
  staged/concurrent ASK changes. TRN-002-owned hunks in that file are limited to
  `_tw03_validate_refresh_mode` and `refresh_trainer_preview` status ordering.

## Verification

- `pytest services/control-plane/bff/test_trn002_trainer_session_contract.py -q`
  - 25 passed
- `pytest services/control-plane/bff/test_tw03_before_after_compare_contract.py -q`
  - 4 passed
- `pytest services/control-plane/bff/test_tw01_teaching_dialog_contract.py services/control-plane/bff/test_tw02_parameter_controls_contract.py services/control-plane/bff/test_tw03_before_after_compare_contract.py -q`
  - 14 passed
- `pytest services/control-plane/bff/test_training_session_service_client.py -q`
  - 3 passed, 2 existing `datetime.utcnow()` deprecation warnings
- `pytest services/training-session/tests/test_http_service.py -q`
  - 7 passed
- `python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/read_store.py services/training-session/main.py`
  - passed

## Review And Closeout

- Reviewer approval: `support/reviews/TRN-002-review-claude.md`.
- Owner closeout verification reran the focused suites on 2026-05-16 with the
  same results: 25 TRN-002 contract tests passed, 14 TW regression tests passed,
  3 training-session service-client tests passed with 2 existing
  `datetime.utcnow()` deprecation warnings, 7 training-session service tests
  passed, and `py_compile` passed.
- Finalization uses a task-scoped commit for the TRN-002-owned files, with
  mixed `services/control-plane/bff/main.py` hunks isolated non-interactively.
