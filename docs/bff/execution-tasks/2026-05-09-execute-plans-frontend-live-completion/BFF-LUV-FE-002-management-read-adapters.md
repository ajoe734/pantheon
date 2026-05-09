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
