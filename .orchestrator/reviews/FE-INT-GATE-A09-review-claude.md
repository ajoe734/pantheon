# Review: FE-INT-GATE-A09 — probe-hosted-browser-bff replace networkidle wait

Reviewer: Claude
Date: 2026-05-14
Artifact: execute-plans/scripts/probe-hosted-browser-bff.mjs (Pantheon mirror commit fae37123)

## Acceptance Criteria Verification

| Criterion | Status | Evidence |
|---|---|---|
| waitUntil 不用 networkidle | PASS | `NAVIGATION_WAIT_UNTIL = "domcontentloaded"` (line 12); no networkidle reference |
| 改用 domcontentloaded+waitForResponse | PASS | `page.goto(..., { waitUntil: NAVIGATION_WAIT_UNTIL })` + `waitForCoreBffResponse` for /bff/me and /bff/v5/control-room |
| timeout 容錯 90s | PASS | `OVERALL_TIMEOUT_MS = 90_000` (line 10); page default timeouts set accordingly |
| oldUrlHitCount===0 維持驗證 | PASS | `pass` condition includes `oldUrlHitCount === 0` (line 193) |
| CI browser_probe step outcome=success | PASS | Codex verified: contains intended BFF URL=true, old BFF URL hit count 0, /bff/v5/control-room 200, request/response 9/9, failed 0 |

## Implementation Notes

- `waitForCoreBffResponse` promises are correctly started **before** `page.goto()` so Playwright captures responses during navigation — this is the canonical pattern.
- `/bff/me` is correctly modelled as optional (5s timeout, status 2xx–4xx acceptable).
- `/bff/v5/control-room` is required (remaining overall timeout, status 2xx–3xx).
- `remainingTimeoutMs()` gates all sub-operations against the 90s wall clock, preventing timeout drift.
- Pass logic is complete and matches the acceptance surface.

## Decision

APPROVED — all acceptance criteria met, implementation is correct.
