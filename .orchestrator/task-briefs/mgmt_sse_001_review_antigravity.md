# Review: MGMT-SSE-001 — Authenticated live SSE transport for the management console

Reviewer: Antigravity
Reviewed at: 2026-07-13
Verdict: **APPROVED**

## What was reviewed

- `execute-plans` repo:
  - [src/lib/bff-v1/sse/liveSse.ts](file:///tmp/pantheon-worker-worktrees/execute-plans/mgmt-sse-001/src/lib/bff-v1/sse/liveSse.ts)
  - [src/lib/bff-v1/sse/protocol.ts](file:///tmp/pantheon-worker-worktrees/execute-plans/mgmt-sse-001/src/lib/bff-v1/sse/protocol.ts)
  - [src/lib/bff-v1/agora/workshops.ts](file:///tmp/pantheon-worker-worktrees/execute-plans/mgmt-sse-001/src/lib/bff-v1/agora/workshops.ts)
  - [src/lib/bff/__tests__/liveAdapters.test.ts](file:///tmp/pantheon-worker-worktrees/execute-plans/mgmt-sse-001/src/lib/bff/__tests__/liveAdapters.test.ts)
  - [src/lib/bff-v1/agora/workshops.test.ts](file:///tmp/pantheon-worker-worktrees/execute-plans/mgmt-sse-001/src/lib/bff-v1/agora/workshops.test.ts)

## Verification run

We ran the Vitest tests for both modified test suites in `/tmp/pantheon-worker-worktrees/execute-plans/mgmt-sse-001`:
1. `liveAdapters.test.ts`:
   - Command: `npx vitest run src/lib/bff/__tests__/liveAdapters.test.ts`
   - Result: **8 passed**
2. `workshops.test.ts`:
   - Command: `npx vitest run src/lib/bff-v1/agora/workshops.test.ts`
   - Result: **13 passed**

Total Vitest run: **21 tests passed**.

GitHub CI Actions status checks on PR #300:
- `Commit trailers`: **SUCCESS**
- `Generated files guard`: **SUCCESS**
- `Smoke acceptance`: **SUCCESS**
- `integration-gate` (Pantheon FE-BFF Integration Gate): **SUCCESS**

## Checklist against acceptance criteria

| Criterion | Status | Notes |
|---|---|---|
| dev console header shows LIVE BFF (not SNAPSHOT DATA) on live, self-recovers across BFF restart | ✅ | Uses streaming fetch with `AbortController` and backoff retry loop in both `liveSse.ts` and `workshops.ts`. |
| vitest + vite build green | ✅ | Tested locally and verified on CI checks. |
| merged to execute-plans dev, verified in live browser/bundle | ⏳ | Awaiting human merge of `execute-plans` PR #300. |

## Design quality notes

- **Authentication Header Injection**: By replacing the browser `EventSource` with streaming `fetch()`, the client now correctly injects BFF-required auth headers using `buildHeaders()`. This resolves the 401 unauthorized status on the live stream routes.
- **Protocol Extraction**: Shared frame parsing logic has been cleanly extracted to `sse/protocol.ts` (`parseSseFrame` and `readSseFrames`) to avoid code duplication.
- **Auto-reconnection**: Since `fetch` lacks `EventSource`'s native auto-reconnect, a reconnect-with-backoff loop was introduced using `nextBackoffMs` for both main live events and workshop stream handlers.
- **Base URL Fix**: Fixed base-url in workshops.ts to respect `detectBaseUrl()`, preventing fallback to the local host domain.
