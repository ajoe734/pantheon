# Review: FE-INT-GATE-B04 — F05 Sentinel remediation confirm-token envelope

Reviewer: Claude
Date: 2026-05-13
Artifact: execute-plans/e2e/04-sentinel-remediation.spec.ts

## Verdict: APPROVED

## Acceptance Criteria Check

| Criterion | Status | Evidence |
|---|---|---|
| Emergency action 缺 token 回 CONFIRM_TOKEN_REQUIRED non-2xx | PASS | Route injects 428 with `error.code: CONFIRM_TOKEN_REQUIRED`; test asserts `response.status() >= 400` and `payload.error.code === "CONFIRM_TOKEN_REQUIRED"` |
| UI 不顯示 requires_confirm_token 為 success | PASS | Three-layered assertion: `CONFIRM_TOKEN_SUCCESS_TEXT` regex, standalone `requires_confirm_token`, and "Remediation executed/處置已執行" toast — all must be absent |
| Advisory action 可執行或 queue | PASS | Advisory POST returns 202 `queued`; page must show "Remediation executed|Open incident|處置已執行" |

## Route Injection Quality

- `installB04Routes` intercepts `**/*` with correct precedence: emergency POST → 428, advisory POST → 202, `/bff/me` → auth session, health endpoints → ok, `/bff/v5/sentinel/findings` GET → B04 fixture, other GET `/bff/*` → neutral stub, remaining → `route.continue()`.
- `isRemediationPost` covers both body-field dispatch and path-pattern fallback, making route matching robust against different UI call shapes.

## Token Precondition Verification

- The spec explicitly asserts `body?.confirmToken ?? body?.confirm_token ?? body?.x_confirm_token` is `undefined`, confirming the missing-token precondition scenario is exercised.

## liveStatus Remains Live

- `/health`, `/healthz`, `/bff/health` return `status: ok` and `/bff/me` returns an authenticated session, preventing the UI from entering a degraded/offline state during the test.

## Async Correctness

- Test 1: `emergencyResponse = page.waitForResponse(...)` is created before `submitEmergencyConfirm` triggers the POST, then awaited — correct sequencing, no race.
- Test 2: `advisoryResponse` is set up before `clickActionRun` — same correct pattern.

## Notes

- `nowIso()` returns a hardcoded timestamp `2026-05-13T13:40:00Z`. Acceptable for deterministic route stubs; the fixture date matches the sprint date.
- Codex2 verified: esbuild bundle passed; `playwright --list` shows 2 tests; advisory grep 1/1; emergency grep 1/1; full Playwright suite passed 2/2 against live-write Vite on `127.0.0.1:5176` with `VITE_BFF_MODE=live VITE_BFF_REAL_WRITES=true`.

## Decision

All three acceptance criteria satisfied. Route injection, precondition assertion, UI leak prevention, and advisory success path are all correct. Returning to owner for finalization.
