# Review: BFF-LUV-FE-002

Reviewer: Codex2
Date: 2026-05-09
Decision: **approved**

## Scope Reviewed

Task: Wire execute-plans Management Console live read adapters
Owner: Claude

execute-plans commits reviewed: `890712d`, `124aa17`

Artifacts reviewed:
- `docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-002-management-read-adapters.md`
- `/home/lupin/code/execute-plans/src/lib/bff/client.ts`
- `/home/lupin/code/execute-plans/src/lib/bff-v1/lists.ts`
- `/home/lupin/code/execute-plans/src/lib/bff/__tests__/client.test.ts`
- `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts`
- `/home/lupin/code/execute-plans/.lovable/feedback/2026-05-07-final/Pantheon_BFF_OpenAPI_3_1.yaml`

## Rev1 Blocking Finding

1. `managementClient.rankingFormulas.get(id)` uses the list path builder instead of a detail path.

   `src/lib/bff/client.ts:124` passes `paths.rankingFormulas` into `liveOrMockDetail`. That builder is defined at `src/lib/bff-v1/paths.ts:98` as `() => /bff/ranking-formulas`, so the supplied `id` is ignored and live detail reads call the list endpoint. The OpenAPI contract includes the detail endpoint at `/bff/ranking-formulas/{id}`.

   This blocked acceptance because the task explicitly added canonical list/detail Management Console adapters; in real/hybrid live mode this one detail adapter did not address its detail route. Requested fix: add a detail path builder or inline `${paths.rankingFormulas()}/${encodeURIComponent(id)}`, and add a focused test that stubs live fetch and asserts `rankingFormulas.get("rank_1")` calls `/bff/ranking-formulas/rank_1`.

## Rev2 Resolution

Approved. Commit `124aa17` fixes the ranking formulas detail adapter by building
`/bff/ranking-formulas/{id}` with `encodeURIComponent(id)` and adds focused live
URL coverage for `managementClient.rankingFormulas.get("rank_1")`.

The FE-002 worktree surface is committed in `/home/lupin/code/execute-plans`.
There are unrelated dirty `runAction*` files in the same repo from FE-004 scope;
they were not part of this review decision.

## Verification Run

```bash
cd /home/lupin/code/execute-plans

npm test -- --run src/lib/bff/__tests__/client.test.ts
# Rev1: Passed: 1 test file, 15 tests.
# Rev2: Passed: 1 test file, 16 tests.

npm run build
# Rev1/Rev2: Passed. Vite emitted existing browserslist/chunk-size/dynamic-import warnings.
```

## Acceptance Assessment

Approved for owner closeout. The delivered list adapters, fallback behavior, and
Rev2 ranking formulas detail path correction satisfy the task acceptance surface
reviewed here.
