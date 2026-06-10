# Review: BFF-WRITE-P0-LIFECYCLE-004

- **Reviewer:** Claude
- **Reviewed at:** 2026-05-30
- **PR:** [#635](https://github.com/ajoe734/pantheon/pull/635)
- **Decision:** APPROVE

## Scope

Adds `POST /bff/command-confirmations/{token}/confirm` to
`services/control-plane/bff/main.py` (P0-4). Unblocks every guarded
high-risk write (retire / promote_live / runtime start / break-glass /
force-transition) by giving the FE a canonical confirm route.

## Pattern conformance

- Auth: `_extract_identity` + `_require_operator_role` (matches every other guarded write).
- Idempotency: `_resolve_final_idempotency_key` + `_GOV_BFF_IDEMPOTENCY` with request-hash conflict detection (409 on payload mismatch, replay returns cached result).
- Headers: `X-Correlation-Id`, `X-Request-Id`, `X-Dry-Run`, `Idempotency-Key` / `X-Idempotency-Key`.
- Envelope: `{data, meta}` with `evidenceKind="command.confirm"`, `dryRun`, `correlationId`, `requestId`, `snapshot_at`.
- Errors: canonical `ErrorCode` enum (`PRECONDITION_FAILED`, `VALIDATION_FAILED`, `RESOURCE_NOT_FOUND`, `IDEMPOTENCY_CONFLICT`), structured `details.precondition_failed` discriminators.
- Status codes: 202 Accepted on happy path; 200 on dry-run (overrides route default via `JSONResponse`); 404 / 412 / 422 / 409 on error paths. Matches FE expectations.

## Bug check

None spotted.

- Empty body handled (`try: payload = await request.json(); except: pass`).
- Body `confirm_token` validated against path `token` (412 on mismatch).
- `command_id` required (422 if missing).
- Token state check uses `_confirm_token_lifecycle_payload(token).get("status") == "available"` to detect "never issued" → 404 typed `RESOURCE_NOT_FOUND`.
- `_raise_if_confirm_token_expired` invoked before redeem.
- Audit event published only on non-dry-run path.

## Test coverage

`services/control-plane/bff/test_bff_write_gap_2026_05_28.py` adds 4 tests covering:

1. Unknown token → typed 404 `RESOURCE_NOT_FOUND` (acceptance gate per spec).
2. Body/path token mismatch → 412 `PRECONDITION_FAILED`.
3. Dry-run → 200, no audit event, no side effects.
4. Valid confirm → 202, audit event `command.confirm` published, idempotent replay returns cached payload.

Uses `_isolated_confirm_bff` context manager to snapshot/restore `command_store`, `_GOV_BFF_IDEMPOTENCY`, and SSE buffer. Clean teardown.

## CI

All checks green at review time: Commit trailers / Runtime mirror guard / Smoke acceptance.

## Decision

APPROVE — merge.
