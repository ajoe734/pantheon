# MGMT-LOAD-003 - Frontend Shell Fanout Reduction

Owner: Claude
Reviewer: Codex
Parent: `MGMT-GAP-010`
Depends on: `MGMT-LOAD-001`, `MGMT-LOAD-002`

## Problem

`TopBar` fetches full list payloads to render small counts, while
`JobProgressDrawer` fetches jobs again during mount. These shell reads compete
with the page's primary Evidence request and make a two-row page feel slow.

## Scope

- Make `TopBar` consume `/bff/management/shell-summary` for badge counts and
  session/transport truth.
- If shell summary is unavailable, defer full list reads until after route
  primary content has rendered and show honest stale/degraded count states.
- Share or lazily hydrate jobs state so first route load does not issue duplicate
  `/bff/jobs` requests.
- Keep notification-center and heavyweight drawer list hydration behind drawer
  open state or a post-primary-content idle callback.
- Keep accessibility and visible shell status intact while counts are loading,
  stale, or degraded.

## Acceptance

- `/management/evidence` starts no more than two non-primary BFF requests before
  first row or empty state is visible.
- No duplicate `/bff/jobs` request occurs before first row or empty state.
- Tests cover shell summary success, degraded summary, unavailable summary
  fallback, and lazy drawer hydration.
- Hosted probe evidence comes from `MGMT-LOAD-001` or its successor gate.

## 2026-07-01 Implementation Evidence

