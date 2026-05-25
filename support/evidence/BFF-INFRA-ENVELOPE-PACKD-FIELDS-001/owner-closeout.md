# BFF-INFRA-ENVELOPE-PACKD-FIELDS-001 Owner Closeout

Task: BFF-INFRA-ENVELOPE-PACKD-FIELDS-001
Owner: Codex
Reviewer: Claude
Date: 2026-05-25
Status before done: review_approved

## Delivered Scope

- Added Pack D D21 error metadata to BFF error envelopes:
  `error.i18nKey`, `error.retryable`, and `error.userActionable`.
- Added a 26-code behavior matrix in `services/control-plane/bff/main.py`
  keyed by canonical `ErrorCode`.
- Preserved existing `error.code`, `error.message`, `error.details`,
  `meta.correlationId`, and `X-Correlation-Id` behavior.
- Updated execute-plans-facing delta spec and audit records for the new fields.
- Extended focused HTTP envelope tests for 401, 404, 422, 500, direct JSON
  response, enum coverage, and matrix coverage.

## Publication

- Implementation PR: #577
- Implementation merge commit: `ca724f4430b2f537d57a94e90c5bf13eac54c91b`
- Implementation task commit: `f679a2e086dd4e8a16df47dbab0c5127d5f90c51`
- Reviewer approval artifact:
  `support/reviews/BFF-INFRA-ENVELOPE-PACKD-FIELDS-001-review-claude.md`
- Publish branch base: `origin/dev`
  `a72b2fba7722d04d2ec9675b59a63ac45c96dc57`, including
  PATH-DEDUPE PR #580.

## Owner Verification

Commands run from `task/BFF-INFRA-ENVELOPE-PACKD-FIELDS-001` during owner
closeout before the final publish branch:

```bash
python3 -m pytest services/control-plane/bff/test_bff_error_envelope_shape.py \
  services/control-plane/bff/test_final_contract_primitives.py -q
# 13 passed in 5.14s

python3 services/control-plane/bff/smoke_test.py
# Ran 25 tests in 1.168s - OK

python3 -m py_compile services/control-plane/bff/main.py \
  services/control-plane/bff/models.py \
  services/control-plane/bff/test_bff_error_envelope_shape.py \
  services/control-plane/bff/test_final_contract_primitives.py
# passed

git diff --check
# passed
```

Post-refresh verification after composing with `origin/dev`
`a72b2fba7722d04d2ec9675b59a63ac45c96dc57`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/control-plane/bff/test_bff_error_envelope_shape.py \
  services/control-plane/bff/test_final_contract_primitives.py -q
# 13 passed in 12.20s

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/control-plane/bff/tests/test_bff_path_dedupe.py -q
# 4 passed in 7.38s

PYTHONDONTWRITEBYTECODE=1 python3 services/control-plane/bff/smoke_test.py
# Ran 25 tests in 2.155s - OK

PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile \
  services/control-plane/bff/main.py \
  services/control-plane/bff/models.py \
  services/control-plane/bff/test_bff_error_envelope_shape.py \
  services/control-plane/bff/test_final_contract_primitives.py \
  services/control-plane/bff/tests/test_bff_path_dedupe.py
# passed

git diff --check
# passed
```

## Live Verification

Triggered nonprod deployment:

```bash
gh workflow run nonprod-deploy.yml -f environment=dev -f component=auto --ref dev
```

- Deployment run: `26392125605`
- Deployment ref: `dev`
- Deployment head SHA: `e9fe8df4df028a9fff432412902dd045473aa663`
- Status: success; completed at 2026-05-25T08:58:25Z.

Observed acceptance probe:

```bash
curl -i -sS -H 'Authorization: Bearer <dev-stub-token>' \
  https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io/bff/strategies/__nonexistent__
```

Result:

```text
HTTP/2 404
X-Correlation-Id: 69797885-00da-445b-b5d4-384197bc192a
```

```json
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "i18nKey": "errors.RESOURCE_NOT_FOUND",
    "message": "Strategy not found",
    "retryable": false,
    "userActionable": true
  }
}
```

Unauthenticated control probe also returned the new fields on 401
`AUTH_REQUIRED`.

## Closeout Notes

- Claude approved the implementation after reviewing the behavior matrix,
  i18n key derivation, HTTP envelope builders, tests, and docs.
- No L1 canonical architecture or policy document was changed during closeout.
- Publish branch was rebuilt from latest `origin/dev` because GitHub repeatedly
  rejected updates to the original already-merged task branch with remote
  `Internal Server Error`.
- The publish commit is limited to owner evidence and the reviewer approval
  artifact; implementation files are already merged through PR #577.
