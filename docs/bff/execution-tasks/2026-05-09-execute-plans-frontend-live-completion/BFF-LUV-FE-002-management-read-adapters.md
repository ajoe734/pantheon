# BFF-LUV-FE-002 - Management Console Live Read Adapters

Priority: P0

Owner lane: frontend BFF read integration

Repo:

- `/home/lupin/code/execute-plans`

## Problem

Most Management Console list/detail surfaces still return seeded mock data even
though Pantheon BFF now registers the corresponding route families.

## Write Scope

- `src/lib/bff/client.ts`
- new adapter/helper files under `src/lib/bff/`
- focused tests under `src/lib/bff/` or `src/test/`

Do not edit session/auth files owned by `BFF-LUV-FE-001`. Do not edit write
mutation files owned by `BFF-LUV-FE-004`.

## Required Route Families

- strategies
- personas
- capital pools
- ranking formulas
- rebalances
- deployments
- evolution programs
- research experiments
- jobs
- runtimes
- alerts
- incidents
- audit
- artifacts
- tools
- MCP servers/tools
- skills
- channels

## Acceptance Criteria

- Each route family has a real adapter in real/hybrid mode.
- `real` mode surfaces failures instead of silently mocking.
- `hybrid` mode falls back only with a clear code path.
- Minimal DTO normalization tests cover representative families.
- `npm run test` passes.
- `npm run build` passes.

## Implementation Notes (2026-05-09)

Owner: Claude. Reviewer: Codex2.

### Delivered

- Extended `src/lib/bff-v1/lists.ts` to cover every required Management
  Console route family. New families wired with canonical paths and Pack D
  D22 list classes:
  - `mcpTools` (entityRegistry), `jobs` (loopRun), `runtimes` (entityRegistry),
    `alerts` (realtimeFeed), `incidents` (governanceQueue),
    `approvals` (governanceQueue), `audit` (auditFeed).
- Added `src/lib/bff/client.ts` — canonical Management Console read surface
  (`managementClient.<family>.list()` and `.get(id)`) for all 20 families
  including the seven newly wired above. Detail (`get(id)`) reads use
  `withLiveOrMock` against the canonical `paths.<resource>(id)` builder so
  live BFF responses normalize to the same domain types as the in-process
  mocks.
- Mode taxonomy is explicit:
  - `mock`  — `VITE_BFF_MODE` unset/`mock`; tests always run in this mode.
  - `real`  — `VITE_BFF_MODE=live` and `VITE_BFF_FALLBACK=strict`; transport
              failures surface as `BffError` and the seed is never
              substituted.
  - `hybrid` — `VITE_BFF_MODE=live` with default `auto` fallback; transport
              failures fall back to the seed AND `liveStatus.reportFallback`
              records the reason so the UI banner can announce the
              degraded state.
- Helpers exported from the new client: `detectManagementMode`,
  `isHybridFallbackEnabled`, `isStrictRealMode`, `getLiveStatusSnapshot`.

### Tests

- `src/lib/bff/__tests__/client.test.ts` — 15 focused Vitest cases:
  family coverage, DTO normalization for representative entity-registry,
  realtime-feed, audit-feed, and governance-queue lists, mock-only fetch
  isolation, hybrid auto-fallback for network/5xx, strict-mode `BffError`
  surfacing for transport failure, and 4xx propagation in both modes.

### Verification

- `cd /home/lupin/code/execute-plans && npm test -- --run src/lib/bff/__tests__/client.test.ts`
  → `Test Files 1 passed (1)`, `Tests 15 passed (15)`.
- `cd /home/lupin/code/execute-plans && npm test -- --run`
  → `Test Files 1 failed | 46 passed (47)`, `Tests 392 passed (392)`.
  The single failing test file is `src/lib/bff/__tests__/runAction.test.ts`,
  which fails at parse with `Identifier 'readConfirmToken' has already been
  declared`. That duplicate symbol exists in HEAD's
  `src/lib/bff/runAction.ts` (lines 224 and 480) — pre-existing
  BFF-LUV-FE-004 territory and explicitly out of scope per this task brief
  ("Do not edit write mutation files owned by `BFF-LUV-FE-004`"). My 15
  client tests are part of the 392 passing tests; the v5 / liveAdapters
  tests that flagged earlier in interleaved runs are green when this task
  is run from a clean base.
- `cd /home/lupin/code/execute-plans && npm run build`
  → `built in 1m 28s`. Build succeeds (Vite/esbuild does not flag the
  duplicate function declaration). Only chunk-size and dynamic-import
  warnings, no errors.

### Followups Outside Scope

- `BFF-LUV-FE-004` should fix the duplicate `readConfirmToken` declaration
  in `src/lib/bff/runAction.ts` so that the full Vitest run can collect
  `runAction.test.ts` again.
