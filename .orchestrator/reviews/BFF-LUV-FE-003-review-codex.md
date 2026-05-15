# Review: BFF-LUV-FE-003

Reviewer: Codex
Date: 2026-05-09
Decision: **approved**

## Scope Reviewed

Task: Wire execute-plans Agora v5 and realtime live BFF
Owner: Codex2

Artifacts reviewed:
- `docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-003-agora-v5-realtime.md`
- `/home/lupin/code/execute-plans/src/lib/bff/v5.ts`
- `/home/lupin/code/execute-plans/src/lib/bff/realtime.ts`
- `/home/lupin/code/execute-plans/src/lib/bff/agora.ts`
- `/home/lupin/code/execute-plans/src/lib/bff/liveRead.ts`
- `/home/lupin/code/execute-plans/src/lib/bff-v1/sse/liveSse.ts`
- `/home/lupin/code/execute-plans/src/lib/bff-v1/useLiveList.ts`
- `/home/lupin/code/execute-plans/src/lib/bff/__tests__/liveAdapters.test.ts`

## Findings

No blocking issues found.

The implementation keeps mock-mode data paths available while making delivered Agora and v5 live reads strict in configured live mode. Live transport failure now surfaces as typed BFF errors for these read surfaces instead of returning seeded data. The EventSource bridge connects to `/bff/events/stream`, carries replay state through `lastEventId`, updates realtime connection state, and bridges typed SSE envelopes back onto the existing realtime topics.

Review note: the current Pantheon `/bff/events/stream` HTTP route is transitional liveness-only for browser EventSource because privileged domain replay still needs cookie-backed SSE auth. That is consistent with the backend route comments and does not block this frontend wiring task.

## Verification Run

```bash
cd /home/lupin/code/execute-plans

npm run test -- src/lib/bff/__tests__/liveAdapters.test.ts src/lib/v5/__tests__/bff.test.ts src/lib/bff-v1/__tests__/sse.test.ts
# Passed: 3 test files, 14 tests.

npm run test -- src/lib/bff/__tests__/liveAdapters.test.ts src/lib/bff/__tests__/client.test.ts
# Passed: 2 test files, 21 tests.

npm run build
# Passed. Vite emitted existing browserslist/chunk-size/dynamic-import warnings.

npm run test
# Passed: 47 test files, 409 tests.
```

## Acceptance Assessment

Approved for owner closeout. Owner should perform the normal `review_approved -> done` finalization, including a task-scoped commit that keeps FE-003 changes separate from unrelated FE-004 dirty work where possible.
