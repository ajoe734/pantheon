# Task Brief: MGMT-SSE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Authenticated live SSE transport for the management console
- Status: review (handed off to Codex2)
- Owner: Claude
- Reviewer: Codex2
- Next: Implementation complete; execute-plans PR open and awaiting CI + human merge (execute-plans PR self-merge is governance-blocked for AI). Reviewer to review the diff; owner finalizes to `done` after merge.

## Summary
EventSource 帶不了 Authorization header→live SSE 必 401→頂欄 SNAPSHOT DATA 徽章；改 streaming fetch（liveSse.ts + agora/workshops.ts），修法範本 execute-plans PR #289。詳見 .orchestrator/task-briefs/mgmt_sse_001_authenticated_sse.md

## Delivered

- `execute-plans` PR: https://github.com/ajoe734/execute-plans/pull/300
  (branch `task/MGMT-SSE-001` → `dev`)
- `src/lib/bff-v1/sse/liveSse.ts`: replaced `EventSource` with an
  authenticated streaming `fetch()` (buildHeaders() + ReadableStream frame
  reader), preserving single-flight/backoff/reconnect-on-drop semantics.
- `src/lib/bff-v1/agora/workshops.ts` (`openWorkshopStream`): same fix,
  plus a new reconnect-with-backoff loop (fetch has no native
  auto-reconnect) and fixed the base-URL bug (was a relative URL bypassing
  `VITE_BFF_BASE_URL`).
- `src/lib/bff-v1/sse/protocol.ts`: extracted shared `parseSseFrame`/
  `readSseFrames` helpers so both call sites share one frame parser.
- Updated `liveAdapters.test.ts` and `workshops.test.ts` to mock
  `fetch`/`ReadableStream` instead of `EventSource`.

## Verification (execute-plans worktree)

- `npx vitest run`: 141 files / 1284 tests pass (1 pre-existing flake in
  `useV5Live.test.tsx`, unrelated subsystem, confirmed passing on rerun)
- `npx tsc -p tsconfig.app.json --noEmit`: 99 pre-existing errors, 0 in
  touched files
- `npx eslint` on touched files: clean
- `npx vite build`: passes

## Discovered during this dispatch

Found an earlier uncommitted draft of this same fix already sitting in the
dedicated execute-plans worktree (`/tmp/pantheon-worker-worktrees/execute-plans/mgmt-sse-001`,
branch `task/MGMT-SSE-001`) from a prior, un-anchored session — same
approach (fetch + ReadableStream) but never verified and not committed.
Backed it up to a patch file, superseded it with this verified
implementation (which additionally shares the frame parser via
`sse/protocol.ts` instead of duplicating it), and fast-forwarded the task
branch to `origin/dev` before applying.