Task branch: `task/MGMT-LOAD-003` in `ajoe734/execute-plans` (frontend-checkout
FE repo, not this `pantheon` repo — see `frontend-checkout:` artifact prefix).
PR: [ajoe734/execute-plans#136](https://github.com/ajoe734/execute-plans/pull/136).

Implemented in the FE:

- `TopBar` now calls `fetchShellSummary()` (`src/lib/bff-v1/shellSummary.ts`)
  for `/bff/management/shell-summary` badge counts/session/transport instead
  of unconditionally fanning out to `/bff/approvals`, `/bff/alerts`, and
  `/bff/jobs` on every route mount.
- When shell-summary reports `unavailable`/`unknown` (transport failure or an
  explicit unavailable surface), TopBar shows an honest "unavailable" badge
  and defers the full-list fallback fetch until the route-primary-ready
  milestone has fired, then schedules it through `src/lib/idleTask.ts`
  (`requestIdleCallback` with a `setTimeout` fallback). For Evidence, that
  marker is emitted only after the row/empty/unavailable state has committed.
- `JobProgressDrawer` no longer fetches the full job list unconditionally on
  mount; it hydrates once when the operator opens the drawer or after the same
  route-primary-ready + idle path, so first route load no longer issues a
  duplicate `/bff/jobs` request alongside TopBar's former jobs read.
- `fetchShellSummary` deliberately does not report through the shared
  `liveStatus` transport signal on failure (unlike `withLiveOrMock`): a
  transport hiccup on this one narrow badge-count endpoint must not flip
  global transport state and spuriously re-trigger every effect keyed on it
  (other live reads may still be healthy). This was found via real-browser
  e2e testing after unit tests alone did not surface it — root-caused with
  console instrumentation against a local dev server to a mismatched e2e
  `/bff/me` fixture shape that was silently triggering a transport-failure
  branch and re-render loop.

Test coverage added: `src/lib/bff-v1/__tests__/shellSummary.test.ts`,
`src/lib/idleTask.test.ts`, `src/platform/components/TopBar.test.tsx`,
`src/platform/components/JobProgressDrawer.test.tsx` (unit/component level —
shell-summary success/degraded/unavailable, deferred full-list fallback,
lazy job-list hydration); `e2e/23-management-shell-fanout.spec.ts` (new,
CI-safe fixture-mocked Playwright spec, hard-gated on shell-summary
success/degraded/unavailable and jobs de-duplication); `e2e/22-management-evidence-load.spec.ts`
(MGMT-LOAD-001's baseline spec) updated with a shell-summary route fixture, a
corrected `/bff/me` fixture shape, and CORS-preflight-aware request counting
so its own fanout counters stay trustworthy.

Validation run:

```text
npx tsc --noEmit -p tsconfig.json
npx vitest run
  Test Files  109 passed (109)
       Tests  1043 passed (1043)
npx eslint <touched files>
  0 errors (1 pre-existing warning on JobProgressDrawer.tsx co-export, unrelated)
npx vite build
  succeeds
npx playwright test e2e/22-management-evidence-load.spec.ts e2e/23-management-shell-fanout.spec.ts
  4 passed (against a local vite dev server with fixture-mocked BFF routes)
```

Hosted dev-environment probe evidence was not produced in this worker;
per this task's own acceptance line, that comes from `MGMT-LOAD-001` or its
successor gate (`MGMT-LOAD-006`) once this branch is deployed to the dev FE.

## 2026-07-01 Reviewer Follow-up

Reviewer feedback on execute-plans PR #136 found that the unavailable
shell-summary path still scheduled the full-list fallback immediately after
summary failure, using only `requestIdleCallback`. On fixture-mocked routes,
that fallback could begin before the Evidence row/empty-state milestone and
therefore did not prove the `<= 2` non-primary BFF request target.

Follow-up FE commit:
`6dae62a7a697e8427ce2623c1ee0dca48e4dd418`
(`MGMT-LOAD-003: gate shell fallback on route ready`).

Follow-up changes:

- Added `src/platform/routePrimaryReady.ts`, a small shared marker/defer helper.
- Evidence list route now marks route-primary-ready after loading completes
  and the row/empty/unavailable state has committed.
- `TopBar` unavailable fallback waits for the current route's primary-ready
  marker, then idles before reading approvals/alerts full lists.
- `JobProgressDrawer` background hydration now also waits for
  route-primary-ready + idle; explicit drawer open still hydrates immediately.
- `e2e/23-management-shell-fanout.spec.ts` now delays the primary Evidence
  fixture in the unavailable case and asserts approvals/alerts/jobs requests do
  not start before the route-primary-ready timestamp. It also hard-checks that
  the only budgeted non-primary BFF requests before that marker are `/bff/me`
  and `/bff/management/shell-summary`.

Follow-up validation:

```text
npx tsc --noEmit -p tsconfig.json
npm run test -- src/platform/components/TopBar.test.tsx src/platform/components/JobProgressDrawer.test.tsx
npx eslint src/platform/routePrimaryReady.ts src/platform/components/TopBar.tsx src/platform/components/JobProgressDrawer.tsx src/management/pages/oversight/_core.tsx src/platform/components/TopBar.test.tsx src/platform/components/JobProgressDrawer.test.tsx e2e/23-management-shell-fanout.spec.ts
  0 errors (1 pre-existing warning on JobProgressDrawer.tsx co-export, unrelated)
npx playwright test e2e/23-management-shell-fanout.spec.ts
  3 passed against local Vite dev server
npx playwright test e2e/22-management-evidence-load.spec.ts
  1 passed; retained MGMT-LOAD-001 soft warning that baseline non-primary requests exceed 2
npm run build
  succeeds; retained existing Browserslist/Rollup/CSS/chunk-size warnings
```

## 2026-07-01 Owner Closeout

Frontend delivery PR:
[ajoe734/execute-plans#136](https://github.com/ajoe734/execute-plans/pull/136)
merged into `execute-plans/dev` at
`75a943ed3fb007c61f056496e5b8f7dfdb305a53`.

Pantheon evidence/follow-up PR:
[ajoe734/pantheon#2705](https://github.com/ajoe734/pantheon/pull/2705)
merged into `pantheon/dev` at
`3f9c91f0c70f37e6645b14cf03611890e645df1a`.

Closeout verification recorded by Codex2:

```text
gh pr view 136 --repo ajoe734/execute-plans --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,title,url,statusCheckRollup
  state MERGED; integration-gate SUCCESS
gh pr view 2705 --repo ajoe734/pantheon --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,title,url,statusCheckRollup
  state MERGED; Commit trailers, Runtime mirror guard, Smoke acceptance, and Orchestrator Sync SUCCESS
git diff --check
```

Owner closeout scope is record-only in this Pantheon repository. It does not
change runtime behavior, frontend code, route registry, BFF contracts, or L1
canonical architecture.
