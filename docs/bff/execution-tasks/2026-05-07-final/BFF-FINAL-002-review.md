# BFF-FINAL-002 Review

Reviewer: Codex
Date: 2026-05-07
Status: approved

## Summary

Implementation reviewed and approved. The final `/bff/v1/commands` route now accepts the canonical `Idempotency-Key` header, preserves `X-Idempotency-Key` as an explicit compatibility alias, rejects body-level `idempotencyKey`, returns `CommandResponse<T>` with required `data`, and leaves the legacy `/api/v1/operator/commands` adapter behavior intact.

## Verification

Commands run and results observed:

```bash
python3 -m pytest services/control-plane/bff/test_governance_command_submission.py services/control-plane/bff/test_command_executor.py -q
```

Result:

```text
42 passed in 13.42s
```

## Acceptance Criteria

1. **Idempotency-Key works** - PASS
   - `/bff/v1/commands` accepts `Idempotency-Key`.
   - `X-Idempotency-Key` remains accepted as an alias when the canonical header is absent.
   - Canonical `Idempotency-Key` takes precedence when both headers are present.

2. **body idempotencyKey rejected** - PASS
   - Final route rejects body-level `idempotencyKey` with `400 INVALID_REQUEST` and `precondition_failed=body_idempotency_key`.

3. **replay conflict tested** - PASS
   - Same key plus same payload replays the existing `CommandResponse`.
   - Same key plus different payload returns `409 IDEMPOTENCY_CONFLICT`.

4. **legacy path explicit** - PASS
   - `/api/v1/operator/commands` remains on `CommandSubmissionResponse` and `X-Idempotency-Key`.
   - The contract document explicitly describes final versus legacy route behavior.

## Notes

No blocking findings. Task returned to owner (Codex2) for `review_approved` closeout.
