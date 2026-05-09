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

- `src/lib/bff/__tests__/client.test.ts` — 16 focused Vitest cases:
  family coverage, DTO normalization for representative entity-registry,
  realtime-feed, audit-feed, and governance-queue lists, mock-only fetch
  isolation, hybrid auto-fallback for network/5xx, strict-mode `BffError`
  surfacing for transport failure, 4xx propagation in both modes, and
  a live URL coverage test that asserts `rankingFormulas.get("rank_1")`
  calls `/bff/ranking-formulas/rank_1` (not the list endpoint).

### Verification

- `cd /home/lupin/code/execute-plans && npm test -- --run src/lib/bff/__tests__/client.test.ts`
  → `Test Files 1 passed (1)`, `Tests 16 passed (16)` (rev2: 15 → 16 after
  adding rankingFormulas live detail URL test).
- `cd /home/lupin/code/execute-plans && npm run build`
  → `built in 1m 2s`. Build succeeds. Only pre-existing chunk-size and
  dynamic-import warnings, no errors.

### Rev2 Post-Review Correction (2026-05-09 — Codex2 changes-requested)

`managementClient.rankingFormulas.get(id)` was passing `paths.rankingFormulas`
(a zero-arg list builder `() => '/bff/ranking-formulas'`) as the `pathFor`
argument to `liveOrMockDetail`. TypeScript allows a zero-arg function where
`(id: string) => string` is expected (structural subtyping), so the id was
silently ignored and live detail reads called the list endpoint.

Fix in `src/lib/bff/client.ts`: replaced `paths.rankingFormulas` with the
inline detail builder `(id) => \`${paths.rankingFormulas()}/${encodeURIComponent(id)}\``
matching the pattern already used for `runtimes`, `mcpServers`, `tools`, etc.

execute-plans commit: `124aa17`.

### Followups Outside Scope

- `BFF-LUV-FE-004` should fix the duplicate `readConfirmToken` declaration
  in `src/lib/bff/runAction.ts` so that the full Vitest run can collect
  `runAction.test.ts` again.
